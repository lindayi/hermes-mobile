"""Parent-owned end-to-end acceptance; synthetic app/auth DBs, no live services."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from backend.app import Settings, create_app
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db
from test_notifications import subscription


def setup_app(tmp_path):
    home = tmp_path / 'native'
    home.mkdir()
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home}, bootstrap_secret=BOOTSTRAP))
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    user = client.get(BASE + '/auth/me').json()['user']
    return app, client, user


def test_user_can_configure_independent_push_categories_without_losing_inbox(tmp_path):
    app, client, user = setup_app(tmp_path)
    response = client.get(BASE + '/push/preferences')
    assert response.status_code == 200
    original = response.json()
    assert original == {'revision': 0, 'enabled': True, 'hide_details': False, 'categories': {
        'completion': True, 'approval': True, 'attention': True, 'scheduled': True, 'operational': True}}
    changed = {**original, 'categories': {**original['categories'], 'scheduled': False}}
    response = client.put(BASE + '/push/preferences', json=changed)
    assert response.status_code == 200
    assert client.get(BASE + '/push/preferences').json() == {**changed, 'revision': 1}
    with app.state.auth.store.transaction() as db:
        device = db.execute('SELECT id FROM sessions WHERE user_id=? AND revoked=0', (user['id'],)).fetchone()[0]
    service = app.state.notifications
    service.subscribe(user['id'], device, subscription())
    sent = []
    service.vapid_private_key = 'synthetic'
    service.vapid_public_key = 'synthetic'
    service.send_push = lambda **kwargs: (sent.append(json.loads(kwargs['data'])) or SimpleNamespace(status_code=201))
    event = service.ingest(user['id'], 'scheduled-real-contract', 'Invoice ready', 'Review your invoice.', category='scheduled')
    assert service.flush()['sent'] == 0
    assert sent == []
    assert any(item['id'] == event['id'] for item in client.get(BASE + '/inbox').json()['items'])
    assert client.put(BASE + '/push/preferences', json={**original, 'revision': 1}).status_code == 200
    assert service.flush()['sent'] == 0, 'enabling a category must not replay previously suppressed events'
    assert sent == []


def test_master_off_never_claims_test_notification_was_queued(tmp_path):
    app, client, user = setup_app(tmp_path)
    response = client.get(BASE + '/push/preferences')
    assert response.status_code == 200
    prefs = response.json()
    assert client.put(BASE + '/push/preferences', json={**prefs, 'enabled': False}).status_code == 200
    service = app.state.notifications
    service.vapid_private_key = service.vapid_public_key = 'synthetic'
    with app.state.auth.store.transaction() as db:
        device = db.execute('SELECT id FROM sessions WHERE user_id=? AND revoked=0', (user['id'],)).fetchone()[0]
    service.subscribe(user['id'], device, subscription())
    response = client.post(BASE + '/push/test', json={})
    assert response.status_code == 409
    assert 'disabled' in response.json()['detail'].lower()
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_stale_settings_cannot_reenable_master_or_reveal_details(tmp_path):
    _, client, _ = setup_app(tmp_path)
    stale = client.get(BASE + '/push/preferences').json()
    fresh = client.put(BASE + '/push/preferences', json={**stale, 'enabled': False, 'hide_details': True})
    assert fresh.status_code == 200
    stale_change = {**stale, 'categories': {**stale['categories'], 'scheduled': False}}
    assert client.put(BASE + '/push/preferences', json=stale_change).status_code == 409
    actual = client.get(BASE + '/push/preferences').json()
    assert actual['enabled'] is False
    assert actual['hide_details'] is True
    assert actual == fresh.json()
