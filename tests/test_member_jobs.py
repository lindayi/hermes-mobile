import importlib.util
import json
import os
import sqlite3

import pytest


def private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text)
    path.chmod(0o600)
    return path


@pytest.fixture
def activated(tmp_path, monkeypatch):
    from backend import profiles
    from backend.runtime_binding import activation_record
    root = tmp_path/'owner'
    home = root/'profiles/member_abc'
    monkeypatch.setattr(profiles, 'PROFILE_ROOT', home.parent)
    for name, text in [('.env', 'OPENAI_API_KEY=member-only'), ('SOUL.md', 'Member'),
                       ('config.yaml', 'model: {provider: openai, default: test}\n')]:
        private(home/name, text)
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    dbpath = state/'auth.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.executescript('CREATE TABLE users(id TEXT, role TEXT, profile TEXT, status TEXT); CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);')
        db.execute("INSERT INTO users VALUES ('abc','member','member_abc','ready')")
        db.execute("INSERT INTO settings VALUES ('provisioning:abc','provisioned')")
        db.execute('INSERT INTO settings VALUES (?,?)', ('runtime_activation:abc', activation_record('abc','member_abc',home,'http://127.0.0.1:18643','listener-member','smoke')))
    dbpath.chmod(0o600)
    config = private(state/'config.json', json.dumps({'state_dir': str(state), 'profiles': {'default':str(root),'member_abc':str(home)}, 'gateway_profiles':{'member_abc':{'url':'http://127.0.0.1:18643','token':'listener-member','execution_ready':True}}, 'upstream_token':'owner-token', 'delivery_tokens':{'default':'owner-delivery'}, 'job_delivery_targets':{'default':'mobile_delivery:owner'}}))
    private(root/'cron/jobs.json', '{"owner":"untouched"}')
    return dict(home=home, config=config, db=dbpath, root=root)


def test_prepare_commits_private_member_delivery_without_touching_owner(activated):
    assert importlib.util.find_spec('backend.member_jobs') is not None, 'Missing member delivery console'
    from backend.member_jobs import prepare
    a = activated
    before = json.loads(a['config'].read_text())
    result = prepare(a['config'], 'abc', 'member_abc')
    after = json.loads(a['config'].read_text())
    assert result == {'profile':'member_abc', 'prepared':True, 'service_started':False}
    assert after['delivery_tokens']['default'] == 'owner-delivery'
    assert after['job_delivery_targets']['default'] == 'mobile_delivery:owner'
    assert after['profiles'] == before['profiles']
    assert after['gateway_profiles'] == before['gateway_profiles']
    assert after['job_delivery_targets']['member_abc'] == 'mobile_delivery:member'
    token = (a['home']/'.mobile-jobs/token').read_text().strip()
    assert len(token) >= 32 and token == after['delivery_tokens']['member_abc']
    assert token not in {'owner-token','owner-delivery','listener-member'}
    assert (a['home']/'.mobile-jobs/token').stat().st_mode & 0o077 == 0
    descriptor = json.loads((a['home']/'.mobile-jobs/runtime.json').read_text())
    assert descriptor['bindings'] == {'member':'profile'}
    assert not (a['home']/'.mobile-jobs/verified.json').exists()
    assert (a['root']/'cron/jobs.json').read_text() == '{"owner":"untouched"}'
    with sqlite3.connect(a['db']) as db:
        assert db.execute('SELECT status FROM users').fetchone()[0] == 'ready'
    assert prepare(a['config'], 'abc', 'member_abc') == result
    assert (a['home']/'.mobile-jobs/token').read_text().strip() == token


