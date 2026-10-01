"""Rotation contracts, using real P-256 WebAuthn assertions and isolated SQLite."""
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
import pytest

from backend.auth import COOKIE, DAY, digest
from test_auth import env, enroll, BASE, ORIGIN


def session_row(service):
    with service.store.transaction() as db:
        return dict(db.execute('SELECT * FROM sessions').fetchone())


def stepup_payload(client, authenticator):
    begin = client.post(BASE + '/auth/verify/options', json={})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    return {'challenge_id': body['challenge_id'],
            'credential': authenticator.assert_(body['options'])}


def test_stepup_rotates_bearer_but_preserves_session_csrf_and_absolute_expiry(env):
    service, client, now = env
    authenticator, enrolled = enroll(client)
    old_raw = client.cookies.get(COOKIE)
    original = session_row(service)
    now[0] += 2 * DAY
    response = client.post(BASE + '/auth/verify/finish', json=stepup_payload(client, authenticator))
    assert response.status_code == 200, response.text
    new_raw = client.cookies.get(COOKIE)
    assert new_raw != old_raw, 'successful step-up must replace the opaque bearer'
    assert response.json() == {'ok': True}
    assert response.headers['cache-control'] == 'no-store'
    cookie = SimpleCookie(response.headers['set-cookie'])[COOKIE]
    assert cookie['path'] == '/hermes'
    assert cookie['secure'] and cookie['httponly'] and cookie['samesite'] == 'strict'
    assert not cookie['domain']
    assert int(cookie['max-age']) == 88 * DAY
    rotated = session_row(service)
    assert rotated['token_hash'] == digest(new_raw)
    assert rotated['id'] == original['id']
    assert rotated['created_at'] == original['created_at']
    assert rotated['csrf_token'] == original['csrf_token']
    assert rotated['last_verified_at'] == now[0]
    assert service.is_session_active(enrolled.json()['user']['id'], original['id'])
    assert client.get(BASE + '/auth/me').json() == enrolled.json()
    # The frontend may immediately perform a protected action using the same CSRF.
    assert client.post(BASE + '/invites', json={'label': 'After rotation'}).status_code == 200
    stale = TestClient(client.app, base_url=ORIGIN, headers={'Origin': ORIGIN,
                       'X-CSRF-Token': original['csrf_token']})
    stale.cookies.set(COOKIE, old_raw, domain='lindayi.me', path='/hermes')
    assert stale.get(BASE + '/auth/me').status_code == 401
    assert stale.post(BASE + '/auth/verify/options', json={}).status_code == 401
    assert stale.post(BASE + '/invites', json={'label': 'Stale token'}).status_code == 401
    assert new_raw.encode() not in service.store.path.read_bytes()
    assert old_raw.encode() not in service.store.path.read_bytes()


def test_concurrent_stepups_from_same_bearer_have_one_winner(env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import HTTPException
    from starlette.requests import Request
    from backend.auth import AssertionVerify

    service, client, _ = env
    authenticator, _ = enroll(client)
    old_raw = client.cookies.get(COOKIE)
    request = Request({'type': 'http', 'headers': [(b'cookie', f'{COOKIE}={old_raw}'.encode())]})
    # Both dependencies resolve before either worker rotates. Zero counters are
    # valid for synced passkeys; replay counters must not mask the bearer race.
    users = [service.require_user(request), service.require_user(request)]
    bodies = []
    for user in users:
        begin = service.stepup_options(user)
        bodies.append(AssertionVerify(challenge_id=begin['challenge_id'],
                      credential=authenticator.assert_(begin['options'], counter=0)))
    barrier = Barrier(2)

    def finish(index):
        barrier.wait(timeout=5)
        try:
            response = service.stepup_verify(users[index], bodies[index])
            return response.status_code, response.headers.get('set-cookie')
        except HTTPException as error:
            return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, range(2)))
    assert sorted(status for status, _ in results) == [200, 401]
    winning_cookie = next(cookie for status, cookie in results if status == 200)
    winner = SimpleCookie(winning_cookie)[COOKIE].value
    assert session_row(service)['token_hash'] == digest(winner)
    assert client.get(BASE + '/auth/me').status_code == 401
    client.cookies.set(COOKIE, winner, domain='lindayi.me', path='/hermes')
    assert client.get(BASE + '/auth/me').status_code == 200


@pytest.mark.parametrize('invalidation', ['rotation', 'revocation', 'disabled', 'idle_expiry', 'absolute_expiry'])
def test_stale_dependency_cannot_touch_activity_after_invalidation(env, invalidation):
    from fastapi import HTTPException
    from starlette.requests import Request

    service, client, now = env
    authenticator, _ = enroll(client)
    raw, csrf = client.cookies.get(COOKIE), client.headers['X-CSRF-Token']
    request = Request({'type': 'http', 'headers': [
        (b'cookie', f'{COOKIE}={raw}'.encode()), (b'origin', ORIGIN.encode()),
        (b'x-csrf-token', csrf.encode())]})
    cached_user = service.require_user(request)
    if invalidation == 'rotation':
        result = client.post(BASE + '/auth/verify/finish', json=stepup_payload(client, authenticator))
        assert result.status_code == 200
    elif invalidation == 'revocation':
        with service.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1')
    elif invalidation == 'disabled':
        with service.store.transaction() as db:
            db.execute("UPDATE users SET status='disabled'")
    elif invalidation == 'idle_expiry':
        now[0] += 30 * DAY
    else:
        now[0] += 90 * DAY
        with service.store.transaction() as db:
            db.execute('UPDATE sessions SET last_active_at=?', (now[0],))
    before = session_row(service)
    now[0] += 1
    with pytest.raises(HTTPException) as rejected:
        service.require_mutation(request, cached_user)
    assert rejected.value.status_code == 401
    assert session_row(service) == before, 'rejected stale requests cannot extend inactivity'


