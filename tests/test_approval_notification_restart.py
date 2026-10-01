"""Actual app lifespan restart; both SQLite databases temporary; all I/O mocked."""
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.hermes_client import GatewayClient
from test_steering import environment
from test_notifications import subscription
from test_auth import ORIGIN


def test_multiple_intents_reconcile_before_run_is_restored(tmp_path):
    def upstream(request):
        return httpx.Response(200, json={'run_id': 'native-run', 'status': 'waiting_for_approval',
            'pending_approvals': [{'run_id': 'native-run', 'request_id': 'current'}]})
    with environment(tmp_path, upstream) as (app, client, user, rid, calls):
        runtime = app.state.orchestrator
        original = runtime.approval_notifier
        runtime.approval_notifier = None
        for request_id in ('current', 'vanished'):
            runtime._approval(user, runtime.get(user, rid), {'event': 'approval.request',
                'run_id': 'native-run', 'request_id': request_id})
        app.state.journal.recover()
        runtime.approval_notifier = original
        client.portal.call(runtime.reconcile_approval_notifications)
        runtime.drain_approval_notifications()
        assert [i['request_id'] for i in app.state.notifications.list_inbox(user['id'])] == ['current']


@pytest.mark.parametrize('gap', ['before_ingest', 'after_ingest'])
@pytest.mark.parametrize('native', ['current', 'vanished', 'expired', 'native_expired', 'unavailable', 'no_snapshot', 'foreign'])
def test_lifespan_restart_reconciles_only_explicit_intents(tmp_path, gap, native):
    with environment(tmp_path) as (app, client, user, rid, _):
        runtime = app.state.orchestrator
        service = app.state.notifications
        with app.state.auth.store.transaction() as db:
            device = db.execute('SELECT id FROM sessions WHERE user_id=?', (user['id'],)).fetchone()[0]
        service.subscribe(user['id'], device, subscription())
        original = runtime.approval_notifier
        def crash(*args):
            if gap == 'after_ingest':
                original(*args)
            raise RuntimeError('crash before intent ACK')
        runtime.approval_notifier = crash
        runtime._approval(user, runtime.get(user, rid), {
            'event': 'approval.request', 'run_id': 'native-run', 'request_id': 'action'})
        settings = app.state.settings
        with app.state.journal.connect() as db:
            db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                       ('historical', rid, 'old-action', '{}', 'pending', time.time()+200))
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0] == 'pending'
            if native == 'expired':
                db.execute("UPDATE orchestration_approvals SET expires_at=0 WHERE request_id='action'")
    calls, sent = [], []
    def upstream(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/v1/runs/native-run'
        if native == 'unavailable':
            return httpx.Response(404)
        payload = {'run_id': 'FOREIGN' if native == 'foreign' else 'native-run',
                   'status': 'waiting_for_approval', 'pending_approvals': []}
        if native != 'vanished':
            payload['pending_approvals'] = [{'run_id': 'native-run', 'request_id': 'action'}]
        if native == 'native_expired':
            payload['pending_approvals'][0]['expires_at'] = 0
        if native == 'no_snapshot':
            payload.pop('pending_approvals')
        return httpx.Response(200, json=payload)
    restarted = create_app(settings, gateway_client=GatewayClient('http://127.0.0.1:8642', 'test', True,
                                           transport=httpx.MockTransport(upstream)))
    notify = restarted.state.notifications
    notify.vapid_private_key, notify.vapid_public_key = 'fake-private', 'fake-public'
    notify.send_push = lambda **kw: (sent.append(kw) or SimpleNamespace(status_code=201))
    with TestClient(restarted, base_url=ORIGIN) as client:
        # Wait for the actual startup worker, not a manually simulated recovery.
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            with restarted.state.journal.connect() as db:
                status = db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]
            if sent or status == 'discarded' or (calls and native in ('unavailable', 'no_snapshot')):
                break
            time.sleep(.01)
        if native == 'current':
            assert len(sent) == 1
            assert status == 'sent'
            assert len(notify.list_inbox(user['id'])) == 1
            restarted.state.orchestrator.drain_approval_notifications()
            notify.flush()
            assert len(sent) == 1
        elif native in ('unavailable', 'no_snapshot'):
            assert not sent
            assert status == 'pending'
            assert restarted.state.journal.get(user['id'], rid)['status'] == 'unknown'
            if gap == 'after_ingest':
                with notify._db() as db:
                    assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'policy_pending'
            native = 'current'
            client.portal.call(restarted.state.orchestrator.reconcile_approval_notifications)
            restarted.state.orchestrator.drain_approval_notifications()
            notify.flush()
            assert len(sent) == 1
        else:
            assert not sent
            assert status == 'discarded'
        with restarted.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0] == 1
        assert all(r.method == 'GET' for r in calls)
