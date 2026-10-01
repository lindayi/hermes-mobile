"""Assembly regression tests: explicit synthetic homes before application creation."""
from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
from backend.app import create_app, Settings
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db


def setup_app(tmp_path):
    home = tmp_path/'native'
    home.mkdir()
    create_native_db(home/'state.db')
    app = create_app(Settings(state_dir=tmp_path/'state', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP))
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    user = client.get(BASE+'/auth/me').json()['user']
    return app, client, user, home


def test_lifecycle_persists_authoritative_completion_without_phone(tmp_path):
    import threading
    app, client, user, home = setup_app(tmp_path)
    assert hasattr(app.state, 'request_notifications'), 'Request producer is not wired'
    assert hasattr(app.state, 'operational_notifications'), 'Operational monitor is not wired'
    journal = app.state.journal
    run, _ = journal.submit(user['id'], 'default', 'wa-1', 'PRIVATE input', 'request')
    journal.set_upstream(user['id'], run['id'], 'native-request')
    journal.finish(user['id'], run['id'], 'completed', output='Public persisted final')
    seen = threading.Event()
    original = app.state.request_notifications.tick
    def tick():
        original()
        seen.set()
    app.state.request_notifications.tick = tick
    with client:
        assert seen.wait(3)
        rows = app.state.notifications.list_inbox(user['id'])
        assert len(rows) == 1
        assert rows[0]['body'] == 'Public persisted final'


def test_optional_producer_and_approval_errors_cannot_starve_push(tmp_path):
    import threading
    app, client, user, home = setup_app(tmp_path)
    pushed = threading.Event()
    def fail():
        raise RuntimeError('PRIVATE producer failure')
    async def approval_fail():
        raise RuntimeError('PRIVATE approval failure')
    app.state.request_notifications.tick = fail
    app.state.operational_notifications.tick = fail
    app.state.orchestrator.reconcile_approval_notifications = approval_fail
    app.state.notifications.flush = lambda: pushed.set()
    with client:
        assert pushed.wait(3), 'Approval or producer error starved the push worker'
        assert app.state.request_notification_error is True


def test_completion_routes_to_current_compression_tip_and_alias_tombstone_fences(tmp_path):
    app, client, user, home = setup_app(tmp_path)
    with sqlite3.connect(home/'state.db') as db:
        db.execute('ALTER TABLE sessions ADD COLUMN end_reason TEXT')
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='wa-1'")
        db.execute("INSERT INTO sessions VALUES('tip','Public chat','whatsapp',3,4,'wa-1',NULL)")
    journal = app.state.journal
    run, _ = journal.submit(user['id'], 'default', 'wa-1', 'PRIVATE', 'compressed')
    journal.finish(user['id'], run['id'], 'completed', output='Public final')
    app.state.request_notifications.tick()
    rows = app.state.notifications.list_inbox(user['id'])
    assert len(rows) == 1
    assert rows[0]['session_id'] == 'tip'
    assert app.state.notifications.presence_validator(user['id'], 'tip')
    # A live view may retain the original ID after compression.
    assert app.state.notifications.presence_validator(user['id'], 'wa-1')
    assert app.state.notifications.event_validator(user['id'], 'default', 'wa-1', 'approval')
    with closing(journal.connect()) as db, db:
        db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                   (user['id'], 'default', 'wa-1', 'op', 'deleted', 100))
    assert not app.state.notifications.presence_validator(user['id'], 'tip')
    assert not app.state.notifications.event_validator(user['id'], 'default', 'tip', 'completion')


def test_monitor_uses_explicit_private_deployment_path_and_unavailable_is_unobserved(tmp_path):
    import threading
    app, client, user, home = setup_app(tmp_path)
    monitor = app.state.operational_notifications
    assert monitor.deployment_status_path == tmp_path/'hermes-mobile-deploy'/'status.json'
    seen = threading.Event()
    original = monitor.tick
    def tick():
        original()
        seen.set()
    monitor.tick = tick
    with client:
        assert seen.wait(3)
        assert app.state.background_worker_error is None


def test_persisted_canonical_run_alias_routes_and_fences_absent_original(tmp_path):
    app, client, user, home = setup_app(tmp_path)
    journal = app.state.journal
    run, _ = journal.submit(user['id'], 'default', 'old-alias', 'PRIVATE', 'anchored',
        history_anchor=lambda: {'session_id': 'old-alias', 'canonical_session_id': 'wa-1', 'message_id': 1})
    journal.finish(user['id'], run['id'], 'completed', output='Public final')
    app.state.request_notifications.tick()
    rows = app.state.notifications.list_inbox(user['id'])
    assert len(rows) == 1
    assert rows[0]['session_id'] == 'wa-1'
    with closing(journal.connect()) as db, db:
        db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                   (user['id'], 'default', 'old-alias', 'op', 'deleted', 100))
    assert not app.state.notifications.presence_validator(user['id'], 'wa-1')
    assert not app.state.notifications.event_validator(user['id'], 'default', 'wa-1', 'completion')


