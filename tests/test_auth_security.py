import json
import secrets
import pytest
from fastapi.testclient import TestClient
from test_auth import env, enroll, login, VirtualAuthenticator, BASE, ORIGIN, BOOTSTRAP, b64, unb64


@pytest.mark.parametrize('defect', ['origin', 'rp', 'challenge', 'uv', 'signature', 'handle', 'crossOrigin'])
def test_signed_assertion_rejects_each_invalid_security_binding(env, defect):
    _, client, _ = env
    auth, _ = enroll(client)
    client.cookies.clear()
    body = client.post(BASE + '/auth/login/options', json={}).json()
    options = dict(body['options'])
    if defect == 'challenge':
        options['challenge'] = b64(secrets.token_bytes(32))
    if defect == 'crossOrigin':
        original = auth.client_data
        def cross_data(*args, **kwargs):
            data = json.loads(original(*args, **kwargs))
            data['crossOrigin'] = True
            return json.dumps(data).encode()
        auth.client_data = cross_data
    credential = auth.assert_(options, origin='https://evil.example' if defect == 'origin' else ORIGIN,
                              rp_id='evil.example' if defect == 'rp' else 'lindayi.me', uv=defect != 'uv')
    if defect == 'signature':
        credential['response']['signature'] = b64(b'forged-signature')
    if defect == 'handle':
        credential['response']['userHandle'] = b64(b'another-user')
    payload = {'challenge_id': body['challenge_id'], 'credential': credential}
    assert client.post(BASE + '/auth/login/verify', json=payload).status_code == 400
    assert client.post(BASE + '/auth/login/verify', json=payload).status_code == 400
    assert client.get(BASE + '/auth/me').status_code == 401


@pytest.mark.parametrize('defect', ['origin', 'rp', 'challenge', 'uv', 'signature'])
def test_registration_rejects_invalid_attestation_without_creating_user(env, defect):
    service, client, _ = env
    body = client.post(BASE + '/auth/register/options', json={'code': BOOTSTRAP, 'display_name': 'Phone'}).json()
    options = dict(body['options'])
    if defect == 'challenge':
        options['challenge'] = b64(secrets.token_bytes(32))
    credential = VirtualAuthenticator().register(options,
        origin='https://evil.example' if defect == 'origin' else ORIGIN,
        rp_id='evil.example' if defect == 'rp' else 'lindayi.me', uv=defect != 'uv')
    if defect == 'signature':
        import cbor2
        attestation = cbor2.loads(unb64(credential['response']['attestationObject']))
        attestation['attStmt']['sig'] = b'forged'
        credential['response']['attestationObject'] = b64(cbor2.dumps(attestation))
    payload = {'enrollment_id': body['enrollment_id'], 'credential': credential}
    assert client.post(BASE + '/auth/register/verify', json=payload).status_code == 400
    assert client.post(BASE + '/auth/register/verify', json=payload).status_code == 400
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 0
    enroll(client)  # failed attestation must not burn bootstrap


def test_synced_zero_counters_login_replay_and_expiring_challenges(env):
    _, client, now = env
    auth, _ = enroll(client)
    assert login(client, auth, counter=0).status_code == 200
    assert login(client, auth, counter=0).status_code == 200
    body = client.post(BASE + '/auth/login/options', json={}).json()
    payload = {'challenge_id': body['challenge_id'], 'credential': auth.assert_(body['options'])}
    assert client.post(BASE + '/auth/login/verify', json=payload).status_code == 200
    assert client.post(BASE + '/auth/login/verify', json=payload).status_code == 400
    body = client.post(BASE + '/auth/login/options', json={}).json()
    now[0] += 300
    assert client.post(BASE + '/auth/login/verify', json={
        'challenge_id': body['challenge_id'], 'credential': auth.assert_(body['options'])}).status_code == 400


def test_polling_does_not_extend_inactivity_or_absolute_expiry(env):
    service, client, now = env
    auth, _ = enroll(client)
    for _ in range(29):
        now[0] += 86400
        assert client.get(BASE + '/auth/me').status_code == 200
    now[0] += 86400
    assert client.get(BASE + '/auth/me').status_code == 401
    assert login(client, auth).status_code == 200
    for _ in range(89):
        now[0] += 86400
        # Mutation activity, not polling; options alone does not refresh full-auth deadline.
        assert client.post(BASE + '/auth/verify/options', json={}).status_code == 200
    now[0] += 86400
    assert client.get(BASE + '/auth/me').status_code == 401


