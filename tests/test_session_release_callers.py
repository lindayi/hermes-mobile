"""Private operational callers are always exercised on injected offline paths."""
import fcntl
import importlib.util
from pathlib import Path
import sqlite3
import pytest


def load(name):
    path=Path('/home/lindayi/.local/share')/f'hermes-mobile-{name}.py'
    spec=importlib.util.spec_from_file_location('scoped_'+name.replace('-','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def gate_paths(tmp_path):
    db=tmp_path/'runs.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE deployment_gate(singleton INTEGER PRIMARY KEY,owner TEXT); CREATE TABLE runs(status TEXT);')
    return tmp_path/'deploy.lock',db


def rows(db):
    with sqlite3.connect(db) as c:return c.execute('SELECT owner FROM deployment_gate').fetchall()


def test_live_probe_serializes_and_releases_only_proved_terminal_work(tmp_path):
    module=load('parallel-live-probe');lock,db=gate_paths(tmp_path)
    with module.ProbeGate(lock,db,'proof') as gate:
        assert rows(db)==[('proof',)]
        gate.dispatched=True
        gate.terminal=True
    assert rows(db)==[]


def test_live_probe_unknown_work_retains_gate(tmp_path):
    module=load('parallel-live-probe');lock,db=gate_paths(tmp_path)
    with pytest.raises(RuntimeError,match='unknown'):
        with module.ProbeGate(lock,db,'proof') as gate:
            gate.dispatched=True
            raise RuntimeError('unknown')
    assert rows(db)==[('proof',)]


def test_live_probe_failure_before_dispatch_releases_only_its_gate(tmp_path):
    module=load('parallel-live-probe');lock,db=gate_paths(tmp_path)
    with pytest.raises(RuntimeError,match='before'):
        with module.ProbeGate(lock,db,'proof'):raise RuntimeError('before')
    assert rows(db)==[]


@pytest.mark.parametrize('busy',['gate','run','lock','missing'])
def test_live_probe_refuses_other_operation_without_creation_or_mutation(tmp_path,busy):
    module=load('parallel-live-probe');lock,db=gate_paths(tmp_path)
    if busy=='missing':db.unlink()
    elif busy in ('gate','run'):
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO deployment_gate VALUES(1,'foreign')" if busy=='gate' else "INSERT INTO runs VALUES('unknown')")
    with lock.open('a') as held:
        if busy=='lock':fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises((RuntimeError,OSError,sqlite3.OperationalError)):
            with module.ProbeGate(lock,db,'proof'):pytest.fail('must refuse')
    if busy=='missing':assert not db.exists()
    elif busy=='gate':assert rows(db)==[('foreign',)]
    else:assert rows(db)==[]


def test_live_probe_cannot_clear_replaced_gate(tmp_path):
    module=load('parallel-live-probe');lock,db=gate_paths(tmp_path)
    with pytest.raises(RuntimeError,match='ownership'):
        with module.ProbeGate(lock,db,'proof'):
            with sqlite3.connect(db) as c:c.execute("UPDATE deployment_gate SET owner='foreign'")
    assert rows(db)==[('foreign',)]


def release_fixture(tmp_path):
    """Only harmless code is importable by the actual caller, even after live approval."""
    import json
    module=load('session-release')
    source=tmp_path/'candidate';source.mkdir(mode=0o700)
    state=tmp_path/'live';state.mkdir(mode=0o700)
    home=tmp_path/'owner-home';home.mkdir(mode=0o700)
    snapshots=tmp_path/'snapshots';snapshots.mkdir(mode=0o700)
    notification=state/'native-notifications.sqlite'
    notification.write_bytes(b'offline-only');notification.chmod(0o600)
    config=state/'config.json'
    config.write_text(json.dumps({'state_dir':str(state)}));config.chmod(0o600)
    stage=tmp_path/'stage';stage.mkdir();(stage/'README.md').write_text('unreviewed')
    marker=tmp_path/'activated.json'
    helpers={
        'deploy/__init__.py':'',
        'deploy/self_deploy.py':f'''from dataclasses import dataclass
from pathlib import Path
@dataclass
class Paths:
    source: Path

def run_checks(*args):
    raise AssertionError('unapproved staged code must not execute')

def deploy(paths, **kwargs):
    kwargs['checks'](Path({str(stage)!r}))
''',
        'deploy/native_controls_release.py':f'''from pathlib import Path
NATIVE_DROPIN=Path({str(tmp_path/'dropin')!r})
class NativeProbe:
    def __init__(self, source):
        self.source=source
        self.config=Path({str(config)!r})
        self.config_bytes=self.config.read_bytes()
''',
        'deploy/native_concurrency.py':f'''import json
from pathlib import Path

def HermesCap(home):
    return str(home)

def activate(paths, **kwargs):
    result=dict(source=str(paths.source), native=str(kwargs['native'].source),
                config=kwargs['config'], notifications=str(kwargs['notifications']),
                approved=kwargs['approved'], exclusive_ingress=kwargs['exclusive_ingress'],
                executed_bytes='REVIEWED', helper=__file__)
    Path({str(marker)!r}).write_text(json.dumps(result))
    return result
''',
        'README.md':'reviewed',
    }
    for name,data in helpers.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data)
    approval=tmp_path/'approved.json'
    approval.write_text(json.dumps({'source':str(source),'files':module.inventory(source)}))
    return dict(source=source,approval=approval,snapshots=snapshots,state=state,home=home,
                config=config,notification=notification,marker=marker)


