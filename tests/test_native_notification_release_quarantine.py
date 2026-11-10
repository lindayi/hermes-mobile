"""Synthetic retained quarantine is preservation evidence, never delivery authority."""
import hashlib
import json
import sqlite3

import pytest

from backend.native_notifications import NotificationOutbox, OwnerRoute
from deploy import native_controls_release as release
from deploy.native_notification_release import NativeNotificationCallbacks
from test_native_notification_release_callbacks import (
    activate, create_owned_ack, probe_under_owned_lock, setup_controller_release,
    setup_release, under_owned_lock,
)


def retained_quarantine(outbox, event, home, state, *, result=None, historical=True):
    event = {**event, 'delegation_id': 'retained-quarantine', 'session_key': 'unknown-key'}
    assert OwnerRoute(home, state)(event) == 'quarantined'
    key = outbox.capture(event, result, route='quarantined', historical=historical,
                         provenance='synthetic-retained-publisher')
    assert outbox.record(key)['source_state'] == 'unexamined'
    return key


@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('result', [None, {'summary': 'Synthetic retained result'}])
@pytest.mark.parametrize('historical', [False, True])
def test_cold_quarantine_preserved_through_release_and_rollback(
        tmp_path, monkeypatch, mixed, result, historical):
    app, outbox, native, paths, original, baseline, old, candidate, event = setup_release(
        tmp_path, with_event=mixed)
    if mixed:
        scope = json.dumps(['default', str(original.home)], separators=(',', ':'))
        create_owned_ack(app, outbox, event, scope)
        foreign = {**event, 'delegation_id': 'retained-foreign', 'platform': 'telegram'}
        key = outbox.capture(foreign, {'summary': 'Synthetic foreign result'}, route='foreign')
        with outbox.transaction() as db:
            db.execute("UPDATE notification_outbox SET source_state='foreign-retained' WHERE event_id=?", (key,))
    key = retained_quarantine(outbox, event, original.home, paths.state,
                              result=result, historical=historical)
    original.close()
    # No warm callback memory or synthetic ACK is needed for retained quarantine.
    callbacks = NativeNotificationCallbacks(paths, native, home=original.home, receipt_timeout=0)
    before = outbox.record(key)
    status = native._status()
    before_bytes = outbox.path.read_bytes()
    with sqlite3.connect(paths.state / 'notifications.sqlite') as db:
        receipts = db.execute('SELECT * FROM background_receipts').fetchall()
    for name in ('claim', 'ack', 'capture', 'import_records'):
        monkeypatch.setattr(NotificationOutbox, name,
                            lambda *a, **kw: pytest.fail('release mutated notification delivery'))
    with callbacks:
        under_owned_lock(paths, lambda: callbacks.capture(baseline))
        under_owned_lock(paths, lambda: callbacks.verify_rollback(old, baseline))
        under_owned_lock(paths, lambda: callbacks.handoff(candidate))
        record = callbacks._records_view(callbacks.handoff_records.snap)[key]
        assert record['route'] == record['state'] == 'quarantined'
        assert record['source_state'] == 'unexamined'
        assert record['historical'] == int(historical)
        assert record['session_id'] is record['receipt_id'] is record['lease_token'] is None
        assert record['payload_sha256'] == before['payload_sha256']
        assert record['provenance_hash'] == hashlib.sha256(before['provenance'].encode()).hexdigest()
        assert key not in callbacks.initial_deliveries
        assert key not in callbacks.initial_receipts
        activate(native, paths, candidate)
        assert probe_under_owned_lock(paths, callbacks, candidate) is True
        activate(native, paths, old)
        under_owned_lock(paths, lambda: callbacks.verify_rollback(old, baseline))
    assert outbox.record(key) == before
    assert outbox.path.read_bytes() == before_bytes
    assert native._status() == status
    assert status['quarantined'] == 1
    assert status['delivered'] == int(mixed)
    with sqlite3.connect(paths.state / 'notifications.sqlite') as db:
        assert db.execute('SELECT * FROM background_receipts').fetchall() == receipts


@pytest.mark.parametrize('failure_phase', ['predrain', 'activated'])
def test_controller_rollback_preserves_quarantine_and_reopens_only_after_proof(
        tmp_path, monkeypatch, failure_phase):
    _, outbox, native, paths, callbacks, args, old, event = setup_controller_release(
        tmp_path, monkeypatch)
    key = retained_quarantine(outbox, event, callbacks.home, paths.database.parent,
                              result={'summary': 'Synthetic retained result'}, historical=False)
    before = outbox.record(key)
    if failure_phase == 'predrain':
        native.idle = lambda *a, **kw: False
        error = 'Native idle wait timed out'
    else:
        def fail_after_positive_probe(stage):
            assert callbacks.probe(stage) is True
            raise RuntimeError('synthetic candidate failure')
        args['probe'] = fail_after_positive_probe
        error = 'synthetic candidate failure'
    with callbacks, pytest.raises(RuntimeError, match=error):
        release.deploy(paths, idle_timeout=0, **args)
    assert callbacks.baseline is not None
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    assert (paths.state / 'current').resolve() == old
    assert outbox.record(key) == before
    with sqlite3.connect(paths.database) as db:
        assert db.execute('SELECT COUNT(*) FROM deployment_gate').fetchone() == (0,)


