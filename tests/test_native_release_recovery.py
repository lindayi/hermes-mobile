"""Recovery exercises real temporary files/SQLite; no production mutations."""
import base64
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from deploy import self_deploy as bridge
try:
    from deploy import recover_native_release as recovery
except ImportError:
    recovery = None

RELEASE = '4ea1cabbd02341698cc6b871dd4971d6'
WORKER = 'hermes-mobile-native-fb0cc677245e412fb565be1112a0f5aa.service'


@pytest.fixture
def incident(tmp_path):
    source, state, webroot, live, systemd = [tmp_path / n for n in ('source', 'state', 'web', 'live', 'systemd')]
    for p in (source, state, webroot, live, systemd):
        p.mkdir(mode=0o700)
    old = state / 'releases' / ('a' * 32)
    old.mkdir(parents=True, mode=0o700)
    (state / 'releases').chmod(0o700)
    for root in (old, source):
        (root / 'backend').mkdir()
        (root / 'backend/native_api_service.py').write_text('launcher')
    (old / 'public').mkdir()
    for root in (old / 'public', webroot):
        (root / 'index.html').write_text('old public')
    (state / 'current').symlink_to(old)
    (state / 'deploy.lock').touch(mode=0o600)
    paths = bridge.Paths(source, state, webroot, live / 'runs.sqlite', systemd / 'bridge.conf')
    paths.dropin.write_bytes(b'[Service]\r\nWorkingDirectory=old\r\n')
    native_dropin = systemd / 'native.conf'
    failed = dict(status='rollback_failed', release=RELEASE,
                  error='Native idle wait timed out', rollback_error='Native verification failed')
    status = state / 'status.json'
    status.write_text(json.dumps(failed)); status.chmod(0o600)
    evidence_dir = state / 'recovery' / RELEASE
    evidence_dir.mkdir(parents=True, mode=0o700)
    evidence_dir.parent.chmod(0o700)
    snapshot = evidence_dir / 'native-rollback.json'
    baseline = dict(root=str(source), pid=2000616, legacy=True, caps={'version': 1})
    saved = dict(current=str(old), dropin_encoding='base64', native=baseline,
                 dropins={str(paths.dropin): base64.b64encode(paths.dropin.read_bytes()).decode(),
                          str(native_dropin): None})
    snapshot.write_text(json.dumps(saved)); snapshot.chmod(0o600)
    with sqlite3.connect(paths.database) as db:
        db.executescript('CREATE TABLE deployment_gate(singleton INTEGER PRIMARY KEY, owner TEXT);'
                         'CREATE TABLE runs(id TEXT, profile TEXT, status TEXT);')
        db.execute('INSERT INTO deployment_gate VALUES(1,?)', (RELEASE,))
        db.execute("INSERT INTO runs VALUES('historic','default','completed')")
    paths.database.chmod(0o600)
    health = {'status': 'ok', 'pid': 2000616, 'readiness': {'checks': {'background_queues': {
        'status': 'ok', 'active_api_runs': 0, 'process_completions': 78, 'active_delegations': 0}}}}
    events = []
    class Native:
        def attest(self, root, *, legacy):
            events.append(('attest', root, legacy))
            assert root == source and legacy is True
            return 2000616
        def request(self, path, *, authenticated=True):
            events.append(('request', path, authenticated))
            if not authenticated:
                raise HTTPError(path, 401, 'Unauthorized', {}, None)
            return health if path == '/health/detailed' else {'version': 1}
    worker_properties = dict(LoadState='loaded', ActiveState='failed', SubState='failed',
                             MainPID='0', ControlPID='0', Job='')
    units = []
    def run(command, **kwargs):
        events.append(('command', command))
        assert command[:3] in (['systemctl', '--user', 'show'], ['systemctl', '--user', 'list-units'])
        if command[2] == 'list-units':
            return SimpleNamespace(stdout=json.dumps(units))
        return SimpleNamespace(stdout='\n'.join(f'{k}={v}' for k, v in worker_properties.items()))
    def verify(root, backend):
        events.append(('verify', root, backend))
        assert root == old and backend is True
        assert (webroot / 'index.html').read_bytes() == (old / 'public/index.html').read_bytes()
    args = dict(release_id=RELEASE, worker_unit=WORKER, native=Native(), verify=verify,
                native_dropin=native_dropin, run=run)
    for path in tmp_path.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)
    return SimpleNamespace(**locals())


