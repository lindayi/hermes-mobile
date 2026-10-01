"""Controls proving legacy fixture adapters exercise the approved policy boundary.

Real temporary auth/notification databases; only the push network is replaced.
"""
import json
from types import SimpleNamespace

import pytest

from test_notifications import delivery_env, subscription


def notification_env(tmp_path, network):
    test = SimpleNamespace(temp=SimpleNamespace(name=str(tmp_path)), db=tmp_path / 'notifications.db')
    return delivery_env(test, network)


def test_explicit_scheduled_fixture_does_not_make_unclassified_ingress_pushable(tmp_path):
    calls = []
    service, _, _, _, user = notification_env(tmp_path, lambda **kw: (
        calls.append(kw) or SimpleNamespace(status_code=201)))
    service.subscribe(user['id'], user['session_id'], subscription())
    internal = service.ingest(user['id'], 'unclassified', 'Internal', 'Inbox only')
    eligible = service.ingest(user['id'], 'scheduled', 'Report ready', 'Three tasks completed',
                              category='scheduled')
    with service._db() as db:
        assert [tuple(row) for row in db.execute('SELECT inbox_id,status FROM outbox')] == [
            (eligible['id'], 'policy_pending')]
    assert service.flush()['sent'] == 1
    assert len(calls) == 1
    assert json.loads(calls[0]['data'])['tag'] == eligible['id']
    assert {item['id'] for item in service.list_inbox(user['id'])} == {internal['id'], eligible['id']}


def test_read_before_claim_suppresses_eligible_fixture_without_losing_inbox(tmp_path):
    calls = []
    service, _, _, _, user = notification_env(tmp_path, lambda **kw: (
        calls.append(kw) or SimpleNamespace(status_code=201)))
    service.subscribe(user['id'], user['session_id'], subscription())
    item = service.ingest(user['id'], 'read-first', 'Report ready', 'Three tasks completed',
                          category='scheduled')
    with service._db() as db:
        assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'policy_pending'
    assert service.mark_read(user['id'], item['id'])
    assert service.flush()['sent'] == 0
    assert calls == []
    assert service.list_inbox(user['id'])[0]['read'] is True
    with service._db() as db:
        assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'suppressed'


def test_privacy_fixture_is_explicit_and_rechecked_on_retry(tmp_path):
    calls = []

    def network(**kwargs):
        calls.append(json.loads(kwargs['data']))
        return SimpleNamespace(status_code=503 if len(calls) == 1 else 201)

    service, _, _, now, user = notification_env(tmp_path, network)
    service.subscribe(user['id'], user['session_id'], subscription())
    preferences = service.get_preferences(user['id'], user['session_id'])
    assert preferences['hide_details'] is False
    item = service.ingest(user['id'], 'privacy-retry', 'Report ready', 'Three tasks completed',
                          category='scheduled')
    assert service.flush()['failed'] == 1
    assert calls[0]['title'] == 'Report ready'
    assert calls[0]['body'] == 'Three tasks completed'
    with service._db() as db:
        status, attempts, retry_at = db.execute(
            'SELECT status,attempts,next_attempt_at FROM outbox').fetchone()
    assert (status, attempts) == ('policy_retry', 1)
    assert retry_at > now[0]
    service.set_preferences(user['id'], user['session_id'], {**preferences, 'hide_details': True})
    assert service.flush()['sent'] == 0
    assert len(calls) == 1
    now[0] = retry_at
    assert service.flush()['sent'] == 1
    assert len(calls) == 2
    assert calls[1]['title'] == 'Hermes'
    assert calls[1]['body'] == 'You have a new notification.'
    assert calls[1]['url'] == '/hermes/?inbox=' + item['id']
    assert 'Report ready' not in json.dumps(calls[1])
    assert 'Three tasks completed' not in json.dumps(calls[1])
    assert service.list_inbox(user['id'])[0]['body'] == 'Three tasks completed'


@pytest.mark.parametrize('legacy_status', ['pending', 'retry'])
def test_legacy_status_write_cannot_revive_policy_queue(tmp_path, legacy_status):
    calls = []
    service, _, _, _, user = notification_env(tmp_path, lambda **kw: (
        calls.append(kw) or SimpleNamespace(status_code=201)))
    service.subscribe(user['id'], user['session_id'], subscription())
    item = service.ingest(user['id'], 'legacy-write', 'Report ready', 'Three tasks completed',
                          category='scheduled')
    with service._db() as db:
        assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'policy_pending'
        db.execute('UPDATE outbox SET status=?', (legacy_status,))
        assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'suppressed'
    assert service.flush()['sent'] == 0
    assert calls == []
    assert service.list_inbox(user['id']) == [item]
