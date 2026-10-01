import hashlib
import json
import os
import sqlite3
from dataclasses import replace

import pytest

from deploy.self_deploy import Paths


def test_cli_receipt_failure_no_notification_by_default(tmp_path, monkeypatch, capsys):
    from deploy import observe_release as observer
    paths = replace(Paths(), state=tmp_path)
    status(paths, {'status': 'failed', 'error': 'sensitive traceback'})
    expected = tmp_path / 'expected.json'
    expected.write_text('{}')
    monkeypatch.setattr(observer, 'notify_owner', lambda *a: pytest.fail('unexpected notification'))
    assert observer.main(['--since-ns', '100', '--expected', str(expected), '--unit', 'release-test'], paths=paths) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {'unit': 'release-test', 'status': 'failed', 'notification': 'disabled'}
    with pytest.raises(SystemExit):
        observer.main(['--since-ns', '100', '--expected', str(expected), '--unit', '../bad'], paths=paths)


def test_cli_hard_deadline(tmp_path, monkeypatch, capsys):
    import signal
    from deploy import observe_release as observer
    expected = tmp_path / 'expected.json'
    expected.write_text('{}')
    previous = signal.getsignal(signal.SIGALRM)
    def stalled(*a, **kw):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    monkeypatch.setattr(observer, 'observe', stalled)
    assert observer.main(['--since-ns', '100', '--expected', str(expected), '--unit', 'release-test']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'timeout'
    assert signal.getsignal(signal.SIGALRM) == previous


def test_timeout_and_terminal_return(tmp_path):
    from deploy.observe_release import observe
    paths = replace(Paths(), state=tmp_path)
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    assert observe(paths, 100, {}, clock=lambda: now[0], sleep=sleep) == 'timeout'
    assert now == [2100.0]
    status(paths, {'status': 'failed'})
    assert observe(paths, 100, {}, clock=lambda: now[0], sleep=sleep) == 'failed'


def test_explicit_timeout_observes_delayed_verified_release(tmp_path):
    from deploy.observe_release import observe
    paths, stage, expected = release(tmp_path)
    status(paths, {'status': 'running'})
    now = [0.0]
    verified = []
    def sleep(seconds):
        now[0] += seconds
        if now[0] >= 2200:
            status(paths, {'status': 'succeeded', 'release': stage.name})
    assert observe(paths, 100, expected, timeout=2400,
                   clock=lambda: now[0], sleep=sleep,
                   verify=lambda *args: verified.append(args)) == 'succeeded'
    assert now == [2200.0]
    assert verified == [(paths, stage, True)]


@pytest.mark.parametrize('timeout', [0, -1, 86401, 1.5, True, None, '2400', float('inf'), float('nan')])
def test_api_rejects_invalid_timeout_before_reading_state(tmp_path, timeout):
    from deploy.observe_release import observe
    paths = replace(Paths(), state=tmp_path)
    with pytest.raises(ValueError, match='timeout'):
        observe(paths, 100, {}, timeout=timeout,
                clock=lambda: pytest.fail('invalid budget must fail before polling'))


@pytest.mark.parametrize('timeout', [1, 2400, 86400])
def test_cli_selected_timeout_bounds_polling_and_absolute_alarm(tmp_path, monkeypatch, capsys, timeout):
    import signal
    from deploy import observe_release as observer
    expected = tmp_path / 'expected.json'
    expected.write_text('{}')
    alarms = []
    previous = signal.getsignal(signal.SIGALRM)
    monkeypatch.setattr(signal, 'alarm', alarms.append)
    def stalled(*args, **kwargs):
        assert kwargs['timeout'] == timeout
        assert alarms == [timeout]
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    monkeypatch.setattr(observer, 'observe', stalled)
    monkeypatch.setattr(observer, 'notify_owner', lambda *a: pytest.fail('notification'))
    assert observer.main(['--since-ns', '100', '--expected', str(expected),
                          '--unit', 'release-test', '--timeout', str(timeout)]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'timeout'
    assert alarms == [timeout, 0]
    assert signal.getsignal(signal.SIGALRM) == previous


@pytest.mark.parametrize('timeout', ['0', '-1', '86401', '1.5', 'nan', 'inf', 'bad', ''])
def test_cli_rejects_invalid_timeout_before_arming_alarm(tmp_path, monkeypatch, capsys, timeout):
    import signal
    from deploy import observe_release as observer
    alarms = []
    monkeypatch.setattr(signal, 'alarm', alarms.append)
    with pytest.raises(SystemExit) as error:
        observer.main(['--since-ns', '100', '--expected', str(tmp_path / 'missing'),
                       '--unit', 'release-test', '--timeout', timeout])
    assert error.value.code == 2
    assert alarms == []
    assert 'timeout' in capsys.readouterr().err


@pytest.mark.parametrize('terminal', ['failed', 'inactive', 'missing'])
def test_worker_termination_without_terminal_status_is_failure(tmp_path, terminal):
    from deploy.observe_release import observe
    paths = replace(Paths(), state=tmp_path)
    status(paths, {'status': 'running'})
    states = iter(['active', terminal])
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    assert observe(paths, 100, {}, timeout=10, clock=lambda: now[0], sleep=sleep,
                   worker_state=lambda: next(states)) == 'failed'
    assert now == [2.0]


def test_worker_terminal_probe_rechecks_final_publication(tmp_path):
    from deploy.observe_release import observe
    paths, stage, expected = release(tmp_path)
    status(paths, {'status': 'running'})
    verified = []
    def worker_state():
        status(paths, {'status': 'succeeded', 'release': stage.name})
        return 'failed'
    assert observe(paths, 100, expected, timeout=10, worker_state=worker_state,
                   verify=lambda *args: verified.append(args)) == 'succeeded'
    assert verified == [(paths, stage, True)]


@pytest.mark.parametrize('load,active,returncode,expected', [
    ('loaded', 'active', 0, 'active'),
    ('loaded', 'activating', 0, 'active'),
    ('loaded', 'deactivating', 0, 'active'),
    ('loaded', 'failed', 0, 'failed'),
    ('loaded', 'inactive', 0, 'inactive'),
    ('not-found', 'inactive', 0, 'missing'),
    ('not-found', 'inactive', 1, 'missing'),
    ('loaded', 'failed', 1, None),
    ('error', 'failed', 0, None),
    ('loaded', 'unknown', 0, None),
    ('', '', 0, None),
])
def test_systemd_worker_probe_uses_state_not_default_result(load, active, returncode, expected):
    from subprocess import CompletedProcess
    from deploy import observe_release as observer
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, returncode,
                                f'LoadState={load}\nActiveState={active}\nResult=success\n')
    assert observer.read_worker_state('release-test', run=run) == expected
    command, kwargs = calls[0]
    assert command == ['systemctl', '--user', 'show', 'release-test.service',
                       '--property=LoadState', '--property=ActiveState']
    assert kwargs == dict(check=False, capture_output=True, text=True, timeout=5)


@pytest.mark.parametrize('kind', ['missing-command', 'probe-timeout'])
def test_systemd_probe_errors_are_unknown_not_worker_failure(kind):
    import subprocess
    from deploy.observe_release import read_worker_state
    def run(*args, **kwargs):
        if kind == 'missing-command':
            raise FileNotFoundError('systemctl unavailable')
        raise subprocess.TimeoutExpired('systemctl', 5)
    assert read_worker_state('release-test', run=run) is None


@pytest.mark.parametrize('watch', [False, True])
def test_cli_worker_tracking_is_opt_in(tmp_path, monkeypatch, capsys, watch):
    from deploy import observe_release as observer
    paths = replace(Paths(), state=tmp_path)
    status(paths, {'status': 'running'})
    expected = tmp_path / 'expected.json'
    expected.write_text('{}')
    probes = []
    def probe(unit):
        probes.append(unit)
        return 'failed'
    monkeypatch.setattr(observer, 'read_worker_state', probe)
    original = observer.observe
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    def observe(*args, **kwargs):
        return original(*args, **kwargs, clock=lambda: now[0], sleep=sleep)
    monkeypatch.setattr(observer, 'observe', observe)
    args = ['--since-ns', '100', '--expected', str(expected),
            '--unit', 'hermes-native-controls-test', '--timeout', '5640']
    assert observer.main(args + (['--watch-worker'] if watch else []), paths=paths) == 1
    assert json.loads(capsys.readouterr().out) == dict(
        unit='hermes-native-controls-test', status='failed' if watch else 'timeout',
        notification='disabled')
    assert probes == (['hermes-native-controls-test'] if watch else [])


@pytest.mark.parametrize('state', [None, 'missing', 'inactive', 'active', 'unknown'])
def test_initial_absence_or_progress_never_implies_success_or_extends_budget(tmp_path, state):
    from deploy.observe_release import observe
    paths = replace(Paths(), state=tmp_path)
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    assert observe(paths, 100, {}, timeout=5, worker_state=lambda: state,
                   clock=lambda: now[0], sleep=sleep) == 'timeout'
    assert now == [5.0]


def test_delayed_worker_start_then_collection_is_not_default_success(tmp_path):
    from subprocess import CompletedProcess
    from deploy.observe_release import observe, read_worker_state
    paths = replace(Paths(), state=tmp_path)
    states = iter([('not-found', 'inactive'), ('loaded', 'inactive'),
                   ('loaded', 'active'), ('not-found', 'inactive')])
    def run(command, **kwargs):
        load, active = next(states)
        return CompletedProcess(command, 0,
                                f'LoadState={load}\nActiveState={active}\nResult=success\n')
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    assert observe(paths, 100, {}, timeout=10,
                   worker_state=lambda: read_worker_state('release-test', run=run),
                   clock=lambda: now[0], sleep=sleep) == 'failed'
    assert now == [6.0]


def test_verification_finishing_at_deadline_cannot_report_success(tmp_path):
    from deploy.observe_release import observe
    paths, stage, expected = release(tmp_path)
    now = [0.0]
    def verify(*args):
        now[0] = 10.0
    assert observe(paths, 100, expected, timeout=10, clock=lambda: now[0],
                   verify=verify, sleep=lambda _: pytest.fail('late sleep')) == 'timeout'


@pytest.mark.parametrize('seam', ['manifest', 'verifier', 'probe'])
def test_real_absolute_alarm_escapes_blocking_io_and_exception_handlers(tmp_path, monkeypatch, capsys, seam):
    import signal
    import time
    from pathlib import Path
    from deploy import observe_release as observer
    paths, stage, hashes = release(tmp_path)
    expected = tmp_path / 'expected.json'
    expected.write_text(json.dumps(hashes))
    original_observe = observer.observe
    original_read = Path.read_text
    original_probe = observer.read_worker_state
    previous = signal.getsignal(signal.SIGALRM)
    def blocked(*args, **kwargs):
        try:
            time.sleep(3)
        except Exception:
            pytest.fail('deadline must escape ordinary exception handlers')
        pytest.fail('absolute alarm did not interrupt blocking operation')
    if seam == 'manifest':
        monkeypatch.setattr(Path, 'read_text',
                            lambda path, *a, **kw: blocked() if path == expected else original_read(path, *a, **kw))
    if seam == 'verifier':
        monkeypatch.setattr(observer, 'observe',
                            lambda *a, **kw: original_observe(*a, **kw, verify=blocked))
    if seam == 'probe':
        status(paths, {'status': 'running'})
        monkeypatch.setattr(observer, 'read_worker_state', lambda unit: original_probe(unit, run=blocked))
    start = time.monotonic()
    assert observer.main(['--since-ns', '100', '--expected', str(expected),
                          '--unit', 'release-test', '--timeout', '1', '--watch-worker'], paths=paths) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'timeout'
    assert time.monotonic() - start < 3
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def owner_fixture(tmp_path):
    from backend.notifications import NotificationService
    tmp_path.chmod(0o700)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'state_dir': str(tmp_path)}))
    config.chmod(0o600)
    auth = tmp_path / 'auth.sqlite'
    with sqlite3.connect(auth) as db:
        db.execute('CREATE TABLE users(id TEXT, role TEXT, status TEXT, profile TEXT)')
        db.executemany('INSERT INTO users VALUES(?,?,?,?)', [
            ('owner', 'owner', 'ready', 'default'),
            ('member', 'member', 'ready', 'default'),
            ('other', 'owner', 'ready', 'member_other'),
            ('pending', 'owner', 'pending', 'default')])
    auth.chmod(0o600)
    service = NotificationService(tmp_path / 'notifications.sqlite')
    with service._db() as db:
        db.execute("INSERT INTO subscriptions VALUES('endpoint','owner','device','{}')")
    return config, auth


