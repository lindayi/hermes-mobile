import importlib.util
from types import SimpleNamespace

import pytest


def test_native_tick_preserves_claims_and_uses_only_mobile_adapter(tmp_path):
    assert importlib.util.find_spec('backend.member_scheduler') is not None, 'Missing scheduler-only entrypoint'
    from backend.member_scheduler import guarded_tick
    job = {'id':'a'*12, 'deliver':'mobile_delivery:member', 'execution_id':'native-execution'}
    calls = []
    class Adapter:
        async def send(self, recipient, text, metadata):
            calls.append((recipient,text,metadata))
            return SimpleNamespace(success=True)
    adapter = Adapter()
    sdk = SimpleNamespace(load_jobs=lambda:[job])
    def run_one_job(record, **kwargs):
        assert record is job  # never replace the native claim/execution identity
        assert kwargs['adapters'] == {'mobile_delivery':adapter}
        assert sdk._deliver_result(record,'harmless',**kwargs) is None
        return True
    sdk.run_one_job = run_one_job
    sdk._deliver_result = lambda *a,**kw: pytest.fail('Native default delivery must never run')
    def tick(**kwargs):
        assert kwargs['sync'] is True
        assert kwargs['can_dispatch']()
        sdk.run_one_job(job, adapters=kwargs['adapters'])
        return 1
    sdk.tick = tick
    native_delivery = sdk._deliver_result
    assert guarded_tick(sdk,adapter,lambda:True) == 1
    assert calls == [('member','harmless',{'job_id':'a'*12,'run_id':'native-execution'})]
    assert sdk.run_one_job is run_one_job
    assert sdk._deliver_result is native_delivery


@pytest.mark.parametrize('bad', [None,'local','origin','all','whatsapp:owner','mobile_delivery:owner','mobile_delivery:member,whatsapp:owner'])
def test_bad_destination_never_reaches_native_tick(bad):
    from backend.member_scheduler import guarded_tick
    from backend.member_runtime import ActivationError
    sdk = SimpleNamespace(load_jobs=lambda:[{'id':'a'*12,'deliver':bad}],
                          tick=lambda **kw:pytest.fail('unsafe dispatch'))
    with pytest.raises(ActivationError): guarded_tick(sdk,None,lambda:True)


def test_scheduler_lock_is_exclusive(tmp_path):
    from backend import member_scheduler as module
    assert hasattr(module, 'scheduler_lock'), 'Missing whole-process family scheduler lock'
    with module.scheduler_lock(tmp_path):
        with pytest.raises(BlockingIOError):
            with module.scheduler_lock(tmp_path): pass
    with module.scheduler_lock(tmp_path): pass


