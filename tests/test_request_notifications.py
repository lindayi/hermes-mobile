"""Local synthetic journals only; no native clients or history reads."""
from contextlib import closing
import importlib.util

from backend.notifications import NotificationService
from backend.runs import RunJournal


def setup_service(tmp_path, now):
    assert importlib.util.find_spec('backend.request_notifications'), 'Request producer is missing'
    from backend.request_notifications import RequestNotificationService
    notifications = NotificationService(tmp_path/'notifications.db', clock=lambda: now[0])
    journal = RunJournal(tmp_path/'runs.db')
    users = {'u': {'id': 'u', 'profile': 'default', 'status': 'ready'}}
    service = RequestNotificationService(notifications, journal, resolve_user=users.get,
        session_validator=lambda user, sid: sid != 'hidden', clock=lambda: now[0])
    return service, notifications, journal, users


def insert_run(journal, rid, *, status='completed', updated=101, output='Public final', session='chat', profile='default'):
    with closing(journal.connect()) as db, db:
        db.execute('''INSERT INTO runs(id,user_id,profile,session_id,input,idempotency_key,status,
            output,error,upstream_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (rid, 'u', profile, session, 'PRIVATE INPUT', rid, status, output,
             'PRIVATE ERROR token=private', 'native-'+rid, updated-1, updated))


def test_public_completion_once_across_restart_without_old_history(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    insert_run(journal, 'old', updated=99)
    insert_run(journal, 'new', output='The report is ready. Which format would you like?')
    now[0] = 102
    service.tick()
    from backend.request_notifications import RequestNotificationService
    restarted = RequestNotificationService(notifications, journal, resolve_user=users.get,
        session_validator=lambda u,s: True, clock=lambda: now[0])
    restarted.tick()
    items = notifications.list_inbox('u')
    assert len(items) == 1
    assert items[0]['body'] == 'The report is ready. Which format would you like?'
    with notifications._db() as db:
        assert db.execute('SELECT delivery_id FROM inbox').fetchone()[0] == 'request:new:terminal'
    assert 'PRIVATE' not in str(items)


def test_failure_uses_generic_reason_not_partial_output_or_internal_error(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    insert_run(journal, 'failed', status='failed', output='PRIVATE partial tool result')
    now[0] = 102
    service.tick()
    items = notifications.list_inbox('u')
    assert len(items) == 1
    assert items[0]['title'] == 'Request needs attention'
    assert items[0]['body'] == 'Your request could not finish. Open Hermes to review its status.'
    assert 'PRIVATE' not in str(items)


def test_unknown_threshold_survives_restart_and_resolves_once(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    insert_run(journal, 'unknown', status='unknown', output='PRIVATE partial')
    now[0] = 220
    service.tick()
    assert notifications.list_inbox('u') == []
    now[0] = 221
    service.tick()
    items = notifications.list_inbox('u')
    assert len(items) == 1
    assert 'unresolved' in items[0]['body']
    assert 'PRIVATE' not in str(items)
    from backend.request_notifications import RequestNotificationService
    restarted = RequestNotificationService(notifications, journal, resolve_user=users.get,
        session_validator=lambda u,s: True, clock=lambda: now[0])
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 1
    journal.finish('u', 'unknown', 'completed', output='The answer is ready.')
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 2
    restarted.tick()
    assert len(notifications.list_inbox('u')) == 2


def test_bounded_scan_progresses_past_first_hundred_after_restart(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    for i in range(205):
        insert_run(journal, str(i))
    now[0] = 102
    service.tick()
    assert len(notifications.list_inbox('u', limit=200)) == 100
    from backend.request_notifications import RequestNotificationService
    restarted = RequestNotificationService(notifications, journal, resolve_user=users.get,
        session_validator=lambda u,s: True, clock=lambda: now[0])
    restarted.tick()
    restarted.tick()
    with notifications._db() as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 205


def test_reconciliation_timestamp_churn_does_not_reset_unknown_duration(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    insert_run(journal, 'uncertain', status='unknown')
    now[0] = 102
    service.tick()
    with closing(journal.connect()) as db, db:
        db.execute("UPDATE runs SET updated_at=200 WHERE id='uncertain'")
    from backend.request_notifications import RequestNotificationService
    service = RequestNotificationService(notifications, journal, resolve_user=users.get,
        session_validator=lambda u,s: True, clock=lambda: now[0])
    now[0] = 221
    service.tick()
    assert len(notifications.list_inbox('u')) == 1


def test_cancel_stop_intent_hidden_unready_and_rebound_runs_are_silent(tmp_path):
    now = [100.0]
    service, notifications, journal, users = setup_service(tmp_path, now)
    insert_run(journal, 'cancelled', status='cancelled')
    insert_run(journal, 'stopped-completion')
    insert_run(journal, 'hidden', session='hidden')
    insert_run(journal, 'rebound', profile='foreign')
    insert_run(journal, 'deleted', session='deleted')
    with closing(journal.connect()) as db, db:
        db.execute('INSERT INTO run_stop_intents VALUES(?)', ('stopped-completion',))
        db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                   ('u', 'default', 'deleted', 'operation', 'prepared', 100))
    now[0] = 102
    service.tick()
    assert notifications.list_inbox('u') == []
    insert_run(journal, 'unready')
    users.clear()
    service.tick()
    assert notifications.list_inbox('u') == []
