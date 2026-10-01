"""Isolated real SQLite approval notifications; never touches live state."""
import json
from types import SimpleNamespace
from backend.notifications import NotificationService
from tests.test_notifications import subscription


def test_new_owned_approval_is_durable_bound_and_deduplicated(tmp_path):
    service = NotificationService(tmp_path/'push.sqlite', clock=lambda: 100)
    service.approval_validator = lambda *identity: identity == ('alice', 'run', 'native', 'approval')
    service.subscribe('alice', 'device', subscription())
    assert callable(getattr(service, 'ingest_approval', None)), 'Approval has no durable push ingestion path'
    first = service.ingest_approval('alice', 'run', 'native', 'approval', 'chat', 200)
    duplicate = service.ingest_approval('alice', 'run', 'native', 'approval', 'chat', 200)
    assert first == duplicate
    assert first['request_id'] == 'native'
    assert first['approval_id'] == 'approval'
    assert first['run_id'] == 'run'
    assert first['session_id'] == 'chat'
    with service._db() as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 1
        assert db.execute('SELECT expires_at FROM outbox').fetchone()[0] == 200
    assert NotificationService(tmp_path/'push.sqlite').list_inbox('alice') == [first]
    assert service.list_inbox('bob') == []


def test_validator_constructor_and_expiry_fail_closed(tmp_path):
    import inspect
    assert 'approval_validator' in inspect.signature(NotificationService).parameters
    now = [100]
    sent = []
    service = NotificationService(tmp_path/'push.sqlite', 'private', 'public',
        clock=lambda: now[0], session_validator=lambda *args: True,
        approval_validator=lambda *args: args[0] == 'alice',
        send_push=lambda **kw: (sent.append(kw) or SimpleNamespace(status_code=201)))
    service.subscribe('alice', 'device', subscription())
    assert service.ingest_approval('bob', 'run', 'native', 'approval', 'chat', 200) is None
    assert service.ingest_approval('alice', 'run', 'expired', 'expired', 'chat', 99) is None
    service.ingest_approval('alice', 'run', 'native', 'approval', 'chat', 200)
    now[0] = 201
    assert service.flush()['sent'] == 0
    assert sent == []


def test_resolved_approval_is_suppressed_but_cron_delivery_is_unchanged(tmp_path):
    sent = []
    service = NotificationService(tmp_path/'push.sqlite', 'private', 'public',
        clock=lambda: 100, session_validator=lambda *args: True,
        send_push=lambda **kw: (sent.append(json.loads(kw['data'])) or SimpleNamespace(status_code=201)))
    service.approval_validator = lambda *args: True
    service.subscribe('alice', 'device', subscription())
    service.ingest_approval('alice', 'run', 'native', 'approval', 'chat', 200)
    cron = service.ingest('alice', 'cron:original', 'Cron', 'Original body', category='scheduled')
    service.approval_validator = lambda *args: False
    result = service.flush()
    assert result['sent'] == 1, 'Resolved approval must not be pushed'
    assert sent[0]['url'] == '/hermes/?inbox=' + cron['id']
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE status='suppressed'").fetchone()[0] == 1
