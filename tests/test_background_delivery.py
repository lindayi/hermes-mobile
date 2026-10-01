"""Synthetic HTTP + SQLite delivery tests; never opens a real Hermes home."""
import asyncio
from contextlib import closing
import hashlib
import importlib
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.hermes_client import GatewayClient
from backend.native_catalog import NativeCatalog
from backend.notifications import NotificationService
from backend.runs import RunJournal


def envelope(**changes):
    event = dict(type='async_delegation', delegation_id='deleg_1', session_key='native-run',
                 origin_session_id='chat', parent_session_id='chat', summary='Full public report',
                 context='PRIVATE CONTEXT', reasoning='PRIVATE REASONING')
    event.update(changes)
    digest = hashlib.sha256(json.dumps({k: v for k, v in event.items() if k != 'restored'},
                            sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return dict(event_id='async:' + event['delegation_id'], payload_sha256=digest,
                lease_token='lease-one', event=event, historical=False)


class Fixture:
    def __init__(self, tmp_path):
        self.user = dict(id='owner', role='owner', profile='default', status='ready')
        self.bound = True
        home = tmp_path/'home'
        home.mkdir()
        with sqlite3.connect(home/'state.db') as db:
            db.execute('''CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT,
                parent_session_id TEXT, end_reason TEXT, ended_at REAL, model_config TEXT,
                profile_name TEXT)''')
            db.execute("INSERT INTO sessions VALUES('chat','cli',NULL,NULL,NULL,'{}','default')")
        self.catalog = NativeCatalog({'default': home})
        self.journal = RunJournal(tmp_path/'runs.sqlite')
        run, _ = self.journal.submit('owner', 'default', 'chat', 'input', 'key')
        self.journal.set_upstream('owner', run['id'], 'native-run')
        self.journal.finish('owner', run['id'], 'completed', output='done')
        self.notifications = NotificationService(tmp_path/'notifications.sqlite')
        self.items = [envelope()]
        self.acks = []
        self.claims = 0
        self.delivered = False
        self.lose_ack = False
        self.after_claim = lambda: None
        app = FastAPI()

        @app.post('/v1/mobile/notifications/claim')
        async def claim(request: Request):
            assert request.headers['authorization'] == 'Bearer test-only'
            assert 1 <= (await request.json())['limit'] <= 50
            self.claims += 1
            self.after_claim()
            if hasattr(self, 'claim_reply'):
                return self.claim_reply
            return {'items': [] if self.delivered else self.items}

        @app.post('/v1/mobile/notifications/ack')
        async def ack(request: Request):
            body = await request.json()
            assert set(body) == {'event_id', 'payload_sha256', 'lease_token', 'receipt_id'}
            with self.notifications._db() as db:
                assert db.execute('SELECT 1 FROM inbox WHERE id=?', (body['receipt_id'],)).fetchone()
            self.acks.append(body)
            self.delivered = True
            if hasattr(self, 'ack_reply'):
                return self.ack_reply
            if self.lose_ack:
                self.lose_ack = False
                return JSONResponse({'error': 'lost reply'}, status_code=503)
            return dict(status='delivered', event_id=body['event_id'], receipt_id=body['receipt_id'])

        self.gateway = GatewayClient('http://127.0.0.1', 'test-only', transport=httpx.ASGITransport(app=app))

    def service(self):
        module = importlib.import_module('backend.background_delivery')
        return module.BackgroundDeliveryService(self.notifications, self.journal, self.catalog,
                    self.gateway, owner=lambda: dict(self.user), binding=lambda user: self.bound)

    def durable_items(self):
        # Storage, not attention presentation: catches accidental commit/rollback
        # changes even when every background item is correctly hidden from Inbox.
        with self.notifications._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM inbox ORDER BY id')]


@pytest.mark.parametrize('change', [
    {'session_key': 'whatsapp:foreign'}, {'origin_session_id': 'foreign'},
    {'parent_session_id': 'foreign'}, {'platform': 'whatsapp'}, {'scope_id': 'relay'},
    {'digest': '0' * 64}, {'event_id': 'async:wrong'}, {'summary': {'reasoning': 'SECRET'}},
])
def test_unproved_or_malformed_envelopes_are_never_receipted(change, tmp_path):
    f = Fixture(tmp_path)
    change = dict(change)
    digest, event_id = change.pop('digest', None), change.pop('event_id', None)
    f.items = [envelope(**change)]
    if digest:
        f.items[0]['payload_sha256'] = digest
    if event_id:
        f.items[0]['event_id'] = event_id
    asyncio.run(f.service().tick())
    assert f.acks == []
    assert f.durable_items() == []
    assert f.notifications.list_inbox('owner') == []


@pytest.mark.parametrize('historical', [True, False])
def test_fanout_keeps_all_public_reports_and_quiet_history(tmp_path, historical):
    f = Fixture(tmp_path)
    with f.notifications._db() as db:
        db.execute("INSERT INTO subscriptions VALUES('endpoint','owner','device','{}')")
    f.items = [envelope(summary=None, is_batch=True, results=[
        {'summary': 'Child α\n' + 'long report ' * 2000, 'context': 'PRIVATE CHILD'},
        {'summary': 'Second result', 'reasoning': 'PRIVATE CHILD TWO'}])]
    f.items[0]['historical'] = historical
    service = f.service()
    asyncio.run(service.tick())
    items = service.session_items(f.user, 'chat')['items']
    assert len(items) == 1
    assert f.items[0]['event']['results'][0]['summary'] in items[0]['body']
    assert 'Second result' in items[0]['body']
    assert 'PRIVATE' not in items[0]['body']
    assert f.notifications.list_inbox('owner') == []
    with f.notifications._db() as db:
        # Child reports stay durable for Activity/history, never attention or push.
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        assert 'PRIVATE CHILD' in db.execute('SELECT event_json FROM background_receipts').fetchone()[0]


@pytest.mark.parametrize('phase', ['before_claim', 'after_claim', 'after_receipt'])
def test_live_owner_binding_deactivation_stops_delivery(tmp_path, phase):
    f = Fixture(tmp_path)
    service = f.service()
    if phase == 'before_claim':
        f.bound = False
    elif phase == 'after_claim':
        f.after_claim = lambda: setattr(f, 'bound', False)
    else:
        original = f.notifications.store_background
        def store(**kw):
            result = original(**kw)
            f.user['status'] = 'disabled'
            return result
        f.notifications.store_background = store
    asyncio.run(service.tick())
    assert not f.acks
    assert f.claims == int(phase != 'before_claim')
    assert len(f.durable_items()) == int(phase == 'after_receipt')
    assert f.notifications.list_inbox('owner') == []
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'chat')


def test_actual_api_cli_source_and_verified_compression_continuation(tmp_path):
    f = Fixture(tmp_path)
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute("UPDATE sessions SET source='cli',end_reason='compression',ended_at=1")
        db.execute("INSERT INTO sessions VALUES('tip','cli','chat',NULL,NULL,'{}','default')")
        db.execute("INSERT INTO sessions VALUES('reset','cli',NULL,NULL,NULL,'{}','default')")
        db.execute("INSERT INTO sessions VALUES('branch','cli','chat',NULL,NULL,?, 'default')",
                   (json.dumps({'_branched_from': 'chat'}),))
    f.items = [envelope(origin_session_id='tip', parent_session_id='chat')]
    service = f.service()
    asyncio.run(service.tick())
    assert len(f.acks) == 1
    assert service.session_items(f.user, 'tip')['items'][0]['session_id'] == 'tip'
    assert service.session_items(f.user, 'chat')['items'][0]['id'] == f.acks[0]['receipt_id']
    assert service.session_items(f.user, 'reset') == {'items': []}
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'branch')
    assert f.durable_items()[0]['session_id'] == 'tip'
    assert f.notifications.list_inbox('owner') == []