def test_expiry_during_assertion_verification_does_not_issue_rotated_cookie(env, monkeypatch):
    service, client, now = env
    authenticator, _ = enroll(client)
    payload = stepup_payload(client, authenticator)
    before = session_row(service)
    verify = service.verify_assertion

    def expiring_verification(*args):
        result = verify(*args)  # Still cryptographically verify the real assertion.
        now[0] = before['created_at'] + 90 * DAY
        return result

    monkeypatch.setattr(service, 'verify_assertion', expiring_verification)
    response = client.post(BASE + '/auth/verify/finish', json=payload)
    assert response.status_code == 401
    assert 'set-cookie' not in response.headers
    assert session_row(service) == before
    with service.store.transaction() as db:
        assert db.execute('SELECT sign_count FROM credentials').fetchone()[0] == 0


# Preservation/regression checks: these intentionally exercise unchanged policy,
# in addition to the RED -> GREEN behavior slices above.
def test_rotated_session_survives_restart_without_read_polling_extending_activity(env):
    from fastapi import FastAPI
    from backend.auth import AuthService, build_auth_router

    service, client, now = env
    authenticator, _ = enroll(client)
    assert client.post(BASE + '/auth/verify/finish', json=stepup_payload(client, authenticator)).status_code == 200
    original = session_row(service)
    restarted = AuthService(service.store.path, clock=lambda: now[0])
    app = FastAPI()
    app.include_router(build_auth_router(restarted), prefix=BASE)
    with TestClient(app, base_url=ORIGIN) as reader:
        reader.cookies.set(COOKIE, client.cookies.get(COOKIE), domain='lindayi.me', path='/hermes')
        for day in (1, 2, 29):
            now[0] = original['last_active_at'] + day * DAY
            response = reader.get(BASE + '/auth/me')
            assert response.status_code == 200
            assert 'set-cookie' not in response.headers, 'silent read rotation deliberately not enabled'
            assert session_row(restarted) == original
        now[0] = original['last_active_at'] + 30 * DAY
        assert reader.get(BASE + '/auth/me').status_code == 401


def test_failed_stepup_never_rotates_or_refreshes_verified_time(env):
    service, client, now = env
    authenticator, _ = enroll(client)
    now[0] += 301
    payload = stepup_payload(client, authenticator)
    payload['credential']['response']['signature'] = 'Zm9yZ2Vk'
    old_cookie, before = client.cookies.get(COOKIE), session_row(service)
    response = client.post(BASE + '/auth/verify/finish', json=payload)
    assert response.status_code == 400
    assert 'set-cookie' not in response.headers
    assert client.cookies.get(COOKIE) == old_cookie
    assert session_row(service) == before
    assert client.post(BASE + '/auth/verify/finish', json=payload).status_code == 400
    assert client.post(BASE + '/invites', json={'label': 'Must not authorize'}).status_code == 403


@pytest.mark.parametrize('state', ['revoked', 'disabled', 'idle_expired', 'absolute_expired'])
def test_stepup_cannot_rotate_an_invalid_session(env, state):
    service, client, now = env
    authenticator, _ = enroll(client)
    payload = stepup_payload(client, authenticator)
    with service.store.transaction() as db:
        if state == 'revoked':
            db.execute('UPDATE sessions SET revoked=1')
        elif state == 'disabled':
            db.execute("UPDATE users SET status='disabled'")
        elif state == 'idle_expired':
            db.execute('UPDATE sessions SET last_active_at=?', (now[0] - 30 * DAY,))
        else:
            db.execute('UPDATE sessions SET created_at=?', (now[0] - 90 * DAY,))
    before = session_row(service)
    response = client.post(BASE + '/auth/verify/finish', json=payload)
    assert response.status_code == 401
    assert 'set-cookie' not in response.headers
    assert session_row(service) == before


def test_rotation_keeps_existing_push_binding_but_revocation_still_stops_delivery(env, tmp_path):
    from types import SimpleNamespace
    from backend.notifications import NotificationService
    from test_notifications import subscription

    service, client, now = env
    authenticator, enrolled = enroll(client)
    original = session_row(service)
    sent = []

    def network(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(status_code=201)

    notifications = NotificationService(tmp_path / 'push.sqlite', vapid_private_key='fixture',
        vapid_public_key='fixture', send_push=network, clock=lambda: now[0],
        session_validator=service.is_session_active)
    user_id = enrolled.json()['user']['id']
    notifications.subscribe(user_id, original['id'], subscription())
    notifications.ingest(user_id, 'before-rotation', 'Fixture', 'Queued before step-up', category='scheduled')
    assert client.post(BASE + '/auth/verify/finish', json=stepup_payload(client, authenticator)).status_code == 200
    assert notifications.flush()['sent'] == 1
    assert len(sent) == 1
    notifications.ingest(user_id, 'after-rotation', 'Fixture', 'Queued before revocation', category='scheduled')
    assert client.delete(BASE + '/devices/' + original['id']).status_code == 200
    assert notifications.flush()['sent'] == 0
    assert len(sent) == 1
