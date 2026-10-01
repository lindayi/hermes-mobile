"""Real auth/CSRF router policy; isolated stores and virtual passkey enrollment."""
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from backend.auth import AuthService, build_auth_router
from backend.notifications import NotificationService, build_notifications_router
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_notifications import subscription


@pytest.fixture
def api(tmp_path):
    now = [1800000000.0]
    auth = AuthService(tmp_path/'auth.db', bootstrap_secret=BOOTSTRAP, clock=lambda:now[0])
    app = FastAPI()
    app.include_router(build_auth_router(auth), prefix=BASE)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    cookie = client.cookies.get('hermes_session')
    user = auth.require_user(Request({'type':'http','headers':[(b'cookie',f'hermes_session={cookie}'.encode())]}))
    sent = []
    service = NotificationService(tmp_path/'n.db','private','public',clock=lambda:now[0],
        send_push=lambda **kw: (sent.append(kw) or SimpleNamespace(status_code=201)))
    app.include_router(build_notifications_router(service,auth),prefix=BASE)
    return SimpleNamespace(app=app,auth=auth,client=client,user=user,service=service,now=now,sent=sent)


def test_preference_routes_require_auth_csrf_exact_types_and_live_device(api):
    c, s, u = api.client, api.service, api.user
    response = c.get(BASE+'/push/preferences')
    assert response.status_code == 200
    prefs = response.json()
    changed = {**prefs,'hide_details':True}
    assert c.put(BASE+'/push/preferences',json=changed,headers={'X-CSRF-Token':'bad'}).status_code == 403
    assert c.put(BASE+'/push/preferences',json=changed,headers={'Origin':'https://evil.test'}).status_code == 403
    assert c.get(BASE+'/push/preferences').json() == prefs
    assert c.put(BASE+'/push/preferences',json={**prefs,'enabled':1}).status_code == 400
    api.now[0] += 10
    assert c.put(BASE+'/push/preferences',json=changed).json() == {**changed,'revision':1}
    with api.auth.store.transaction() as db:
        assert db.execute('SELECT last_active_at FROM sessions WHERE id=?',(u['session_id'],)).fetchone()[0] == api.now[0]
        db.execute('UPDATE sessions SET revoked=1 WHERE id=?',(u['session_id'],))
    assert c.get(BASE+'/push/preferences').status_code == 401
    assert c.put(BASE+'/push/preferences',json=prefs).status_code == 401
    anonymous = TestClient(api.app,base_url=ORIGIN)
    assert anonymous.get(BASE+'/push/preferences').status_code == 401


def test_stale_preference_revision_cannot_overwrite_privacy(api):
    c = api.client
    old = c.get(BASE+'/push/preferences').json()
    assert old.get('revision') == 0
    private = {**old,'hide_details':True}
    saved = c.put(BASE+'/push/preferences',json=private)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    stale = {**old,'enabled':False}
    assert c.put(BASE+'/push/preferences',json=stale).status_code == 409
    assert c.get(BASE+'/push/preferences').json() == saved.json()
    for invalid in (-1, True, '1', 2**53):
        assert c.put(BASE+'/push/preferences',json={**saved.json(),'revision':invalid}).status_code == 400


def test_explicit_test_only_requests_this_device_not_all_user_devices(api):
    s, c, u = api.service, api.client, api.user
    with api.auth.store.transaction() as db:
        db.execute('''INSERT INTO sessions(id,token_hash,user_id,csrf_token,created_at,last_active_at,last_verified_at)
            VALUES(?,?,?,?,?,?,?)''',('second','otherhash',u['id'],'csrf',api.now[0],api.now[0],api.now[0]))
    s.subscribe(u['id'],u['session_id'],subscription('first'))
    s.subscribe(u['id'],'second',subscription('second'))
    prefs = c.get(BASE+'/push/preferences').json()
    prefs['categories'] = dict.fromkeys(prefs['categories'],False)
    assert c.put(BASE+'/push/preferences',json=prefs).status_code == 200
    response = c.post(BASE+'/push/test')
    assert response.status_code == 202
    assert s.flush()['sent'] == 1
    assert [x['subscription_info']['endpoint'] for x in api.sent] == [subscription('first')['endpoint']]
    prefs = c.get(BASE+'/push/preferences').json()
    assert c.put(BASE+'/push/preferences',json={**prefs,'enabled':False}).status_code == 200
    response = c.post(BASE+'/push/test')
    assert response.status_code == 409
    assert 'disabled' in response.json()['detail'].lower()
    assert s.flush()['sent'] == 0


def test_presence_route_csrf_live_authorization_without_inactivity_refresh(api):
    from test_push_policy_presence import presence
    c, s, u = api.client, api.service, api.user
    s.presence_validator = lambda user, sid: user == u['id'] and sid == 'chat'
    before = api.now[0]
    for seq in range(1,4):
        api.now[0] += 15
        assert c.post(BASE+'/push/presence',json=presence(sequence=seq)).status_code == 200
    with api.auth.store.transaction() as db:
        assert db.execute('SELECT last_active_at FROM sessions WHERE id=?',(u['session_id'],)).fetchone()[0] == before
    assert c.post(BASE+'/push/presence',json=presence(),headers={'X-CSRF-Token':'bad'}).status_code == 403
    assert c.post(BASE+'/push/presence',json=presence(),headers={'Origin':'https://evil.test'}).status_code == 403
    assert c.post(BASE+'/push/presence',json=presence(session='foreign')).status_code == 403
    assert c.post(BASE+'/push/presence',json=presence(visible='true')).status_code == 400
    api.app.dependency_overrides[api.auth.require_user] = lambda: u
    with api.auth.store.transaction() as db:
        db.execute('UPDATE sessions SET token_hash=? WHERE id=?',('rotated',u['session_id']))
    assert c.post(BASE+'/push/presence',json=presence(sequence=99)).status_code == 401
    with s._db() as db:
        assert db.execute('SELECT sequence FROM push_presence').fetchone()[0] == 3


def test_presence_revalidates_bearer_after_slow_ownership_callback(api):
    from test_push_policy_presence import presence
    def ownership(user, sid):
        with api.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET token_hash=? WHERE id=?',('rotated',api.user['session_id']))
        return True
    api.service.presence_validator = ownership
    assert api.client.post(BASE+'/push/presence',json=presence()).status_code == 401
    with api.service._db() as db:
        assert db.execute('SELECT count(*) FROM push_presence').fetchone()[0] == 0


def test_presence_and_flush_real_auth_lock_order_two_threads(api):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, current_thread
    from test_push_policy_presence import presence
    s, u = api.service, api.user
    s.subscribe(u['id'],u['session_id'],subscription())
    s.ingest(u['id'],'concurrent','Finished','Public result',category='completion')
    s.presence_validator = lambda *args: True
    entered, authorized, release = Event(), Event(), Event()
    real_validator = s.session_validator
    def device(user, device_id):
        if current_thread().name.startswith('policy-flush') and not entered.is_set():
            entered.set()
            assert release.wait(3)
        return real_validator(user, device_id)
    s.session_validator = device
    real_authorized = api.auth.authorized_transaction
    @contextmanager
    def observed(user, kind='full'):
        with real_authorized(user,kind) as db:
            authorized.set()
            yield db
    api.auth.authorized_transaction = observed
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='policy-flush') as pool:
        flush = pool.submit(s.flush)
        assert entered.wait(3)
        posted = pool.submit(api.client.post,BASE+'/push/presence',json=presence())
        try:
            assert authorized.wait(3)
        finally:
            release.set()
        assert posted.result(timeout=3).status_code == 200
        assert flush.result(timeout=3)['sent'] == 1