def run_release(fixture, *, mode='--native-concurrency', before=''):
    import os,subprocess,sys
    # -I prevents pytest's already-imported deploy modules and PYTHONPATH from
    # masking what main() really imports. All operation code is harmless fixture code.
    script=f'''import importlib.util,sys,os,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('offline_caller','/home/lindayi/.local/share/hermes-mobile-session-release.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.SOURCE=Path({str(fixture['source'])!r})
module.APPROVAL=Path({str(fixture['approval'])!r})
module.SNAPSHOT_ROOT=Path({str(fixture['snapshots'])!r})
module.OWNER_HOME=Path({str(fixture['home'])!r})
module.NOTIFICATIONS=Path({str(fixture['notification'])!r})
module.NATIVE_CONFIG=Path({str(fixture['config'])!r})
sys.argv=['operator']+{([mode] if mode else [])!r}
{before}
module.main()
'''
    env=dict(os.environ,INVOCATION_ID='isolated-fixture',HERMES_HOME=str(fixture['home']))
    return subprocess.run([sys.executable,'-I','-B','-c',script],env=env,
                          capture_output=True,text=True,timeout=15)


def test_release_native_caller_is_explicit_and_offline_under_injection(tmp_path):
    import json
    fixture=release_fixture(tmp_path)
    result=run_release(fixture)
    assert result.returncode==0,result.stderr
    call=json.loads(fixture['marker'].read_text())
    assert call['approved'] is True and call['exclusive_ingress'] is True
    assert call['native']==call['source']
    assert Path(call['source']).parent==fixture['snapshots']
    assert Path(call['helper']).is_relative_to(call['source'])
    assert call['config']==str(fixture['home'])
    assert call['notifications']==str(fixture['notification'])
    assert not Path(call['source']).exists()
    assert list(fixture['snapshots'].iterdir())==[]
    assert call['executed_bytes']=='REVIEWED'
    result=run_release(fixture,before="os.environ.pop('INVOCATION_ID')")
    assert result.returncode!=0 and 'systemd' in result.stderr
    assert json.loads(fixture['marker'].read_text())==call


def test_release_checks_reject_copy_changed_after_initial_approval(tmp_path):
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,mode='--deploy')
    assert result.returncode!=0 and 'approval' in result.stderr
    assert 'unapproved staged code must not execute' not in result.stderr
    assert not fixture['marker'].exists()


def test_release_never_imports_candidate_changed_after_pin_check(tmp_path):
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,before='''original_inventory=module.inventory
changed=False
def race(root):
    global changed
    pins=original_inventory(root)
    if root==module.SOURCE and not changed:
        changed=True
        target=root/'deploy/native_concurrency.py'
        target.write_text(target.read_text().replace('REVIEWED','UNREVIEWED_CHANGED_AFTER_PIN_CHECK'))
    return pins
module.inventory=race
''')
    assert not fixture['marker'].exists(), 'actual caller executed candidate bytes changed after approval'
    assert result.returncode!=0 and 'approval' in result.stderr,result.stderr
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_imports_frozen_bytes_even_if_candidate_changes_after_snapshot(tmp_path):
    import json
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,before='''original_check=module.require_approved_stage
def race(stage,pins):
    original_check(stage,pins)
    if stage.parent==module.SNAPSHOT_ROOT:
        target=module.SOURCE/'deploy/native_concurrency.py'
        target.write_text(target.read_text().replace('REVIEWED','UNREVIEWED_LATE_CHANGE'))
module.require_approved_stage=race
''')
    assert result.returncode==0,result.stderr
    call=json.loads(fixture['marker'].read_text())
    assert call['executed_bytes']=='REVIEWED'
    assert Path(call['source']).parent==fixture['snapshots']
    assert list(fixture['snapshots'].iterdir())==[]