@pytest.mark.parametrize('change', ['default','wrong_profile','pending','disabled','unprovisioned','unactivated','stale_proof','alias','symlink','conflicting_target','shared_token'])
def test_prepare_fails_closed(activated, change):
    from backend.member_jobs import prepare
    a = activated
    config = json.loads(a['config'].read_text())
    selected = 'member_abc'
    with sqlite3.connect(a['db']) as db:
        if change == 'default': selected = 'default'
        elif change == 'wrong_profile': selected = 'member_other'
        elif change in ('pending','disabled'): db.execute('UPDATE users SET status=?', (change,))
        elif change == 'unprovisioned': db.execute("DELETE FROM settings WHERE key='provisioning:abc'")
        elif change == 'unactivated': db.execute("DELETE FROM settings WHERE key='runtime_activation:abc'")
        elif change == 'stale_proof': config['gateway_profiles']['member_abc']['token'] = 'stale'
        elif change == 'alias': config['profiles']['member_other'] = str(a['home'])
        elif change == 'conflicting_target': config['job_delivery_targets']['member_abc'] = 'whatsapp:owner'
        elif change == 'symlink':
            (a['home']/'.mobile-jobs').symlink_to(a['root'], target_is_directory=True)
    a['config'].write_text(json.dumps(config))
    if change == 'shared_token':
        prepare(a['config'],'abc',selected)
        private(a['home']/'.mobile-jobs/token', 'owner-delivery'+'x'*40)
        config = json.loads(a['config'].read_text())
        config['delivery_tokens']['default'] = config['delivery_tokens']['member_abc'] = 'owner-delivery'+'x'*40
        a['config'].write_text(json.dumps(config))
    before = a['config'].read_bytes()
    with pytest.raises(Exception):
        prepare(a['config'],'abc',selected)
    assert a['config'].read_bytes() == before


def test_prepare_uses_shared_activation_lock_and_recovers_inert_stage(activated, monkeypatch):
    import fcntl
    from backend import member_jobs as module
    a = activated
    with open(str(a['config'])+'.activation.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): module.prepare(a['config'],'abc','member_abc')
    real = module.atomic_config
    def fail_commit(path, data):
        if path == a['config']: raise OSError('fixture disk failure')
        real(path,data)
    monkeypatch.setattr(module,'atomic_config',fail_commit)
    before = a['config'].read_bytes()
    with pytest.raises(OSError): module.prepare(a['config'],'abc','member_abc')
    assert a['config'].read_bytes() == before
    token = (a['home']/'.mobile-jobs/token').read_text()
    monkeypatch.setattr(module,'atomic_config',real)
    module.prepare(a['config'],'abc','member_abc')
    assert (a['home']/'.mobile-jobs/token').read_text() == token


def test_delivery_settings_require_verified_real_inbox_receipt(activated):
    from backend import member_jobs as module
    assert hasattr(module,'delivery_settings'), 'Missing validated member scheduler settings'
    a = activated
    module.prepare(a['config'],'abc','member_abc')
    config, home, descriptor = module.delivery_settings(a['config'],'abc','member_abc',require_verified=False)
    assert home == a['home']
    assert descriptor['profile'] == 'member_abc'
    with pytest.raises(Exception): module.delivery_settings(a['config'],'abc','member_abc')
    private(home/'.mobile-jobs/verified.json', json.dumps({'version':1,'binding':'invented','receipt_id':'invented'}))
    with pytest.raises(Exception): module.delivery_settings(a['config'],'abc','member_abc')
    config['job_delivery_targets']['member_abc'] = 'whatsapp:owner'
    a['config'].write_text(json.dumps(config))
    with pytest.raises(Exception): module.delivery_settings(a['config'],'abc','member_abc',require_verified=False)


@pytest.mark.parametrize('loaded', [True, False])
def test_bounded_verify_real_native_receipt_and_owned_cleanup(activated, loaded):
    import subprocess
    from pathlib import Path
    from backend import member_jobs as module
    assert hasattr(module,'verify'), 'Missing executable native family delivery verification'
    python = Path('/usr/local/lib/hermes-agent/venv/bin/python')
    if not python.exists(): pytest.skip('Native interpreter not installed')
    a = activated
    module.prepare(a['config'],'abc','member_abc')
    code = '''
import json, sys
from pathlib import Path
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import profiles, member_jobs, member_scheduler
from backend.delivery import build_delivery_router
from backend.notifications import NotificationService
config_path, home, loaded = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == 'True'
profiles.PROFILE_ROOT = home.parent
config = json.loads(config_path.read_text())
notifications = NotificationService(Path(config['state_dir'])/'notifications.sqlite')
app = FastAPI()
def resolve(p,j):
    from cron.jobs import get_job
    return 'abc' if p == 'member_abc' and get_job(j) else None
app.include_router(build_delivery_router(notifications,config['delivery_tokens'] if loaded else {},resolve),prefix='/hermes/app-api')
client = TestClient(app)
requests = []
def handle(request):
    requests.append(json.loads(request.content))
    response = client.post(request.url.path,content=request.content,headers=dict(request.headers))
    return httpx.Response(response.status_code,json=response.json())
transport = httpx.MockTransport(handle)
real_client = httpx.AsyncClient
httpx.AsyncClient = lambda **kw: real_client(**kw,transport=transport)
real_load = member_scheduler.load_native
existing = []
def native(*args):
    sdk,adapter = real_load(*args)
    from cron.jobs import create_job
    (home/'scripts').mkdir(exist_ok=True)
    (home/'scripts/existing.py').write_text("raise RuntimeError('MUST NOT RUN')")
    job = create_job(prompt=None,schedule='every 1m',name='existing',script='existing.py',no_agent=True,deliver='mobile_delivery:member')
    existing.append(job)
    return sdk,adapter
member_scheduler.load_native = native
try:
    result = member_jobs.verify(config_path,'abc','member_abc',timeout=15,transport=transport)
    assert loaded, result
    assert result['verified'] is True
    assert result['receipt_id'] == notifications.list_inbox('abc')[0]['id']
    member_jobs.delivery_settings(config_path,'abc','member_abc')
except Exception:
    if loaded: raise
    assert not (home/'.mobile-jobs/verified.json').exists()
from cron.jobs import load_jobs
remaining = load_jobs()
assert len(remaining) == 1 and remaining[0]['id'] == existing[0]['id'],remaining
assert remaining[0].get('last_run_at') == existing[0].get('last_run_at')
assert list((home/'scripts').glob('family_verify_*.py')) == []
assert requests[0]['body'] == ''
assert len(notifications.list_inbox('abc')) == int(loaded)
print('verify-fixture-ok')
'''
    result = subprocess.run([str(python),'-c',code,str(a['config']),str(a['home']),str(loaded)],capture_output=True,text=True,timeout=45)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'verify-fixture-ok' in result.stdout


def test_member_jobs_cli_help_and_error_redaction(activated, monkeypatch, capsys):
    from backend import member_jobs as module
    assert hasattr(module,'main'), 'Missing supported console entrypoint'
    with pytest.raises(SystemExit) as exit:
        module.main(['--help'])
    assert exit.value.code == 0
    assert 'prepare' in capsys.readouterr().out
    monkeypatch.setattr(module,'prepare',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('PRIVATE SECRET')))
    with pytest.raises(SystemExit) as exit:
        module.main(['prepare','--config',str(activated['config']),'--member-id','abc','--profile','member_abc'])
    assert exit.value.code == 1
    captured = capsys.readouterr()
    assert 'PRIVATE SECRET' not in captured.out+captured.err


