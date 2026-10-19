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


def test_request_and_session_approval_rows_are_hidden_without_changing_push_storage(attention_env):
    service, client, now, user = attention_env
    from types import SimpleNamespace
    sent = []
    service.send_push = lambda **payload: (sent.append(payload) or SimpleNamespace(status_code=201))
    service.approval_validator = lambda *identity: True
    service.subscribe(user['id'], user['session_id'], subscription())
    completed = service.ingest(user['id'], 'request:run-1:terminal', 'Request finished',
        'The result is ready.', 'chat-1', category='completion')
    failed = service.ingest(user['id'], 'request:run-2:terminal', 'Request needs attention',
        'The request failed.', 'chat-1', category='attention')
    approval = service.ingest_approval(user['id'], 'run-3', 'native-3', 'approval-3',
        'chat-1', now[0] + 100)
    scheduled = service.ingest(user['id'], 'scheduled:1', 'Scheduled update', 'Still visible',
        category='scheduled')

    assert service.flush()['sent'] == 4
    assert len(sent) == 4
    for item in (completed, failed, approval, scheduled):
        service.mark_read(user['id'], item['id'])

    assert [item['id'] for item in service.list_inbox(user['id'])] == [scheduled['id']]
    assert service.read_count(user['id']) == 1
    assert service.clear_read(user['id']) == 1
    with service._db() as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 4
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 4
        assert db.execute('SELECT count(*) FROM dismissed_inbox').fetchone()[0] == 1
        assert {row[0] for row in db.execute('SELECT status FROM outbox')} == {'sent'}


def test_inbox_route_batches_approval_visibility_and_shares_snapshot(attention_env):
    service, client, now, user = attention_env
    owner = user['id']
    dismissed_ids = set()
    visible_ids = set()
    with service._db() as db:
        for index in range(72):
            item_id = f'history-{index}'
            session_id = 'chat-alias' if index % 3 == 0 else (
                'cron-session' if index % 3 == 1 else None)
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                       (item_id, owner, f'approval:{index}', 'Approval required',
                        'Review the action.', session_id, now[0] - 100 + index, 1))
            db.execute('INSERT INTO approval_notifications VALUES(?,?,?,?,?)',
                       (item_id, f'action-{index}', f'run-{index}',
                        f'request-{index}', now[0] + 600))
            if index % 10 == 0:
                db.execute('INSERT INTO dismissed_inbox VALUES(?,?,?)',
                           (item_id, owner, now[0]))
                dismissed_ids.add(item_id)
            else:
                if session_id != 'chat-alias':
                    visible_ids.add(item_id)
        latest = 'newest-non-chat'
        db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                   (latest, owner, 'approval:newest', 'Approval required',
                    'Review the newest action.', None, now[0] + 2, 1))
        db.execute('INSERT INTO approval_notifications VALUES(?,?,?,?,?)',
                   (latest, 'action-newest', 'run-newest', 'request-newest', now[0] + 600))
    visible_ids.add(latest)

    stats = {'batches': 0, 'auth_reads': 0, 'approval_queries': 0, 'lineage_reads': 0}

    def batch_resolver(user_id, rows):
        stats['batches'] += 1
        stats['auth_reads'] += 1
        stats['approval_queries'] += 1
        assert user_id == owner
        ids = {row['id'] for row in rows}
        assert ids == {f'history-{index}' for index in range(72)} - dismissed_ids | {latest}
        sessions = {row['session_id'] for row in rows}
        stats['lineage_reads'] += len(sessions & {'chat-alias'})
        return {
            row['id']: {'kind': 'chat' if row['session_id'] == 'chat-alias' else 'inbox'}
            for row in rows
        }

    service.approval_targets_resolver = batch_resolver
    scheduled = service.ingest(owner, 'scheduled:batch', 'Scheduled update', 'Still visible',
                               category='scheduled')
    with service._db() as db:
        db.execute('UPDATE inbox SET created_at=? WHERE id=?', (now[0] + 1, scheduled['id']))

    page = client.get(BASE + '/inbox?limit=1').json()
    assert [item['id'] for item in page['items']] == [latest]
    assert page['read_count'] == len(visible_ids)
    next_page = client.get(BASE + '/inbox?limit=1&offset=1').json()
    assert [item['id'] for item in next_page['items']] == [scheduled['id']]
    assert stats == {'batches': 2, 'auth_reads': 2, 'approval_queries': 2,
                     'lineage_reads': 2}