@pytest.mark.parametrize('cached',['deploy','deploy.native_concurrency','backend'])
def test_release_refuses_preimported_scoped_modules(tmp_path,cached):
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,before=f'''import types
sys.modules[{cached!r}]=types.ModuleType({cached!r})
''')
    assert result.returncode!=0 and 'Fresh caller' in result.stderr,result.stderr
    assert not fixture['marker'].exists()
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_does_not_fall_back_to_mutable_namespace_package(tmp_path):
    import json
    fixture=release_fixture(tmp_path)
    source=fixture['source']
    (source/'deploy/__init__.py').unlink()
    helper=source/'deploy/native_concurrency.py'
    helper.write_text('from backend import injected\n'+helper.read_text())
    fixture['approval'].write_text(json.dumps({'source':str(source),'files':load('session-release').inventory(source)}))
    result=run_release(fixture,before='''original_check=module.require_approved_stage
def race(stage,pins):
    original_check(stage,pins)
    if stage.parent==module.SNAPSHOT_ROOT:
        (module.SOURCE/'backend').mkdir()
        (module.SOURCE/'backend/injected.py').write_text('raise AssertionError("MUTABLE_MODULE_EXECUTED")')
        sys.path.append(str(module.SOURCE))
module.require_approved_stage=race
''')
    assert result.returncode!=0 and 'absent from approved snapshot' in result.stderr,result.stderr
    assert 'MUTABLE_MODULE_EXECUTED' not in result.stderr
    assert not fixture['marker'].exists()
    assert list(fixture['snapshots'].iterdir())==[]


def assert_legacy_staging_scope(module):
    """Pin the approval-bound caller, not the independently evolving main deployer."""
    import json
    # Extracted with ast.literal_eval from git show e1894f3:deploy/self_deploy.py;
    # the fixture records the full commit and source SHA-256. No candidate imports
    # or Git checkout/history are required to check this historical contract.
    recorded=json.loads((Path(__file__).parent/'fixtures/legacy-session-stage-scope.json').read_text())
    constants=recorded['constants']
    constants={name:set(value) if name=='SKIP' else tuple(value)
               for name,value in constants.items()}
    assert set(constants)=={'SKIP','SOURCE_TREES','SOURCE_FILES'}
    for name,value in constants.items():
        assert getattr(module,name)==value,f'Legacy {name} differs from its pinned staging contract'
    return constants


@pytest.mark.parametrize('name',['SKIP','SOURCE_TREES','SOURCE_FILES'])
@pytest.mark.parametrize('change',['added','removed'])
def test_release_legacy_staging_contract_rejects_scope_drift(monkeypatch,name,change):
    module=load('session-release')
    original=getattr(module,name)
    if isinstance(original,set):
        changed=original|{'unexpected-scope'} if change=='added' else original-{'.env'}
    else:
        changed=(*original,'unexpected-scope') if change=='added' else original[:-1]
    monkeypatch.setattr(module,name,changed)
    with pytest.raises(AssertionError,match=name):
        assert_legacy_staging_scope(module)


def test_release_snapshot_matches_staging_scope_and_never_copies_secrets(tmp_path):
    import json
    module=load('session-release');fixture=release_fixture(tmp_path)
    source=fixture['source']
    # This retained legacy candidate caller is not the canonical-main controller.
    # Current .github staging is covered by test_ci_workflow.py; partitioning by
    # test_ci_selection.py. Never repin this operator to follow current main.
    constants=assert_legacy_staging_scope(module)
    for name in constants['SOURCE_TREES']:
        (source/name).mkdir(exist_ok=True)
        (source/name/'approved.txt').write_text('reviewed '+name)
        for ignored in constants['SKIP']:
            (source/name/ignored).write_text('DO NOT COPY: fixture only')
    (source/'config.json').write_text('DO NOT COPY')
    (source/'.env').write_text('DO NOT COPY')
    (source/'public').mkdir();(source/'public/unreviewed.js').write_text('DO NOT COPY')
    fixture['approval'].write_text(json.dumps({'source':str(source),'files':module.inventory(source)}))
    result=run_release(fixture,before='''original_check=module.require_approved_stage
def inspect(stage,pins):
    original_check(stage,pins)
    if stage.parent==module.SNAPSHOT_ROOT:
        assert all(not module.SKIP.intersection(p.relative_to(stage).parts) for p in stage.rglob('*'))
        assert not (stage/'public').exists()
        for name in module.SOURCE_TREES:
            assert (stage/name/'approved.txt').read_text()=='reviewed '+name
        assert all(p.read_text()!='DO NOT COPY' for p in stage.rglob('*') if p.is_file())
module.require_approved_stage=inspect
''')
    assert result.returncode==0,result.stderr
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_default_is_readonly_pincheck_without_service_context(tmp_path):
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,mode='',before="os.environ.pop('INVOCATION_ID');module.SNAPSHOT_ROOT=Path('/nonexistent/offline-snapshot')")
    assert result.returncode==0,result.stderr
    assert not fixture['marker'].exists()
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_mutation_modes_are_mutually_exclusive(tmp_path):
    fixture=release_fixture(tmp_path)
    result=run_release(fixture,before="sys.argv.append('--deploy')")
    assert result.returncode!=0 and 'not allowed with argument' in result.stderr
    assert not fixture['marker'].exists()
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_rejects_attested_state_dir_mismatch_before_activation(tmp_path):
    import json
    fixture=release_fixture(tmp_path)
    fixture['config'].write_text(json.dumps({'state_dir':str(tmp_path/'different-private-state')}))
    result=run_release(fixture)
    assert not fixture['marker'].exists(), 'caller activated against DB outside attested state_dir'
    assert result.returncode!=0 and 'notification' in result.stderr.lower(),result.stderr
    assert list(fixture['snapshots'].iterdir())==[]