def test_bounded_worker_is_reaped_without_returning_fake_success():
    import sys
    import time
    from backend import member_jobs as module
    assert hasattr(module,'run_bounded'), 'Missing hard deadline for verification subprocess'
    start = time.monotonic()
    with pytest.raises(Exception):
        module.run_bounded([sys.executable,'-c','import time; time.sleep(30)'],0.2,os.environ.copy())
    assert time.monotonic()-start < 5
    assert module.run_bounded([sys.executable,'-c','print(\'{"verified": true}\')'],3,os.environ.copy()) == {'verified':True}


def test_cli_verify_is_bounded_and_always_schedules_owned_cleanup(activated,monkeypatch,capsys):
    from backend import member_jobs as module
    module.prepare(activated['config'],'abc','member_abc')
    calls = []
    def bounded(command,timeout,env):
        calls.append((command,timeout,env))
        raise RuntimeError('SECRET WORKER FAILURE')
    monkeypatch.setattr(module,'run_bounded',bounded)
    with pytest.raises(SystemExit):
        module.main(['verify','--config',str(activated['config']),'--member-id','abc','--profile','member_abc','--timeout','10'])
    assert len(calls) == 2
    assert calls[0][1] == 15 and calls[1][1] == 10
    assert calls[0][2]['HERMES_FAMILY_VERIFY_ATTEMPT'] == calls[1][2]['HERMES_FAMILY_VERIFY_ATTEMPT']
    assert calls[1][2]['HERMES_FAMILY_VERIFY_CLEANUP'] == '1'
    output = capsys.readouterr()
    assert 'SECRET WORKER FAILURE' not in output.out+output.err