@pytest.mark.parametrize('access', ['session_read', 'inbox_link'])
def test_subagent_child_cannot_inherit_delivered_parent_receipt(tmp_path, access):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    receipt = f.acks[0]['receipt_id']
    assert service.session_items(f.user, 'chat')['items'][0]['body'] == 'Full public report'
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression',ended_at=1 WHERE id='chat'")
        db.execute("INSERT INTO sessions VALUES('delegate-child','subagent','chat',NULL,NULL,'{}','default')")
    if access == 'session_read':
        with pytest.raises(PermissionError):
            service.session_items(f.user, 'delegate-child')
    else:
        stored = f.durable_items()
        assert len(stored) == 1
        assert stored[0]['id'] == receipt
        # Retain link safety independently of attention presentation.
        row = f.notifications.background_rows(service.scope, 'owner')[0]
        assert service._inbox_link('owner', row) is None
        assert f.notifications.list_inbox('owner') == []


def test_subagent_sibling_does_not_block_owned_cli_continuation(tmp_path):
    f = Fixture(tmp_path)
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression',ended_at=1 WHERE id='chat'")
        db.execute("INSERT INTO sessions VALUES('tip','cli','chat',NULL,NULL,'{}','default')")
        db.execute("INSERT INTO sessions VALUES('delegate-child','subagent','chat',NULL,NULL,'{}','default')")
    f.items = [envelope(origin_session_id='tip', parent_session_id='chat')]
    service = f.service()
    assert asyncio.run(service.tick()) == {'status': 'ok', 'rejected': 0}
    assert len(f.acks) == 1
    items = service.session_items(f.user, 'tip')['items']
    assert len(items) == 1
    assert items[0]['body'] == 'Full public report'
    assert items[0]['session_id'] == 'tip'
    assert items[0]['id'] == f.acks[0]['receipt_id']
    assert service.session_items(f.user, 'chat')['items'] == items
    assert f.durable_items()[0]['session_id'] == 'tip'
    assert f.notifications.list_inbox('owner') == []
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'delegate-child')


@pytest.mark.parametrize('phase', ['before_claim', 'after_claim', 'after_receipt'])
def test_deletion_pause_and_deleted_receipt_never_resurrects_link(tmp_path, phase):
    f = Fixture(tmp_path)
    service = f.service()
    def deletion():
        return f.journal.claim_deletion('owner', 'default', 'chat')
    if phase == 'before_claim':
        deletion()
    elif phase == 'after_claim':
        f.after_claim = deletion
    else:
        asyncio.run(service.tick())
        deletion()
        with closing(f.journal.connect()) as db, db:
            db.execute("UPDATE session_deletions SET state='deleted'")
            db.execute("UPDATE session_deletion_operations SET state='deleted'")
        with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
            db.execute('DELETE FROM sessions')
        f.delivered = False
    previous_acks = len(f.acks)
    asyncio.run(service.tick())
    assert len(f.acks) == previous_acks
    assert f.claims == (0 if phase == 'before_claim' else 1 if phase == 'after_claim' else 2)
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'chat')
    stored = f.durable_items()
    if phase == 'after_receipt':
        assert len(stored) == 1 and stored[0]['id'] == f.acks[0]['receipt_id']
        row = f.notifications.background_rows(service.scope, 'owner')[0]
        assert service._inbox_link('owner', row) is None
    else:
        assert not stored
    assert f.notifications.list_inbox('owner') == []


@pytest.mark.parametrize('bad', [None, [], {'event': []},
    envelope(summary=None, is_batch=True, results=[None]),
    dict(envelope(), event_id=32),
])
def test_poison_item_cannot_block_fresh_sibling_and_reports_aggregate(tmp_path, bad):
    f = Fixture(tmp_path)
    f.items = [bad, envelope()]
    report = asyncio.run(f.service().tick())
    assert report['rejected'] == 1
    assert len(f.acks) == 1
    assert 'PRIVATE' not in json.dumps(report)


@pytest.mark.parametrize('kind,key,fields', [
    ('completion', 'process:proc_1:12.5', {'session_id': 'proc_1', 'started_at': 12.5}),
    ('watch_match', 'occurrence:watch-1', {'occurrence_id': 'watch-1'}),
    ('watch_match', 'migration:retained-1', {}),
])
def test_proved_process_and_watch_results_without_async_metadata(tmp_path, kind, key, fields):
    f = Fixture(tmp_path)
    event = dict(type=kind, session_key='native-run', task_id='chat',
                 command='fixture-command', exit_code=0, completion_reason='exited',
                 termination_source=None, output='public terminal result', **fields)
    digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=False).encode()).hexdigest()
    f.items = [dict(event_id=key, payload_sha256=digest, lease_token='lease-one',
                    event=event, historical=False)]
    service = f.service()
    asyncio.run(service.tick())
    assert len(f.acks) == 1
    assert service.session_items(f.user, 'chat')['items'][0]['body'] == 'public terminal result'


