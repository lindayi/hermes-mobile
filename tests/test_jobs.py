"""Real GatewayClient + mocked HTTP transport, no live cron mutations."""
import importlib.util
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthService, build_auth_router
from backend.hermes_client import GatewayClient
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll

JOB_ID = 'abcdef123456'


@pytest.fixture
def jobs_env(tmp_path):
    assert importlib.util.find_spec('backend.jobs'), 'Job service missing'
    from backend.jobs import JobService, build_jobs_router
    now = [1_800_000_000.0]
    auth = AuthService(tmp_path / 'auth.sqlite', clock=lambda: now[0], bootstrap_secret=BOOTSTRAP)
    calls = []
    state = {'jobs': [], 'code': 200}

    def upstream(request):
        calls.append(request)
        if state['code'] != 200:
            return httpx.Response(state['code'], json={'error': 'PRIVATE token'})
        if 'response' in state:
            return httpx.Response(200, json=state['response'])
        if request.method == 'GET':
            return httpx.Response(200, json={'jobs': state['jobs']})
        if request.method == 'DELETE':
            return httpx.Response(200, json={'ok': True})
        job = {'id': JOB_ID, **json.loads(request.content), 'origin': {'private': 'secret'}}
        return httpx.Response(200, json={'job': job})

    gateway = GatewayClient('http://127.0.0.1:9901', 'test-private-token', execution_ready=True,
                            transport=httpx.MockTransport(upstream))
    service = JobService(auth, {'default': gateway}, delivery_targets={'default': 'hermes_mobile:owner'})
    app = FastAPI()
    app.include_router(build_auth_router(auth), prefix=BASE)
    app.include_router(build_jobs_router(service), prefix=BASE)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    return service, auth, client, calls, state, now


def test_create_routes_only_to_private_gateway_and_returns_native_id(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    response = client.post(BASE + '/jobs', json={'name': 'Reminder', 'schedule': '0 9 * * *',
                                               'prompt': 'Summarize news', 'confirm': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'job': {'id': JOB_ID, 'name': 'Reminder',
                                       'schedule': '0 9 * * *', 'prompt': 'Summarize news'}}
    assert len(calls) == 1
    assert calls[0].method == 'POST' and calls[0].url.path == '/api/jobs'
    assert calls[0].headers['authorization'] == 'Bearer test-private-token'
    assert json.loads(calls[0].content) == {'name': 'Reminder', 'schedule': '0 9 * * *',
                                          'prompt': 'Summarize news', 'deliver': 'hermes_mobile:owner'}
    assert 'secret' not in response.text and 'token' not in response.text


@pytest.mark.parametrize('gate', ['missing-client', 'unconfigured', 'not-ready', 'pending-member', 'confirm', 'stale'])
def test_job_creation_fails_closed_before_any_upstream_call(jobs_env, gate):
    service, auth, client, calls, state, now = jobs_env
    payload = {'name': 'Reminder', 'schedule': 'every 1h', 'prompt': 'News', 'confirm': True}
    code = 503
    if gate == 'missing-client':
        service.gateways.clear()
    elif gate == 'unconfigured':
        service.gateways['default'].token = ''
    elif gate == 'not-ready':
        service.gateways['default'].execution_ready = False
    elif gate == 'pending-member':
        with auth.store.transaction() as db:
            db.execute("UPDATE users SET status='pending'")
    elif gate == 'confirm':
        payload['confirm'] = False
        code = 409
    elif gate == 'stale':
        now[0] += 300
        code = 403
    response = client.post(BASE + '/jobs', json=payload)
    assert response.status_code == code, response.text
    assert calls == []


def test_upstream_failure_is_sanitized_without_success_or_retry(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    state['code'] = 503
    response = client.post(BASE + '/jobs', json={'name': 'Reminder', 'schedule': 'every 1h',
                                               'prompt': 'News', 'confirm': True})
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('method,changes', [('PATCH', {'enabled': False}), ('DELETE', {})])
def test_modify_checks_own_profile_jobs_including_disabled_then_mutates(jobs_env, method, changes):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID, 'name': 'Native', 'enabled': False, 'deliver': 'whatsapp:existing'}]
    response = client.request(method, BASE + '/jobs/' + JOB_ID, json={**changes, 'confirm': True})
    assert response.status_code == 200, response.text
    assert [(r.method, r.url.path) for r in calls] == [('GET', '/api/jobs'), (method, '/api/jobs/' + JOB_ID)]
    assert calls[0].url.params['include_disabled'] == 'true'
    if method == 'PATCH':
        assert json.loads(calls[1].content) == changes
        assert response.json() == {'job': {'id': JOB_ID, 'enabled': False}}
    else:
        assert response.json() == {'ok': True}
        assert not calls[1].content


@pytest.mark.parametrize('method', ['PATCH', 'DELETE'])
def test_foreign_job_id_never_reaches_mutation(jobs_env, method):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': '111111111111'}]
    response = client.request(method, BASE + '/jobs/' + JOB_ID,
                              json={'confirm': True, **({'enabled': False} if method == 'PATCH' else {})})
    assert response.status_code == 404
    assert len(calls) == 1 and calls[0].method == 'GET'


def test_patch_preserves_native_id_schedule_and_does_not_recreate_or_retarget(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID}]
    changes = {'name': 'New title', 'prompt': 'New prompt', 'schedule': '0 8 * * *', 'enabled': True}
    response = client.patch(BASE + '/jobs/' + JOB_ID, json={**changes, 'confirm': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'job': {'id': JOB_ID, **changes}}
    assert json.loads(calls[-1].content) == changes


@pytest.mark.parametrize('method,payload', [
    ('PATCH', {'confirm': True}), ('PATCH', {'confirm': True, 'enabled': None}),
    ('PATCH', {'confirm': True, 'name': ' '}), ('PATCH', {'confirm': True, 'schedule': ''}),
    ('POST', {'confirm': True, 'name': ' ', 'schedule': 'every 1h', 'prompt': 'x'}),
])
def test_empty_or_invalid_edits_fail_before_upstream(jobs_env, method, payload):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID}]
    response = client.request(method, BASE + '/jobs' + ('/' + JOB_ID if method == 'PATCH' else ''), json=payload)
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize('job_id', ['not-an-id', 'abcdef123456%3Fother=1', 'abcdef123456%23fragment'])
def test_invalid_job_id_rejected_before_lookup(jobs_env, job_id):
    service, auth, client, calls, state, now = jobs_env
    response = client.request('DELETE', BASE + '/jobs/' + job_id, json={'confirm': True})
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize('payload', [{}, {'job': {}}, {'job': {'id': 'wrong'}}, {'error': 'PRIVATE'}, []])
def test_malformed_upstream_never_becomes_success(jobs_env, payload):
    service, auth, client, calls, state, now = jobs_env
    state['response'] = payload
    response = client.post(BASE + '/jobs', json={'name': 'x', 'schedule': 'every 1h', 'prompt': 'x', 'confirm': True})
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text