def test_live_presence_survives_compression_but_unrelated_view_does_not_suppress(tmp_path):
    from test_notifications import subscription
    from types import SimpleNamespace
    app, client, user, home = setup_app(tmp_path)
    notifications = app.state.notifications
    assert callable(getattr(notifications, 'presence_resolver', None)), 'Canonical presence resolver is not wired'
    with app.state.auth.store.transaction() as db:
        device = db.execute("SELECT id FROM sessions WHERE user_id=? AND kind='full' AND revoked=0", (user['id'],)).fetchone()[0]
    notifications.vapid_private_key = 'synthetic-private'
    notifications.vapid_public_key = 'synthetic-public'
    notifications.send_push = lambda **kwargs: SimpleNamespace(status_code=201)
    notifications.subscribe(user['id'],device,subscription())
    notifications.record_presence(user['id'],device,
        {'client_id': 'tab', 'session_id': 'wa-1', 'visible': True, 'sequence': 1})
    with sqlite3.connect(home/'state.db') as db:
        db.execute('ALTER TABLE sessions ADD COLUMN end_reason TEXT')
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='wa-1'")
        db.execute("INSERT INTO sessions VALUES('tip','Public chat','whatsapp',3,4,'wa-1',NULL)")
    assert notifications.presence_resolver(user['id'],'wa-1') == 'tip'
    journal = app.state.journal
    run, _ = journal.submit(user['id'],'default','wa-1','PRIVATE','first')
    journal.finish(user['id'],run['id'],'completed',output='Public answer')
    app.state.request_notifications.tick()
    assert notifications.flush()['sent'] == 0
    notifications.record_presence(user['id'],device,
        {'client_id': 'tab', 'session_id': 'cli-1', 'visible': True, 'sequence': 2})
    run, _ = journal.submit(user['id'],'default','wa-1','PRIVATE','second')
    journal.finish(user['id'],run['id'],'completed',output='Another public answer')
    app.state.request_notifications.tick()
    assert notifications.flush()['sent'] == 1


def test_unknown_retry_rechecks_exact_source_run_before_send(tmp_path):
    import time
    from test_notifications import subscription
    from types import SimpleNamespace
    app, client, user, home = setup_app(tmp_path)
    notifications = app.state.notifications
    assert callable(getattr(notifications, 'request_validator', None)), 'Request source validator is not wired'
    with app.state.auth.store.transaction() as db:
        device = db.execute("SELECT id FROM sessions WHERE user_id=? AND kind='full' AND revoked=0", (user['id'],)).fetchone()[0]
    notifications.vapid_private_key = 'synthetic-private'
    notifications.vapid_public_key = 'synthetic-public'
    notifications.subscribe(user['id'], device, subscription())
    now = [time.time()+121]
    notifications.clock = lambda: now[0]
    app.state.request_notifications.clock = lambda: now[0]
    journal = app.state.journal
    for transition in ('running','completed','stopped'):
        run, _ = journal.submit(user['id'],'default','wa-1','PRIVATE',transition)
        journal.finish(user['id'],run['id'],'unknown')
        app.state.request_notifications.tick()
        notifications.send_push = lambda **kwargs: SimpleNamespace(status_code=503)
        assert notifications.flush()['failed'] == 1
        if transition == 'running':
            journal.set_upstream(user['id'],run['id'],'native')
        elif transition == 'stopped':
            journal.finish(user['id'],run['id'],'stopping')
            journal.finish(user['id'],run['id'],'completed',output='Stopped race final')
        else:
            journal.finish(user['id'],run['id'],'completed',output='Public final')
        now[0] += 120
        notifications.send_push = lambda **kwargs: SimpleNamespace(status_code=201)
        assert notifications.flush()['sent'] == 0, 'Obsolete unknown retry must not send before producer next tick'
        if transition == 'running':
            journal.finish(user['id'],run['id'],'cancelled')
        app.state.request_notifications.tick()
        assert notifications.flush()['sent'] == (1 if transition == 'completed' else 0)


def test_presence_and_final_send_validate_fresh_owned_eligible_session(tmp_path):
    app, client, user, home = setup_app(tmp_path)
    notifications = app.state.notifications
    assert callable(getattr(notifications, 'presence_validator', None)), 'Presence validator is not wired'
    assert callable(getattr(notifications, 'event_validator', None)), 'Final delivery validator is not wired'
    uid = user['id']
    assert notifications.presence_validator(uid, 'wa-1')
    assert not notifications.presence_validator('foreign', 'wa-1')
    assert not notifications.presence_validator(uid, 'missing')
    with sqlite3.connect(home/'state.db') as db:
        db.execute("INSERT INTO sessions VALUES('hidden','private','subagent',1,2,'wa-1')")
    assert not notifications.presence_validator(uid, 'hidden')
    assert notifications.event_validator(uid, 'default', 'wa-1', 'completion')
    assert not notifications.event_validator(uid, 'foreign', 'wa-1', 'completion')
    assert notifications.event_validator(uid, 'default', None, 'operational')
    with closing(app.state.journal.connect()) as db, db:
        db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                   (uid, 'default', 'wa-1', 'operation', 'prepared', 100))
    assert not notifications.presence_validator(uid, 'wa-1')
    assert not notifications.event_validator(uid, 'default', 'wa-1', 'completion')
    with app.state.auth.store.transaction() as db:
        db.execute("UPDATE users SET status='pending' WHERE id=?", (uid,))
    assert not notifications.presence_validator(uid, 'cli-1')
    assert not notifications.event_validator(uid, 'default', None, 'operational')