def test_owner_notification_silent_scoped_and_deduplicated(tmp_path):
    from deploy.observe_release import notify_owner
    config, auth = owner_fixture(tmp_path)
    before = auth.read_bytes()
    first = notify_owner('hermes-mobile-deploy-test', 'failed', config_path=config)
    assert notify_owner('hermes-mobile-deploy-test', 'failed', config_path=config) == first
    assert auth.read_bytes() == before
    with sqlite3.connect(tmp_path / 'notifications.sqlite') as db:
        assert db.execute('SELECT user_id,session_id FROM inbox').fetchall() == [('owner', None)]
        assert db.execute('SELECT count(*) FROM outbox').fetchone() == (0,)
        body = db.execute('SELECT body FROM inbox').fetchone()[0]
        assert 'secret' not in body
        assert 'could not be verified' in body
        assert 'may already be running' in body
        assert 'did not complete successfully' not in body


@pytest.mark.parametrize('bad', ['missing', 'public', 'symlink', 'ambiguous', 'state_alias'])
def test_owner_notification_refuses_unsafe_state(tmp_path, bad):
    from deploy.observe_release import notify_owner
    config, auth = owner_fixture(tmp_path)
    inbox = tmp_path / 'notifications.sqlite'
    if bad == 'missing':
        inbox.unlink()
    if bad == 'public':
        auth.chmod(0o644)
    if bad == 'symlink':
        auth.rename(tmp_path / 'real.sqlite')
        auth.symlink_to(tmp_path / 'real.sqlite')
    if bad == 'state_alias':
        alias = tmp_path / 'alias'
        alias.symlink_to(tmp_path, target_is_directory=True)
        config.write_text(json.dumps({'state_dir': str(alias)}))
    if bad == 'ambiguous':
        with sqlite3.connect(auth) as db:
            db.execute("INSERT INTO users VALUES('second','owner','ready','default')")
    before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises((ValueError, OSError)):
        notify_owner('release-test', 'failed', config_path=config)
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def status(paths, value, mtime=100):
    paths.state.mkdir(exist_ok=True)
    file = paths.state / 'status.json'
    file.write_text(json.dumps(value))
    os.utime(file, ns=(mtime, mtime))


