"""Review lifecycle regressions: isolated SQLite and a captured fake provider only."""
import json
from types import SimpleNamespace

import pytest

from backend.operational_notifications import OperationalNotificationService
from test_operational_notifications import setup_monitor, GIB
from test_notifications import subscription


def monitor_with_device(tmp_path):
    now, free = [100.], [3*GIB]
    _, notifications, owner = setup_monitor(tmp_path, now, free)
    notifications.session_validator = lambda u, d: True
    notifications.vapid_private_key = notifications.vapid_public_key = 'synthetic'
    notifications.subscribe('u', 'device', subscription())
    return notifications, owner, now, free


def monitor(n, owner, now, free, **kwargs):
    return OperationalNotificationService(n, owner=lambda: owner,
        disk_free=lambda: free[0], clock=lambda: now[0], **kwargs)


def push_state(n):
    with n._db() as db:
        return dict(db.execute("SELECT * FROM operational_notification_state WHERE kind='push'").fetchone())


@pytest.mark.parametrize('lost', ['expired', 'pruned', 'removed', 'suppressed'])
def test_failed_work_disappearance_requires_positive_owned_delivery(tmp_path, lost):
    n, owner, now, free = monitor_with_device(tmp_path)
    m = monitor(n, owner, now, free, push_worker_error=lambda: False)
    original = n.ingest('u', 'cron:one', 'Scheduled', 'Public', category='scheduled', profile='default')
    sent = []
    def transport(**kw):
        payload = json.loads(kw['data'])
        if payload['inbox_id'] == original['id']:
            return SimpleNamespace(status_code=503)
        sent.append(payload)
        return SimpleNamespace(status_code=201)
    n.send_push = transport
    assert n.flush()['failed'] == 1
    m.tick()
    now[0] = 400
    m.tick()
    assert n.flush()['sent'] == 1
    assert len(sent) == 1
    incident = push_state(n)['incident_id']
    if lost == 'pruned':
        n.unsubscribe('u', 'device', subscription()['endpoint'])
    else:
        with n._db() as db:
            if lost == 'expired':
                db.execute('UPDATE outbox SET expires_at=401 WHERE inbox_id=?', (original['id'],))
            elif lost == 'removed':
                db.execute('DELETE FROM outbox WHERE inbox_id=?', (original['id'],))
            else:
                db.execute("UPDATE outbox SET status='suppressed' WHERE inbox_id=?", (original['id'],))
    for tick in (401, 702, 1000):
        now[0] = tick
        assert n.flush()['sent'] == 0
        m = monitor(n, owner, now, free, push_worker_error=lambda: False)
        m.tick()
        assert 'Push delivery recovered' not in [i['title'] for i in n.list_inbox('u')]
        assert push_state(n)['incident_id'] == incident


def test_nonraising_empty_or_unavailable_flush_is_unknown_in_app(tmp_path):
    import threading
    from test_push_wiring import setup_app
    app, client, user, home = setup_app(tmp_path)
    seen = threading.Event()
    observations = []
    def tick():
        observations.append(app.state.operational_notifications.push_worker_error())
        seen.set()
    app.state.operational_notifications.tick = tick
    with client:
        assert seen.wait(3)
        assert observations == [None]
        assert app.state.push_transport_error is None