def gate(f):
    with sqlite3.connect(f.paths.database) as db:
        return db.execute('SELECT singleton, owner FROM deployment_gate').fetchall()


def test_abort_preserves_backlog_and_old_service(incident):
    f = incident
    assert recovery is not None, 'pre-mutation recovery module is not implemented'
    original_status, original_snapshot = f.status.read_bytes(), f.snapshot.read_bytes()
    result = recovery.recover(f.paths, **f.args)
    assert gate(f) == []
    assert result['status'] == 'rolled_back' and result['recovery_kind'] == 'pre_mutation_abort'
    assert json.loads(f.status.read_text()) == result
    assert (f.evidence_dir / 'original-failure.json').read_bytes() == original_status
    receipt = json.loads((f.evidence_dir / 'recovery-receipt.json').read_text())
    assert receipt['release'] == RELEASE and receipt['worker_unit'] == WORKER
    assert receipt['original_failure'] == f.failed
    assert f.snapshot.read_bytes() == original_snapshot
    assert (f.state / 'current').resolve() == f.old
    assert not f.native_dropin.exists()
    assert f.health['readiness']['checks']['background_queues']['process_completions'] == 78
    assert sum(e[0] == 'verify' for e in f.events) >= 2
    for path in (f.evidence_dir / 'original-failure.json', f.evidence_dir / 'recovery-receipt.json'):
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('change', ['active', 'pid', 'job', 'unknown', 'missing', 'other-worker', 'timer'])
def test_worker_must_be_demonstrably_terminated(incident, change):
    f = incident
    if change == 'active':
        f.worker_properties.update(ActiveState='active', SubState='running')
    elif change == 'pid':
        f.worker_properties['MainPID'] = '88'
    elif change == 'job':
        f.worker_properties['Job'] = '45'
    elif change == 'unknown':
        f.worker_properties['ActiveState'] = 'unknown'
    elif change == 'missing':
        f.worker_properties.pop('MainPID')
    else:
        f.units.append(dict(unit='hermes-native-controls-' + 'b' * 32 +
                           ('.timer' if change == 'timer' else '.service'),
                           load='loaded', active='active', sub='running'))
    original = f.status.read_bytes()
    with pytest.raises(RuntimeError, match='worker|unit'):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)] and f.status.read_bytes() == original


def test_worker_checked_again_after_public_verifier(incident):
    f = incident
    def verify(*args):
        f.worker_properties['MainPID'] = '55'
    f.args['verify'] = verify
    with pytest.raises(RuntimeError, match='worker|unit'):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]


@pytest.mark.parametrize('change', ['publication', 'state-mode', 'snapshot-mode', 'status-link',
                                  'snapshot-link', 'database-link', 'lock-link', 'native-dropin-link',
                                  'hardlink', 'native-root', 'extra-dropin', 'encoding',
                                  'snapshot-pid', 'snapshot-legacy', 'snapshot-caps', 'current-not-link'])
def test_unsafe_paths_or_snapshot_fail_closed(incident, change):
    f = incident
    if change == 'publication':
        (f.state / 'backups' / RELEASE).mkdir(parents=True)
    elif change == 'state-mode':
        f.state.chmod(0o755)
    elif change == 'snapshot-mode':
        f.snapshot.chmod(0o644)
    elif change.endswith('-link') and change != 'current-not-link':
        path = {'status-link': f.status, 'snapshot-link': f.snapshot, 'database-link': f.paths.database,
                'lock-link': f.state / 'deploy.lock', 'native-dropin-link': f.native_dropin}[change]
        other = f.tmp_path / ('outside-' + path.name)
        if path.exists():
            path.rename(other)
        path.symlink_to(other)
    elif change == 'hardlink':
        os.link(f.snapshot, f.tmp_path / 'snapshot-alias')
    elif change == 'current-not-link':
        (f.state / 'current').unlink()
        (f.state / 'current').mkdir()
        f.saved['current'] = str(f.state / 'current')
        f.snapshot.write_text(json.dumps(f.saved))
    else:
        if change == 'native-root':
            f.saved['native']['root'] = str(f.tmp_path)
        elif change == 'extra-dropin':
            f.saved['dropins'][str(f.tmp_path / 'evil')] = None
        elif change == 'encoding':
            f.saved['dropin_encoding'] = 'plain'
        elif change == 'snapshot-pid':
            f.saved['native']['pid'] = 2000616.0
        elif change == 'snapshot-legacy':
            f.saved['native']['legacy'] = 1
        elif change == 'snapshot-caps':
            f.saved['native']['caps'] = []
        f.snapshot.write_text(json.dumps(f.saved))
    with pytest.raises((RuntimeError, ValueError, OSError)):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]


