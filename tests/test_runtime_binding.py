"""Activation against disk must not authorize stale, already-loaded BFF mappings.

All accounts/files are temporary fixtures; only HTTP transport is mocked.
"""
import json
import sqlite3
from http.cookies import SimpleCookie

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.auth import COOKIE
from backend.hermes_client import GatewayClient
from backend.member_runtime import activate
from test_auth import BASE, ORIGIN
from test_member_runtime import family, private, transport
from test_native_catalog import create_native_db

PROFILE = 'member_abc123'
JOB = 'abcdef123456'


def login(client, app, user_id='abc123'):
    with app.state.auth.store.transaction() as db:
        response = app.state.auth.new_session(db, user_id)
    cookie = SimpleCookie()
    cookie.load(response.headers['set-cookie'])
    client.cookies.set(COOKIE, cookie[COOKIE].value)
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': json.loads(response.body)['csrf_token']})


def setup_stale(family, monkeypatch, *, stale='both'):
    import backend.app as module
    calls = []

    def upstream(request):
        calls.append((str(request.url), request.method))
        if request.url.path == '/api/sessions':
            return httpx.Response(200, json={'session': {'id': 'new-member-chat'}})
        if request.url.path == '/api/jobs':
            if request.method == 'GET':
                return httpx.Response(200, json={'jobs': [{'id': JOB}]})
            return httpx.Response(200, json={'job': {'id': JOB}})
        if request.method == 'DELETE':
            return httpx.Response(200, json={'ok': True})
        return httpx.Response(200, json={'job': {'id': JOB}})

    def gateway(*args, **kwargs):
        return GatewayClient(*args, **kwargs, transport=httpx.MockTransport(upstream))

    monkeypatch.setattr(module, 'GatewayClient', gateway)
    # Give the owner a real, readable native catalogue containing private history.
    (family['owner']/'state.db').unlink()
    create_native_db(family['owner']/'state.db')
    (family['owner']/'state.db').chmod(0o600)
    private(family['owner']/'cron/jobs.json', json.dumps({'jobs': [{'id': JOB, 'name': 'OWNER PRIVATE JOB'}]}))
    original = json.loads(family['config'].read_text())
    mapping = {'url': 'http://127.0.0.1:18643', 'token': 'member-token-'+'x'*40, 'execution_ready': True}
    loaded = {**original, 'profiles': {**original['profiles'], PROFILE: str(family['home'])},
              'gateway_profiles': {PROFILE: mapping}}
    if stale in ('both', 'home'):
        loaded['profiles'][PROFILE] = str(family['owner'])
    if stale in ('both', 'url'):
        mapping['url'] = original['upstream_url']
    if stale in ('both', 'token'):
        mapping['token'] = original['upstream_token']
    family['config'].write_text(json.dumps(loaded))
    app = create_app(Settings(**loaded, delivery_tokens={PROFILE: 'fixture-delivery'},
                              job_delivery_targets={PROFILE: 'mobile_delivery:member'}))
    # Operator repairs disk only. The old app must remain locked after activation.
    family['config'].write_text(json.dumps(original))
    result = activate(family['config'], 'abc123', family['runtime'], transport=transport(family))
    # The activation fixture has a deliberately narrow schema; extend it for catalog reads.
    with sqlite3.connect(family['home']/'state.db') as db:
        for column in ('source TEXT', 'started_at REAL', 'last_activity_at REAL', 'parent_session_id TEXT'):
            db.execute('ALTER TABLE sessions ADD COLUMN '+column)
        for column in ('id INTEGER', 'tool_calls TEXT', 'timestamp REAL'):
            db.execute('ALTER TABLE messages ADD COLUMN '+column)
    return app, calls, result


@pytest.mark.parametrize('stale', ['both', 'home', 'url', 'token'])
def test_stale_app_cannot_read_owner_history_or_dispatch_after_activation(family, monkeypatch, stale):
    app, calls, result = setup_stale(family, monkeypatch, stale=stale)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, app)
        response = client.get(BASE+'/sessions')
        assert response.status_code == 409, response.text
        assert 'WhatsApp conversation' not in response.text
        assert client.post(BASE+'/sessions', json={'title': 'Member'}).status_code == 409
        assert calls == []
        assert client.get(BASE+'/auth/me').status_code == 200
        login(client, app, 'owner')
        assert client.get(BASE+'/sessions').json()['total'] == 2
    corrected = create_app(Settings(**json.loads(family['config'].read_text())))
    with TestClient(corrected, base_url=ORIGIN) as client:
        login(client, corrected)
        assert client.get(BASE+'/sessions').json()['items'][0]['id'] == result['smoke_session']
        assert client.post(BASE+'/sessions', json={'title': 'Member'}).status_code == 200
        assert len(calls) == 1 and calls[0][0].startswith('http://127.0.0.1:18643/')