@pytest.mark.parametrize('headers', [{'Origin': 'https://evil.example'}, {'Origin': ''}, {'X-CSRF-Token': ''}, {'X-CSRF-Token': 'wrong'}])
def test_mutation_rejects_csrf_or_origin(env, headers):
    _, client, _ = env
    enroll(client)
    assert client.post(BASE + '/invites', json={'label': 'No'}, headers=headers).status_code == 403
    assert client.post(BASE + '/auth/logout', headers=headers).status_code == 403
    assert client.get(BASE + '/auth/me').status_code == 200


def test_auth_rate_limits_are_durable_and_payloads_bounded(env):
    service, client, now = env
    for _ in range(20):
        assert client.post(BASE + '/auth/login/options', json={}).status_code == 200
    assert client.post(BASE + '/auth/login/options', json={}, headers={'X-Forwarded-For': '8.8.8.8'}).status_code == 429
    now[0] += 61
    assert client.post(BASE + '/auth/login/options', json={}).status_code == 200
    assert client.post(BASE + '/auth/login/verify', json={'challenge_id': 'no', 'credential': {'large': 'x'*70000}}).status_code == 413
    assert client.get(BASE + '/auth/me').headers['cache-control'] == 'no-store'


def test_bootstrap_expiry_persists_across_service_restart(env):
    service, client, now = env
    from backend.auth import AuthService, build_auth_router
    from fastapi import FastAPI
    now[0] += 900
    restarted = AuthService(service.store.path, clock=lambda: now[0], bootstrap_secret=BOOTSTRAP)
    app = FastAPI()
    app.include_router(build_auth_router(restarted), prefix=BASE)
    second = TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    assert second.post(BASE + '/auth/register/options', json={'code': BOOTSTRAP, 'display_name': 'Expired'}).status_code == 400


@pytest.mark.parametrize('credential_id', [[], {'x': 'y'}, 123])
def test_malformed_credential_id_is_a_client_error(env, credential_id):
    _, client, _ = env
    body = client.post(BASE + '/auth/login/options', json={}).json()
    response = client.post(BASE + '/auth/login/verify', json={
        'challenge_id': body['challenge_id'], 'credential': {'id': credential_id}})
    assert response.status_code == 400


def test_registration_rejects_cross_origin_context_even_with_valid_signature(env):
    _, client, _ = env
    auth = VirtualAuthenticator()
    original = auth.client_data
    def cross_data(*args, **kwargs):
        data = json.loads(original(*args, **kwargs))
        data['crossOrigin'] = True
        return json.dumps(data).encode()
    auth.client_data = cross_data
    body = client.post(BASE + '/auth/register/options', json={'code': BOOTSTRAP, 'display_name': 'Cross'}).json()
    assert client.post(BASE + '/auth/register/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': auth.register(body['options'])}).status_code == 400


def test_stepup_is_bound_to_session_and_account(env):
    _, owner, now = env
    auth, _ = enroll(owner)
    code = owner.post(BASE + '/invites', json={'label': 'Member'}).json()['code']
    member = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    other_key, _ = enroll(member, code)
    now[0] += 301
    body = owner.post(BASE + '/auth/verify/options', json={}).json()
    assert owner.post(BASE + '/auth/verify/finish', json={
        'challenge_id': body['challenge_id'], 'credential': other_key.assert_(body['options'])}).status_code == 400
    assert owner.post(BASE + '/invites', json={'label': 'No'}, headers={'X-WebAuthn-Verified': 'true'}).status_code == 403
    second = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    assert login(second, auth).status_code == 200
    body = owner.post(BASE + '/auth/verify/options', json={}).json()
    assert second.post(BASE + '/auth/verify/finish', json={
        'challenge_id': body['challenge_id'], 'credential': auth.assert_(body['options'])}).status_code == 400


def test_non_ascii_csrf_header_is_rejected_not_server_error(env):
    _, client, _ = env
    enroll(client)
    assert client.post(BASE + '/auth/logout', headers={'X-CSRF-Token': b'\xff'}).status_code == 403