def test_assembled_inbox_batches_real_approval_proofs_and_repeated_aliases(tmp_path, monkeypatch):
    import sqlite3
    import time
    from contextlib import contextmanager
    from test_steering import environment

    with environment(tmp_path) as (app, client, user, original_run, _):
        journal = app.state.journal
        service = app.state.notifications
        journal.finish(user['id'], original_run, 'completed')
        with sqlite3.connect(app.state.settings.profiles['default'] / 'state.db') as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                       ('cron-session', 'Synthetic scheduled session', 'cron', 3, 4, None))

        retained = []
        dismissed = set()
        visible = []
        for index in range(210):
            is_chat = index % 2 == 1
            session_id = 'old-alias' if is_chat else 'cron-session'
            run, _ = journal.submit(user['id'], 'default', session_id, 'Synthetic request',
                f'inbox-batch-{index}',
                history_anchor=(lambda: {'session_id': 'old-alias',
                    'canonical_session_id': 'wa-1', 'message_id': 1}) if is_chat else None)
            journal.finish(user['id'], run['id'], 'completed')
            approval_id = f'approval-{index}'
            request_id = f'request-{index}'
            with journal.connect() as db:
                db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                           (approval_id, run['id'], request_id, '{}', 'resolved', time.time() + 600))
            item_id = f'receipt-{index}'
            retained.append(item_id)
            with service._db() as db:
                db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)',
                           (item_id, user['id'], f'approval:{run["id"]}', 'Approval required',
                            'Synthetic action.', session_id, 100 + index, 1))
                db.execute('INSERT INTO approval_notifications VALUES(?,?,?,?,?)',
                           (item_id, approval_id, run['id'], request_id, time.time() + 600))
                if index % 13 == 0:
                    db.execute('INSERT INTO dismissed_inbox VALUES(?,?,?)',
                               (item_id, user['id'], time.time()))
                    dismissed.add(item_id)
                elif not is_chat:
                    visible.append((index, item_id))

        scheduled = service.ingest(user['id'], 'scheduled:batch-route', 'Scheduled update',
                                   'Preserved Inbox item.', category='scheduled')
        with service._db() as db:
            db.execute('UPDATE inbox SET created_at=99 WHERE id=?', (scheduled['id'],))

        work = {'batches': 0, 'auth': 0, 'journal': 0, 'native': 0, 'lineage': 0}
        resolver = service.approval_targets_resolver

        def batch_resolver(owner, rows):
            work['batches'] += 1
            assert owner == user['id']
            assert len(rows) == len(retained) - len(dismissed)
            assert {row['id'] for row in rows} == set(retained) - dismissed
            return resolver(owner, rows)

        service.approval_targets_resolver = batch_resolver
        original_transaction = app.state.auth.store.transaction

        @contextmanager
        def tracked_transaction(*args, **kwargs):
            work['auth'] += 1
            with original_transaction(*args, **kwargs) as db:
                yield db

        original_journal_connect = journal.connect

        def tracked_journal_connect(*args, **kwargs):
            work['journal'] += 1
            return original_journal_connect(*args, **kwargs)

        original_native_connect = app.state.catalog._connect

        def tracked_native_connect(profile):
            work['native'] += 1
            return original_native_connect(profile)

        original_lineage = app.state.background_delivery._lineage

        def tracked_lineage(*args, **kwargs):
            work['lineage'] += 1
            return original_lineage(*args, **kwargs)

        monkeypatch.setattr(app.state.auth.store, 'transaction', tracked_transaction)
        monkeypatch.setattr(journal, 'connect', tracked_journal_connect)
        monkeypatch.setattr(app.state.catalog, '_connect', tracked_native_connect)
        monkeypatch.setattr(app.state.background_delivery, '_lineage', tracked_lineage)

        page = client.get(BASE + '/inbox?limit=1').json()
        visible.sort(reverse=True)
        assert [item['id'] for item in page['items']] == [visible[0][1]]
        assert page['read_count'] == len(visible)
        next_page = client.get(BASE + '/inbox?limit=1&offset=1').json()
        assert [item['id'] for item in next_page['items']] == [visible[1][1]]
        assert work['batches'] == 2
        assert work['auth'] <= 8
        assert work['journal'] <= 12
        assert work['native'] <= 16
        assert work['lineage'] == 4
        with service._db() as db:
            assert db.execute('SELECT count(*) FROM inbox WHERE user_id=?', (user['id'],)).fetchone()[0] == 211
            assert db.execute('SELECT count(*) FROM approval_notifications').fetchone()[0] == 210
            assert db.execute('SELECT count(*) FROM dismissed_inbox').fetchone()[0] == len(dismissed)


def test_notification_target_lookup_is_owner_scoped_and_binds_request_or_approval(attention_env):
    service, client, now, user = attention_env
    service.approval_validator = lambda *identity: True
    service.approval_target_resolver = lambda owner, run, request, approval, session: {
        'kind':'chat','session_id':session,'run_session_id':session
    }
    service.request_target_resolver = lambda owner, delivery_id, session_id, category: {
        'session_id': session_id, 'run_session_id': delivery_id.split(':')[1]
    }
    request = service.ingest(user['id'], 'request:run-1:terminal', 'Finished', 'Public result',
        'chat-1', category='completion')
    failed = service.ingest(user['id'], 'request:run-failed:terminal', 'Needs attention',
        'Safe failure summary', 'chat-1', category='attention')
    approval = service.ingest_approval(user['id'], 'run-2', 'native-2', 'approval-2',
        'chat-1', now[0] + 100)
    foreign = service.ingest('foreign', 'request:foreign-run:terminal', 'Finished', 'Private',
        'foreign-chat', category='completion')

    request_target = client.get(BASE + f"/inbox/{request['id']}/target")
    failed_target = client.get(BASE + f"/inbox/{failed['id']}/target")
    approval_target = client.get(BASE + f"/inbox/{approval['id']}/target")

    assert request_target.status_code == 200
    assert request_target.json() == {
        'kind': 'request', 'session_id': 'chat-1', 'run_id': 'run-1', 'run_session_id': 'run-1'
    }
    assert failed_target.json() == {
        'kind': 'request', 'session_id': 'chat-1', 'run_id': 'run-failed',
        'run_session_id': 'run-failed'
    }
    assert approval_target.status_code == 200
    assert approval_target.json() == {
        'kind': 'approval', 'session_id': 'chat-1', 'run_id': 'run-2',
        'approval_id': 'approval-2', 'request_id': 'native-2', 'run_session_id': 'chat-1'
    }
    assert client.get(BASE + f"/inbox/{request['id']}/target").status_code == 200
    service.request_target_resolver = lambda *identity: None
    assert client.get(BASE + f"/inbox/{request['id']}/target").status_code == 404
    assert client.get(BASE + f"/inbox/{foreign['id']}/target").status_code == 404
    assert client.get(BASE + '/inbox/foreign-notice/target').status_code == 404