def test_tcp_http_native_leases_two_consumers_and_lost_ack(tmp_path):
    from aiohttp import web
    from backend.native_notifications import NotificationOutbox, NotificationConflict
    f = Fixture(tmp_path)
    clock = [100.0]
    source = NotificationOutbox(tmp_path/'source.sqlite', clock=lambda: clock[0], lease_seconds=5)
    first = source.capture(envelope()['event'], route='owned')
    held = source.claim()[0]  # An independent consumer owns this sibling.
    second = source.capture(envelope(delegation_id='deleg_2', summary='Fresh sibling')['event'], route='owned')
    calls = []
    lose = [True]

    async def claim(request):
        assert request.headers['Authorization'] == 'Bearer test-only'
        return web.json_response({'items': source.claim((await request.json())['limit'])})

    async def ack(request):
        body = await request.json()
        with f.notifications._db() as db:
            assert db.execute('SELECT 1 FROM background_receipts WHERE inbox_id=?',
                              (body['receipt_id'],)).fetchone()
        calls.append(body)
        try:
            result = source.ack(**body)
        except NotificationConflict:
            return web.json_response({'error': 'stale lease'}, status=409)
        if lose[0]:
            lose[0] = False
            return web.json_response({'error': 'lost reply after commit'}, status=503)
        return web.json_response(result)

    async def scenario():
        app = web.Application()
        app.router.add_post('/v1/mobile/notifications/claim', claim)
        app.router.add_post('/v1/mobile/notifications/ack', ack)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        await f.gateway.close()
        f.gateway = GatewayClient('http://127.0.0.1:' + str(port), 'test-only')
        other = GatewayClient('http://127.0.0.1:' + str(port), 'test-only')
        try:
            service = f.service()
            independent = f.service()
            independent.notifications = NotificationService(f.notifications.db_path)
            independent.gateway = other
            await asyncio.gather(service.tick(), independent.tick())
            assert source.record(first)['state'] == 'pending'
            assert source.record(second)['state'] == 'delivered'
            assert [i['body'] for i in service.session_items(f.user, 'chat')['items']] == ['Fresh sibling']
            await f.service().tick()  # Durable ACK intent, despite no claimable source row.
            assert calls[0] == calls[1]
            clock[0] += 10
            await asyncio.gather(service.tick(), independent.tick())
            assert source.record(first)['state'] == 'delivered'
            assert len(service.session_items(f.user, 'chat')['items']) == 2
            assert source.record(first)['lease_token'] != held['lease_token']
            assert {i['id'] for i in f.durable_items()} == {call['receipt_id'] for call in calls}
            assert len(f.durable_items()) == 2
            assert f.notifications.list_inbox('owner') == []
        finally:
            await f.gateway.close()
            await other.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_receipt_transaction_rolls_back_inbox_and_push_if_private_insert_fails(tmp_path):
    f = Fixture(tmp_path)
    with f.notifications._db() as db:
        db.execute("CREATE TRIGGER break_receipt BEFORE INSERT ON background_receipts BEGIN SELECT RAISE(ABORT,'fixture crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(f.service().tick())
    assert not f.durable_items()
    assert not f.notifications.list_inbox('owner')
    assert not f.acks
    with f.notifications._db() as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        db.execute('DROP TRIGGER break_receipt')
    asyncio.run(f.service().tick())
    assert len(f.acks) == 1


@pytest.mark.parametrize('change', ['route_read_deactivation', 'home_change', 'foreign_chat'])
def test_revalidate_immediately_before_receipt_and_pin_home(tmp_path, change):
    f = Fixture(tmp_path)
    service = f.service()
    if change == 'route_read_deactivation':
        route = service._route
        def deactivate(*args):
            sid = route(*args)
            f.bound = False
            return sid
        service._route = deactivate
    elif change == 'home_change':
        import shutil
        other = tmp_path/'other-home'
        shutil.copytree(f.catalog.profiles['default'], other)
        f.catalog.profiles['default'] = other
    else:
        f.items = [envelope(chat_id='foreign-gateway-chat')]
    asyncio.run(service.tick())
    assert not f.acks
    assert not f.durable_items()
    assert not f.notifications.list_inbox('owner')


def test_fanout_failed_child_preserved_without_exposing_private_error(tmp_path):
    f = Fixture(tmp_path)
    f.items = [envelope(summary=None, is_batch=True, results=[
        {'summary': 'Finished public report', 'status': 'completed'},
        {'summary': None, 'status': 'failed', 'error': 'SECRET CONFIG PATH'},
        {'summary': None, 'status': 'unknown', 'context': 'SECRET CONTEXT'}])]
    service = f.service()
    asyncio.run(service.tick())
    body = service.session_items(f.user, 'chat')['items'][0]['body']
    assert 'Finished public report' in body and 'failed' in body and 'unknown' in body
    assert 'SECRET' not in body
    assert len(f.acks) == 1


def test_real_authstore_runtime_binding_sync_default_owner_and_member_closed(tmp_path):
    from backend.auth_store import AuthStore
    from backend.runtime_binding import RuntimeBinding
    f = Fixture(tmp_path)
    auth = AuthStore(tmp_path/'auth.sqlite')
    with auth.transaction() as db:
        db.execute("INSERT INTO users VALUES('owner','owner','default','ready','Test owner',0)")
    def owner():
        with auth.transaction() as db:
            return db.execute("SELECT * FROM users WHERE id='owner'").fetchone()
    service = f.service()
    service.owner = owner
    service.binding = RuntimeBinding(auth, f.catalog.profiles, {}).matches
    f.journal.set_deployment_gate('fixture-deploy')
    asyncio.run(service.tick())  # Gate blocks model admissions, not receipts.
    assert len(f.acks) == 1
    with auth.transaction() as db:
        db.execute("UPDATE users SET status='disabled'")
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'chat')
    assert asyncio.run(service.tick())['status'] == 'deferred'
    assert f.claims == 1
    with auth.transaction() as db:
        db.execute("UPDATE users SET role='member',profile='member_owner',status='ready'")
    # Even a hypothetically verified member does not enable a new profile rail.
    service.binding = lambda user: True
    assert asyncio.run(service.tick())['status'] == 'deferred'
    assert f.claims == 1


