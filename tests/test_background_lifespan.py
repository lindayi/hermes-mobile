"""Application worker failure domains; temporary SQLite and mocked network only."""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.hermes_client import GatewayClient
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db
from test_notifications import subscription


CAPABILITY = {'version': 1, 'delivery': 'durable-inbox', 'automatic_model_wake': False}


@pytest.fixture(autouse=True)
def synthetic_owner_listener(tmp_path, monkeypatch):
    import backend.app as app_module
    import backend.model_controls as controls
    monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', tmp_path / 'home')
    monkeypatch.setattr(controls, 'standalone_owner_verified', lambda: True)


@pytest.fixture
def cycle_finished(monkeypatch):
    finished = threading.Event()
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        if timeout == 15:
            finished.set()  # The existing worker reached its inter-cycle wait.
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, 'wait_for', wait_for)
    return finished


def assembled(tmp_path, upstream):
    home = tmp_path / 'home'
    home.mkdir()
    create_native_db(home / 'state.db')
    gateway = GatewayClient('http://127.0.0.1:18642', 'test-only',
                            transport=httpx.MockTransport(upstream))
    app = create_app(Settings(state_dir=tmp_path / 'state', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP), gateway_client=gateway)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    user = client.get(BASE + '/auth/me').json()['user']
    return app, client, user


def queued_push(app, user):
    sent = []
    service = app.state.notifications
    with app.state.auth.store.transaction() as db:
        device = db.execute('SELECT id FROM sessions WHERE user_id=?', (user['id'],)).fetchone()[0]
    service.subscribe(user['id'], device, subscription())
    service.vapid_private_key, service.vapid_public_key = 'fake-private', 'fake-public'
    service.send_push = lambda **kw: (sent.append(kw) or SimpleNamespace(status_code=201))
    service.ingest(user['id'], 'existing-notification', 'Existing', 'Private body', category='scheduled', profile='default')
    return sent


def test_background_failure_cannot_starve_existing_push(tmp_path, monkeypatch, cycle_finished):
    app, client, user = assembled(tmp_path, lambda request: httpx.Response(
        200, json={'mobile_notifications': CAPABILITY}))
    sent = queued_push(app, user)
    attempted = []

    async def broken_tick():
        attempted.append(True)
        raise RuntimeError('synthetic background failure')

    monkeypatch.setattr(app.state.background_delivery, 'tick', broken_tick)
    with client:
        assert cycle_finished.wait(2)
        assert attempted == [True]
        assert len(sent) == 1
        with app.state.notifications._db() as db:
            assert db.execute('SELECT status FROM outbox').fetchone()[0] == 'sent'
    assert app.state.gateway.client.is_closed


def pending_receipt(app, user):
    from test_background_delivery import envelope
    item = envelope(origin_session_id='wa-1', parent_session_id='wa-1')
    app.state.notifications.store_background(
        scope=app.state.background_delivery.scope, event_id=item['event_id'],
        digest=item['payload_sha256'], user_id=user['id'], origin='wa-1',
        session_id='wa-1', event=item['event'], lease_token=item['lease_token'],
        body='Full public report', historical=True)


INVALID_CAPABILITIES = [
    None, [], True, {}, {'features': {'mobile_notifications': CAPABILITY}},
    *[{'mobile_notifications': value} for value in (None, True, [], 'durable-inbox', {})],
    *[{'mobile_notifications': {k: v for k, v in CAPABILITY.items() if k != missing}}
      for missing in CAPABILITY],
    *[{'mobile_notifications': {**CAPABILITY, 'version': value}}
      for value in (True, False, 1.0, '1', 2, None)],
    *[{'mobile_notifications': {**CAPABILITY, 'delivery': value}}
      for value in (True, 1, None, 'inbox', 'Durable-inbox')],
    *[{'mobile_notifications': {**CAPABILITY, 'automatic_model_wake': value}}
      for value in (True, 0, 'false', None)],
]