@pytest.mark.parametrize('change', ['source', 'old-source', 'status', 'snapshot', 'pointer',
                                  'dropin', 'native-dropin', 'database', 'lock', 'publication'])
def test_last_verifier_races_cannot_clear_gate(incident, change):
    f = incident
    calls = 0
    def verify(*args):
        nonlocal calls
        calls += 1
        if calls != 2:
            return
        if change in ('source', 'old-source'):
            root = f.source if change == 'source' else f.old
            (root / 'backend/native_api_service.py').write_text('mutated')
        elif change in ('status', 'snapshot', 'database', 'lock'):
            path = {'status': f.status, 'snapshot': f.snapshot, 'database': f.paths.database,
                    'lock': f.state / 'deploy.lock'}[change]
            data = path.read_bytes()
            path.rename(path.with_suffix('.replaced'))
            path.write_bytes(data); path.chmod(0o600)
        elif change == 'pointer':
            (f.state / 'current').unlink()
            (f.state / 'current').symlink_to(f.source)
        elif change in ('dropin', 'native-dropin'):
            (f.paths.dropin if change == 'dropin' else f.native_dropin).write_text('changed')
        else:
            (f.state / 'backups' / RELEASE).mkdir(parents=True)
    f.args['verify'] = verify
    with pytest.raises((RuntimeError, ValueError, OSError)):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]


@pytest.mark.parametrize('change', ['health-status', 'pid-type', 'missing-counts', 'malformed-counts',
                                  'run-busy', 'delegation-busy', 'negative', 'bool-count', 'float-count',
                                  'pid-changed', 'caps', 'auth'])
def test_native_health_or_identity_mismatch_blocks_abort(incident, change):
    f = incident
    counts = f.health['readiness']['checks']['background_queues']
    if change == 'health-status':
        f.health['status'] = 'degraded'
    elif change == 'pid-type':
        f.health['pid'] = 2000616.0
    elif change == 'missing-counts':
        del counts['process_completions']
    elif change == 'malformed-counts':
        f.health['readiness'] = None
    elif change == 'run-busy':
        counts['active_api_runs'] = 1
    elif change == 'delegation-busy':
        counts['active_delegations'] = 1
    elif change == 'negative':
        counts['process_completions'] = -1
    elif change == 'bool-count':
        counts['active_api_runs'] = False
    elif change == 'float-count':
        counts['process_completions'] = 78.0
    elif change == 'pid-changed':
        f.args['native'].attest = lambda *a, **k: 2000617
    else:
        original = f.args['native'].request
        def request(path, *, authenticated=True):
            if change == 'auth' and not authenticated:
                return f.health
            if change == 'caps' and path == '/v1/capabilities':
                return {'version': 2}
            return original(path, authenticated=authenticated)
        f.args['native'].request = request
    with pytest.raises(RuntimeError):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)] and json.loads(f.status.read_text()) == f.failed


