"""Isolated durable-notification release proof tests; no live native state."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from backend.native_notifications import NotificationCapture, NotificationOutbox, OwnerRoute
from deploy import native_controls_release as release
from deploy.native_notification_release import NativeNotificationCallbacks
from test_background_delivery import Fixture


def setup_release(tmp_path, *, with_event=True):
    app = Fixture(tmp_path)
    state = tmp_path
    home = app.catalog.profiles['default']
    with sqlite3.connect(state / 'auth.sqlite') as db:
        db.execute('CREATE TABLE users(id TEXT, role TEXT, profile TEXT, status TEXT)')
        db.execute("INSERT INTO users VALUES('owner','owner','default','ready')")
    (state / 'auth.sqlite').chmod(0o600)
    outbox_path = state / 'native-notifications.sqlite'
    outbox = NotificationOutbox(outbox_path)
    NotificationCapture(outbox, home, OwnerRoute(home, state))
    event = app.items[0]['event']
    if with_event:
        outbox.capture(event, {'summary': 'Synthetic result'}, route='owned')
    for path in (home / 'state.db', app.journal.path, state / 'notifications.sqlite'):
        path.chmod(0o600)

    releases = state / 'releases'
    previous, candidate = releases / ('a' * 32), releases / ('b' * 32)
    for root in (previous, candidate):
        for name in (*release.APPROVED_CONTROL_HASHES, 'backend/model_controls.py'):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).resolve().parents[1] / name, target)
    (state / 'current').symlink_to(previous)
    gate_owner = 'b' * 32
    with app.journal.connect() as db:
        db.execute('INSERT INTO deployment_gate VALUES(1,?)', (gate_owner,))
    (state / 'deploy.lock').touch(mode=0o600)

    native = FakeNative(previous, candidate, outbox)
    paths = SimpleNamespace(state=state, database=app.journal.path)
    native.config_bytes = json.dumps({'state_dir': str(state)}).encode()
    callbacks = NativeNotificationCallbacks(paths, native, home=home)
    baseline = dict(root=str(previous), source_hashes=release.APPROVED_CONTROL_HASHES,
                    pid=123, start_ticks=456, gate_owner=gate_owner)
    return app, outbox, native, paths, callbacks, baseline, previous, candidate, event


class FakeNative:
    def __init__(self, previous, _candidate, outbox):
        self.active_root = previous
        self.outbox = outbox
        self.pid, self.started = 123, 456

    def attest(self, root, **_):
        assert Path(root) == self.active_root
        return self.pid

    def _start_ticks(self, pid):
        assert pid == self.pid
        return self.started

    def _status(self):
        return {**self.outbox.status(), 'active_workers': 0, 'shutdown_publications': 0}

    def request(self, path):
        from test_native_readiness import evidence
        if path == '/v1/mobile/notifications/status':
            return self._status()
        health = evidence()
        status = self._status()
        notices = health['native_maintenance']['notifications']
        notices.update(backlog=status['pending'] + status['quarantined'],
                       durable_retained=status['pending'] + status['quarantined'],
                       unpreserved=0, web_pending=status['pending'],
                       quarantined=status['quarantined'],
                       foreign_retained=status['foreign_retained'],
                       web_delivered=status['delivered'],
                       shutdown_publications=status['shutdown_publications'])
        health['pid'] = health['native_maintenance']['pid'] = self.pid
        health['native_maintenance']['start_ticks'] = self.started
        health['native_maintenance']['work']['notification_workers'] = status['active_workers']
        health['native_maintenance']['work']['notification_lifecycle_uncertain'] = 0
        health['native_maintenance']['work']['session_deletion_workers'] = 0
        return health

    def _ready(self, health, *, baseline=None, root=None):
        from deploy.native_readiness import require_native_readiness
        from deploy.native_controls_release import attested_controls
        source = baseline['source_hashes'] if baseline else attested_controls(Path(root))
        return require_native_readiness(
            health, expected_pid=health['pid'],
            expected_start_ticks=health['native_maintenance']['start_ticks'],
            session_delete_version=int('backend/native_session_deletion.py' in source),
            notification_version=1)


def under_owned_lock(paths, callback):
    with (paths.state / 'deploy.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        callback()


def capture_and_handoff(paths, callbacks, baseline, candidate):
    under_owned_lock(paths, lambda: callbacks.capture(baseline))
    under_owned_lock(paths, lambda: callbacks.handoff(candidate))


def activate(native, paths, candidate):
    (paths.state / 'current').unlink()
    (paths.state / 'current').symlink_to(candidate)
    native.active_root = candidate
    native.pid, native.started = 789, 999


def probe_under_owned_lock(paths, callbacks, candidate):
    result = []
    under_owned_lock(paths, lambda: result.append(callbacks.probe(candidate)))
    return result[0]


def create_owned_ack(app, outbox, event, scope):
    from backend.background_delivery import BackgroundDeliveryService
    item = outbox.claim(1)[0]
    receipt_id = app.notifications.store_background(
        scope=scope, event_id=item['event_id'], digest=item['payload_sha256'],
        user_id='owner', origin=BackgroundDeliveryService._origin(event),
        session_id='chat', event=event, lease_token=item['lease_token'],
        body=BackgroundDeliveryService._body(event))
    app.notifications.background_acknowledged(scope, item['event_id'], item['lease_token'])
    outbox.ack(event_id=item['event_id'], payload_sha256=item['payload_sha256'],
               lease_token=item['lease_token'], receipt_id=receipt_id)


def create_unacked_owned_receipt(app, outbox, event, scope):
    from backend.background_delivery import BackgroundDeliveryService
    item = outbox.claim(1)[0]
    receipt_id = app.notifications.store_background(
        scope=scope, event_id=item['event_id'], digest=item['payload_sha256'],
        user_id='owner', origin=BackgroundDeliveryService._origin(event),
        session_id='chat', event=event, lease_token=item['lease_token'],
        body=BackgroundDeliveryService._body(event))
    return item, receipt_id


def setup_controller_release(tmp_path, monkeypatch, *, delivered=True, wire_callbacks=True):
    from backend import model_controls
    from test_native_controls_release import fixture as controller_fixture

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
    tmp_path.mkdir(exist_ok=True)
    paths, old, _, _, events, args = controller_fixture(tmp_path)
    assert release.attested_controls(paths.source) == approved
    for name in approved:
        target = old / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths.source / name, target)
    # Preserve the controller fixture's distinct live journal root.
    state = paths.database.parent
    app = Fixture(state)
    assert paths.database == app.journal.path

    with sqlite3.connect(state / 'auth.sqlite') as db:
        db.execute('CREATE TABLE users(id TEXT, role TEXT, profile TEXT, status TEXT)')
        db.execute("INSERT INTO users VALUES('owner','owner','default','ready')")
    (state / 'auth.sqlite').chmod(0o600)
    outbox_path = state / 'native-notifications.sqlite'
    outbox = NotificationOutbox(outbox_path)
    home = app.catalog.profiles['default']
    NotificationCapture(outbox, home, OwnerRoute(home, state))
    event = app.items[0]['event']
    outbox.capture(event, {'summary': 'Synthetic result'}, route='owned')
    for path in (home / 'state.db', paths.database,
                 state / 'notifications.sqlite', outbox_path):
        path.chmod(0o600)

    controller_native = args['native']
    native = FakeNative(old, None, outbox)

    def capture(root, bootstrap):
        events.append(('capture', root))
        return dict(root=str(root), source_hashes=approved, pid=native.pid,
                    start_ticks=native.started, caps={'legacy': False})

    native.capture = capture
    native.idle = controller_native.idle
    native.verify = controller_native.verify
    native.verify_unchanged = controller_native.verify_unchanged
    native.config_bytes = json.dumps({'state_dir': str(state)}).encode()
    base_run = args['run']

    def run(command, **kwargs):
        base_run(command, **kwargs)
        if command[:2] == ['systemctl', '--user'] and command[2] == 'restart':
            native.active_root = (paths.state / 'current').resolve()
            native.pid += 1
            native.started += 1

    args['run'] = run
    callbacks = None
    args['native'] = native
    if wire_callbacks:
        callbacks = NativeNotificationCallbacks(paths, native, home=home, receipt_timeout=0)
        args['rollback_verify'] = callbacks.verify_rollback
        args['handoff'] = callbacks
        args['probe'] = callbacks.probe
    if delivered:
        scope = json.dumps(['default', str(home.resolve())], separators=(',', ':'))
        create_owned_ack(app, outbox, event, scope)
    return app, outbox, native, paths, callbacks, args, old, event


def delete_captured_evidence(app, outbox, event, kind):
    if kind == 'record':
        with outbox.transaction() as db:
            db.execute('DELETE FROM notification_outbox WHERE event_id=?',
                       ('async:' + event['delegation_id'],))
    else:
        with app.notifications._db() as db:
            db.execute('DELETE FROM background_receipts WHERE event_id=?',
                       ('async:' + event['delegation_id'],))


@pytest.mark.parametrize(('kind', 'expected_error'), [
    ('record', 'Durable native notification record was not preserved'),
    ('receipt', 'Owned notification receipt was not preserved'),
])
def test_controller_keeps_gate_closed_on_prepublication_evidence_loss(
        tmp_path, monkeypatch, kind, expected_error):
    app, outbox, _, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)

    class FailingHandoff:
        capture = callbacks.capture

        def __call__(self, stage):
            delete_captured_evidence(app, outbox, event, kind)
            callbacks.handoff(stage)
            raise RuntimeError('injected prepublication receipt evidence loss')

    args['handoff'] = FailingHandoff()
    with pytest.raises(RuntimeError):
        release.deploy(paths, idle_timeout=0, **args)

    status = json.loads((paths.state / 'status.json').read_text())
    assert status['error'] == expected_error
    assert status['status'] == 'rollback_failed'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT owner FROM deployment_gate').fetchone() == (status['release'],)


@pytest.mark.parametrize(('kind', 'expected_error'), [
    ('record', 'Native notification records changed after handoff'),
    ('receipt', 'Owned notification receipt was not preserved'),
])
def test_controller_keeps_gate_closed_on_postpublication_evidence_loss(
        tmp_path, monkeypatch, kind, expected_error):
    app, outbox, _, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)

    def fail_probe(stage):
        delete_captured_evidence(app, outbox, event, kind)
        return callbacks.probe(stage)

    args['probe'] = fail_probe
    with pytest.raises(RuntimeError):
        release.deploy(paths, idle_timeout=0, **args)

    status = json.loads((paths.state / 'status.json').read_text())
    assert status['error'] == expected_error
    assert status['status'] == 'rollback_failed'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT owner FROM deployment_gate').fetchone() == (status['release'],)


def test_controller_reopens_after_verified_candidate_rollback(tmp_path, monkeypatch):
    _, _, _, paths, callbacks, args, _, _ = setup_controller_release(tmp_path, monkeypatch)

    def fail_after_positive_probe(stage):
        callbacks.probe(stage)
        raise RuntimeError('ordinary candidate failure')

    args['probe'] = fail_after_positive_probe
    with pytest.raises(RuntimeError, match='ordinary candidate failure'):
        release.deploy(paths, idle_timeout=0, **args)

    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_controller_reopens_prepublication_after_positive_rollback_proof(
        tmp_path, monkeypatch):
    _, _, native, paths, callbacks, args, _, _ = setup_controller_release(tmp_path, monkeypatch)
    native.idle = lambda *a, **kw: False

    with pytest.raises(RuntimeError, match='Native idle wait timed out'):
        release.deploy(paths, idle_timeout=0, **args)

    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_controller_reopens_after_receipt_delay_with_preserved_pending_record(
        tmp_path, monkeypatch):
    _, _, _, paths, callbacks, args, _, _ = setup_controller_release(
        tmp_path, monkeypatch, delivered=False)
    callbacks.receipt_timeout = 0

    with pytest.raises(RuntimeError, match='receipt verification timed out'):
        release.deploy(paths, idle_timeout=0, **args)

    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_controller_allows_receipt_ack_progress_during_verified_rollback(tmp_path, monkeypatch):
    app, outbox, _, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch, delivered=False)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    item, receipt_id = create_unacked_owned_receipt(app, outbox, event, scope)

    def acknowledge_then_fail(stage):
        app.notifications.background_acknowledged(scope, item['event_id'], item['lease_token'])
        outbox.ack(event_id=item['event_id'], payload_sha256=item['payload_sha256'],
                   lease_token=item['lease_token'], receipt_id=receipt_id)
        callbacks.probe(stage)
        raise RuntimeError('ordinary candidate failure after receipt ACK')

    args['probe'] = acknowledge_then_fail
    with pytest.raises(RuntimeError, match='ordinary candidate failure after receipt ACK'):
        release.deploy(paths, idle_timeout=0, **args)

    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_handoff_verifies_durable_rows_without_claiming_or_importing(tmp_path, monkeypatch):
    _, _, _, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path)
    monkeypatch.setattr(NotificationOutbox, 'claim', lambda *a, **kw: pytest.fail('handoff replayed outbox'))
    monkeypatch.setattr(NotificationOutbox, 'import_records', lambda *a, **kw: pytest.fail('handoff imported rows'))
    before = hashlib.sha256((paths.state / 'native-notifications.sqlite').read_bytes()).hexdigest()
    capture_and_handoff(paths, callbacks, baseline, candidate)
    after = hashlib.sha256((paths.state / 'native-notifications.sqlite').read_bytes()).hexdigest()
    assert before == after
    assert set(callbacks.handoff_records['async:deleg_1']) == {
        'event_hash', 'result_hash', 'payload_sha256', 'route', 'historical', 'provenance_hash'}


def test_handoff_fails_closed_when_preexisting_record_disappears(tmp_path):
    _, outbox, _, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path)
    under_owned_lock(paths, lambda: callbacks.capture(baseline))
    with outbox.transaction() as db:
        db.execute('DELETE FROM notification_outbox')
    with pytest.raises(RuntimeError, match='preserved'):
        under_owned_lock(paths, lambda: callbacks.handoff(candidate))


def test_handoff_rejects_owner_mutation_after_capture(tmp_path):
    _, _, _, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path)
    under_owned_lock(paths, lambda: callbacks.capture(baseline))
    with sqlite3.connect(callbacks.auth) as db:
        db.execute("UPDATE users SET id='replacement-owner'")
    with pytest.raises(RuntimeError, match='owner changed'):
        under_owned_lock(paths, lambda: callbacks.handoff(candidate))


def test_empty_backlog_requires_and_accepts_positive_bound_status(tmp_path):
    _, _, native, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path, with_event=False)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    activate(native, paths, candidate)
    assert probe_under_owned_lock(paths, callbacks, candidate) is True
    assert sum(row['route'] == 'owned' for row in callbacks.handoff_records.values()) == 0


def test_probe_requires_existing_owned_delivery_and_ack_chain(tmp_path):
    app, outbox, native, paths, callbacks, baseline, _, candidate, event = setup_release(tmp_path)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    create_owned_ack(app, outbox, event, callbacks.scope)
    activate(native, paths, candidate)
    assert probe_under_owned_lock(paths, callbacks, candidate) is True
    assert sum(row['route'] == 'owned' for row in callbacks.handoff_records.values()) == 1


def test_receipt_must_match_the_routed_owner_session(tmp_path):
    app, outbox, _, paths, callbacks, baseline, _, _, event = setup_release(tmp_path)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    create_owned_ack(app, outbox, event, scope)
    with app.notifications._db() as db:
        db.execute('''UPDATE inbox SET session_id=?
            WHERE id=(SELECT inbox_id FROM background_receipts WHERE event_id=?)''',
                   ('another-session', 'async:' + event['delegation_id']))
    with pytest.raises(RuntimeError, match='receipt is inconsistent'):
        under_owned_lock(paths, lambda: callbacks.capture(baseline))


def test_bounded_route_snapshot_supplies_receipt_session_binding(tmp_path, monkeypatch):
    app, outbox, _, paths, callbacks, baseline, _, _, event = setup_release(tmp_path)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    create_owned_ack(app, outbox, event, scope)
    under_owned_lock(paths, lambda: callbacks.capture(baseline))
    second = {**event, 'delegation_id': 'deleg_2'}
    outbox.capture(second, {'summary': 'Second synthetic result'}, route='owned')
    original = OwnerRoute.resolve
    calls = []

    def resolve(router, routed_event):
        calls.append(routed_event['delegation_id'])
        return original(router, routed_event)

    monkeypatch.setattr(OwnerRoute, 'resolve', resolve)
    snapshot = callbacks._snapshot(include_receipts=True)
    assert calls == ['deleg_1', 'deleg_2']
    assert {record['session_id'] for record in snapshot['records'].values()} == {'chat'}
    monkeypatch.setattr(OwnerRoute, 'resolve',
                        lambda *_: pytest.fail('receipt validation rescanned owner routes'))
    callbacks._require_delivered_receipts(snapshot)


def test_receipt_retry_token_change_does_not_break_preservation(tmp_path):
    app, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(tmp_path)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    create_owned_ack(app, outbox, event, scope)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    retried_token = 'lease-after-retry'
    with outbox.transaction() as db:
        db.execute('UPDATE notification_outbox SET lease_token=? WHERE event_id=?',
                   (retried_token, 'async:' + event['delegation_id']))
    with app.notifications._db() as db:
        db.execute('UPDATE background_receipts SET lease_token=? WHERE event_id=?',
                   (retried_token, 'async:' + event['delegation_id']))
    activate(native, paths, candidate)
    assert probe_under_owned_lock(paths, callbacks, candidate) is True
    (paths.state / 'current').unlink()
    (paths.state / 'current').symlink_to(old)
    native.active_root = old
    native.pid, native.started = 321, 654
    results = []
    under_owned_lock(paths, lambda: results.append(callbacks.verify_rollback(old, baseline)))
    assert results == [True]


def test_probe_retries_a_transient_status_snapshot_race(tmp_path):
    app, outbox, native, paths, callbacks, baseline, _, candidate, event = setup_release(tmp_path)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    create_owned_ack(app, outbox, event, callbacks.scope)
    activate(native, paths, candidate)
    original_request = native.request
    first_status = True

    def request(path):
        nonlocal first_status
        status = original_request(path)
        if path == '/v1/mobile/notifications/status' and first_status:
            first_status = False
            return {**status, 'pending': 1, 'delivered': 0}
        return status

    native.request = request

    class Clock:
        value = 0

        def __call__(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = Clock()
    callbacks.clock = clock
    callbacks.sleep = clock.sleep
    assert probe_under_owned_lock(paths, callbacks, candidate) is True
    assert sum(row['route'] == 'owned' for row in callbacks.handoff_records.values()) == 1


def test_probe_does_not_reopen_with_missing_owned_ack(tmp_path):
    _, _, native, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    activate(native, paths, candidate)
    clock = iter((0, 1, 2, 3))
    callbacks.clock = lambda: next(clock)
    callbacks.receipt_timeout = 2
    callbacks.sleep = lambda _: None
    with pytest.raises(RuntimeError, match='receipt verification timed out'):
        probe_under_owned_lock(paths, callbacks, candidate)


def test_legacy_source_without_notification_attestation_is_a_blocker(tmp_path):
    _, _, _, paths, callbacks, baseline, _, _, _ = setup_release(tmp_path, with_event=False)
    baseline['source_hashes'] = dict(release.PREVIOUS_CONTROL_HASHES)
    with pytest.raises(RuntimeError, match='unsupported'):
        under_owned_lock(paths, lambda: callbacks.capture(baseline))


@pytest.mark.parametrize('phase', ['capture', 'handoff', 'probe'])
@pytest.mark.parametrize('result', [None, False, 0, 1, {}, {'status': 'verified'}])
def test_controller_requires_literal_true_from_each_proof(tmp_path, monkeypatch, phase, result):
    _, _, native, paths, callbacks, args, old, _ = setup_controller_release(tmp_path, monkeypatch)
    calls = []

    def proof(*values):
        calls.append(phase)
        return result  # Deliberately no proof: even truthy values must fail closed.

    if phase == 'capture':
        callbacks.capture = proof
    elif phase == 'handoff':
        callbacks.handoff = proof
    else:
        args['probe'] = proof
    with pytest.raises(RuntimeError, match='did not verify|did not pass'):
        release.deploy(paths, idle_timeout=0, **args)
    status = json.loads((paths.state / 'status.json').read_text())
    assert calls == [phase]
    assert status['status'] == 'rolled_back'  # Separate real rollback proof succeeded.
    assert (paths.state / 'current').resolve() == old
    assert native.pid == (127 if phase == 'probe' else 123)
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_real_proofs_return_literal_true_without_database_writes(tmp_path, monkeypatch):
    app, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(tmp_path)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    create_owned_ack(app, outbox, event, scope)
    monkeypatch.setattr(NotificationOutbox, 'claim', lambda *a, **kw: pytest.fail('claimed'))
    monkeypatch.setattr(NotificationOutbox, 'import_records', lambda *a, **kw: pytest.fail('imported'))
    databases = (callbacks.home / 'state.db', callbacks.auth, callbacks.runs,
                 callbacks.inbox, callbacks.outbox)
    before = {str(path): path.read_bytes() for path in databases}

    def prove(callback):
        result = []
        under_owned_lock(paths, lambda: result.append(callback()))
        assert result == [True] and result[0] is True
        assert {str(path): path.read_bytes() for path in databases} == before

    prove(lambda: callbacks.capture(baseline))
    prove(lambda: callbacks.handoff(candidate))
    activate(native, paths, candidate)
    prove(lambda: callbacks.probe(candidate))
    (paths.state / 'current').unlink()
    (paths.state / 'current').symlink_to(old)
    native.active_root = old
    native.pid, native.started = 321, 654  # Rollback is a new listener, not the baseline PID.
    prove(lambda: callbacks.verify_rollback(old, baseline))


def prepare_proof_phase(tmp_path, phase):
    app, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(tmp_path)
    # Exercise the actual source-selected NativeProbe readiness implementation,
    # while keeping process and HTTP observation boundaries entirely synthetic.
    native._ready = lambda health, *, baseline=None, root=None: release.NativeProbe._ready(
        native, health, baseline={**baseline, 'legacy': False} if baseline else None, root=root)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    create_owned_ack(app, outbox, event, scope)
    if phase == 'capture':
        action = lambda: callbacks.capture(baseline)
    else:
        under_owned_lock(paths, lambda: callbacks.capture(baseline))
        if phase == 'handoff':
            action = lambda: callbacks.handoff(candidate)
        else:
            under_owned_lock(paths, lambda: callbacks.handoff(candidate))
            if phase == 'probe':
                activate(native, paths, candidate)
                action = lambda: callbacks.probe(candidate)
            else:
                if phase == 'rollback-restarted':
                    native.pid, native.started = 321, 654
                action = lambda: callbacks.verify_rollback(old, baseline)
    return native, paths, callbacks, action


@pytest.mark.parametrize('phase', ['capture', 'handoff', 'probe', 'rollback', 'rollback-restarted'])
@pytest.mark.parametrize('window', ['snapshot-status', 'health', 'final-status'])
@pytest.mark.parametrize('reuse_pid', [False, True])
def test_each_proof_rejects_restart_between_identity_and_reads(tmp_path, phase, window, reuse_pid):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, phase)
    original = native.request
    statuses = 0
    fired = []

    def request(path):
        nonlocal statuses
        if path == '/v1/mobile/notifications/status':
            statuses += 1
        trigger = (path == '/health/detailed' if window == 'health' else
                   path == '/v1/mobile/notifications/status' and
                   statuses == (1 if window == 'snapshot-status' else 2))
        if trigger and not fired:
            if not reuse_pid:
                native.pid += 1
            native.started += 1
            fired.append(True)
        return original(path)

    native.request = request
    with pytest.raises(RuntimeError, match='identity|readiness'):
        under_owned_lock(paths, action)
    assert fired == [True]


@pytest.mark.parametrize('phase', ['capture', 'handoff', 'probe', 'rollback', 'rollback-restarted'])
@pytest.mark.parametrize('field', ['pid', 'start_ticks'])
def test_health_cannot_choose_its_own_expected_identity(tmp_path, phase, field):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, phase)
    original = native.request

    def request(path):
        value = original(path)
        if path == '/health/detailed':
            value['native_maintenance'][field] += 1
            if field == 'pid':
                value['pid'] += 1
        return value

    native.request = request
    with pytest.raises(RuntimeError, match='identity|readiness'):
        under_owned_lock(paths, action)


def test_handoff_does_not_prove_success_when_readiness_is_busy(tmp_path):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, 'handoff')
    original = native.request

    def request(path):
        value = original(path)
        if path == '/health/detailed':
            value['native_maintenance']['work']['active_run_tasks'] = 1
        return value

    native.request = request
    with pytest.raises(RuntimeError, match='not idle'):
        under_owned_lock(paths, action)
    assert callbacks.handoff_records is None


@pytest.mark.parametrize('phase', ['capture', 'handoff', 'probe', 'rollback'])
@pytest.mark.parametrize('source_state', ['', 'unknown', 'foreign-retained', b'accepted', 1.5])
def test_owned_source_state_is_closed_and_route_bound(tmp_path, phase, source_state):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, phase)
    with native.outbox.transaction() as db:
        db.execute('UPDATE notification_outbox SET source_state=?', (source_state,))
    with pytest.raises(RuntimeError, match='unknown or inconsistent'):
        under_owned_lock(paths, action)


@pytest.mark.parametrize('source_state', ['busy', 'conflict', 'incomplete', 'uncertain'])
def test_delivered_record_rejects_unresolved_source_state(tmp_path, source_state):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, 'probe')
    with native.outbox.transaction() as db:
        db.execute('UPDATE notification_outbox SET source_state=?', (source_state,))
    with pytest.raises(RuntimeError, match='unknown or inconsistent'):
        under_owned_lock(paths, action)


@pytest.mark.parametrize('source_state', [
    'unexamined', 'accepted', 'missing', 'dropped', 'delivered',
    'busy', 'conflict', 'incomplete', 'uncertain', 'queue-only',
])
@pytest.mark.parametrize('delivered', [False, True])
def test_source_attested_owned_states_keep_valid_positive_proofs(tmp_path, source_state, delivered):
    app, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(tmp_path)
    if source_state == 'queue-only':
        # Actual non-delegation producer, not an invented source marker.
        event = dict(type='completion', session_id='process-1', started_at=1,
                     session_key=event['session_key'], task_id='chat', output='Synthetic result')
        with outbox.transaction() as db:
            db.execute('DELETE FROM notification_outbox')
        outbox.capture(event, route='owned')
    if delivered:
        scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
        create_owned_ack(app, outbox, event, scope)
    with outbox.transaction() as db:
        db.execute('UPDATE notification_outbox SET source_state=?', (source_state,))
    if delivered and source_state in ('busy', 'conflict', 'incomplete', 'uncertain'):
        with pytest.raises(RuntimeError, match='unknown or inconsistent'):
            under_owned_lock(paths, lambda: callbacks.capture(baseline))
        return
    results = []
    under_owned_lock(paths, lambda: results.append(callbacks.capture(baseline)))
    under_owned_lock(paths, lambda: results.append(callbacks.handoff(candidate)))
    if delivered:
        activate(native, paths, candidate)
        results.append(probe_under_owned_lock(paths, callbacks, candidate))
    else:
        under_owned_lock(paths, lambda: results.append(callbacks.verify_rollback(old, baseline)))
    assert results == [True, True, True] and all(value is True for value in results)


@pytest.mark.parametrize('state,source_state', [
    ('foreign', 'foreign-retained'), ('foreign', 'unexamined'),
    ('foreign', 'accepted'), ('foreign', 'unknown'), ('foreign', b'foreign-retained'),
    ('pending', 'foreign-retained'), ('delivered', 'foreign-retained'),
])
def test_foreign_route_state_combinations_are_fail_closed(tmp_path, state, source_state):
    app, outbox, native, paths, callbacks, baseline, _, candidate, event = setup_release(tmp_path, with_event=False)
    foreign = {**event, 'platform': 'telegram'}
    assert OwnerRoute(callbacks.home, paths.state)(foreign) == 'foreign'
    outbox.capture(foreign, {'summary': 'Synthetic foreign result'}, route='foreign')
    with outbox.transaction() as db:
        db.execute('UPDATE notification_outbox SET state=?,source_state=?', (state, source_state))
    if (state, source_state) == ('foreign', 'foreign-retained'):
        capture_and_handoff(paths, callbacks, baseline, candidate)
        activate(native, paths, candidate)
        assert probe_under_owned_lock(paths, callbacks, candidate) is True
    else:
        with pytest.raises(RuntimeError, match='unknown or inconsistent'):
            under_owned_lock(paths, lambda: callbacks.capture(baseline))


def test_outbox_path_must_match_the_native_private_config(tmp_path):
    _, _, native, paths, callbacks, baseline, _, _, _ = setup_release(tmp_path, with_event=False)
    native.config_bytes = json.dumps({'state_dir': str(tmp_path / 'other-state')}).encode()
    with pytest.raises(RuntimeError, match='outbox path'):
        under_owned_lock(paths, lambda: callbacks.capture(baseline))


def append_owned_record(outbox, event, delegation_id):
    outbox.capture({**event, 'delegation_id': delegation_id},
                   {'summary': 'Synthetic ' + delegation_id}, route='owned')


def test_controller_retries_capture_after_legitimate_predrain_append(tmp_path, monkeypatch):
    _, outbox, native, paths, callbacks, args, old, event = setup_controller_release(
        tmp_path, monkeypatch)
    callbacks.sleep = lambda _: None
    original = native.request
    appended = []

    def request(path):
        # Active pre-drain work appends between the local snapshot and native status.
        if path == '/v1/mobile/notifications/status' and not appended:
            append_owned_record(outbox, event, 'deleg_2')
            appended.append(True)
        return original(path)

    native.request = request
    with pytest.raises(RuntimeError, match='receipt verification timed out'):
        release.deploy(paths, idle_timeout=0, **args)

    assert appended == [True]
    assert set(callbacks.initial_records) == {'async:deleg_1', 'async:deleg_2'}
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    assert (paths.state / 'current').resolve() == old
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_controller_capture_retry_is_bounded_with_stable_diagnostic(tmp_path, monkeypatch):
    _, outbox, native, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)
    sleeps = []
    callbacks.sleep = sleeps.append
    original = native.request
    appended = []

    def request(path):
        if path == '/v1/mobile/notifications/status':
            appended.append(True)
            append_owned_record(outbox, event, 'churn_%d' % len(appended))
        return original(path)

    native.request = request
    with pytest.raises(RuntimeError, match='admission gate remains closed'):
        release.deploy(paths, idle_timeout=0, **args)

    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == 'rollback_failed'
    assert status['error'] == 'Native notification evidence changed during observation'
    assert len(appended) == len(sleeps) + 1 > 1
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT owner FROM deployment_gate').fetchone() == (status['release'],)


def test_controller_capture_retry_rechecks_process_identity(tmp_path, monkeypatch):
    _, outbox, native, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)
    original = native.request
    appended = []

    def request(path):
        if path == '/v1/mobile/notifications/status' and not appended:
            append_owned_record(outbox, event, 'deleg_2')
            appended.append(True)
        return original(path)

    def restart_between_attempts(_):
        native.pid, native.started = native.pid + 10, native.started + 10

    native.request = request
    callbacks.sleep = restart_between_attempts
    with pytest.raises(RuntimeError, match='admission gate remains closed'):
        release.deploy(paths, idle_timeout=0, **args)
    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == 'rollback_failed'
    assert status['error'] == 'Native notification process identity changed'
    assert callbacks.baseline is None


def test_evidence_change_has_a_stable_diagnostic():
    from deploy.native_notification_release import _EvidenceChanged
    assert str(_EvidenceChanged()) == 'Native notification evidence changed during observation'


def regress_delivered_record(outbox, event):
    with outbox.transaction() as db:
        db.execute('''UPDATE notification_outbox SET state='pending',receipt_id=NULL,
            lease_until=NULL WHERE event_id=?''', ('async:' + event['delegation_id'],))


def test_controller_keeps_gate_closed_when_delivered_record_regresses(tmp_path, monkeypatch):
    _, outbox, _, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)

    def regress_then_fail(stage):
        regress_delivered_record(outbox, event)
        raise RuntimeError('ordinary candidate failure after regression')

    args['probe'] = regress_then_fail
    with pytest.raises(RuntimeError, match='admission gate remains closed'):
        release.deploy(paths, idle_timeout=0, **args)

    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == 'rollback_failed'
    assert status['rollback_error'] == 'Delivered native notification record was not preserved'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT owner FROM deployment_gate').fetchone() == (status['release'],)


@pytest.mark.parametrize('phase', ['handoff', 'probe', 'rollback'])
@pytest.mark.parametrize('change', ['pending', 'receipt'])
def test_delivered_state_and_receipt_binding_are_monotonic(tmp_path, phase, change):
    native, paths, callbacks, action = prepare_proof_phase(tmp_path, phase)
    callbacks.receipt_timeout, callbacks.sleep = 0, lambda _: None
    with native.outbox.transaction() as db:
        if change == 'pending':
            db.execute("UPDATE notification_outbox SET state='pending',receipt_id=NULL")
        else:
            db.execute("UPDATE notification_outbox SET receipt_id='another-receipt'")
    with pytest.raises(RuntimeError, match='not preserved|inconsistent'):
        under_owned_lock(paths, action)


def test_rollback_allows_predrain_append_before_handoff(tmp_path, monkeypatch):
    _, outbox, native, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)

    def append_then_stay_busy(*values, **kwargs):
        if not any(key == 'async:deleg_2' for key in callbacks.initial_records):
            append_owned_record(outbox, event, 'deleg_2')
        return False

    native.idle = append_then_stay_busy
    with pytest.raises(RuntimeError, match='Native idle wait timed out'):
        release.deploy(paths, idle_timeout=0, **args)
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


def test_snapshot_pages_retained_delivered_history_in_one_transaction(tmp_path, monkeypatch):
    from deploy import native_notification_release as callbacks_module
    app, outbox, native, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)
    scope = json.dumps(['default', str(callbacks.home)], separators=(',', ':'))
    for delegation_id in ('deleg_2', 'deleg_3', 'deleg_4'):
        routed = {**event, 'delegation_id': delegation_id}
        append_owned_record(outbox, event, delegation_id)
        create_owned_ack(app, outbox, routed, scope)
    monkeypatch.setattr(callbacks_module, 'PAGE_SIZE', 3)
    opened, original_readonly = [], callbacks_module._readonly
    resolved, original_resolve = [], OwnerRoute.resolve

    def readonly(path):
        opened.append(Path(path).name)
        return original_readonly(path)

    def resolve(router, routed_event):
        resolved.append(routed_event['delegation_id'])
        return original_resolve(router, routed_event)

    monkeypatch.setattr(callbacks_module, '_readonly', readonly)
    monkeypatch.setattr(OwnerRoute, 'resolve', resolve)
    snapshot = callbacks._snapshot(include_receipts=True)
    assert opened.count('native-notifications.sqlite') == 1
    assert sorted(resolved) == ['deleg_1', 'deleg_2', 'deleg_3', 'deleg_4']
    assert {record['state'] for record in snapshot['records'].values()} == {'delivered'}
    assert len(snapshot['records']) == 4

    release.deploy(paths, idle_timeout=0, **args)
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'
    assert set(callbacks.handoff_records) == {
        'async:deleg_1', 'async:deleg_2', 'async:deleg_3', 'async:deleg_4'}


def test_retained_history_beyond_former_cap_does_not_deadlock_release(tmp_path, monkeypatch):
    _, outbox, _, paths, callbacks, args, _, event = setup_controller_release(
        tmp_path, monkeypatch)
    rows = []
    for index in range(10001):
        foreign = {**event, 'delegation_id': 'history_%05d' % index, 'platform': 'telegram'}
        encoded = json.dumps(foreign, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        rows.append(('async:' + foreign['delegation_id'], encoded,
                     hashlib.sha256(encoded.encode()).hexdigest()))
    with outbox.transaction() as db:
        db.executemany('''INSERT INTO notification_outbox(event_id,event_json,payload_sha256,
            route,state,historical,provenance,source_state)
            VALUES(?,?,?,'foreign','foreign',0,'publisher','foreign-retained')''', rows)

    release.deploy(paths, idle_timeout=0, **args)
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'
    assert len(callbacks.handoff_records) == 10002
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)