@pytest.mark.parametrize('field,value', [('user_id', 'other-owner'), ('origin', 'foreign'), ('digest', '0'*64)])
def test_same_source_receipt_conflict_does_not_silently_accept(field, value, tmp_path):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    item = f.items[0]
    args = dict(scope=service.scope, event_id=item['event_id'], digest=item['payload_sha256'],
                user_id='owner', origin='chat', session_id='chat', event=item['event'],
                lease_token='new-lease', body='public report')
    args[field] = value
    with pytest.raises(ValueError, match='conflict'):
        f.notifications.store_background(**args)
    with f.notifications._db() as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
        assert db.execute('SELECT lease_token FROM background_receipts').fetchone()[0] == 'lease-one'


@pytest.mark.parametrize('changes', [
    {'session_key': []}, {'origin_session_id': {}}, {'parent_session_id': []},
    {'session_key': ''}, {'delegation_id': ''},
])
def test_route_identity_types_are_validated_before_database_binding(tmp_path, changes):
    f = Fixture(tmp_path)
    f.items = [envelope(**changes), envelope(delegation_id='deleg_2')]
    result = asyncio.run(f.service().tick())
    assert result['rejected'] == 1
    assert len(f.acks) == 1 and f.acks[0]['event_id'] == 'async:deleg_2'


@pytest.mark.parametrize('source', ['whatsapp', 'telegram', 'cli', 'api_server'])
def test_web_origin_proof_allows_existing_cross_channel_conversation(tmp_path, source):
    f = Fixture(tmp_path)
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('UPDATE sessions SET source=?', (source,))
        for field in ('session_key', 'origin_json', 'chat_id', 'chat_type', 'thread_id'):
            db.execute('ALTER TABLE sessions ADD COLUMN ' + field + ' TEXT')
            db.execute('UPDATE sessions SET ' + field + '=?', ('saved-historical-gateway-metadata',))
    service = f.service()
    asyncio.run(service.tick())
    assert len(f.acks) == 1
    assert len(service.session_items(f.user, 'chat')['items']) == 1
    # An actual messaging-routed event does not acquire the owner's Inbox.
    f.delivered = False
    f.items = [envelope(delegation_id='foreign-event', session_key='whatsapp:chat', platform='whatsapp')]
    asyncio.run(service.tick())
    assert len(f.acks) == 1
    assert len(service.session_items(f.user, 'chat')['items']) == 1


def test_read_revalidates_live_binding_after_native_route_read(tmp_path):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    original = service._route
    def deactivate(*args):
        result = original(*args)
        f.bound = False
        return result
    service._route = deactivate
    with pytest.raises(PermissionError):
        service.session_items(f.user, 'chat')


@pytest.mark.parametrize('where,reply', [('claim', []), ('claim', {'items': {}}), ('ack', [])])
def test_malformed_transport_reply_remains_retriable(tmp_path, where, reply):
    f = Fixture(tmp_path)
    setattr(f, where + '_reply', reply)
    asyncio.run(f.service().tick())
    with f.notifications._db() as db:
        assert db.execute('SELECT count(*) FROM background_receipts WHERE acknowledged=1').fetchone()[0] == 0
    delattr(f, where + '_reply')
    asyncio.run(f.service().tick())
    with f.notifications._db() as db:
        assert db.execute('SELECT count(*) FROM background_receipts WHERE acknowledged=1').fetchone()[0] == 1
    assert len(f.durable_items()) == 1
    assert f.service().session_items(f.user, 'chat')['items'][0]['id'] == f.durable_items()[0]['id']
    assert f.notifications.list_inbox('owner') == []


def test_cancellation_after_receipt_before_ack_survives_reopen(tmp_path):
    f = Fixture(tmp_path)
    original = f.notifications.store_background
    def crash(**kwargs):
        original(**kwargs)
        raise asyncio.CancelledError()
    f.notifications.store_background = crash
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(f.service().tick())
    assert not f.acks
    f.notifications = NotificationService(f.notifications.db_path)
    asyncio.run(f.service().tick())
    assert len(f.acks) == 1
    assert len(f.durable_items()) == 1
    assert f.service().session_items(f.user, 'chat')['items'][0]['id'] == f.durable_items()[0]['id']
    assert f.notifications.list_inbox('owner') == []


@pytest.mark.parametrize('edge', ['tool', 'delegate', 'branch', 'reset', 'ambiguous'])
def test_noncontinuation_native_children_cannot_receive_origin_receipt(tmp_path, edge):
    f = Fixture(tmp_path)
    config = {'_' + ('delegate' if edge == 'delegate' else 'branched') + '_from': 'chat'} if edge in ('delegate', 'branch') else {}
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute("UPDATE sessions SET end_reason=?,ended_at=1", ('session_reset' if edge == 'reset' else 'compression',))
        db.execute("INSERT INTO sessions VALUES('child',?,'chat',NULL,NULL,?,'default')",
                   ('tool' if edge == 'tool' else 'cli', json.dumps(config)))
        if edge == 'ambiguous':
            db.execute("INSERT INTO sessions VALUES('other','cli','chat',NULL,NULL,'{}','default')")
    f.items = [envelope(origin_session_id='child')]
    asyncio.run(f.service().tick())
    assert not f.acks and not f.durable_items()
    assert not f.notifications.list_inbox('owner')


def test_replay_dismiss_dedup_and_conflicts(tmp_path):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    receipt = f.acks[0]['receipt_id']
    f.notifications.mark_read('owner', receipt)
    f.notifications.dismiss('owner', receipt)
    f.delivered = False
    f.items[0]['lease_token'] = 'lease-two'
    asyncio.run(service.tick())
    assert f.acks[-1]['receipt_id'] == receipt
    assert f.acks[-1]['lease_token'] == 'lease-two'
    assert f.notifications.list_inbox('owner') == []
    f.delivered = False
    f.items = [envelope(summary='changed report')]
    asyncio.run(service.tick())
    assert len(f.acks) == 2
    assert service.session_items(f.user, 'chat')['items'][0]['body'] == 'Full public report'


def test_restart_retries_durable_ack_intent_after_lost_reply(tmp_path):
    f = Fixture(tmp_path)
    f.lose_ack = True
    asyncio.run(f.service().tick())
    f.notifications = NotificationService(f.notifications.db_path)
    asyncio.run(f.service().tick())
    assert len(f.acks) == 2
    assert f.acks[0] == f.acks[1]
    assert len(f.durable_items()) == 1
    assert f.durable_items()[0]['read'] == 0
    assert f.service().session_items(f.user, 'chat')['items'][0]['id'] == f.durable_items()[0]['id']
    assert f.notifications.list_inbox('owner') == []
    with f.notifications._db() as db:
        assert db.execute('SELECT acknowledged FROM background_receipts').fetchone()[0] == 1