@pytest.mark.parametrize('tree_check', range(1, 7))
@pytest.mark.parametrize('target_kind', ['file', 'directory'])
@pytest.mark.parametrize('problem', ['world-writable', 'foreign-owner'])
def test_webroot_descendants_are_safe_at_every_recheck(incident, monkeypatch, tree_check,
                                                      target_kind, problem):
    f = incident
    # An empty descendant directory must be checked too, even under a name
    # omitted from source fingerprints; no privileged chown is needed.
    nested = f.webroot / '__pycache__'
    nested.mkdir(mode=0o700)
    target = nested / ('asset.js' if target_kind == 'file' else 'empty')
    if target_kind == 'file':
        target.write_text('published asset')
        target.chmod(0o600)
    else:
        target.mkdir(mode=0o700)
    original_status = f.status.read_bytes()
    original_snapshot = f.snapshot.read_bytes()
    original_lstat, original_trees = Path.lstat, recovery._trees
    calls, unsafe = 0, False

    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if unsafe and problem == 'foreign-owner' and path == target:
            values = list(info)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return info

    def trees(*args):
        nonlocal calls, unsafe
        calls += 1
        if calls == tree_check:
            unsafe = True
            if problem == 'world-writable':
                target.chmod(0o666 if target_kind == 'file' else 0o777)
        return original_trees(*args)

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(recovery, '_trees', trees)
    with pytest.raises(RuntimeError, match='Unsafe recovery path ownership, type or permissions'):
        recovery.recover(f.paths, **f.args)
    assert calls == tree_check
    assert gate(f) == [(1, RELEASE)]
    assert f.status.read_bytes() == original_status
    assert f.snapshot.read_bytes() == original_snapshot


def test_owner_source_group_modes_are_not_private_state_modes(incident):
    f = incident
    f.source.chmod(0o775)
    (f.source / 'backend/native_api_service.py').chmod(0o664)
    assert recovery.recover(f.paths, **f.args)['recovered'] is True


def test_recovered_status_is_private_even_with_permissive_umask(incident):
    f = incident
    before = os.umask(0o002)
    try:
        recovery.recover(f.paths, **f.args)
    finally:
        os.umask(before)
    assert f.status.stat().st_mode & 0o777 == 0o600


def test_changes_while_writing_receipt_fail_before_gate_delete(incident, monkeypatch):
    f = incident
    original = recovery._exclusive
    def write(path, data):
        original(path, data)
        if path.name == 'recovery-receipt.json':
            f.paths.dropin.write_text('raced')
    monkeypatch.setattr(recovery, '_exclusive', write)
    with pytest.raises(RuntimeError):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]
    assert json.loads(f.status.read_text()) == f.failed
    assert json.loads((f.evidence_dir / 'original-failure.json').read_text()) == f.failed


@pytest.mark.parametrize('phase', ['initial', 'during-verify'])
@pytest.mark.parametrize('problem', ['gate', 'running', 'unknown', 'null', 'other-profile'])
def test_journal_is_checked_again_and_never_clears_foreign_gate(incident, phase, problem):
    f = incident
    def mutate(*args):
        with sqlite3.connect(f.paths.database) as db:
            if problem == 'gate':
                db.execute("UPDATE deployment_gate SET owner='other'")
            else:
                state = None if problem == 'null' else ('running' if problem == 'other-profile' else problem)
                db.execute('INSERT INTO runs VALUES(?,?,?)', ('new', 'member', state))
    if phase == 'initial':
        mutate()
    else:
        f.args['verify'] = mutate
    with pytest.raises(RuntimeError):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, 'other' if problem == 'gate' else RELEASE)]
    assert json.loads(f.status.read_text()) == f.failed


@pytest.mark.parametrize('change', ['wrong-release', 'wrong-worker', 'wrong-error', 'wrong-status',
                                  'public-failure', 'no-database', 'lock-busy', 'existing-receipt'])
def test_refuses_unknown_or_incomplete_recovery(incident, change):
    import fcntl
    f = incident
    lock = None
    if change == 'wrong-release':
        f.args['release_id'] = '../other'
    elif change == 'wrong-worker':
        f.args['worker_unit'] = 'hermes-gateway.service'
    elif change in ('wrong-error', 'wrong-status'):
        record = dict(f.failed)
        record['error' if change == 'wrong-error' else 'status'] = 'different'
        f.status.write_text(json.dumps(record))
    elif change == 'public-failure':
        def fail(*a):
            raise RuntimeError('Public verification failed')
        f.args['verify'] = fail
    elif change == 'no-database':
        f.paths.database.unlink()
    elif change == 'lock-busy':
        lock = (f.state / 'deploy.lock').open()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        (f.evidence_dir / 'recovery-receipt.json').write_text('do not overwrite')
    original_status = f.status.read_bytes()
    try:
        with pytest.raises((RuntimeError, OSError, ValueError)):
            recovery.recover(f.paths, **f.args)
    finally:
        if lock:
            lock.close()
    assert f.status.read_bytes() == original_status
    if change != 'no-database':
        assert gate(f) == [(1, RELEASE)]
    else:
        assert not f.paths.database.exists()


