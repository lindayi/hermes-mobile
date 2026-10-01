"""Offline transactions: real staging/journal/publishing, fake service boundary."""
import json
from pathlib import Path
import pytest
from deploy import native_controls_release as release
from deploy import self_deploy as bridge
from backend.runs import RunJournal, RunConflict
from test_self_deploy import deploy_fixture


@pytest.fixture(autouse=True)
def approved_fixture_hashes(monkeypatch):
    import hashlib
    from backend import model_controls
    approved = {name: hashlib.sha256(content).hexdigest() for name, content in {
        'backend/native_api_service.py': b'old launcher',
        'backend/native_controls_service.py': b'new launcher',
        'backend/native_run_controls.py': b'controls',
        'backend/native_maintenance.py': b'maintenance',
        'backend/native_session_deletion.py': b'deletion',
        'backend/native_notifications.py': b'notifications',
    }.items()}
    monkeypatch.setattr(model_controls, '_CONTROL_HASHES', approved)
    monkeypatch.setattr(release, 'APPROVED_CONTROL_HASHES', approved, raising=False)


def test_prepublication_busy_abort_keeps_baseline_and_reopens_owned_gate(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    def idle(*a, **kw):
        return False  # actual incident: 78 completion notifications, no live runs
    args['native'].idle = idle
    def verify_idle(*a, **kw):
        raise RuntimeError('Native verification not idle')
    args['native'].verify = verify_idle
    def verify_unchanged(root, *, baseline):
        events.append(('unchanged-native', root))
    args['native'].verify_unchanged = verify_unchanged
    with pytest.raises(RuntimeError, match='Native idle wait timed out'):
        release.deploy(paths, idle_timeout=0, **args)
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    assert ('unchanged-native', old) in events
    assert (paths.state / 'current').resolve() == old
    assert not any(e[0] == 'command' for e in events)
    assert journal.submit('owner', 'default', 'new', 'ok', 'post-abort')[1]


@pytest.mark.parametrize('changed', ['bridge-dropin', 'native-dropin', 'dangling-native-dropin',
                                   'pointer', 'assets', 'foreign-gate'])
def test_preswitch_abort_rechecks_after_public_verifier(tmp_path, changed):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    args['native'].idle = lambda *a, **kw: False
    original = args['verify']
    calls = []
    saved_snapshot = []
    def verify(*a, **kw):
        original(*a, **kw)
        calls.append(True)
        if len(calls) != 2:
            return
        saved_snapshot.append(next((paths.state / 'recovery').glob('*/native-rollback.json')).read_bytes())
        if changed == 'bridge-dropin':
            paths.dropin.write_text('changed during verification')
        elif changed == 'native-dropin':
            dropin.write_text('changed during verification')
        elif changed == 'dangling-native-dropin':
            dropin.symlink_to(tmp_path / 'absent')
        elif changed == 'pointer':
            other = paths.state / 'releases' / ('b' * 32)
            other.mkdir()
            bridge.point_current(paths.state / 'current', other)
        elif changed == 'assets':
            (paths.webroot / 'index.html').write_text('changed during verification')
        else:
            with journal.connect() as db:
                db.execute("UPDATE deployment_gate SET owner='foreign'")
    args['verify'] = verify
    with pytest.raises(RuntimeError, match='Native rollback failed; admission gate remains closed'):
        release.deploy(paths, idle_timeout=0, **args)
    receipt = json.loads((paths.state / 'status.json').read_text())
    assert receipt == dict(status='rollback_failed', release=receipt['release'],
                          error='Native idle wait timed out', rollback_error=(
                              'Pre-mutation gate ownership changed' if changed == 'foreign-gate'
                              else 'Symlinked deployment paths are not allowed' if changed == 'dangling-native-dropin'
                              else 'Pre-mutation baseline changed'))
    with journal.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == [
            (1, 'foreign' if changed == 'foreign-gate' else receipt['release'])]
    snapshot = paths.state / 'recovery' / receipt['release'] / 'native-rollback.json'
    assert snapshot.read_bytes() == saved_snapshot[0]
    assert len(calls) == 2
    assert not any(e[0] == 'command' for e in events)


def test_preswitch_abort_requires_exactly_one_gate_deletion(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    args['native'].idle = lambda *a, **kw: False
    with journal.connect() as db:
        db.execute('CREATE TRIGGER keep_gate BEFORE DELETE ON deployment_gate '
                   'BEGIN SELECT RAISE(IGNORE); END')
    with pytest.raises(RuntimeError, match='Native rollback failed; admission gate remains closed'):
        release.deploy(paths, idle_timeout=0, **args)
    receipt = json.loads((paths.state / 'status.json').read_text())
    assert receipt == dict(status='rollback_failed', release=receipt['release'],
                          error='Native idle wait timed out',
                          rollback_error='Owned pre-mutation gate was not cleared')
    with journal.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == [
            (1, receipt['release'])]
    assert not any(e[0] == 'command' for e in events)


@pytest.mark.parametrize('changed', [None, 'dropin', 'native'])
def test_preswitch_abort_native_and_final_recheck_hold_both_locks(tmp_path, changed):
    import fcntl
    import sqlite3
    from contextlib import closing
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    args['native'].idle = lambda *a, **kw: False
    original = args['verify']
    public_calls = []
    native_calls = []
    def verify(*a, **kw):
        original(*a, **kw)
        public_calls.append(True)
        # No public-network work while the write transaction is held.
        with closing(sqlite3.connect(paths.database, timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE')
            db.rollback()
    args['verify'] = verify
    def verify_unchanged(root, *, baseline):
        assert len(public_calls) == 2
        native_calls.append(True)
        with (paths.state / 'deploy.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(sqlite3.connect(paths.database, timeout=0)) as db:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                db.execute('BEGIN IMMEDIATE')
        if changed == 'dropin':
            paths.dropin.write_text('changed during native boundary')
        elif changed == 'native':
            raise RuntimeError('native boundary changed')
    args['native'].verify_unchanged = verify_unchanged
    with pytest.raises(RuntimeError, match='Native rollback failed' if changed else 'Native idle wait timed out'):
        release.deploy(paths, idle_timeout=0, **args)
    receipt = json.loads((paths.state / 'status.json').read_text())
    assert receipt['status'] == ('rollback_failed' if changed else 'rolled_back')
    assert receipt['error'] == 'Native idle wait timed out'
    if changed:
        assert receipt['rollback_error'] == ('Pre-mutation baseline changed' if changed == 'dropin'
                                             else 'native boundary changed')
    with journal.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT singleton, owner FROM deployment_gate')] == (
            [(1, receipt['release'])] if changed else [])
    assert len(native_calls) == 1 and len(public_calls) == 2
    assert not any(e[0] == 'command' for e in events)


def test_unapproved_candidate_rejected_before_mutation(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    (paths.source / 'backend/native_run_controls.py').write_text('unapproved candidate')
    with pytest.raises(RuntimeError, match='approved'):
        release.deploy(paths, **args)
    assert not any(e[0] in ('capture', 'command') for e in events)
    assert (paths.state / 'current').resolve() == old


def test_mismatched_stage_constants_rejected(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    (paths.source / 'backend/model_controls.py').write_text('_CONTROL_HASHES = {}\n')
    with pytest.raises(RuntimeError, match='approved'):
        release.deploy(paths, **args)
    assert not any(e[0] in ('capture', 'command') for e in events)


def fixture(tmp_path):
    _, paths = deploy_fixture(tmp_path)
    (paths.source / 'backend/native_api_service.py').write_text('old launcher')
    old = bridge.stage_release(paths.source, paths.state / 'releases' / ('a' * 32))
    (old / 'public').mkdir()
    (old / 'public/index.html').write_text('<h1>old</h1>')
    bridge.point_current(paths.state / 'current', old)
    bridge.atomic_write(paths.dropin, 'old bridge dropin\n')
    (paths.source / 'backend/native_controls_service.py').write_text('new launcher')
    (paths.source / 'backend/native_run_controls.py').write_text('controls')
    (paths.source / 'backend/native_maintenance.py').write_text('maintenance')
    (paths.source / 'backend/native_session_deletion.py').write_text('deletion')
    (paths.source / 'backend/native_notifications.py').write_text('notifications')
    from backend.model_controls import _CONTROL_HASHES, _PREVIOUS_CONTROL_HASHES
    (paths.source / 'backend/model_controls.py').write_text(
        '_CONTROL_HASHES = ' + repr(_CONTROL_HASHES) + '\n_PREVIOUS_CONTROL_HASHES = ' + repr(_PREVIOUS_CONTROL_HASHES)
        + '\n_TIMEOUT_BASELINE_CONTROL_HASHES = ' + repr(release.TIMEOUT_BASELINE_CONTROL_HASHES))
    journal = RunJournal(paths.database)
    native_dropin = tmp_path / 'systemd/native.conf'
    events = []
    def verify(stage, backend, **kw):
        events.append(('bridge', stage))
        expected = kw.get('assets', stage / 'public')
        assert (paths.webroot / 'index.html').read_bytes() == (expected / 'index.html').read_bytes()
    class Native:
        def capture(self, baseline, bootstrap):
            events.append(('capture', baseline))
            return {'root': str(old), 'caps': {'legacy': True}}
        def idle(self, journal, baseline, *, required_ids=()):
            events.append(('idle', Path(baseline['root'])))
            return True
        def verify(self, root, *, baseline=None):
            events.append(('native', root))
        def verify_unchanged(self, root, *, baseline):
            events.append(('unchanged-native', root))
    def run(cmd, **kw):
        events.append(('command', cmd))
    def rollback_verify(root, baseline):
        assert (paths.state / 'current').resolve() == root
        with journal.connect() as db:
            assert [tuple(row) for row in db.execute(
                'SELECT singleton, owner FROM deployment_gate')] == [(1, baseline['gate_owner'])]
        events.append(('rollback-preservation', root))
        return True
    args = dict(checks=lambda stage: events.append(('checks', stage)), verify=verify,
                native=Native(), native_dropin=native_dropin, run=run,
                bootstrap_dedicated_native=True,
                handoff=lambda stage: None,
                probe=lambda stage: events.append(('receipt-probe', stage)),
                rollback_verify=rollback_verify)
    return paths, old, journal, native_dropin, events, args


def test_private_recovery_outside_stage(tmp_path):
    import base64
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    prior = paths.dropin.read_bytes()
    stage = release.deploy(paths, **args)
    assert not (stage / 'native-rollback.json').exists()
    recovery = paths.state / 'recovery' / stage.name
    snapshot = recovery / 'native-rollback.json'
    assert recovery.stat().st_mode & 0o777 == 0o700
    assert snapshot.stat().st_mode & 0o777 == 0o600
    data = json.loads(snapshot.read_text())
    assert base64.b64decode(data['dropins'][str(paths.dropin)]) == prior
    assert data['dropins'][str(dropin)] is None


def test_recovery_symlink_rejected(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    (paths.state / 'recovery').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='Symlink'):
        release.deploy(paths, **args)
    assert not any(e[0] == 'command' for e in events)


def test_activation_waits_for_parent_and_uses_one_checked_stage(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    parent, _ = journal.submit('owner', 'default', 's', 'parent', 'p')
    def sleep(_):
        assert not any(e[0] == 'command' for e in events)
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', 'other', 'blocked', 'b')
        journal.finish('owner', parent['id'], 'completed')
    stage = release.deploy(paths, sleep=sleep, **args)
    assert stage != old and (paths.state / 'current').resolve() == stage
    assert 'ExecStart=\n' in dropin.read_text()
    assert str(stage / 'backend/native_controls_service.py') in dropin.read_text()
    assert events.index(('bridge', old)) < events.index(('idle', old))
    assert ('native', stage) in events
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'
    assert journal.submit('owner', 'default', 'new', 'ok', 'n')[1]
    commands = [e[1] for e in events if e[0] == 'command']
    assert commands == [['systemctl', '--user', 'daemon-reload'],
                        ['systemctl', '--user', 'restart', 'hermes-mobile-api.service'],
                        ['systemctl', '--user', 'restart', 'hermes-mobile.service']]



@pytest.mark.parametrize('value', [None, {}, {'status': 'ok'}, {'readiness': {'checks': {'background_queues': {'status':'ok','active_api_runs':False,'process_completions':0,'active_delegations':0}}}}])
def test_unknown_native_counts_rejected(value):
    with pytest.raises(RuntimeError, match='counts'):
        release.require_native_quiescence(value)


def health(active=0):
    return {'pid': 123, 'readiness': {'checks': {'background_queues': {
        'status': 'ok', 'active_api_runs': active, 'process_completions': 0,
        'active_delegations': 0}}}}


def test_positive_known_idle_counts():
    assert release.require_native_quiescence(health()) is True
    assert release.require_native_quiescence(health(1)) is False


@pytest.mark.parametrize('content', [b'[Service]\r\nNoNewPrivileges=yes\r\n', b'\xff'])
def test_exact_dropin_byte_preimage(tmp_path, content):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    for path in (paths.dropin, dropin):
        path.write_bytes(content)
    def fail(root, *, baseline=None):
        if baseline is None:
            raise RuntimeError('injected activation')
    args['native'].verify = fail
    with pytest.raises((RuntimeError, UnicodeError)):
        release.deploy(paths, **args)
    for path in (paths.dropin, dropin):
        assert path.read_bytes() == content
    if content == b'\xff':
        assert not any(e[0] == 'command' for e in events)


@pytest.mark.parametrize('failure', ['publish', 'native', 'rollback'])
def test_rollback_exact_pointers_assets_and_owned_gate(tmp_path, monkeypatch, failure):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    dropin.write_text('old native dropin\n')
    if failure == 'publish':
        from deploy import assets
        original = assets.publish_assets
        def fail(*a):
            original(*a)
            raise RuntimeError('injected publish')
        monkeypatch.setattr(assets, 'publish_assets', fail)
    else:
        def fail(root, *, baseline=None):
            if baseline is None or failure == 'rollback':
                raise RuntimeError('injected native')
        args['native'].verify = fail
    with pytest.raises(RuntimeError, match='injected|rollback failed'):
        release.deploy(paths, **args)
    assert (paths.state / 'current').resolve() == old
    assert paths.dropin.read_text() == 'old bridge dropin\n'
    assert dropin.read_text() == 'old native dropin\n'
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    status = json.loads((paths.state / 'status.json').read_text())['status']
    assert status == ('rollback_failed' if failure == 'rollback' else 'rolled_back')
    if failure == 'rollback':
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', 's', 'x', 'x')
    else:
        assert journal.submit('owner', 'default', 's', 'x', 'x')[1]


@pytest.mark.parametrize('failure', ['protected', 'public', 'changed', 'unknown'])
def test_rejects_before_native_restart(tmp_path, failure):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    if failure == 'protected':
        (paths.source / 'requirements.lock').write_text('changed')
    elif failure == 'changed':
        args['checks'] = lambda stage: (stage / 'backend/serve.py').write_text('changed')
    elif failure == 'public':
        def fail(*a, **kw): raise RuntimeError('public failure')
        args['verify'] = fail
    else:
        row, _ = journal.submit('owner', 'default', 's', 'x', 'x')
        journal.finish('owner', row['id'], 'unknown')
    with pytest.raises(RuntimeError):
        release.deploy(paths, **args)
    assert not any(e[0] == 'command' for e in events)
    assert not dropin.exists()
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'



def test_native_idle_checks_known_latest_ids_and_counts(tmp_path):
    _, _, journal, _, _, _ = fixture(tmp_path)
    row, _ = journal.submit('owner', 'default', 's', 'x', 'x')
    with journal.connect() as db:
        db.execute("UPDATE runs SET status='completed', upstream_id='native1'")
    probe = object.__new__(release.NativeProbe)
    probe.attest = lambda root, legacy=False: 123
    calls = []
    def request(path, **kw):
        calls.append(path)
        return health() if path == '/health/detailed' else {'status': 'completed'}
    probe.request = request
    # This test isolates terminal-ID cache policy; full gate/readiness integration
    # is exercised in test_native_maintenance_integration.py.
    probe._ready = lambda evidence, **kw: release.require_native_quiescence(evidence)
    assert probe.idle(journal, {'root': '/old', 'legacy': True, 'pid': 123}, required_ids=('native1',))
    assert '/v1/runs/native1' in calls
    probe.request = lambda path, **kw: health() if path == '/health/detailed' else {'status': 'unknown'}
    with pytest.raises(RuntimeError, match='Unknown'):
        probe.idle(journal, {'root': '/old', 'legacy': True, 'pid': 123}, required_ids=('native1',))


@pytest.mark.parametrize('changed', [None, 'caps-order', 'busy', 'pid', 'caps', 'anonymous', 'unknown',
                                   'degraded', 'missing-status', 'attest-float', 'attest-bool',
                                   'baseline-float', 'baseline-bool', 'health-float', 'health-bool',
                                   'caps-bool', 'caps-float', 'caps-nested-type', 'caps-nan'])
def test_unchanged_native_abort_verification_is_not_queue_drain(changed):
    from urllib.error import HTTPError
    probe = object.__new__(release.NativeProbe)
    # PID 1 exposes bool/int equality as well as float/int equality.
    pid = {'pid': 2, 'attest-float': 1.0, 'attest-bool': True}.get(changed, 1)
    probe.attest = lambda root, legacy=False: pid
    evidence = health()
    evidence.update(status='ok', pid=1)
    counts = evidence['readiness']['checks']['background_queues']
    counts['process_completions'] = 78
    if changed == 'busy':
        counts.update(active_api_runs=1, active_delegations=1)
    if changed == 'unknown':
        del counts['active_api_runs']
    if changed == 'degraded':
        evidence['status'] = 'degraded'
    if changed == 'missing-status':
        del evidence['status']
    if changed in ('health-float', 'health-bool'):
        evidence['pid'] = 1.0 if changed == 'health-float' else True
    baseline = {'pid': 1, 'legacy': True, 'caps': {'legacy': True, 'version': 1,
                                                'nested': {'enabled': True}}}
    caps = {'legacy': True, 'version': 1, 'nested': {'enabled': True}}
    if changed in ('baseline-float', 'baseline-bool'):
        baseline['pid'] = 1.0 if changed == 'baseline-float' else True
    if changed == 'caps':
        caps['legacy'] = False
    if changed in ('caps-bool', 'caps-float'):
        caps['version'] = True if changed == 'caps-bool' else 1.0
    if changed == 'caps-nested-type':
        caps['nested']['enabled'] = 1
    if changed == 'caps-nan':
        caps['version'] = baseline['caps']['version'] = float('nan')
    if changed == 'caps-order':
        caps = dict(reversed(list(caps.items())))
    def request(path, *, authenticated=True):
        if not authenticated:
            if changed == 'anonymous':
                return evidence
            raise HTTPError('private', 401, 'Unauthorized', {}, None)
        return evidence if path == '/health/detailed' else caps
    probe.request = request
    if changed not in (None, 'caps-order', 'busy'):
        with pytest.raises((RuntimeError, ValueError)):
            probe.verify_unchanged(Path('/old'), baseline=baseline)
    else:
        probe.verify_unchanged(Path('/old'), baseline=baseline)


@pytest.mark.parametrize('saved,observed', [
    (456, [999, 999]), (456, [456, 999]),
    (None, [456, 456]), ('456', [456, 456]), (True, [1, 1]),
    (456.0, [456, 456]), (0, [0, 0]), (-1, [-1, -1]),
    (456, [456.0, 456.0]), (1, [True, True]),
])
def test_unchanged_native_rejects_changed_or_malformed_start_identity(saved, observed):
    from urllib.error import HTTPError
    probe = object.__new__(release.NativeProbe)
    probe.attest = lambda root, legacy=False: 123
    ticks = iter(observed)
    probe._start_ticks = lambda pid: next(ticks)
    def request(path, *, authenticated=True):
        if not authenticated:
            raise HTTPError('private', 401, 'Unauthorized', {}, None)
        return dict(health(), status='ok') if path == '/health/detailed' else {'legacy': True}
    probe.request = request
    baseline = dict(pid=123, legacy=True, caps={'legacy': True}, start_ticks=saved)
    with pytest.raises(RuntimeError, match='Unchanged native process start identity mismatch'):
        probe.verify_unchanged(Path('/old'), baseline=baseline)


@pytest.mark.parametrize('pid', [123, 789])
def test_legacy_postrestart_verification_uses_replacement_start_identity(monkeypatch, pid):
    from urllib.error import HTTPError
    probe = object.__new__(release.NativeProbe)
    probe.attest = lambda root, legacy=False: pid
    reads = []
    def start_ticks(observed_pid):
        reads.append(observed_pid)
        return 999
    probe._start_ticks = start_ticks
    def request(path, *, authenticated=True):
        if not authenticated:
            raise HTTPError('private', 401, 'Unauthorized', {}, None)
        return dict(health(), status='ok', pid=pid) if path == '/health/detailed' else {'legacy': True}
    probe.request = request
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    baseline = dict(pid=123, legacy=True, caps={'legacy': True}, start_ticks=456)
    probe.verify(Path('/old'), baseline=baseline)
    assert reads == [pid, pid, pid]  # fresh identity plus unchanged boundary brackets
    assert baseline['start_ticks'] == 456 and baseline['pid'] == 123


def test_cli_schedules_worker_and_durable_observer(tmp_path, monkeypatch):
    _, paths = deploy_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(release.os, 'geteuid', lambda: 1000)
    assert release.main(['--schedule', '--bootstrap-dedicated-native'], paths=paths,
                        run=lambda cmd, **kw: calls.append(cmd)) == 0
    assert len(calls) == 2
    assert all(c[:2] == ['systemd-run', '--user'] for c in calls)
    assert 'deploy.observe_release' in calls[0]
    assert '--native' in calls[0] and '--watch-worker' in calls[0]
    assert '--worker' in calls[1] and '--bootstrap-dedicated-native' in calls[1]
    assert '--on-active=5s' in calls[1]
    observer_timeout = int(calls[0][calls[0].index('--timeout') + 1])
    def runtime(command):
        return int(next(arg.split('=', 2)[2] for arg in command
                        if arg.startswith('--property=RuntimeMaxSec=')))
    assert runtime(calls[1]) >= 5400  # full checks + two 1800s drain windows + rollback
    assert observer_timeout > runtime(calls[1]) + 5
    assert runtime(calls[0]) > observer_timeout


def test_delegate_worker_contract_dependency_is_pinned():
    assert release.NATIVE_DEPENDENCIES.get(
        Path('/usr/local/lib/hermes-agent/tools/delegate_tool.py')) == (
        '9559ddd8d407cf8d321b8751c714a9f221dd8bd7f09cd016274c5b830940f385')


def test_worker_requires_separate_invocation(tmp_path, monkeypatch):
    _, paths = deploy_fixture(tmp_path)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    with pytest.raises(RuntimeError, match='separate'):
        release.main(['--worker'], paths=paths)



@pytest.mark.parametrize('bad', [None, 'command', 'socket', 'config', 'hash'])
def test_native_attestation_exact_owner_process_and_socket(tmp_path, monkeypatch, bad):
    import hashlib
    import os
    from types import SimpleNamespace
    source = tmp_path / 'source'
    (source / 'backend').mkdir(parents=True)
    launcher = source / 'backend/native_api_service.py'
    launcher.write_text('legacy')
    installed = tmp_path / 'installed.py'
    installed.write_text('installed')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'upstream_url': 'http://127.0.0.1:18642', 'upstream_token': 'x' * 32}))
    config.chmod(0o600)
    proc = tmp_path / 'proc'
    pid = proc / '123'
    (pid / 'fd').mkdir(parents=True)
    (pid / 'cwd').symlink_to(source)
    (pid / 'cmdline').write_bytes((release.NATIVE_PYTHON + '\0' + str(launcher) + '\0').encode())
    (pid / 'environ').write_bytes(('HERMES_MOBILE_CONFIG=' + str(config) + '\0HERMES_HOME=/home/lindayi/.hermes\0').encode())
    (pid / 'fd/1').symlink_to('socket:[99]')
    (proc / 'net').mkdir()
    (proc / 'net/tcp').write_text('header\n0: 0100007F:48D2 remote 0A x x x x x 99\n')
    (proc / 'net/tcp6').write_text('header\n')
    monkeypatch.setattr(release, 'PROC_ROOT', proc, raising=False)
    monkeypatch.setattr(release, 'NATIVE_API', installed)
    monkeypatch.setattr(release, 'INSTALLED_API', hashlib.sha256(installed.read_bytes()).hexdigest())
    monkeypatch.setattr(release, 'LEGACY_LAUNCHER', hashlib.sha256(launcher.read_bytes()).hexdigest())
    probe = release.NativeProbe(source, config=config, run=lambda *a, **kw: SimpleNamespace(stdout='123'))
    if bad == 'command': (pid / 'cmdline').write_bytes(b'wrong\0')
    if bad == 'socket': (proc / 'net/tcp').write_text('header\n0: 00000000:48D2 remote 0A x x x x x 99\n')
    if bad == 'config': config.write_text('{}')
    if bad == 'hash': installed.write_text('changed')
    if bad:
        with pytest.raises(RuntimeError): probe.attest(source, legacy=True)
    else:
        assert probe.attest(source, legacy=True) == 123


def test_private_native_http_bearer_and_no_redirect(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/should-not-follow')
            else:
                self.send_response(200 if self.headers.get('Authorization') == 'Bearer test-token' else 401)
            self.end_headers()
            self.wfile.write(json.dumps(health()).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    probe = object.__new__(release.NativeProbe)
    probe.endpoint = 'http://127.0.0.1:' + str(server.server_port)
    probe.token = 'test-token'
    try:
        assert probe.request('/health/detailed') == health()
        with pytest.raises(HTTPError): probe.request('/health/detailed', authenticated=False)
        with pytest.raises(HTTPError): probe.request('/redirect')
        assert not any(path == '/should-not-follow' for path, _ in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()



def test_expired_historical_terminal_ids_are_not_requeried(tmp_path):
    _, _, journal, _, _, _ = fixture(tmp_path)
    row, _ = journal.submit('owner', 'default', 's', 'x', 'x')
    with journal.connect() as db:
        db.execute("UPDATE runs SET status='completed', upstream_id='expired'")
    probe = object.__new__(release.NativeProbe)
    probe.attest = lambda root, legacy=False: 123
    def request(path):
        assert path == '/health/detailed', 'Historical terminal IDs must not depend on TTL cache'
        return health()
    probe.request = request
    # This test isolates terminal-ID cache policy; full gate/readiness integration
    # is exercised in test_native_maintenance_integration.py.
    probe._ready = lambda evidence, **kw: release.require_native_quiescence(evidence)
    assert probe.idle(journal, {'root': '/old', 'legacy': True, 'pid': 123}, required_ids=())


def test_gate_commit_race_keeps_active_local_id(tmp_path, monkeypatch):
    import sqlite3
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    row, _ = journal.submit('owner', 'default', 's', 'x', 'x')
    original = RunJournal.connect
    fired = []
    class Connection(sqlite3.Connection):
        def __exit__(self, *a):
            result = super().__exit__(*a)
            if not fired and self.execute('SELECT 1 FROM deployment_gate').fetchone():
                fired.append(True)
                with original(journal) as db:
                    db.execute("UPDATE runs SET status='completed', upstream_id='gate-active-native' WHERE id=?", (row['id'],))
            return result
    def connect(self):
        db = sqlite3.connect(self.path, factory=Connection)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(RunJournal, 'connect', connect)
    observed = []
    def idle(journal, baseline, *, required_ids=()):
        observed.append(required_ids)
        return True
    args['native'].idle = idle
    release.deploy(paths, **args)
    assert fired
    # The same admitted identity must survive both drain and post-handoff checks.
    assert observed == [('gate-active-native',), ('gate-active-native',)]


def test_active_drain_id_dispatched_while_waiting_is_required(tmp_path):
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    row, _ = journal.submit('owner', 'default', 's', 'x', 'x')
    def sleep(_):
        with journal.connect() as db:
            db.execute("UPDATE runs SET status='completed', upstream_id='active-drain-id'")
    def idle(journal, baseline, *, required_ids=()):
        assert required_ids == ('active-drain-id',)
        from urllib.error import HTTPError
        raise HTTPError('private', 404, 'Missing active drain ID', {}, None)
    args['native'].idle = idle
    with pytest.raises(Exception, match='Missing active drain ID'):
        release.deploy(paths, sleep=sleep, **args)
    assert not any(e[0] == 'command' for e in events)


def test_rollback_retains_distinct_native_and_bridge_roots(tmp_path):
    import base64
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    native_root = paths.state / 'releases' / ('d' * 32)
    native_root.mkdir()
    prior = b'[Service]\r\nWorkingDirectory=' + str(native_root).encode() + b'\r\n'
    dropin.write_bytes(prior)
    captured = dict(root=str(native_root), bridge_root=str(old), legacy=False,
                    pid=123, start_ticks=456, source_hashes=release.PREVIOUS_CONTROL_HASHES,
                    caps={'mobile_run_controls': {'version': 1}})
    args['native'].capture = lambda *_: dict(captured)
    def fail_candidate(root, *, baseline=None):
        if baseline is None:
            raise RuntimeError('candidate deletion capability unavailable')
        assert root == native_root and baseline['root'] != baseline['bridge_root']
        assert baseline['caps'] == captured['caps']
        assert baseline['source_hashes'] == captured['source_hashes']
        assert dropin.read_bytes() == prior
        events.append(('restored-distinct-native', root))
    args['native'].verify = fail_candidate
    with pytest.raises(RuntimeError, match='deletion capability unavailable'):
        release.deploy(paths, **args)
    assert ('restored-distinct-native', native_root) in events
    assert (paths.state / 'current').resolve() == old
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    snapshot = json.loads(next((paths.state / 'recovery').glob('*/native-rollback.json')).read_text())
    assert snapshot['current'] == str(old)
    assert snapshot['native']['root'] == str(native_root)
    assert base64.b64decode(snapshot['dropins'][str(dropin)]) == prior
    assert journal.submit('owner', 'default', 'after', 'ok', 'after-rollback')[1]
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'


def test_authorized_controls_delta_allowed_but_legacy_still_protected(tmp_path, monkeypatch):
    import hashlib
    from backend import model_controls
    paths, old, journal, dropin, events, args = fixture(tmp_path)
    helper = 'backend/native_session_deletion.py'
    (paths.source / helper).write_bytes(b'deletion')
    approved = {**release.APPROVED_CONTROL_HASHES, helper: hashlib.sha256(b'deletion').hexdigest()}
    monkeypatch.setattr(model_controls, '_CONTROL_HASHES', approved)
    monkeypatch.setattr(release, 'APPROVED_CONTROL_HASHES', approved)
    (paths.source / 'backend/model_controls.py').write_text(
        '_CONTROL_HASHES = ' + repr(approved) + '\n_PREVIOUS_CONTROL_HASHES = ' + repr(release.PREVIOUS_CONTROL_HASHES)
        + '\n_TIMEOUT_BASELINE_CONTROL_HASHES = ' + repr(release.TIMEOUT_BASELINE_CONTROL_HASHES))
    assert release.deploy(paths, **args)