@pytest.mark.parametrize('mode', ['intervening-user', 'duplicate-boundary'])
def test_background_origin_requires_unambiguous_coherent_admission(tmp_path, mode):
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    if mode == 'duplicate-boundary':
        other, _ = f.journal.submit('owner', 'default', 'chat', 'input', 'second')
        f.journal.set_upstream('owner', other['id'], 'second-native')
        f.journal.finish('owner', other['id'], 'completed', output='done')
        with closing(f.journal.connect()) as db, db:
            db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (other['id'], 'chat', 'chat', 10))
        f.items.append(envelope(delegation_id='second', session_key='second-native'))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL)')
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,NULL,100)', [
            (11, 'chat', 'user', 'Unrelated CLI input' if mode == 'intervening-user' else 'input'),
            (12, 'chat', 'assistant', 'done'),
            (13, 'chat', 'user', 'input'),
            (14, 'chat', 'assistant', 'done')])
    service = f.service()
    asyncio.run(service.tick())
    items = service.session_items(f.user, 'chat')['items']
    assert len(items) == (2 if mode == 'duplicate-boundary' else 1)
    assert {item['origin_run_id'] for item in items} == ({run, other['id']} if mode == 'duplicate-boundary' else {run})
    assert all(item['origin_message_id'] is None for item in items), items
    assert all(item['origin_anchor'] == {'session_id': 'chat', 'message_id': 10} for item in items)


@pytest.mark.parametrize('mode', ['wrong-output', 'unfinished', 'anonymous-tool', 'missing-tool', 'later-tool', 'legacy-tool-schema', 'steering'])
def test_background_origin_rejects_incomplete_native_proof(tmp_path, mode):
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
        if mode == 'unfinished':
            db.execute("UPDATE runs SET status='running' WHERE id=?", (run,))
    if 'tool' in mode:
        data = {'name': 'terminal'}
        if mode != 'anonymous-tool':
            data['tool_call_id'] = 'owned-tool'
        f.journal.event('owner', run, 'tool', data)
    if mode == 'steering':
        f.journal.event('owner', run, 'steering', {'input': 'extra guidance'})
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, tool_call_id TEXT)')
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,NULL,100,NULL)', [
            (11, 'chat', 'user', 'input'),
            (13, 'chat', 'assistant', 'different answer' if mode == 'wrong-output' else 'done'),
            (14, 'chat', 'user', 'input'),
            (16, 'chat', 'assistant', 'done')])
        db.execute("INSERT INTO messages VALUES(12,'chat','tool','result',NULL,100,?)",
                   ('foreign-tool' if mode in ('missing-tool', 'later-tool') else 'owned-tool',))
        db.execute("INSERT INTO messages VALUES(15,'chat','tool','result',NULL,100,'owned-tool')")
        if mode == 'legacy-tool-schema':
            db.execute('ALTER TABLE messages DROP COLUMN tool_call_id')
    service = f.service()
    asyncio.run(service.tick())
    item = service.session_items(f.user, 'chat')['items'][0]
    assert item['origin_message_id'] is None
    assert item['origin_run_id'] == run
    assert item['event_at'] is None


def test_origin_metadata_uses_owned_admission_and_producer_time_not_replay_receipt(tmp_path):
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute("UPDATE runs SET input='Original request',output='Original answer' WHERE id=?", (run,))
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    f.journal.event('owner', run, 'tool', {'name': 'terminal', 'tool_call_id': 'native-tool'})
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, tool_call_id TEXT)')
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,NULL,?,NULL)', [
            (10, 'chat', 'assistant', 'Earlier answer', 100),
            (11, 'chat', 'user', 'Original request', 110),
            (14, 'chat', 'assistant', 'Original answer', 120),
            (15, 'chat', 'user', 'Later request', 200)])
        db.execute('INSERT INTO messages VALUES(12,\'chat\',\'assistant\',NULL,?,111,NULL)',
                   (json.dumps([{'id': 'native-tool', 'function': {'name': 'terminal'}}]),))
        db.execute("INSERT INTO messages VALUES(13,'chat','tool','result',NULL,112,'native-tool')")
    f.items = [envelope(dispatched_at=115.5, completed_at=250.0)]
    f.items[0]['historical'] = True
    service = f.service()
    asyncio.run(service.tick())
    item = service.session_items(f.user, 'chat')['items'][0]
    assert item.get('origin_run_id') == run
    assert item['origin_session_id'] == 'chat'
    assert item['origin_anchor'] == {'session_id': 'chat', 'message_id': 10}
    assert item['origin_message_id'] == 11
    assert (item['event_at'], item['event_at_source']) == (115.5, 'dispatched_at')
    assert item['created_at'] != item['event_at']
    assert 'Original request' not in json.dumps(item)
    f.delivered = False
    f.items[0]['lease_token'] = 'replay'
    asyncio.run(service.tick())
    assert service.session_items(f.user, 'chat')['items'][0] == item


@pytest.mark.parametrize('fields,expected', [
    ({'dispatched_at': False, 'completed_at': 150}, (150, 'completed_at')),
    ({'dispatched_at': -10, 'started_at': 125}, (125, 'started_at')),
    ({'timestamp': 100}, (100, 'timestamp')),
    ({}, (None, None)),
])
def test_background_origin_fallback_never_uses_receipt_ingestion(fields, expected, tmp_path):
    f = Fixture(tmp_path)
    f.items = [envelope(**fields)]
    f.items[0]['historical'] = True
    service = f.service()
    asyncio.run(service.tick())
    item = service.session_items(f.user, 'chat')['items'][0]
    assert (item['event_at'], item['event_at_source']) == expected
    assert item['origin_anchor'] is None
    assert item['origin_message_id'] is None
    assert item['origin_run_id']


