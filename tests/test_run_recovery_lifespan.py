"""Real ASGI lifespan/routes, isolated native HTTP and temporary databases."""
import asyncio
import time

import pytest
import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.hermes_client import GatewayClient
from test_auth import BASE, ORIGIN
from test_steering import environment


def test_lifespan_observes_restart_and_later_completion_without_browser(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, _):
        settings = app.state.settings
    calls = []
    status = {'run_id': 'native-run', 'status': 'running'}
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=status)
    restarted = create_app(settings, gateway_client=GatewayClient('http://127.0.0.1:8642', 'test', True,
        transport=httpx.MockTransport(upstream)))
    restarted.state.orchestrator.recovery_interval = .05
    with TestClient(restarted, base_url=ORIGIN):
        deadline = time.monotonic() + 1
        while not calls and time.monotonic() < deadline:
            time.sleep(.01)
        assert calls, 'startup must observe persisted unresolved identity'
        status.update(status='completed', output='natural original completion')
        deadline = time.monotonic() + 1
        while restarted.state.journal.get(user['id'], rid)['status'] != 'completed' and time.monotonic() < deadline:
            time.sleep(.01)
        assert restarted.state.journal.get(user['id'], rid)['output'] == 'natural original completion'
        assert all(r.method == 'GET' and r.url.path == '/v1/runs/native-run' for r in calls)


def test_background_recovery_rejects_changed_owner_binding(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, _):
        settings = app.state.settings
        with app.state.auth.store.transaction() as db:
            db.execute("UPDATE users SET profile='different' WHERE id=?", (user['id'],))
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'run_id': 'native-run', 'status': 'completed'})
    restarted = create_app(settings, gateway_client=GatewayClient('http://127.0.0.1:8642', 'test', True,
        transport=httpx.MockTransport(upstream)))
    restarted.state.orchestrator.recovery_interval = .02
    with TestClient(restarted, base_url=ORIGIN):
        time.sleep(.1)
        assert not calls
        assert restarted.state.journal.get(user['id'], rid)['status'] == 'unknown'


@pytest.mark.asyncio
async def test_lifespan_shutdown_cancels_stalled_observers(tmp_path):
    from backend.app import Settings
    from test_native_catalog import create_native_db
    home = tmp_path / 'home'
    home.mkdir()
    create_native_db(home / 'state.db')
    gateway = GatewayClient('http://127.0.0.1:8642', 'test', True, transport=httpx.MockTransport(
        lambda r: httpx.Response(404)))
    app = create_app(Settings(state_dir=tmp_path/'state', profiles={'default': home}), gateway_client=gateway)
    entered = asyncio.Event()
    async def stalled():
        entered.set()
        await asyncio.Event().wait()
    app.state.orchestrator.reconcile_approval_notifications = stalled
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    await entered.wait()
    async with asyncio.timeout(.2):
        await lifespan.__aexit__(None, None, None)
    assert app.state.orchestrator._closed


def test_get_run_refreshes_owned_original(tmp_path):
    def upstream(request):
        return httpx.Response(200, json={'run_id': 'native-run', 'status': 'completed', 'output': 'final result'})
    with environment(tmp_path, upstream) as (app, client, user, rid, calls):
        app.state.journal.finish(user['id'], rid, 'unknown')
        response = client.get(BASE + '/runs/' + rid)
        assert response.status_code == 200
        assert response.json()['output'] == 'final result'
        assert response.json()['status'] == 'completed'
        assert all(r.method == 'GET' for r in calls)