@pytest.mark.parametrize('phase', ['capture', 'handoff', 'probe', 'rollback'])
@pytest.mark.parametrize('field,value', [
    ('state', 'pending'), ('state', 'delivered'), ('state', 'unknown'),
    ('route', 'owned'), ('route', 'foreign'),
    ('source_state', 'accepted'), ('source_state', 'foreign-retained'),
    ('source_state', 'busy'), ('source_state', 'unknown'), ('source_state', b'unexamined'),
    ('receipt_id', 'forged-receipt'), ('receipt_id', ''),
    ('lease_token', 'unexpected-lease'), ('lease_token', ''), ('lease_until', 1),
    ('payload_sha256', '0' * 64), ('event_json', '{}'),
    ('event_id', 'async:wrong-id'), ('result_json', '{malformed'),
    ('historical', 2), ('provenance', ''),
])
def test_quarantine_malformed_evidence_fails_closed(tmp_path, phase, field, value):
    _, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(
        tmp_path, with_event=False)
    key = retained_quarantine(outbox, event, callbacks.home, paths.state)
    with callbacks:
        if phase != 'capture':
            under_owned_lock(paths, lambda: callbacks.capture(baseline))
        if phase in ('probe', 'rollback'):
            under_owned_lock(paths, lambda: callbacks.handoff(candidate))
        if phase == 'probe':
            activate(native, paths, candidate)
        with outbox.transaction() as db:
            db.execute(f'UPDATE notification_outbox SET {field}=? WHERE event_id=?', (value, key))
        actions = dict(capture=lambda: callbacks.capture(baseline),
                       handoff=lambda: callbacks.handoff(candidate),
                       probe=lambda: callbacks.probe(candidate),
                       rollback=lambda: callbacks.verify_rollback(old, baseline))
        with pytest.raises(RuntimeError, match='unknown or inconsistent'):
            under_owned_lock(paths, actions[phase])
        with sqlite3.connect(paths.database) as db:
            assert db.execute('SELECT owner FROM deployment_gate').fetchone() == (baseline['gate_owner'],)


@pytest.mark.parametrize('phase', ['handoff', 'probe', 'rollback'])
@pytest.mark.parametrize('mutation', ['result', 'absent-result', 'payload', 'provenance', 'route', 'removed'])
def test_quarantine_evidence_cannot_change_after_capture(tmp_path, phase, mutation):
    _, outbox, native, paths, callbacks, baseline, old, candidate, event = setup_release(
        tmp_path, with_event=False)
    key = retained_quarantine(outbox, event, callbacks.home, paths.state,
                              result=None if mutation == 'absent-result' else {'summary': 'Original'})
    with callbacks:
        under_owned_lock(paths, lambda: callbacks.capture(baseline))
        if phase in ('probe', 'rollback'):
            under_owned_lock(paths, lambda: callbacks.handoff(candidate))
        if phase == 'probe':
            activate(native, paths, candidate)
        with outbox.transaction() as db:
            if mutation in ('result', 'absent-result'):
                db.execute("UPDATE notification_outbox SET result_json=? WHERE event_id=?",
                           ('{"summary":"Changed"}', key))
            elif mutation in ('payload', 'route'):
                payload = json.loads(db.execute('SELECT event_json FROM notification_outbox WHERE event_id=?',
                                                (key,)).fetchone()[0])
                payload['session_key' if mutation == 'route' else 'summary'] = (
                    event['session_key'] if mutation == 'route' else 'Changed')
                encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                db.execute('UPDATE notification_outbox SET event_json=?,payload_sha256=? WHERE event_id=?',
                           (encoded, hashlib.sha256(encoded.encode()).hexdigest(), key))
            elif mutation == 'provenance':
                db.execute("UPDATE notification_outbox SET provenance='changed' WHERE event_id=?", (key,))
            else:
                db.execute('DELETE FROM notification_outbox WHERE event_id=?', (key,))
        actions = dict(handoff=lambda: callbacks.handoff(candidate),
                       probe=lambda: callbacks.probe(candidate),
                       rollback=lambda: callbacks.verify_rollback(old, baseline))
        with pytest.raises(RuntimeError, match='not preserved|changed after handoff|unknown or inconsistent'):
            under_owned_lock(paths, actions[phase])