def test_background_placement_reuses_native_structure_but_not_event_time(tmp_path, monkeypatch):
    from backend import native_catalog
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 0))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL)')
        db.execute("INSERT INTO messages VALUES(1,'chat','user','input',NULL,100)")
        db.execute("INSERT INTO messages VALUES(2,'chat','assistant','done',NULL,105)")
    counts = {'compression': 0, 'rewrites': 0, 'proof': 0}
    for name, key in [('compression_ids', 'compression'), ('_native_rewrites', 'rewrites'), ('_completed_turn', 'proof')]:
        original = getattr(native_catalog, name)
        def counted(*args, _fn=original, _key=key):
            counts[_key] += 1
            return _fn(*args)
        monkeypatch.setattr(native_catalog, name, counted)
    other, _ = f.journal.submit('owner', 'default', 'chat', 'Next request', 'next')
    f.journal.set_upstream('owner', other['id'], 'native-next')
    f.journal.finish('owner', other['id'], 'completed', output='Next answer')
    with closing(f.journal.connect()) as db, db:
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (other['id'], 'chat', 'chat', 2))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute("INSERT INTO messages VALUES(3,'chat','user','Next request',NULL,110)")
        db.execute("INSERT INTO messages VALUES(4,'chat','assistant','Next answer',NULL,115)")
    f.items = [envelope(delegation_id='d'+str(i), dispatched_at=100+i,
                        session_key='native-run' if i < 10 else 'native-next') for i in range(20)]
    service = f.service()
    asyncio.run(service.tick())
    items = service.session_items(f.user, 'chat')['items']
    assert len(items) == 20
    assert {i['event_at'] for i in items} == set(range(100, 120))
    assert {i['origin_run_id'] for i in items} == {run, other['id']}
    assert {i['origin_message_id'] for i in items} == {1, 3}
    assert counts == {'compression': 1, 'rewrites': 1, 'proof': 2}


@pytest.mark.parametrize('foreign_user,foreign_profile,boundary', [
    ('other', 'other-profile', 11), ('owner', 'other-profile', 10), ('other', 'default', 10)])
def test_background_origin_interval_ignores_foreign_profile_admission(tmp_path, foreign_user, foreign_profile, boundary):
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    other, _ = f.journal.submit(foreign_user, foreign_profile, 'chat', 'Foreign', 'foreign')
    with closing(f.journal.connect()) as db, db:
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (other['id'], 'chat', 'chat', boundary))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL)')
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,NULL,100)', [(11,'chat','system','Internal'),(12,'chat','user','input'),(13,'chat','assistant','done')])
    service = f.service()
    asyncio.run(service.tick())
    assert service.session_items(f.user, 'chat')['items'][0]['origin_message_id'] == 12


@pytest.mark.parametrize('mode', ['copy', 'compression', 'intervening-assistant'])
def test_background_native_origin_uses_identity_not_repeated_input_text(tmp_path, mode):
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, active INTEGER, compacted INTEGER)')
        db.execute("INSERT INTO messages VALUES(11,'chat',?,'input',NULL,100,0,1)", ('assistant' if mode == 'intervening-assistant' else 'user',))
        db.execute("INSERT INTO messages VALUES(12,'chat','assistant','done',NULL,110,0,1)")
        db.execute("INSERT INTO messages VALUES(13,'chat','user','input',NULL,200,1,0)")
        if mode == 'copy':
            db.execute("INSERT INTO messages VALUES(20,'chat','user','input',NULL,100,1,0)")
        if mode == 'compression':
            db.execute("UPDATE sessions SET end_reason='compression',ended_at=1 WHERE id='chat'")
            db.execute("INSERT INTO sessions VALUES('tip','cli','chat',NULL,NULL,'{}','default')")
    service = f.service()
    asyncio.run(service.tick())
    item = service.session_items(f.user, 'chat')['items'][0]
    assert item['origin_message_id'] == {'copy': 20, 'compression': 11, 'intervening-assistant': None}[mode]
    assert item['origin_session_id'] == 'chat'
    assert item['session_id'] == ('tip' if mode == 'compression' else 'chat')


def _placement_databases():
    journal = sqlite3.connect(':memory:')
    journal.row_factory = sqlite3.Row
    journal.executescript('''
        CREATE TABLE runs(id TEXT,user_id TEXT,profile TEXT,upstream_id TEXT,
                          session_id TEXT,status TEXT,input TEXT,output TEXT);
        CREATE TABLE run_history_anchors(run_id TEXT,session_id TEXT,message_id INTEGER);
        CREATE TABLE events(id INTEGER,run_id TEXT,name TEXT,data TEXT);
        INSERT INTO runs VALUES('run','owner','default','upstream','chat','completed','input','done');
        INSERT INTO run_history_anchors VALUES('run','chat',10);
    ''')
    native = sqlite3.connect(':memory:')
    native.row_factory = sqlite3.Row
    native.executescript('''
        CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,
            content TEXT,tool_calls TEXT,timestamp REAL,tool_call_id TEXT);
        INSERT INTO messages VALUES(11,'chat','user','input',NULL,100,NULL);
        INSERT INTO messages VALUES(12,'chat','assistant','done',NULL,101,NULL);
    ''')
    return journal, native


def _isolated_placement(journal, native, **kwargs):
    from backend.background_delivery import BackgroundDeliveryService
    service = object.__new__(BackgroundDeliveryService)
    return service._placement({'id': 'owner', 'profile': 'default'},
                              {'session_key': 'upstream'}, journal, {'chat'}, native, {}, **kwargs)


def _capture_public_boundary(connection):
    captured = []
    def capture(cursor, values):
        # Check every materialized value, regardless of SELECT spelling or fetch API.
        assert not any(isinstance(value, str) and len(value.encode()) > 1024 * 1024
                       for value in values), 'oversized payload crossed SQL/Python boundary'
        row = sqlite3.Row(cursor, values)
        if 'content' in row.keys() and row['content']:
            captured.append((row['id'], row['content']))
        return row
    connection.row_factory = capture
    return captured


def test_background_proof_stops_before_unrelated_later_tool_body():
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        native.execute("INSERT INTO messages VALUES(13,'chat','user','later',NULL,200,NULL)")
        native.execute("INSERT INTO messages VALUES(14,'chat','tool',?,NULL,201,'later-tool')",
                       ('X' * (9 * 1024 * 1024),))
        captured = _capture_public_boundary(native)
        result = _isolated_placement(journal, native)
        assert result['origin_message_id'] == 11
        assert {row[0] for row in captured} <= {11, 12, 13}


@pytest.mark.parametrize('source', ['native-content', 'native-calls', 'native-call-id',
    'journal-input', 'journal-output', 'event', 'structure-content', 'structure-calls', 'structure-metadata'])
