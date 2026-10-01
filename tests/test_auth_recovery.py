from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from test_auth import env, enroll, login, VirtualAuthenticator, BASE, ORIGIN


def test_recovery_code_only_allows_verified_replacement_then_revokes_old_keys(env):
    service, owner, _ = env
    old_key, _ = enroll(owner)
    result = owner.post(BASE + '/auth/recovery/codes', json={})
    assert result.status_code == 200, result.text
    codes = result.json()['codes']
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(code.encode() not in service.store.path.read_bytes() for code in codes)
    recovery = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    begin = recovery.post(BASE + '/auth/recovery/options', json={'code': codes[0], 'display_name': 'Replacement'})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    assert body['recovery'] is True
    recovery.headers['X-CSRF-Token'] = body['csrf_token']
    assert owner.get(BASE + '/auth/me').status_code == 401
    assert recovery.get(BASE + '/auth/me').status_code == 401
    assert recovery.get(BASE + '/passkeys').status_code == 401
    assert recovery.post(BASE + '/invites', json={'label': 'No'}).status_code == 401
    assert recovery.post(BASE + '/auth/recovery/options', json={
        'code': codes[0], 'display_name': 'Reused'}).status_code == 400
    replacement = VirtualAuthenticator()
    credential = replacement.register(body['options'])
    invalid = dict(credential, response={**credential['response'], 'attestationObject': 'AAAA'})
    # CSRF rejection does not consume the ceremony.
    assert recovery.post(BASE + '/auth/recovery/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': credential},
        headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    finish = recovery.post(BASE + '/auth/recovery/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': credential})
    assert finish.status_code == 200, finish.text
    assert finish.json()['user']['role'] == 'owner'
    assert recovery.get(BASE + '/auth/me').status_code == 200
    assert login(owner, old_key).status_code == 400
    assert login(owner, replacement).status_code == 200
    assert recovery.post(BASE + '/auth/recovery/options', json={
        'code': codes[1], 'display_name': 'Old set'}).status_code == 400


def test_forged_recovery_attestation_cannot_gain_full_session(env):
    _, client, _ = env
    enroll(client)
    code = client.post(BASE + '/auth/recovery/codes', json={}).json()['codes'][0]
    body = client.post(BASE + '/auth/recovery/options', json={'code': code, 'display_name': 'Replacement'}).json()
    client.headers['X-CSRF-Token'] = body['csrf_token']
    credential = VirtualAuthenticator().register(body['options'], origin='https://evil.example')
    assert client.post(BASE + '/auth/recovery/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': credential}).status_code == 400
    assert client.get(BASE + '/auth/me').status_code == 401


def test_recovery_rechecks_revocation_inside_credential_commit(env):
    """Simulate revoke arriving between dependency authentication and credential commit."""
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request
    from backend.auth import RegisterVerify
    _, client, _ = env
    service = env[0]
    enroll(client)
    codes = client.post(BASE + '/auth/recovery/codes', json={}).json()['codes']
    body = client.post(BASE + '/auth/recovery/options', json={'code': codes[0], 'display_name': 'First'}).json()
    cookie = client.cookies.get('hermes_session')
    request = Request({'type': 'http', 'headers': [(b'cookie', ('hermes_session='+cookie).encode())]})
    user = service.require_recovery(request)
    credential = VirtualAuthenticator().register(body['options'])
    second = TestClient(client.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    assert second.post(BASE + '/auth/recovery/options', json={'code': codes[1], 'display_name': 'Second'}).status_code == 200
    with pytest.raises(HTTPException) as rejected:
        service.recovery_verify(user, RegisterVerify(enrollment_id=body['enrollment_id'], credential=credential))
    assert rejected.value.status_code == 401


def test_recovery_operations_leave_secret_free_audit_records(env):
    service, client, _ = env
    enroll(client)
    code = client.post(BASE + '/auth/recovery/codes', json={}).json()['codes'][0]
    body = client.post(BASE + '/auth/recovery/options', json={'code': code, 'display_name': 'Replacement'}).json()
    client.headers['X-CSRF-Token'] = body['csrf_token']
    assert client.post(BASE + '/auth/recovery/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': VirtualAuthenticator().register(body['options'])}).status_code == 200
    with service.store.transaction() as db:
        names = [r[0] for r in db.execute('SELECT event FROM audit ORDER BY id')]
    assert names == ['recovery_codes_issued', 'recovery_started', 'recovery_completed']
    assert code.encode() not in service.store.path.read_bytes()


def test_concurrent_recovery_code_redemption_is_single_use(env):
    _, client, _ = env
    enroll(client)
    code = client.post(BASE + '/auth/recovery/codes', json={}).json()['codes'][0]
    clients = [TestClient(client.app, base_url=ORIGIN, headers={'Origin': ORIGIN}) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: c.post(BASE + '/auth/recovery/options', json={'code': code, 'display_name': 'Race'}), clients))
    assert sorted(r.status_code for r in results) == [200, 400]
    assert all(c.get(BASE + '/auth/me').status_code == 401 for c in clients)