@pytest.mark.parametrize('unsafe',[
    'state_mode','db_mode','config_mode','home_mode','db_symlink','db_hardlink',
    'state_symlink','home_symlink','db_owner','state_owner','home_owner',
    'home_env','profile_env','config_env','changed_config','substituted_config',
])
def test_release_rejects_unsafe_native_bindings_before_activation(tmp_path,unsafe):
    import os
    fixture=release_fixture(tmp_path)
    before=''
    key={'state_mode':'state','db_mode':'notification','config_mode':'config','home_mode':'home'}.get(unsafe)
    if key:
        fixture[key].chmod(0o755 if fixture[key].is_dir() else 0o644)
    elif unsafe in ('db_symlink','state_symlink','home_symlink'):
        key={'db_symlink':'notification','state_symlink':'state','home_symlink':'home'}[unsafe]
        original=fixture[key];alias=original.with_name(original.name+'-alias')
        original.rename(alias);original.symlink_to(alias)
    elif unsafe=='db_hardlink':
        os.link(fixture['notification'],tmp_path/'another-name.sqlite')
    elif unsafe.endswith('_owner'):
        key={'db_owner':'notification','state_owner':'state','home_owner':'home'}[unsafe]
        before=f'''original_lstat=Path.lstat
def foreign_owner(path,*args,**kwargs):
    value=original_lstat(path,*args,**kwargs)
    if path==Path({str(fixture[key])!r}):
        items=list(value);items[4]=os.getuid()+1
        return os.stat_result(items)
    return value
Path.lstat=foreign_owner
'''
    elif unsafe=='home_env':
        before="os.environ['HERMES_HOME']='/tmp/not-the-owner-home'"
    elif unsafe=='profile_env':
        before="os.environ['HERMES_PROFILE']='other-profile'"
    elif unsafe=='config_env':
        before="os.environ['HERMES_MOBILE_CONFIG']='/tmp/not-the-listener-config'"
    elif unsafe=='changed_config':
        before='''original_read=Path.read_bytes
reads=0
def changed_after_capture(path):
    global reads
    data=original_read(path)
    if path==module.NATIVE_CONFIG:
        reads+=1
        if reads==1:
            path.write_bytes(data+b' ')
    return data
Path.read_bytes=changed_after_capture
'''
    elif unsafe=='substituted_config':
        before="module.NATIVE_CONFIG=module.NATIVE_CONFIG.with_name('another-config.json')"
    result=run_release(fixture,before=before)
    assert not fixture['marker'].exists(),f'activation accepted unsafe native binding: {unsafe}'
    assert result.returncode!=0,result.stdout
    assert list(fixture['snapshots'].iterdir())==[]


def test_release_approval_rechecked_against_staged_bytes(tmp_path):
    import hashlib
    module=load('session-release');source=tmp_path/'source';source.mkdir()
    (source/'README.md').write_text('reviewed')
    pins={'files':{'README.md':hashlib.sha256(b'reviewed').hexdigest()}}
    module.require_approved_stage(source,pins)
    (source/'README.md').write_text('unreviewed later copy')
    with pytest.raises(RuntimeError,match='approval'):
        module.require_approved_stage(source,pins)