def test_background_proof_preflights_oversized_fields_before_materialization(source):
    journal, native = _placement_databases()
    large = 'X' * (9 * 1024 * 1024)
    with closing(journal), closing(native):
        if source.startswith('native-'):
            field = {'native-content': 'content', 'native-calls': 'tool_calls',
                     'native-call-id': 'tool_call_id'}[source]
            if source == 'native-call-id':
                native.execute("UPDATE messages SET role='tool' WHERE id=12")
            native.execute('UPDATE messages SET ' + field + '=? WHERE id=12', (large,))
        elif source.startswith('journal-'):
            journal.execute('UPDATE runs SET ' + source.removeprefix('journal-') + '=?', (large,))
        elif source == 'event':
            journal.execute("INSERT INTO events VALUES(1,'run','tool',?)",
                            (json.dumps({'tool_call_id': 'call', 'arguments': large}),))
        else:
            native.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            native.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
            native.execute('ALTER TABLE messages ADD COLUMN display_metadata TEXT')
            field = {'structure-content': 'content', 'structure-calls': 'tool_calls',
                     'structure-metadata': 'display_metadata'}[source]
            native.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active,compacted) "
                           "VALUES(1,'chat','assistant','',50,0,1)")
            native.execute('UPDATE messages SET ' + field + '=? WHERE id=1', (large,))
        _capture_public_boundary(journal)
        _capture_public_boundary(native)
        result = _isolated_placement(journal, native)
        assert result == dict(origin_run_id='run', origin_session_id='chat',
                              origin_anchor={'session_id': 'chat', 'message_id': 10}, origin_message_id=None)


@pytest.mark.parametrize('limit', ['steps', 'seconds'])
def test_background_owned_execution_budget_preserves_anchor_and_releases_readers(monkeypatch, limit):
    from backend import background_delivery
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        # Real SQLite work, not a synthetic OperationalError pretending to be a timeout.
        native.executemany("INSERT INTO messages VALUES(?,'other','assistant','x',NULL,1,NULL)",
                           [(i,) for i in range(100, 3000)])
        for connection in (journal, native):
            if not connection.in_transaction:
                connection.execute('BEGIN')
        monkeypatch.setattr(background_delivery, '_PROOF_' + limit.upper(), 0, raising=False)
        result = _isolated_placement(journal, native)
        assert result == dict(origin_run_id='run', origin_session_id='chat',
                              origin_anchor={'session_id': 'chat', 'message_id': 10}, origin_message_id=None)
        # The budget handler must not poison subsequent route/fence reads.
        for connection in (journal, native):
            assert connection.in_transaction
            assert connection.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL '
                                      'SELECT x+1 FROM n WHERE x<2000) SELECT max(x) FROM n').fetchone()[0] == 2000


def test_background_proof_keeps_trailing_system_row_in_completion_semantics():
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        native.execute("INSERT INTO messages VALUES(13,'chat','system','not final',NULL,102,NULL)")
        assert _isolated_placement(journal, native)['origin_message_id'] is None


def test_background_budget_exhaustion_retains_event_fallback_in_session_response(tmp_path, monkeypatch):
    from backend import background_delivery
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT)')
        db.execute("INSERT INTO messages VALUES(11,'chat','user','input',NULL)")
        db.execute("INSERT INTO messages VALUES(12,'chat','assistant','done',NULL)")
    f.items = [envelope(dispatched_at=115)]
    service = f.service()
    asyncio.run(service.tick())
    monkeypatch.setattr(background_delivery, '_PROOF_SECONDS', 0, raising=False)
    item = service.session_items(f.user, 'chat')['items'][0]
    assert item['origin_message_id'] is None
    assert item['origin_run_id'] == run
    assert item['origin_anchor'] == {'session_id': 'chat', 'message_id': 10}
    assert (item['event_at'], item['event_at_source']) == (115, 'dispatched_at')


def test_background_response_shares_one_budget_across_distinct_owned_runs(tmp_path, monkeypatch):
    from backend import background_delivery
    f = Fixture(tmp_path)
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
    other, _ = f.journal.submit('owner', 'default', 'chat', 'x' * 500, 'next')
    f.journal.set_upstream('owner', other['id'], 'next-upstream')
    f.journal.finish('owner', other['id'], 'completed', output='done')
    with closing(f.journal.connect()) as db, db:
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (other['id'], 'chat', 'chat', 12))
    with sqlite3.connect(f.catalog.profiles['default']/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT)')
        db.executemany("INSERT INTO messages VALUES(?,'chat',?,?,NULL)",
                       [(11, 'user', 'input'), (12, 'assistant', 'done'),
                        (13, 'user', 'x' * 500), (14, 'assistant', 'done')])
    f.items = [envelope(), envelope(delegation_id='next', session_key='next-upstream', dispatched_at=120)]
    service = f.service()
    asyncio.run(service.tick())
    monkeypatch.setattr(background_delivery, '_PROOF_BYTES', 1020)
    items = service.session_items(f.user, 'chat')['items']
    # Each proof individually fits. Their combined work must not get fresh budgets.
    assert len([item for item in items if item['origin_message_id'] is not None]) == 1
    assert {item['origin_run_id'] for item in items} == {run, other['id']}
    assert all(item['origin_anchor'] for item in items)
    assert next(item for item in items if item['origin_run_id'] == other['id'])['event_at'] == 120


def test_background_route_does_not_preload_unbounded_run_payload(tmp_path, monkeypatch):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    with closing(f.journal.connect()) as db, db:
        run = db.execute('SELECT id FROM runs').fetchone()[0]
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run, 'chat', 'chat', 10))
        db.execute('UPDATE runs SET input=?', ('X' * (9 * 1024 * 1024),))
    connect = f.journal.connect
    def guarded_connect():
        connection = connect()
        _capture_public_boundary(connection)
        return connection
    monkeypatch.setattr(f.journal, 'connect', guarded_connect)
    item = service.session_items(f.user, 'chat')['items'][0]
    assert item['origin_run_id'] == run
    assert item['origin_anchor'] == {'session_id': 'chat', 'message_id': 10}
    assert item['origin_message_id'] is None


