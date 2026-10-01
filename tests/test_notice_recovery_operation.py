"""Private one-operation executor tests: every mutable path is under tmp_path."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from test_notice_row_recovery import make_db, row, read_rows

SCRIPT = Path('/home/lindayi/.local/share/hermes-mobile-notification-repair/apply-recovery.py')


def load():
    assert SCRIPT.is_file(), 'private checked recovery executor is implemented'
    spec = importlib.util.spec_from_file_location('private_notice_operation', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    module = load()
    private = tmp_path/'private'; private.mkdir(mode=0o700)
    archive = make_db(tmp_path/'archive.sqlite', [row()])
    live = make_db(tmp_path/'live.sqlite')
    for p in (live, archive):
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO sessions VALUES('api_original')")
        p.chmod(0o600)
    auth = tmp_path/'auth.sqlite'; runs = tmp_path/'runs.sqlite'
    with sqlite3.connect(auth) as db:
        db.executescript("CREATE TABLE users(id,role,profile,status); INSERT INTO users VALUES('owner','owner','default','ready');")
    with sqlite3.connect(runs) as db:
        db.executescript("CREATE TABLE runs(user_id,profile,session_id,upstream_id,status); INSERT INTO runs VALUES('owner','default','api_original','run_original','completed'); CREATE TABLE deployment_gate(owner);")
    plan = private/'plan.json'; manifest = private/'manifest.json'
    manifest.write_text(json.dumps({'version':1,'files':{'profiles/default/state.db':{'sha256':digest(archive),'sqlite':True}}}))
    plan.write_text(json.dumps({'archive':str(archive),'archive_sha256':digest(archive), 'manifest_sha256':digest(manifest), 'owner_pid':1234, 'ids':['deleg_a'], 'routes':[{'id':'deleg_a','origin':'run_original','sid':'api_original','payload_hash':hashlib.sha256(row()['event_json'].encode()).hexdigest()}], 'replay_policy':'recovered-held-no-model-replay','inserted':1}))
    for p in (plan, manifest, auth, runs): p.chmod(0o600)
    lock = tmp_path/'deploy.lock'; lock.touch(mode=0o600)
    helper = Path(__file__).resolve().parents[1]/'deploy/notice_recovery.py'
    paths = dict(helper=helper, plan=plan, manifest=manifest, archive=archive, live=live, runs=runs, auth=auth, private=private, lock=lock)
    pins = dict(helper=digest(helper), plan=digest(plan), archive=digest(archive), manifest=digest(manifest), owner_pid=1234, count=1, pid=123, start=456, backlog=1)
    state = SimpleNamespace(calls=0, post_retained=1, busy=False, changed=False)

    def observe():
        state.calls += 1
        if state.changed: raise RuntimeError('baseline drift')
        retained = len(read_rows(live)) if state.post_retained else 0
        work = {key:0 for key in module.WORK}
        work['active_run_tasks'] = int(state.busy)
        return {'status':'ok','pid':123,'native_maintenance': {'version':1,'scope':'dedicated-listener','status':'ok','pid':123,'start_ticks':456,'work':work,'notifications':{'status':'ok','backlog':1,'durable_retained':retained,'unpreserved':1-retained,'policy':'retain-durable-no-drain-no-replay'}}}
    return module, paths, pins, state, observe


def test_default_check_is_read_only(tmp_path):
    mod, paths, pins, state, observe = fixture(tmp_path)
    before = {p:p.read_bytes() for p in paths.values() if p.is_file()}
    result = mod.run(paths=paths, pins=pins, observe=observe)
    assert result['status'] == 'checked-not-applied'
    assert read_rows(paths['live']) == {}
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert sorted(p.name for p in paths['private'].iterdir()) == ['manifest.json','plan.json']


def test_apply_backs_up_preserves_plan_and_proves_same_process_retention(tmp_path):
    mod, paths, pins, state, observe = fixture(tmp_path)
    plan = paths['plan'].read_bytes()
    result = mod.run(apply=True, paths=paths, pins=pins, observe=observe)
    assert result['status'] == 'applied-durable-proven'
    assert result['inserted'] == ['deleg_a']
    assert state.calls >= 2
    assert read_rows(paths['live'])['deleg_a']['delivery_state'] == 'dropped'
    assert paths['plan'].read_bytes() == plan
    backup = Path(result['preimage'])
    assert read_rows(backup) == {}
    assert backup.stat().st_mode & 0o777 == 0o600
    assert result['preimage_sha256'] == digest(backup)
    for audit in paths['private'].glob('recovery-*.json'):
        assert audit.stat().st_mode & 0o777 == 0o600
        receipt = json.loads(audit.read_text())
        assert receipt['preimage'] == str(backup)
        assert receipt['preimage_sha256'] == digest(backup)
    with sqlite3.connect(paths['runs']) as db:
        assert db.execute('SELECT * FROM deployment_gate').fetchall() == []


@pytest.mark.parametrize('failure', ['helper','plan','archive','manifest','owner','route','busy','baseline'])
def test_preflight_failures_do_not_insert(tmp_path, failure):
    mod, paths, pins, state, observe = fixture(tmp_path)
    if failure in ('helper','plan','archive','manifest'):
        pins[failure] = '0'*64
    elif failure == 'owner':
        with sqlite3.connect(paths['auth']) as db: db.execute("UPDATE users SET status='pending'")
    elif failure == 'route':
        with sqlite3.connect(paths['runs']) as db: db.execute("UPDATE runs SET user_id='foreign'")
    elif failure == 'busy': state.busy = True
    elif failure == 'baseline': state.changed = True
    with pytest.raises((ValueError,RuntimeError)):
        mod.run(apply=True, paths=paths, pins=pins, observe=observe)
    assert read_rows(paths['live']) == {}
    assert sorted(p.name for p in paths['private'].iterdir()) == ['manifest.json','plan.json']


def test_missing_postapply_payload_proof_never_claims_success_or_rewinds(tmp_path):
    mod, paths, pins, state, observe = fixture(tmp_path)
    state.post_retained = 0
    with pytest.raises(RuntimeError, match='preserv'):
        mod.run(apply=True, paths=paths, pins=pins, observe=observe)
    assert list(read_rows(paths['live'])) == ['deleg_a']
    records = [json.loads(p.read_text()) for p in paths['private'].glob('recovery-*.json')]
    assert any(r['status'] == 'needs-review-no-rewind' for r in records)
    assert not any(r['status'] == 'applied-durable-proven' for r in records)


def test_deployment_lock_contention_fails_without_inserting(tmp_path):
    import fcntl
    mod, paths, pins, state, observe = fixture(tmp_path)
    with paths['lock'].open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises((BlockingIOError, RuntimeError)):
            mod.run(apply=True, paths=paths, pins=pins, observe=observe)
    assert read_rows(paths['live']) == {}


@pytest.mark.parametrize('change', ['degraded','pid','start','count-only','partial'])
def test_health_requires_full_same_process_evidence(tmp_path, change):
    mod, paths, pins, state, observe = fixture(tmp_path)
    h = observe()
    if change != 'partial':
        h['native_maintenance']['notifications'].update(durable_retained=1, unpreserved=0)
    if change == 'degraded': h['status'] = 'degraded'
    elif change == 'pid': h['pid'] += 1
    elif change == 'start': h['native_maintenance']['start_ticks'] += 1
    elif change == 'count-only':
        h = {'status':'ok','pid':123,'readiness':{'checks':{'background_queues':{'process_completions':1}}}}
    with pytest.raises(RuntimeError):
        mod.validate_health(h, pins, full=True)


def test_unapproved_helper_cannot_run_even_dry_check(tmp_path):
    mod, paths, pins, state, observe = fixture(tmp_path)
    pins['helper'] = None
    with pytest.raises(RuntimeError, match='Reviewed helper'):
        mod.run(paths=paths, pins=pins, observe=observe)
    assert state.calls == 0


@pytest.mark.parametrize('replacement', ['symlink', 'file', 'ancestor', 'aba'])
def test_preimage_replaced_at_sqlite_open_never_overwrites_unrelated_database(tmp_path, monkeypatch, replacement):
    mod, paths, pins, state, observe = fixture(tmp_path)
    unintended = tmp_path/'unintended.sqlite'
    with sqlite3.connect(unintended) as db:
        db.execute('CREATE TABLE protected(value TEXT)')
        db.execute("INSERT INTO protected VALUES('must survive backup')")
    unintended.chmod(0o600)
    protected_bytes = unintended.read_bytes()
    live_before = paths['live'].read_bytes()
    connect = sqlite3.connect
    switched = False
    victim = unintended
    original = tmp_path/'created-preimage.sqlite'

    def raced_connect(database, *args, **kwargs):
        nonlocal switched, victim, original
        if str(database).split('?')[0].endswith('-preimage.sqlite') and not switched:
            switched = True
            backup, = paths['private'].glob('*-preimage.sqlite')
            if replacement == 'ancestor':
                moved_dir = tmp_path/'original-private'
                paths['private'].rename(moved_dir)
                original = moved_dir/backup.name
                paths['private'].mkdir(mode=0o700)
                backup.write_bytes(protected_bytes)
                victim = backup
            else:
                backup.rename(original)
                if replacement == 'file':
                    backup.write_bytes(protected_bytes)
                    victim = backup
                else:
                    backup.symlink_to(unintended)
            connection = connect(database, *args, **kwargs)
            if replacement == 'aba':
                backup.unlink()
                original.rename(backup)
                original = backup
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', raced_connect)
    error = None
    try:
        mod.run(apply=True, paths=paths, pins=pins, observe=observe)
    except (ValueError, RuntimeError, OSError, sqlite3.DatabaseError) as caught:
        error = caught
    assert switched, 'the deterministic preimage-open replacement ran'
    assert victim.read_bytes() == protected_bytes, 'backup must not overwrite the unrelated database'
    assert unintended.read_bytes() == protected_bytes
    assert original.read_bytes() == b'', 'reject before writing the created preimage too'
    assert paths['live'].read_bytes() == live_before
    assert isinstance(error, ValueError), 'replacement must fail closed before application'
    assert not list(paths['private'].glob('recovery-*.json'))


def test_cli_defaults_to_check_and_requires_explicit_apply(monkeypatch):
    mod = load(); calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return {'status':'checked-not-applied'}
    monkeypatch.setattr(mod, 'run', run)
    assert mod.main([]) == 0
    assert mod.main(['--check']) == 0
    assert mod.main(['--apply']) == 0
    assert calls == [{'apply':False},{'apply':False},{'apply':True}]
