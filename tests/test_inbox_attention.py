"""Attention is a query view; synthetic receipt storage/ACK state stays intact."""
from types import SimpleNamespace

import pytest

from backend.notifications import NotificationService, build_notifications_router
from test_auth import BASE
from test_notifications import delivery_env, subscription


@pytest.fixture
def attention_env(tmp_path):
    service, auth, client, now, user = delivery_env(
        SimpleNamespace(temp=SimpleNamespace(name=str(tmp_path)), db=tmp_path/'notifications.db'), None)
    client.app.include_router(build_notifications_router(service, auth), prefix=BASE)
    try:
        yield service, client, now, user
    finally:
        client.close()


def background(service, owner, source, event='one'):
    if source == 'category':
        return service.ingest(owner, event, 'Routine task', 'Public result', 'chat',
                              category='background')['id']
    return service.store_background(scope='fixture', event_id=event, digest='fixture-digest',
        user_id=owner, origin='chat', session_id='chat', event={'summary': 'Public result'},
        lease_token='fixture-lease', body='Public result')


@pytest.mark.parametrize('source', ['receipt', 'category'])
@pytest.mark.parametrize('operation', ['list', 'read_count', 'clear_read'])
def test_attention_api_uses_same_visibility_without_mutating_receipts(attention_env, source, operation):
    service, client, now, user = attention_env
    owner = user['id']
    service.subscribe(owner, user['session_id'], subscription())
    notice = service.ingest(owner, 'attention', 'Needs attention', 'Review this', 'chat', silent=True,
                            category='attention')
    service.mark_read(owner, notice['id'])
    read_bg = background(service, owner, source, 'read')
    unread_bg = background(service, owner, source, 'unread')
    service.mark_read(owner, read_bg)
    with service._db() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM inbox ORDER BY id')]
        receipts = [tuple(r) for r in db.execute('SELECT * FROM background_receipts')]
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
    if operation == 'clear_read':
        response = client.post(BASE + '/inbox/clear-read', json={})
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'dismissed': 1}
        assert client.post(BASE + '/inbox/clear-read', json={}).json()['dismissed'] == 0
    else:
        response = client.get(BASE + '/inbox')
        assert response.status_code == 200
        if operation == 'list':
            assert [r['id'] for r in response.json()['items']] == [notice['id']]
        else:
            assert response.json()['read_count'] == 1
    with service._db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM inbox ORDER BY id')] == before
        assert [tuple(r) for r in db.execute('SELECT * FROM background_receipts')] == receipts
        assert db.execute('SELECT count(*) FROM dismissed_inbox WHERE inbox_id IN (?,?)',
                          (read_bg, unread_bg)).fetchone()[0] == 0
    reopened = NotificationService(service.db_path)
    assert reopened.read_count(owner) == (0 if operation == 'clear_read' else 1)
    assert [r['id'] for r in reopened.list_inbox(owner)] == ([] if operation == 'clear_read' else [notice['id']])