@pytest.mark.parametrize('incident_sent', [False, True])
def test_only_fresh_owned_nonoperational_success_can_confirm_recovery(tmp_path, incident_sent):
    n, owner, now, free = monitor_with_device(tmp_path)
    error = [True]
    m = monitor(n, owner, now, free, push_worker_error=lambda: error[0])
    sent = []
    n.send_push = lambda **kw: (sent.append(json.loads(kw['data'])) or SimpleNamespace(status_code=201))
    # Success before this episode cannot prove recovery later.
    n.ingest('u', 'old:success', 'Earlier', 'Public', category='scheduled', profile='default')
    assert n.flush()['sent'] == 1
    m.tick()
    now[0] = 400
    m.tick()
    if incident_sent:
        assert n.flush()['sent'] == 1
    error[0] = False
    now[0] = 401
    m.tick()
    assert 'Push delivery recovered' not in [i['title'] for i in n.list_inbox('u')]
    foreign = subscription()
    foreign['endpoint'] += '/foreign'
    n.subscribe('foreign', 'foreign-device', foreign)
    n.ingest('foreign', 'foreign:success', 'Foreign', 'Public', category='scheduled', profile='default')
    # Leave the unclaimed owner incident untouched while delivering foreign work.
    with n._db() as db:
        db.execute("UPDATE outbox SET next_attempt_at=10000 WHERE inbox_id IN (SELECT inbox_id FROM notification_policy WHERE category='operational')")
    assert n.flush()['sent'] == 1
    m.tick()
    assert 'Push delivery recovered' not in [i['title'] for i in n.list_inbox('u')]
    item = n.ingest('u', 'fresh:success', 'Fresh', 'Public', category='scheduled', profile='default')
    assert n.flush()['sent'] == 1
    assert sent[-1]['inbox_id'] == item['id']
    # Positive evidence survives both monitor restart and successful row pruning.
    with n._db() as db:
        db.execute('DELETE FROM outbox WHERE inbox_id=?', (item['id'],))
    m = monitor(n, owner, now, free, push_worker_error=lambda: error[0])
    m.tick()
    assert [i['title'] for i in n.list_inbox('u')].count('Push delivery recovered') == 1
    assert n.flush()['sent'] == int(incident_sent)
    now[0] = 12000
    m.tick()
    assert n.flush()['sent'] == 0
    assert [i['title'] for i in n.list_inbox('u')].count('Push delivery recovered') == 1


@pytest.mark.parametrize('failure', ['failed', 'rolled_back', 'rollback_failed'])
def test_running_at_activation_failure_before_first_tick_is_fresh(tmp_path, failure):
    n, owner, now, free = monitor_with_device(tmp_path)
    path = tmp_path/'deployment.json'
    path.write_text(json.dumps({'status': 'running', 'release': 'fresh'}))
    m = monitor(n, owner, now, free, deployment_status_path=path)
    path.write_text(json.dumps({'status': failure, 'release': 'fresh'}))
    now[0] = 101
    m.tick()
    if failure == 'failed':
        assert n.list_inbox('u') == []
        now[0] = 400
        m = monitor(n, owner, now, free, deployment_status_path=path)
        m.tick()
        assert n.list_inbox('u') == []
        now[0] = 401
        m.tick()
    assert [i['title'] for i in n.list_inbox('u')] == ['Deployment needs attention']
    now[0] = 1000
    m = monitor(n, owner, now, free, deployment_status_path=path)
    m.tick()
    assert [i['title'] for i in n.list_inbox('u')] == ['Deployment needs attention']


def test_continuous_worker_failure_keeps_one_unsent_incident_across_restarts(tmp_path):
    n, owner, now, free = monitor_with_device(tmp_path)
    m = monitor(n, owner, now, free, push_worker_error=lambda: True)
    m.tick()
    now[0] = 400
    m.tick()
    original = push_state(n)
    assert [i['title'] for i in n.list_inbox('u')] == ['Push delivery interrupted']
    for tick in [401, 402, 702, 703, 1000, 1400, 2000, 3000]:
        now[0] = tick
        m = monitor(n, owner, now, free, push_worker_error=lambda: True)
        m.tick()
        assert [i['title'] for i in n.list_inbox('u')] == ['Push delivery interrupted']
        assert push_state(n)['incident_id'] == original['incident_id']
        assert push_state(n)['bad_since'] == 100
        with n._db() as db:
            assert db.execute("SELECT count(*) FROM outbox WHERE last_error='operational_recovered'").fetchone()[0] == 0
