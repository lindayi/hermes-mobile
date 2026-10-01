"""Deterministic local incident monitoring with real persisted SQLite state."""
import importlib.util

from backend.notifications import NotificationService

GIB = 1024**3


def setup_monitor(tmp_path, now, free):
    assert importlib.util.find_spec('backend.operational_notifications'), 'Operational producer is missing'
    from backend.operational_notifications import OperationalNotificationService
    notifications = NotificationService(tmp_path/'notifications.db', clock=lambda: now[0])
    owner = {'id': 'u', 'profile': 'default', 'role': 'owner', 'status': 'ready'}
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0])
    return monitor, notifications, owner


def test_disk_incident_restart_hysteresis_and_single_recovery(tmp_path):
    now, free = [100.0], [GIB-1]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    monitor.tick()
    now[0] = 220
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1
    assert notifications.list_inbox('u')[0]['title'] == 'Server storage critically low'
    from backend.operational_notifications import OperationalNotificationService
    restarted = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0])
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 1
    free[0] = 2*GIB
    now[0] = 500
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 1
    free[0] += 1
    restarted.tick()
    now[0] = 619
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 1
    now[0] = 620
    restarted.tick()
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 2
    assert notifications.list_inbox('u')[0]['title'] == 'Server storage recovered'


def test_push_transport_failure_threshold_excludes_own_operational_retries(tmp_path):
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    item = notifications.ingest('u','request:example:terminal','Finished','Public', category='completion', profile='default')
    with notifications._db() as db:
        db.execute("INSERT INTO subscriptions VALUES('endpoint','u','device','{}')")
        db.execute("INSERT INTO outbox VALUES('retry',?,'endpoint','policy_retry',1,100,10000,'transport_error')", (item['id'],))
    monitor.tick()
    now[0] = 399
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1
    now[0] = 400
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 2
    assert notifications.list_inbox('u')[0]['title'] == 'Push delivery interrupted'
    with notifications._db() as db:
        db.execute("UPDATE outbox SET status='sent',last_error=NULL")
        operational = db.execute("SELECT inbox_id FROM notification_policy WHERE category='operational'").fetchone()[0]
        db.execute("UPDATE outbox SET status='policy_retry',last_error='provider_error' WHERE inbox_id=?", (operational,))
    now[0] = 401
    monitor.tick()
    assert notifications.list_inbox('u')[0]['title'] == 'Push delivery recovered'
    now[0] = 1000
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 3


def test_existing_worker_observations_sustain_recover_without_optional_absence_alarm(tmp_path):
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    from backend.operational_notifications import OperationalNotificationService
    flags = {'push': False, 'background': False}
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0],
        push_worker_error=lambda: flags['push'], background_worker_error=lambda: flags['background'])
    monitor.tick()
    now[0] = 1000
    monitor.tick()
    assert notifications.list_inbox('u') == []
    flags.update(push=True, background=True)
    monitor.tick()
    now[0] = 1299
    monitor.tick()
    assert notifications.list_inbox('u') == []
    now[0] = 1300
    monitor.tick()
    assert {i['title'] for i in notifications.list_inbox('u')} == {
        'Push delivery interrupted', 'Background result delivery interrupted'}
    flags.update(push=False, background=False)
    now[0] = 1301
    # No exception alone is not delivery health: supply an actual owned success.
    from test_notifications import subscription
    from types import SimpleNamespace
    notifications.session_validator = lambda u, d: True
    notifications.vapid_private_key = notifications.vapid_public_key = 'synthetic'
    notifications.subscribe('u', 'device', subscription())
    sent = []
    notifications.send_push = lambda **kw: (sent.append(kw) or SimpleNamespace(status_code=201))
    probe = notifications.ingest('u', 'fixture:healthy', 'Public', 'Public', category='scheduled', profile='default')
    assert notifications.flush()['sent'] == 1
    assert len(sent) == 1
    notifications.mark_read('u', probe['id'])
    notifications.dismiss('u', probe['id'])  # Keep the original incident-only Inbox assertions.
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 4
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 4


def test_deployment_status_baseline_debounce_rollback_and_recovery(tmp_path):
    import json
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    path = tmp_path/'status.json'
    def status(state, release='release-a'):
        path.write_text(json.dumps({'status': state, 'release': release, 'error': 'PRIVATE traceback token=secret'}))
    status('rollback_failed', 'historical')
    from backend.operational_notifications import OperationalNotificationService
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], deployment_status_path=path)
    monitor.tick()
    now[0] = 1000
    monitor.tick()
    assert notifications.list_inbox('u') == []
    status('failed')
    monitor.tick()
    now[0] = 1299
    monitor.tick()
    assert notifications.list_inbox('u') == []
    status('succeeded')
    monitor.tick()
    assert notifications.list_inbox('u') == []
    status('rolled_back', 'release-b')
    now[0] = 1300
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1
    assert notifications.list_inbox('u')[0]['title'] == 'Deployment needs attention'
    restarted = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], deployment_status_path=path)
    status('rollback_failed', 'release-b')
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 1
    status('succeeded', 'release-c')
    now[0] = 1301
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 2
    assert notifications.list_inbox('u')[0]['title'] == 'Deployment recovered'
    assert 'PRIVATE' not in str(notifications.list_inbox('u'))


