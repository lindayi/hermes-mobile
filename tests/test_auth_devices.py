from fastapi.testclient import TestClient
from test_auth import env, enroll, login, VirtualAuthenticator, BASE, ORIGIN, b64
from test_invites import step_up


def test_second_passkey_add_delete_and_device_revocation(env):
    service, client, now = env
    primary, _ = enroll(client)
    assert client.get(BASE + '/passkeys').status_code == 200
    primary_id = client.get(BASE + '/passkeys').json()['items'][0]['id']
    assert client.delete(BASE + '/passkeys/' + primary_id).status_code == 409
    second = VirtualAuthenticator()
    begin = client.post(BASE + '/passkeys', json={'label': 'Backup'})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    payload = {'enrollment_id': body['enrollment_id'], 'credential': second.register(body['options'])}
    assert client.post(BASE + '/passkeys/verify', json=payload).status_code == 200
    assert client.post(BASE + '/passkeys/verify', json=payload).status_code == 400
    other = TestClient(client.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    assert login(other, second).status_code == 200
    assert len(client.get(BASE + '/devices').json()['items']) == 2
    assert client.delete(BASE + '/passkeys/' + primary_id).status_code == 200
    assert login(other, primary).status_code == 400
    device = next(x for x in client.get(BASE + '/devices').json()['items'] if not x['current'])
    assert client.delete(BASE + '/devices/' + device['id']).status_code == 200
    assert other.get(BASE + '/auth/me').status_code == 401
    me = client.get(BASE + '/auth/me').json()['user']
    assert not service.is_session_active(me['id'], device['id'])
    assert client.delete(BASE + '/devices').status_code == 200
    assert client.get(BASE + '/auth/me').status_code == 401


def test_foreign_device_and_passkey_cannot_be_deleted(env):
    _, owner, _ = env
    enroll(owner)
    code = owner.post(BASE + '/invites', json={'label': 'Member'}).json()['code']
    member = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    enroll(member, code)
    device = owner.get(BASE + '/devices').json()['items'][0]['id']
    key = owner.get(BASE + '/passkeys').json()['items'][0]['id']
    assert member.delete(BASE + '/devices/' + device).status_code == 404
    assert member.delete(BASE + '/passkeys/' + key).status_code == 404
    assert len(owner.get(BASE + '/devices').json()['items']) == 1


def test_revocation_between_auth_dependency_and_mutation_prevents_invite(env, monkeypatch):
    service, client, _ = env
    enroll(client)
    original = service.require_mutation
    def revoke_after_check(request, user):
        original(request, user)
        with service.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
    monkeypatch.setattr(service, 'require_mutation', revoke_after_check)
    assert client.post(BASE + '/invites', json={'label': 'No after revoke'}).status_code == 401


def test_add_passkey_cannot_commit_after_session_revoked(env):
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request
    from backend.auth import RegisterVerify
    service, client, _ = env
    enroll(client)
    body = client.post(BASE + '/passkeys', json={'label': 'Second'}).json()
    cookie = client.cookies.get('hermes_session')
    user = service.require_user(Request({'type': 'http', 'headers': [(b'cookie', ('hermes_session='+cookie).encode())]}))
    credential = VirtualAuthenticator().register(body['options'])
    assert client.delete(BASE + '/devices').status_code == 200
    with pytest.raises(HTTPException) as rejected:
        service.add_passkey_verify(user, RegisterVerify(enrollment_id=body['enrollment_id'], credential=credential))
    assert rejected.value.status_code == 401