def release(tmp_path):
    paths = replace(Paths(), state=tmp_path, database=tmp_path / 'runs.sqlite')
    stage = tmp_path / 'releases' / ('a' * 32)
    stage.mkdir(parents=True)
    (stage / 'backend').mkdir()
    (stage / 'backend/app.py').write_text('tested')
    (tmp_path / 'current').symlink_to(stage)
    with sqlite3.connect(paths.database) as db:
        db.execute('CREATE TABLE deployment_gate(singleton INTEGER, owner TEXT)')
    status(paths, {'status': 'succeeded', 'release': stage.name})
    return paths, stage, {'backend/app.py': hashlib.sha256(b'tested').hexdigest()}


@pytest.mark.parametrize('bad', ['hash', 'gate', 'pointer', 'id', 'verify', 'empty', None])
def test_success_requires_every_proof(tmp_path, bad):
    from deploy.observe_release import classify
    paths, stage, expected = release(tmp_path)
    calls = []
    def verify(*args):
        calls.append(args)
        if bad == 'verify':
            raise RuntimeError('secret')
    if bad == 'hash':
        expected['backend/app.py'] = '0' * 64
    if bad == 'empty':
        expected.clear()
    if bad == 'gate':
        with sqlite3.connect(paths.database) as db:
            db.execute("INSERT INTO deployment_gate VALUES(1, 'busy')")
    if bad == 'pointer':
        (paths.state / 'current').unlink()
    if bad == 'id':
        status(paths, {'status': 'succeeded', 'release': '../escape'})
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert classify(paths, 100, expected, verify=verify) == ('failed' if bad else 'succeeded')
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if not bad:
        assert calls == [(paths, stage, True)]


def test_fresh_failure_without_release_and_stale_ignored(tmp_path):
    from deploy.observe_release import classify
    paths = replace(Paths(), state=tmp_path)
    for outcome in ('failed', 'rolled_back', 'rollback_failed'):
        status(paths, {'status': outcome, 'error': 'secret'})
        assert classify(paths, 100, {}, verify=lambda *a: pytest.fail('verify')) == 'failed'
        assert classify(paths, 101, {}, verify=lambda *a: pytest.fail('verify')) is None
