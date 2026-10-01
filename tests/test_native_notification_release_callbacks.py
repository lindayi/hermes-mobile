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


def test_empty_backlog_requires_and_accepts_positive_bound_status(tmp_path):
    _, _, native, paths, callbacks, baseline, _, candidate, _ = setup_release(tmp_path, with_event=False)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    activate(native, paths, candidate)
    assert probe_under_owned_lock(paths, callbacks, candidate) == {'status': 'verified', 'owned_count': 0}


def test_probe_requires_existing_owned_delivery_and_ack_chain(tmp_path):
    app, outbox, native, paths, callbacks, baseline, _, candidate, event = setup_release(tmp_path)
    capture_and_handoff(paths, callbacks, baseline, candidate)
    create_owned_ack(app, outbox, event, callbacks.scope)
    activate(native, paths, candidate)
    assert probe_under_owned_lock(paths, callbacks, candidate) == {'status': 'verified', 'owned_count': 1}


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
    assert probe_under_owned_lock(paths, callbacks, candidate) == {
        'status': 'verified', 'owned_count': 1}


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


def test_outbox_path_must_match_the_native_private_config(tmp_path):
    _, _, native, paths, callbacks, baseline, _, _, _ = setup_release(tmp_path, with_event=False)
    native.config_bytes = json.dumps({'state_dir': str(tmp_path / 'other-state')}).encode()
    with pytest.raises(RuntimeError, match='outbox path'):
        under_owned_lock(paths, lambda: callbacks.capture(baseline))