@pytest.mark.parametrize('payload', [{}, {'jobs': {}}, {'jobs': [None]}, {'jobs': [{'id': JOB_ID}, None]}])
def test_invalid_ownership_listing_never_dispatches(jobs_env, payload):
    service, auth, client, calls, state, now = jobs_env
    state['response'] = payload
    response = client.request('DELETE', BASE + '/jobs/' + JOB_ID, json={'confirm': True})
    assert response.status_code == 503
    assert len(calls) == 1


@pytest.mark.parametrize('method,payload', [('DELETE', {'ok': False}), ('DELETE', {'ok': 'true'}),
    ('DELETE', {'ok': True, 'private': 'PRIVATE'}), ('PATCH', {'job': {'id': '111111111111'}})])
def test_mutation_response_must_confirm_exact_native_action(jobs_env, method, payload):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID}]
    gateway = service.gateways['default']
    original = gateway.request

    async def response_after_lookup(verb, path, **kwargs):
        result = await original(verb, path, **kwargs)
        return result if verb == 'GET' else payload

    gateway.request = response_after_lookup
    response = client.request(method, BASE + '/jobs/' + JOB_ID,
                              json={'confirm': True, **({'enabled': False} if method == 'PATCH' else {})})
    if payload.get('ok') is True:
        assert response.status_code == 200
        assert response.json() == {'ok': True}
    else:
        assert response.status_code == 503
    assert 'PRIVATE' not in response.text