def test_background_duplicate_event_identity_is_not_reinterpreted():
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        native.execute("INSERT INTO messages VALUES(15,'chat','assistant','done',NULL,102,NULL)")
        native.execute("UPDATE messages SET role='tool',tool_call_id='first-id' WHERE id=12")
        journal.execute("INSERT INTO events VALUES(1,'run','tool',?)",
                        ('{"tool_call_id":"first-id","tool_call_id":"different-id"}',))
        assert _isolated_placement(journal, native)['origin_message_id'] is None


@pytest.mark.parametrize('source', ['native', 'event', 'structure'])
def test_background_aggregate_budget_rejects_many_small_payloads(monkeypatch, source):
    from backend import background_delivery
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        monkeypatch.setattr(background_delivery, '_PROOF_BYTES', 100)
        if source == 'native':
            native.executemany("INSERT INTO messages VALUES(?,'chat','assistant',?,NULL,100,NULL)",
                               [(i, 'x' * 20) for i in range(13, 23)])
            native.execute("INSERT INTO messages VALUES(23,'chat','assistant','done',NULL,101,NULL)")
        elif source == 'event':
            monkeypatch.setattr(background_delivery, '_PROOF_BYTES', 1000)
            native.execute("UPDATE messages SET role='tool',tool_call_id='id' WHERE id=12")
            native.execute("INSERT INTO messages VALUES(13,'chat','assistant','done',NULL,102,NULL)")
            journal.executemany("INSERT INTO events VALUES(?,'run','tool',?)",
                                [(i, '{"tool_call_id":"id"}') for i in range(10)])
        else:
            native.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            native.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        assert _isolated_placement(journal, native)['origin_message_id'] is None


@pytest.mark.parametrize('source', ['native', 'event', 'structure'])
def test_background_row_budget_never_accepts_truncated_proof(monkeypatch, source):
    from backend import background_delivery
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        monkeypatch.setattr(background_delivery, '_PROOF_ROWS', 3)
        if source == 'native':
            native.executemany("INSERT INTO messages VALUES(?,'chat','assistant','done',NULL,100,NULL)",
                               [(i,) for i in range(13, 20)])
        elif source == 'event':
            journal.executemany("INSERT INTO events VALUES(?,'run','tool',?)",
                                [(i, '{"tool_call_id":"id"}') for i in range(10)])
        else:
            native.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            native.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        assert _isolated_placement(journal, native)['origin_message_id'] is None


@pytest.mark.parametrize('failure', ['query', 'foreign-interrupt'])
def test_background_real_query_errors_are_not_budget_fallback(monkeypatch, failure):
    from backend import native_catalog
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        if failure == 'query':
            journal.execute('DROP TABLE events')
        else:
            def foreign_interrupt(*args):
                error = sqlite3.OperationalError('interrupted')
                error.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
                raise error
            monkeypatch.setattr(native_catalog, '_native_rewrites', foreign_interrupt)
        with pytest.raises(sqlite3.OperationalError, match='no such table|interrupted'):
            _isolated_placement(journal, native)
        assert native.execute('SELECT count(*) FROM messages').fetchone()[0] == 2


def test_background_proof_only_projects_public_tool_identity():
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        native.execute('ALTER TABLE messages ADD COLUMN reasoning TEXT')
        native.execute('ALTER TABLE messages ADD COLUMN api_content TEXT')
        native.execute("UPDATE messages SET content=NULL,tool_calls=? WHERE id=12",
                       (json.dumps([{'id': 'owned', 'function': {'name': 'terminal',
                                                              'arguments': 'PRIVATE ARGUMENTS'}}]),))
        native.execute("INSERT INTO messages(id,session_id,role,content,tool_call_id,timestamp) "
                       "VALUES(13,'chat','tool',?,'owned',102)", ('PRIVATE TOOL BODY' * 100000,))
        native.execute("INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp) "
                       "VALUES(14,'chat','assistant','done',NULL,103)")
        journal.execute("INSERT INTO events VALUES(1,'run','tool',?)",
                        (json.dumps({'tool_call_id': 'owned', 'arguments': 'PRIVATE ARGUMENTS'}),))
        private_reads = []
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and column in ('reasoning', 'api_content'):
                private_reads.append(column)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        native.set_authorizer(authorize)
        def public_only(cursor, values):
            assert all('PRIVATE' not in value for value in values if isinstance(value, str))
            return sqlite3.Row(cursor, values)
        native.row_factory = journal.row_factory = public_only
        assert _isolated_placement(journal, native)['origin_message_id'] == 11
        assert not private_reads


def test_background_nonstandard_json_calls_cannot_prove_a_final_answer():
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        native.execute('UPDATE messages SET tool_calls=? WHERE id=12', ('[{"arguments":NaN}]',))
        assert _isolated_placement(journal, native)['origin_message_id'] is None


def test_background_simple_structural_map_is_row_bounded_before_helper(monkeypatch):
    from backend import background_delivery, native_catalog
    journal, native = _placement_databases()
    with closing(journal), closing(native):
        monkeypatch.setattr(background_delivery, '_PROOF_ROWS', 3)
        native.executemany("INSERT INTO messages VALUES(?,'chat','system','x',NULL,1,NULL)",
                           [(i,) for i in range(100, 120)])
        calls = []
        original = native_catalog.runtime_notice_ids
        def counted(*args):
            calls.append(True)
            return original(*args)
        monkeypatch.setattr(native_catalog, 'runtime_notice_ids', counted)
        assert _isolated_placement(journal, native)['origin_message_id'] is None
        assert not calls


def test_http_completion_receipt_and_public_card(tmp_path):
    f = Fixture(tmp_path)
    service = f.service()
    asyncio.run(service.tick())
    items = service.session_items(f.user, 'chat')['items']
    assert len(items) == 1
    assert items[0] == dict(id=f.acks[0]['receipt_id'], session_id='chat',
        title='Background result', body='Full public report', created_at=items[0]['created_at'],
        kind='background_result', source_event_id='async:deleg_1', agent_context_state='not_injected',
        origin_run_id=f.journal.snapshot_state('owner', 'default', 'chat')['run']['id'],
        origin_session_id='chat', origin_anchor=None, origin_message_id=None,
        event_at=None, event_at_source=None)
    assert 'PRIVATE' not in json.dumps(items)
    with f.notifications._db() as db:
        assert db.execute('SELECT id,read FROM inbox').fetchone()[:] == (f.acks[0]['receipt_id'], 0)
        assert db.execute('SELECT acknowledged FROM background_receipts').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
    assert f.notifications.list_inbox('owner') == []
    asyncio.run(f.gateway.close())