def test_isolated_native_loader_uses_only_member_credentials(tmp_path):
    import os
    import subprocess
    from pathlib import Path
    from backend import member_scheduler as module
    assert hasattr(module, 'load_native'), 'Missing isolated scheduler SDK bootstrap'
    python = Path('/usr/local/lib/hermes-agent/venv/bin/python')
    if not python.exists(): pytest.skip('Native SDK interpreter not installed')
    home = tmp_path/'member_test'
    home.mkdir(mode=0o700)
    for name,text in [('.env','OPENAI_API_KEY=fixture-member\nHOME=/owner\n'),
                      ('config.yaml','model: {provider: openai, default: fixture}\n'),
                      ('.mobile-jobs/token','fixture-token-'+'x'*40)]:
        path = home/name
        path.parent.mkdir(exist_ok=True,mode=0o700)
        path.write_text(text)
        path.chmod(0o600)
    code = '''
import os, sys
from pathlib import Path
from backend.member_scheduler import load_native
assert 'hermes_cli.auth' not in sys.modules
home = Path(sys.argv[1])
sdk, adapter = load_native(home, {'profile':'member_test','bindings':{'member':'profile'},'token_file':'.mobile-jobs/token'})
import hermes_constants
assert os.environ['HOME'] == str(home)
assert os.environ['HERMES_HOME'] == str(home)
assert os.environ['OPENAI_API_KEY'] == 'fixture-member'
assert 'ANTHROPIC_API_KEY' not in os.environ
assert hermes_constants.get_default_hermes_root() == home
assert adapter.home == home
assert adapter.bindings == {'member':'profile'}
assert 'gateway.platforms.api_server' not in sys.modules
assert 'gateway.platforms.whatsapp' not in sys.modules
assert 'gateway.run' not in sys.modules
print('isolated-native-ok')
'''
    result = subprocess.run([str(python),'-c',code,str(home)], capture_output=True,text=True,timeout=45,
                            env={**os.environ,'OPENAI_API_KEY':'OWNER','ANTHROPIC_API_KEY':'OWNER'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'isolated-native-ok' in result.stdout


def test_real_native_tick_adapter_and_web_inbox(tmp_path):
    """Real SDK, subprocess script, adapter and ingress; only HTTP is in-process."""
    import os
    import subprocess
    from pathlib import Path
    python = Path('/usr/local/lib/hermes-agent/venv/bin/python')
    if not python.exists(): pytest.skip('Native SDK interpreter not installed')
    home = tmp_path/'member_test'
    home.mkdir(mode=0o700)
    for name,text in [('.env',''), ('config.yaml','model: {provider: openai, default: fixture}\ncron: {wrap_response: false}\n'),
                      ('.mobile-jobs/token','fixture-token-'+'x'*40), ('scripts/fixture.py',"print('FAMILY_FIXTURE_RECEIPT')\n")]:
        path = home/name
        path.parent.mkdir(exist_ok=True,mode=0o700)
        path.write_text(text)
        path.chmod(0o600)
    code = '''
import asyncio, json, sys, time, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.member_scheduler import load_native, guarded_tick, scheduler_lock
home = Path(sys.argv[1])
sdk, adapter = load_native(home, {'profile':'member_test','bindings':{'member':'profile'},'token_file':'.mobile-jobs/token'})
from cron.jobs import create_job, get_job
from backend.delivery import build_delivery_router
from backend.notifications import NotificationService
from fastapi import FastAPI
import httpx
notifications = NotificationService(home/'inbox.sqlite')
app = FastAPI()
app.include_router(build_delivery_router(notifications, {'member_test':adapter.token}, lambda p,j: 'member-id' if p == 'member_test' and get_job(j) else None), prefix='/hermes/app-api')
real_client = httpx.AsyncClient
httpx.AsyncClient = lambda **kw: real_client(**kw, transport=httpx.ASGITransport(app=app))
with scheduler_lock(home):
    job = create_job(prompt=None, schedule=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(), name='fixture', script='fixture.py', no_agent=True, deliver='mobile_delivery:member')
    time.sleep(1.1)
    assert guarded_tick(sdk,adapter,lambda:True) == 1
    assert guarded_tick(sdk,adapter,lambda:True) == 0
items = notifications.list_inbox('member-id')
assert len(items) == 1, items
assert items[0]['body'].strip() == 'FAMILY_FIXTURE_RECEIPT', items
with sqlite3.connect(home/'inbox.sqlite') as db:
    assert db.execute('SELECT delivery_id FROM inbox').fetchone()[0].startswith(job['id']+':')
assert 'gateway.platforms.whatsapp' not in sys.modules
print('real-native-receipt-ok')
'''
    result = subprocess.run([str(python),'-c',code,str(home)],capture_output=True,text=True,timeout=60,env=os.environ.copy())
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'real-native-receipt-ok' in result.stdout


def test_rechecks_claimed_job_destination_after_prescan():
    from backend.member_scheduler import guarded_tick
    from backend.member_runtime import ActivationError
    safe = {'id':'a'*12,'deliver':'mobile_delivery:member'}
    sdk = SimpleNamespace(load_jobs=lambda:[safe],run_one_job=lambda *a,**kw:pytest.fail('unsafe execution'),
                          _deliver_result=lambda *a,**kw:pytest.fail('unsafe routing'))
    def tick(**kwargs):
        sdk.run_one_job({**safe,'deliver':'whatsapp:owner','execution_id':'native-id'})
    sdk.tick = tick
    with pytest.raises(ActivationError): guarded_tick(sdk,None,lambda:True)


def test_scheduler_cli_is_fail_closed_and_redacts(capsys, monkeypatch):
    from backend import member_scheduler as module
    assert hasattr(module,'main'), 'Missing scheduler entrypoint'
    with pytest.raises(SystemExit) as exit: module.main(['--help'])
    assert exit.value.code == 0
    assert '--profile' in capsys.readouterr().out
    monkeypatch.setattr(module,'serve',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('PRIVATE SECRET')))
    with pytest.raises(SystemExit) as exit:
        module.main(['--config','/not/config','--member-id','abc','--profile','member_abc'])
    assert exit.value.code == 1
    output = capsys.readouterr()
    assert 'PRIVATE SECRET' not in output.out+output.err


def test_serve_requires_verification_before_sdk_import(tmp_path,monkeypatch):
    from backend import member_scheduler as module
    assert hasattr(module,'serve'), 'Missing scheduler lifecycle'
    monkeypatch.setattr(module,'load_native',lambda *a:pytest.fail('Native SDK must not load before validation'))
    with pytest.raises(Exception): module.serve(tmp_path/'missing','abc','member_abc')


@pytest.mark.parametrize('payload', ['{}','null','{"jobs":null}','{"jobs":[{"id":"aaaaaaaaaaaa","deliver":"origin"}]}'])
def test_scheduler_store_audit_rejects_missing_or_unsafe_job_records(tmp_path,payload):
    from backend import member_scheduler as module
    assert hasattr(module,'audit_store'), 'Missing strict raw native store validation'
    cron = tmp_path/'cron'
    cron.mkdir()
    path = cron/'jobs.json'
    path.write_text(payload)
    path.chmod(0o600)
    with pytest.raises(Exception): module.audit_store(tmp_path)
    assert path.read_text() == payload


def test_scheduler_store_audit_rejects_symlink_tick_lock(tmp_path):
    from backend import member_scheduler as module
    assert hasattr(module,'audit_store')
    (tmp_path/'cron').mkdir()
    owner = tmp_path/'owner-lock'
    owner.write_text('owner lock untouched')
    (tmp_path/'cron/.tick.lock').symlink_to(owner)
    with pytest.raises(Exception): module.audit_store(tmp_path)
    assert owner.read_text() == 'owner lock untouched'