@pytest.mark.parametrize('payload', INVALID_CAPABILITIES)
def test_only_exact_top_level_capability_allows_claim_or_pending_ack(tmp_path, cycle_finished, payload):
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            import json
            return httpx.Response(200, content=json.dumps(payload))
        return httpx.Response(200, json={'items': []})

    app, client, user = assembled(tmp_path, upstream)
    pending_receipt(app, user)
    sent = queued_push(app, user)
    with client:
        assert cycle_finished.wait(2)
        assert len(sent) == 1
        assert calls == [('GET', '/v1/capabilities')]
        rows = app.state.notifications.background_rows(app.state.background_delivery.scope, user['id'])
        assert len(rows) == 1 and not rows[0]['acknowledged']


@pytest.mark.parametrize('state', ['pending', 'missing', 'member', 'other-profile', 'unbound'])
def test_capability_probe_requires_fresh_ready_default_owner_binding(tmp_path, monkeypatch, cycle_finished, state):
    from backend.runtime_binding import RuntimeBinding
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'mobile_notifications': CAPABILITY, 'items': []})

    if state == 'unbound':
        monkeypatch.setattr(RuntimeBinding, 'matches', lambda self, user: False)
    app, client, user = assembled(tmp_path, upstream)
    with app.state.auth.store.transaction() as db:
        if state == 'pending':
            db.execute("UPDATE users SET status='pending' WHERE id=?", (user['id'],))
        elif state == 'missing':
            # Keep the fixture's credential/session FKs intact; remove only owner eligibility.
            db.execute("UPDATE users SET role='deleted' WHERE id=?", (user['id'],))
        elif state == 'member':
            db.execute("UPDATE users SET role='member' WHERE id=?", (user['id'],))
        elif state == 'other-profile':
            db.execute("UPDATE users SET profile='other' WHERE id=?", (user['id'],))
    with client:
        assert cycle_finished.wait(2)
        assert calls == []


def test_capability_probe_has_bounded_transport_and_total_timeout(tmp_path, cycle_finished):
    calls, timeouts, cancelled = [], [], []

    async def upstream(request):
        calls.append((request.method, request.url.path))
        timeouts.append(request.extensions['timeout'])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    app, client, user = assembled(tmp_path, upstream)
    app.state.background_delivery.timeout = .05
    sent = queued_push(app, user)
    with client:
        assert cycle_finished.wait(1), 'capability probe blocked the existing worker cycle'
        assert len(sent) == 1
        assert calls == [('GET', '/v1/capabilities')]
        assert cancelled == [True]
        assert all(value == .05 for value in timeouts[0].values())
        assert app.state.push_worker_error is False
        assert app.state.background_worker_error is True
    assert app.state.gateway.client.is_closed


@pytest.mark.parametrize('failure', ['404', '503', 'invalid-json', 'network', 'unsupported-mock'])
def test_failed_capability_read_preserves_existing_push_and_pending_ack(tmp_path, cycle_finished, failure):
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        assert len(sent) == 1, 'existing outbox must flush before optional probing'
        if failure == 'network':
            raise httpx.ConnectError('synthetic unavailable', request=request)
        if failure == 'unsupported-mock':
            raise AssertionError('legacy mock supports only run recovery GET')
        if failure == 'invalid-json':
            return httpx.Response(200, content='not json')
        return httpx.Response(int(failure))

    app, client, user = assembled(tmp_path, upstream)
    pending_receipt(app, user)
    sent = queued_push(app, user)
    with client:
        assert cycle_finished.wait(2)
        assert calls == [('GET', '/v1/capabilities')]
        assert len(sent) == 1
        assert app.state.push_worker_error is False
        assert app.state.background_worker_error is True
        assert not app.state.notifications.background_rows(
            app.state.background_delivery.scope, user['id'])[0]['acknowledged']
    assert app.state.gateway.client.is_closed