def test_member_uses_only_own_configured_profile_not_default(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    me = client.get(BASE + '/auth/me').json()['user']
    profile = 'member_' + me['id']
    with auth.store.transaction() as db:
        db.execute("UPDATE users SET role='member',profile=? WHERE id=?", (profile, me['id']))
    # No member config: never fall back to the existing default gateway.
    data = {'confirm': True, 'name': 'x', 'schedule': 'every 1h', 'prompt': 'x'}
    assert client.post(BASE + '/jobs', json=data).status_code == 503
    assert calls == []
    member_calls = []

    def member_upstream(request):
        member_calls.append(request)
        return httpx.Response(200, json={'job': {'id': JOB_ID}})

    service.delivery_targets[profile] = 'hermes_mobile:member'
    service.gateways[profile] = GatewayClient('http://127.0.0.1:9902', 'member-token', execution_ready=True,
                                            transport=httpx.MockTransport(member_upstream))
    assert client.post(BASE + '/jobs', json=data).status_code == 200
    assert calls == []
    assert len(member_calls) == 1
    assert member_calls[0].headers['authorization'] == 'Bearer member-token'


def test_creation_is_rate_limited(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    data = {'confirm': True, 'name': 'x', 'schedule': 'every 1h', 'prompt': 'x'}
    for _ in range(20):
        assert client.post(BASE + '/jobs', json=data).status_code == 200
    assert client.post(BASE + '/jobs', json=data).status_code == 429
    assert len(calls) == 20


@pytest.mark.parametrize('method', ['POST', 'PATCH', 'DELETE'])
@pytest.mark.parametrize('gate', ['anonymous', 'origin', 'csrf', 'confirm', 'stale', 'revoked'])
def test_all_job_mutations_require_authentication_csrf_and_confirmation(jobs_env, method, gate):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID}]
    data = {'confirm': True}
    if method == 'POST':
        data.update(name='x', prompt='x', schedule='every 1h')
    elif method == 'PATCH':
        data['enabled'] = False
    code = 403
    if gate == 'anonymous':
        client.cookies.clear()
        code = 401
    elif gate == 'origin':
        client.headers['Origin'] = 'https://evil.example'
    elif gate == 'csrf':
        client.headers['X-CSRF-Token'] = 'wrong'
    elif gate == 'confirm':
        data['confirm'] = False
        code = 409
    elif gate == 'stale':
        now[0] += 300
    else:
        with auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1')
        code = 401
    url = BASE + '/jobs' + ('' if method == 'POST' else '/' + JOB_ID)
    assert client.request(method, url, json=data).status_code == code
    assert calls == []


@pytest.mark.parametrize('field,value', [('profile', 'default'), ('origin', {'chat_id': 'other'}),
    ('deliver', 'whatsapp:other'), ('script', '/tmp/evil.py'), ('skills', ['/tmp/evil']),
    ('url', 'http://localhost/admin'), ('no_agent', True), ('user_id', 'other')])
@pytest.mark.parametrize('method', ['POST', 'PATCH', 'DELETE'])
def test_browser_cannot_supply_destinations_or_paths(jobs_env, method, field, value):
    service, auth, client, calls, state, now = jobs_env
    data = {'confirm': True, field: value}
    if method == 'POST':
        data.update(name='x', prompt='x', schedule='every 1h')
    elif method == 'PATCH':
        data['enabled'] = False
    url = BASE + '/jobs' + ('' if method == 'POST' else '/' + JOB_ID)
    assert client.request(method, url, json=data).status_code == 422
    assert calls == []


def test_revocation_during_ownership_lookup_prevents_mutation(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    state['jobs'] = [{'id': JOB_ID}]
    original = service.gateways['default'].request

    async def revoke(method, path, **kwargs):
        result = await original(method, path, **kwargs)
        if method == 'GET':
            with auth.store.transaction() as db:
                db.execute('UPDATE sessions SET revoked=1')
        return result

    service.gateways['default'].request = revoke
    assert client.request('DELETE', BASE + '/jobs/' + JOB_ID, json={'confirm': True}).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize('target', [None, '', '  ', 'local', 'origin', 'all'])
def test_create_without_explicit_notification_binding_fails_closed(jobs_env, target):
    service, auth, client, calls, state, now = jobs_env
    service.delivery_targets = {} if target is None else {'default': target}
    response = client.post(BASE + '/jobs', json={'name': 'x', 'prompt': 'x', 'schedule': 'every 1h', 'confirm': True})
    assert response.status_code == 503
    assert calls == []


def test_existing_job_edit_does_not_require_new_delivery_binding(jobs_env):
    service, auth, client, calls, state, now = jobs_env
    service.delivery_targets.clear()
    state['jobs'] = [{'id': JOB_ID, 'deliver': 'whatsapp:existing'}]
    response = client.patch(BASE + '/jobs/' + JOB_ID, json={'enabled': False, 'confirm': True})
    assert response.status_code == 200
    assert json.loads(calls[-1].content) == {'enabled': False}