def test_deployment_source_rejects_symlink_oversize_and_unsafe_release(tmp_path):
    import json
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    from backend.operational_notifications import OperationalNotificationService
    path = tmp_path/'status.json'
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], deployment_status_path=path)
    target = tmp_path/'target.json'
    target.write_text(json.dumps({'status': 'rolled_back', 'release': 'b'}))
    path.symlink_to(target)
    monitor.tick()
    assert notifications.list_inbox('u') == []
    path.unlink()
    path.write_text(json.dumps({'status': 'rolled_back', 'release': 'b', 'error': 'x'*65536}))
    monitor.tick()
    assert notifications.list_inbox('u') == []
    path.write_text(json.dumps({'status': 'rolled_back', 'release': 'token=secret\nPRIVATE'}))
    monitor.tick()
    assert notifications.list_inbox('u') == []


def test_unobserved_optional_background_capability_is_not_a_recovery(tmp_path):
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    from backend.operational_notifications import OperationalNotificationService
    error = [True]
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], background_worker_error=lambda: error[0])
    monitor.tick()
    now[0] = 400
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1
    error[0] = None
    now[0] = 401
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1
    error[0] = False
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 2


def test_recovery_push_requires_preceding_incident_actually_sent(tmp_path):
    from test_notifications import subscription
    from types import SimpleNamespace
    now, free = [100.0], [GIB-1]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    notifications.session_validator = lambda u,d: True
    notifications.event_validator = lambda u,p,s,c: True
    notifications.vapid_private_key = 'synthetic-private'
    notifications.vapid_public_key = 'synthetic-public'
    sent = []
    notifications.send_push = lambda **kwargs: (sent.append(kwargs) or SimpleNamespace(status_code=201))
    notifications.subscribe('u','device',subscription())
    preferences = notifications.get_preferences('u','device')
    preferences['categories']['operational'] = False
    notifications.set_preferences('u','device',preferences)
    monitor.tick()
    now[0] = 220
    monitor.tick()
    assert notifications.flush()['sent'] == 0
    preferences = notifications.get_preferences('u','device')
    preferences['categories']['operational'] = True
    notifications.set_preferences('u','device',preferences)
    free[0] = 3*GIB
    now[0] = 221
    monitor.tick()
    now[0] = 341
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 2
    assert notifications.flush()['sent'] == 0, 'Never push a recovery for an incident suppressed on all devices'
    free[0] = GIB-1
    now[0] = 400
    monitor.tick()
    now[0] = 520
    monitor.tick()
    assert notifications.flush()['sent'] == 1
    free[0] = 3*GIB
    now[0] = 521
    monitor.tick()
    now[0] = 641
    monitor.tick()
    assert notifications.flush()['sent'] == 1
    assert len(sent) == 2


def test_push_worker_failure_with_only_operational_work_does_not_self_alert(tmp_path):
    now, free = [100.0], [3*GIB]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    item = notifications.ingest('u','operational:disk:example','Storage','Generic',category='operational',profile='default')
    with notifications._db() as db:
        db.execute("INSERT INTO subscriptions VALUES('endpoint','u','device','{}')")
        db.execute("INSERT INTO outbox VALUES('self',?,'endpoint','policy_retry',1,100,10000,'transport_error')", (item['id'],))
    from backend.operational_notifications import OperationalNotificationService
    monitor = OperationalNotificationService(notifications, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], push_worker_error=lambda: True)
    monitor.tick()
    now[0] = 400
    monitor.tick()
    assert len(notifications.list_inbox('u')) == 1


def test_confirmed_recovery_retires_unsent_incident_retry(tmp_path):
    from test_notifications import subscription
    from types import SimpleNamespace
    now, free = [100.0], [GIB-1]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    notifications.session_validator = lambda u,d: True
    notifications.event_validator = lambda u,p,s,c: True
    notifications.vapid_private_key = 'synthetic-private'
    notifications.vapid_public_key = 'synthetic-public'
    notifications.subscribe('u','device',subscription())
    notifications.send_push = lambda **kwargs: SimpleNamespace(status_code=503)
    monitor.tick()
    now[0] = 220
    monitor.tick()
    assert notifications.flush()['failed'] == 1
    free[0] = 3*GIB
    now[0] = 221
    monitor.tick()
    now[0] = 341
    monitor.tick()
    notifications.send_push = lambda **kwargs: SimpleNamespace(status_code=201)
    assert notifications.flush()['sent'] == 0, 'Healthy recovery must retire an obsolete incident retry'
    assert len(notifications.list_inbox('u')) == 2


def test_disk_observation_is_durable_but_short_dips_are_silent(tmp_path):
    now, free = [100.0], [GIB-1]
    monitor, notifications, owner = setup_monitor(tmp_path, now, free)
    monitor.tick()
    with notifications._db() as db:
        row = db.execute("SELECT * FROM operational_notification_state WHERE kind='disk'").fetchone()
        assert row['bad_since'] == 100
        assert row['incident_id'] is None
    now[0] = 219
    monitor.tick()
    free[0] = 3*GIB
    now[0] = 220
    monitor.tick()
    assert notifications.list_inbox('u') == []
    with notifications._db() as db:
        assert db.execute("SELECT bad_since FROM operational_notification_state WHERE kind='disk'").fetchone()[0] is None