@pytest.mark.parametrize('method,body', [
    ('POST', {'name': 'Member job', 'schedule': 'every 1h', 'prompt': 'Hi'}),
    ('PATCH', {'enabled': False}), ('DELETE', {}),
])
def test_stale_jobs_service_never_dispatches(family, monkeypatch, method, body):
    app, calls, _ = setup_stale(family, monkeypatch)
    path = BASE+'/jobs'+('' if method == 'POST' else '/'+JOB)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, app)
        assert client.request(method, path, json={**body, 'confirm': True}).status_code == 409
        assert calls == []
    corrected = create_app(Settings(**json.loads(family['config'].read_text()),
                                    job_delivery_targets={PROFILE: 'mobile_delivery:member'}))
    with TestClient(corrected, base_url=ORIGIN) as client:
        login(client, corrected)
        assert client.request(method, path, json={**body, 'confirm': True}).status_code == 200
        assert calls and all(url.startswith('http://127.0.0.1:18643/') for url, _ in calls)


def test_stale_delivery_cannot_bind_owner_job_to_member(family, monkeypatch):
    app, calls, _ = setup_stale(family, monkeypatch)
    payload = {'profile': PROFILE, 'job_id': JOB, 'run_id': 'fixture-tick', 'body': 'Private result'}
    headers = {'Origin': ORIGIN, 'Authorization': 'Bearer fixture-delivery'}
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(BASE+'/internal/deliver', json=payload, headers=headers)
        assert response.status_code == 404, response.text
        assert app.state.notifications.list_inbox('abc123') == []
        assert calls == []
    private(family['home']/'cron/jobs.json', json.dumps({'jobs': [{'id': JOB, 'name': 'Member job'}]}))
    corrected = create_app(Settings(**json.loads(family['config'].read_text()),
                                    delivery_tokens={PROFILE: 'fixture-delivery', 'default': 'fixture-owner-delivery'}))
    with TestClient(corrected, base_url=ORIGIN) as client:
        assert client.post(BASE+'/internal/deliver', json=payload, headers=headers).status_code == 200
        assert len(corrected.state.notifications.list_inbox('abc123')) == 1
        headers['Authorization'] = 'Bearer fixture-owner-delivery'
        assert client.post(BASE+'/internal/deliver', json={**payload, 'profile': 'default'}, headers=headers).status_code == 200


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/sessions/wa-1/messages', None),
    ('GET', '/jobs', None),
    ('PATCH', '/sessions/wa-1', {'title': 'Member rename'}),
    ('POST', '/runs', {'session_id': 'wa-1', 'input': 'Hi', 'idempotency_key': 'fixture'}),
    ('GET', '/runs/fixture', None),
    ('GET', '/runs/fixture/events', None),
    ('POST', '/runs/fixture/stop', None),
    ('GET', '/approvals', None),
    ('POST', '/approvals/fixture/decision', {'decision': 'deny'}),
])
def test_every_native_route_checks_loaded_binding(family, monkeypatch, method, path, body):
    app, calls, _ = setup_stale(family, monkeypatch)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, app)
        response = client.request(method, BASE+path, json=body)
        assert response.status_code == 409, response.text
        assert 'Retained answer' not in response.text and 'OWNER PRIVATE JOB' not in response.text
        assert calls == []


@pytest.mark.parametrize('marker', [None, 'legacy-smoke-session', 'null', '[]', '{}',
                                    '{"version":1,"binding":7}', '{"version":1,"binding":"é"}'])
def test_missing_or_malformed_proof_fails_closed_on_correctly_loaded_app(family, monkeypatch, marker):
    old, calls, _ = setup_stale(family, monkeypatch)
    with TestClient(old, base_url=ORIGIN):
        pass
    corrected = create_app(Settings(**json.loads(family['config'].read_text())))
    with TestClient(corrected, base_url=ORIGIN) as client:
        login(client, corrected)
        assert client.get(BASE+'/sessions').status_code == 200
        with family['store'].transaction() as db:
            if marker is None:
                db.execute("DELETE FROM settings WHERE key='runtime_activation:abc123'")
            else:
                db.execute("UPDATE settings SET value=? WHERE key='runtime_activation:abc123'", (marker,))
        assert client.get(BASE+'/sessions').status_code == 409
        assert client.post(BASE+'/sessions', json={'title': 'Member'}).status_code == 409
        assert calls == []
        # Authentication/account management must not depend on native activation.
        assert client.get(BASE+'/auth/me').status_code == 200
        login(client, corrected, 'owner')
        assert client.get(BASE+'/sessions').status_code == 200