@pytest.mark.parametrize('source', ['receipt', 'category'])
def test_attention_legacy_newest_page_saturation_reopen_and_title_lookalikes(attention_env, source):
    service, client, now, user = attention_env
    owner = user['id']
    with service._db() as db:
        # No policy metadata is needed for an old real attention row, and neither
        # its title nor body is provenance. Read/unread counts follow eligibility.
        for n in range(3):
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                       (f'notice-{n}', owner, f'notice-{n}', 'Background result',
                        'async_delegation process exited', 'chat', n, int(n != 0)))
        for n in range(205):
            item_id = f'legacy-{n}'
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                       (item_id, owner, item_id, 'Any arbitrary title', 'Retained report', 'chat', 100+n, n % 2))
            if source == 'receipt':
                db.execute('INSERT INTO background_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                           ('fixture', item_id, 'digest', owner, 'chat', item_id, '{}', 'lease', 1))
            else:
                db.execute('INSERT INTO notification_policy VALUES(?,?,NULL,NULL)', (item_id, 'background'))
        # An already dismissed result remains durable and cannot be resurrected.
        db.execute('INSERT INTO dismissed_inbox VALUES(?,?,?)', ('legacy-1', owner, now[0]))
        before = [tuple(r) for r in db.execute('SELECT * FROM inbox ORDER BY id')]
        receipts = [tuple(r) for r in db.execute('SELECT * FROM background_receipts ORDER BY inbox_id')]
    reopened = NotificationService(service.db_path)
    assert [i['id'] for i in reopened.list_inbox(owner, limit=1, offset=1)] == ['notice-1']
    page = client.get(BASE + '/inbox?limit=2').json()
    assert [i['id'] for i in page['items']] == ['notice-2', 'notice-1']
    assert page['read_count'] == 2
    assert [i['id'] for i in client.get(BASE + '/inbox?limit=2&offset=2').json()['items']] == ['notice-0']
    assert client.post(BASE + '/inbox/clear-read', json={}).json() == {'ok': True, 'dismissed': 2}
    assert [i['id'] for i in reopened.list_inbox(owner)] == ['notice-0']
    assert reopened.read_count(owner) == 0
    with service._db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM inbox ORDER BY id')] == before
        assert [tuple(r) for r in db.execute('SELECT * FROM background_receipts ORDER BY inbox_id')] == receipts
        assert {r[0] for r in db.execute('SELECT inbox_id FROM dismissed_inbox')} == {'notice-1', 'notice-2', 'legacy-1'}


def test_attention_foreign_receipt_cannot_hide_or_retarget_owned_notice(attention_env):
    service, client, now, user = attention_env
    owner = user['id']
    notice = service.ingest(owner, 'owned', 'Background result', 'Needs attention', 'owned-chat', category='attention')
    foreign_notice = service.ingest('foreign', 'other', 'Other attention', 'Private', 'foreign-chat', category='attention')
    foreign_bg = background(service, 'foreign', 'receipt')
    with service._db() as db:
        # Corrupt/foreign provenance must not classify an owner's unrelated row.
        db.execute('UPDATE background_receipts SET inbox_id=? WHERE inbox_id=?', (notice['id'], foreign_bg))
    service.background_link = lambda *args: 'foreign-chat'
    page = client.get(BASE + '/inbox').json()
    assert page == {'items': [notice], 'read_count': 0}
    service.mark_read(owner, notice['id'])
    service.mark_read('foreign', foreign_notice['id'])
    assert client.get(BASE + '/inbox').json()['read_count'] == 1
    assert client.post(BASE + '/inbox/clear-read', json={}).json()['dismissed'] == 1
    assert service.read_count('foreign') == 1
    with service._db() as db:
        assert db.execute('SELECT count(*) FROM dismissed_inbox WHERE user_id=?', ('foreign',)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 3


def test_attention_legacy_absent_policy_keeps_receipt_filter_without_schema_writes(attention_env):
    service, client, now, user = attention_env
    owner = user['id']
    notice = service.ingest(owner, 'legacy', 'Background result', 'Real attention', silent=True)
    item_id = background(service, owner, 'receipt')
    service.mark_read(owner, notice['id'])
    service.mark_read(owner, item_id)
    with service._db() as db:
        db.execute('DROP TABLE notification_policy')
        schema = [tuple(r) for r in db.execute('SELECT * FROM sqlite_master ORDER BY name')]
    assert [i['id'] for i in service.list_inbox(owner)] == [notice['id']]
    assert service.read_count(owner) == 1
    assert service.clear_read(owner) == 1
    assert service.list_inbox(owner) == []
    with service._db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM sqlite_master ORDER BY name')] == schema
        assert {r[0] for r in db.execute('SELECT inbox_id FROM dismissed_inbox')} == {notice['id']}
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM background_receipts').fetchone()[0] == 1


def test_attention_policy_query_errors_never_fall_back_to_unfiltered_rows(attention_env):
    import sqlite3
    service, client, now, user = attention_env
    owner = user['id']
    item_id = background(service, owner, 'category')
    service.mark_read(owner, item_id)
    with service._db() as db:
        db.execute('ALTER TABLE notification_policy RENAME COLUMN category TO broken_category')
    for query in (service.list_inbox, service.read_count, service.clear_read):
        with pytest.raises(sqlite3.OperationalError, match='category'):
            query(owner)
    with service._db() as db:
        assert db.execute('SELECT count(*) FROM dismissed_inbox').fetchone()[0] == 0
        assert db.execute('SELECT id,read FROM inbox').fetchone()[:] == (item_id, 1)


def test_attention_receipt_provenance_wins_over_nonbackground_category(attention_env):
    service, client, now, user = attention_env
    item_id = background(service, user['id'], 'receipt')
    with service._db() as db:
        db.execute('INSERT INTO notification_policy VALUES(?,?,NULL,NULL)', (item_id, 'operational'))
    assert client.get(BASE + '/inbox').json() == {'items': [], 'read_count': 0}
