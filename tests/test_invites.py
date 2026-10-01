"""Invitation + account authorization tests; all sign-ins use real signatures."""
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from test_auth import env, enroll, login, VirtualAuthenticator, BASE, ORIGIN, BOOTSTRAP


def step_up(client, authenticator):
    begin = client.post(BASE + '/auth/verify/options', json={})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    result = client.post(BASE + '/auth/verify/finish', json={
        'challenge_id': body['challenge_id'], 'credential': authenticator.assert_(body['options'])})
    assert result.status_code == 200, result.text


def test_owner_invites_require_stepup_and_redeem_once_as_pending_member(env):
    service, owner, now = env
    auth, _ = enroll(owner)
    now[0] += 301
    assert owner.post(BASE + '/invites', json={'label': 'Family', 'expires_days': 7}).status_code == 403
    step_up(owner, auth)
    response = owner.post(BASE + '/invites', json={'label': 'Family', 'expires_days': 7})
    assert response.status_code == 200, response.text
    invitation = response.json()
    assert invitation['code'].encode() not in service.store.path.read_bytes()
    assert 'code' not in owner.get(BASE + '/invites').text
    member = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    member_auth, result = enroll(member, invitation['code'])
    user = result.json()['user']
    assert user['role'] == 'member' and user['status'] == 'pending'
    assert user['profile'] == 'member_' + user['id']
    assert member.get(BASE + '/invites').status_code == 403
    assert member.post(BASE + '/invites', json={'label': 'No'}).status_code == 403
    assert member.post(BASE + '/auth/register/options', json={
        'code': invitation['code'], 'display_name': 'Replay'}).status_code == 400
    assert owner.get(BASE + '/members').json()['items'][0]['id'] == user['id']
    assert owner.post(BASE + '/members/' + user['id'] + '/disable').status_code == 200
    assert member.get(BASE + '/auth/me').status_code == 401
    assert login(member, member_auth).status_code == 400


def test_revoked_invite_is_rejected_even_after_registration_options(env):
    _, owner, _ = env
    enroll(owner)
    invitation = owner.post(BASE + '/invites', json={'label': 'Revoke'}).json()
    member = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    body = member.post(BASE + '/auth/register/options', json={
        'code': invitation['code'], 'display_name': 'Member'}).json()
    assert owner.delete(BASE + '/invites/' + invitation['invite']['id']).status_code == 200
    assert member.post(BASE + '/auth/register/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': VirtualAuthenticator().register(body['options'])}).status_code == 400
    assert owner.get(BASE + '/members').json()['items'] == []


def test_concurrent_invite_redemption_creates_exactly_one_protected_member(env):
    service, owner, _ = env
    enroll(owner)
    code = owner.post(BASE + '/invites', json={'label': 'Race'}).json()['code']
    clients = [TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN}) for _ in range(2)]
    payloads = []
    for client in clients:
        body = client.post(BASE + '/auth/register/options', json={'code': code, 'display_name': 'Race'}).json()
        payloads.append({'enrollment_id': body['enrollment_id'], 'credential': VirtualAuthenticator().register(body['options'])})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].post(BASE + '/auth/register/verify', json=pair[1]), zip(clients, payloads)))
    assert sorted(r.status_code for r in results) == [200, 400]
    with service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='member'").fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM credentials').fetchone()[0] == 2


def test_invite_expiry_and_server_assigned_roles(env):
    _, client, now = env
    enroll(client)
    code = client.post(BASE + '/invites', json={'label': 'Expire', 'expires_days': 1}).json()['code']
    assert client.post(BASE + '/auth/register/options', json={
        'code': code, 'display_name': 'Attacker', 'role': 'owner', 'profile': 'default'}).status_code == 422
    now[0] += 86400
    assert client.post(BASE + '/auth/register/options', json={'code': code, 'display_name': 'Expired'}).status_code == 400