@pytest.mark.parametrize('failure', ['reconcile', 'flush'])
def test_existing_worker_failure_does_not_disable_optional_background(tmp_path, monkeypatch, cycle_finished, failure):
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'mobile_notifications': CAPABILITY, 'items': []})

    app, client, user = assembled(tmp_path, upstream)
    if failure == 'reconcile':
        async def broken_reconcile():
            raise RuntimeError('synthetic reconciliation failure')
        monkeypatch.setattr(app.state.orchestrator, 'reconcile_approval_notifications', broken_reconcile)
    else:
        original = app.state.notifications.flush
        attempted = []

        def broken_first_flush():
            attempted.append(True)
            if len(attempted) == 1:
                raise RuntimeError('synthetic first flush failure')
            return original()

        monkeypatch.setattr(app.state.notifications, 'flush', broken_first_flush)
    with client:
        assert cycle_finished.wait(2)
        assert calls == [('GET', '/v1/capabilities'), ('POST', '/v1/mobile/notifications/claim')]
        assert app.state.push_worker_error is True
        assert app.state.background_worker_error is False
    assert app.state.gateway.client.is_closed


@pytest.mark.parametrize('change', ['capability', 'owner'])
def test_each_worker_cycle_revalidates_before_pending_ack_retry(tmp_path, monkeypatch, change):
    calls, cycles = [], []
    finished = threading.Event()
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        if timeout == 15:
            cycles.append(True)
            if len(cycles) == 1:
                if change == 'owner':
                    with app.state.auth.store.transaction() as db:
                        db.execute("UPDATE users SET status='pending' WHERE id=?", (user['id'],))
                return await original(awaitable, .001)
            finished.set()
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, 'wait_for', wait_for)

    def upstream(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={} if cycles else {'mobile_notifications': CAPABILITY})
        if request.url.path.endswith('/ack'):
            return httpx.Response(503)  # Keep a pending ACK across worker cycles.
        return httpx.Response(200, json={'items': []})

    app, client, user = assembled(tmp_path, upstream)
    pending_receipt(app, user)
    with client:
        assert finished.wait(2)
        expected = [('GET', '/v1/capabilities'), ('POST', '/v1/mobile/notifications/ack'),
                    ('POST', '/v1/mobile/notifications/claim')]
        if change == 'capability':
            expected.append(('GET', '/v1/capabilities'))
        assert calls == expected
        assert not app.state.notifications.background_rows(
            app.state.background_delivery.scope, user['id'])[0]['acknowledged']


@pytest.mark.parametrize('unaligned', ['home', 'port', 'hostname', 'client-type', 'unattested', 'attestation-error'])
def test_no_notification_network_before_positive_owner_listener_attestation(tmp_path, monkeypatch, cycle_finished, unaligned):
    import backend.app as app_module
    import backend.model_controls as controls
    calls, attestations = [], []

    def upstream(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'mobile_notifications': CAPABILITY, 'items': []})

    def verify():
        attestations.append(True)
        if unaligned == 'attestation-error':
            raise RuntimeError('synthetic attestation failure')
        return False

    monkeypatch.setattr(controls, 'standalone_owner_verified', verify)
    app, client, user = assembled(tmp_path, upstream)
    pending_receipt(app, user)
    sent = queued_push(app, user)
    if unaligned == 'home':
        monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', tmp_path / 'different-home')
    elif unaligned == 'port':
        app.state.gateway.client.base_url = httpx.URL('http://127.0.0.1:8642')
    elif unaligned == 'hostname':
        app.state.gateway.client.base_url = httpx.URL('http://localhost:18642')
    elif unaligned == 'client-type':
        # Keep the isolated client usable, but make it fail the genuine adapter check.
        monkeypatch.setattr(app_module, 'GatewayClient', type('DifferentGateway', (), {}))
    with client:
        assert cycle_finished.wait(2)
        assert len(sent) == 1
        assert calls == []
        assert attestations == ([True] if unaligned in ('unattested', 'attestation-error') else [])
    assert app.state.gateway.client.is_closed


def test_owner_deactivated_during_attestation_cannot_probe_capabilities(tmp_path, monkeypatch, cycle_finished):
    import backend.model_controls as controls
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'mobile_notifications': CAPABILITY, 'items': []})

    app, client, user = assembled(tmp_path, upstream)

    def verify():
        with app.state.auth.store.transaction() as db:
            db.execute("UPDATE users SET status='pending' WHERE id=?", (user['id'],))
        return True

    monkeypatch.setattr(controls, 'standalone_owner_verified', verify)
    with client:
        assert cycle_finished.wait(2)
        assert calls == []