def test_cli_wires_read_only_probes_and_explicit_incident(incident, monkeypatch, capsys):
    f = incident
    seen = []
    monkeypatch.setattr(recovery, 'NativeProbe', lambda source, **kw: f.args['native'])
    monkeypatch.setattr(bridge, 'verify_release', lambda paths, old, backend, **kw: seen.append((paths, old, backend)))
    assert recovery.main(['--release', RELEASE, '--worker-unit', WORKER], paths=f.paths,
                         native_dropin=f.native_dropin, run=f.run) == 0
    assert gate(f) == [] and len(seen) >= 2
    assert json.loads(capsys.readouterr().out)['recovered'] is True


def test_caps_require_exact_json_types_not_python_numeric_equality(incident):
    f = incident
    original = f.args['native'].request
    def request(path, **kw):
        return {'version': True} if path == '/v1/capabilities' else original(path, **kw)
    f.args['native'].request = request
    with pytest.raises(RuntimeError, match='capabilities'):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]


def test_old_release_directory_identity_is_pinned(incident):
    f = incident
    calls = 0
    def verify(*args):
        import shutil
        nonlocal calls
        calls += 1
        if calls == 2:
            renamed = f.old.with_name('b' * 32)
            f.old.rename(renamed)
            shutil.copytree(renamed, f.old)
    f.args['verify'] = verify
    with pytest.raises(RuntimeError, match='identity'):
        recovery.recover(f.paths, **f.args)
    assert gate(f) == [(1, RELEASE)]


def test_gate_is_write_locked_during_second_verification(incident):
    f = incident
    calls = 0
    def verify(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(f.paths.database, timeout=0) as db:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    db.execute("UPDATE deployment_gate SET owner='racer'")
    f.args['verify'] = verify
    recovery.recover(f.paths, **f.args)
    assert gate(f) == []


@pytest.mark.parametrize('failure', ['evidence-write', 'status-publish'])
def test_write_failure_preserves_original_failure_evidence(incident, monkeypatch, failure):
    f = incident
    if failure == 'evidence-write':
        original = recovery._exclusive
        def write(path, data):
            if path.name == 'recovery-receipt.json':
                raise OSError('disk full')
            original(path, data)
        monkeypatch.setattr(recovery, '_exclusive', write)
    else:
        def replace(*args):
            # Simulate an unrelated owner taking the gate after our commit.
            with sqlite3.connect(f.paths.database) as db:
                db.execute("INSERT INTO deployment_gate VALUES(1, 'other-owner')")
            raise OSError('publication failed')
        monkeypatch.setattr(recovery.os, 'replace', replace)
    with pytest.raises(OSError):
        recovery.recover(f.paths, **f.args)
    assert json.loads(f.status.read_text()) == f.failed
    assert json.loads((f.evidence_dir / 'original-failure.json').read_text()) == f.failed
    assert gate(f) == [(1, RELEASE if failure == 'evidence-write' else 'other-owner')]


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_sqlite_sidecar_symlinks_rejected_before_connect(incident, monkeypatch, suffix):
    f = incident
    sidecar = Path(str(f.paths.database) + suffix)
    sidecar.symlink_to(f.tmp_path / 'outside-sidecar')
    connected = []
    def connect(*a, **kw):
        connected.append(True)
        raise AssertionError('unsafe SQLite path reached connection')
    monkeypatch.setattr(recovery.sqlite3, 'connect', connect)
    with pytest.raises((RuntimeError, ValueError), match='[Ss]ymlink'):
        recovery.recover(f.paths, **f.args)
    assert not connected


def test_absent_native_dropin_directory_is_valid_unchanged_baseline(incident):
    f = incident
    new_path = f.native_dropin.parent / 'native.service.d' / '60-native-controls.conf'
    del f.saved['dropins'][str(f.native_dropin)]
    f.saved['dropins'][str(new_path)] = None
    f.snapshot.write_text(json.dumps(f.saved))
    f.args['native_dropin'] = new_path
    recovery.recover(f.paths, **f.args)
    assert gate(f) == []
    assert not new_path.parent.exists()

