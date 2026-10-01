"""Approval ingress -> real journal intent -> real owned Inbox/outbox."""
import json
import time

from test_steering import environment
from test_auth import BASE
from test_notifications import subscription


def test_new_approval_creates_owned_deduplicated_notification(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,calls):
        notification=app.state.notifications
        with app.state.auth.store.transaction() as db:
            device=db.execute('SELECT id FROM sessions WHERE user_id=?',(user['id'],)).fetchone()[0]
        notification.subscribe(user['id'],device,subscription())
        runtime=app.state.orchestrator
        event={'event':'approval.request','request_id':'native-action','run_id':'native-run','command':'do not expose full command'}
        runtime._approval(user,runtime.get(user,rid),event)
        runtime._approval(user,runtime.get(user,rid),event)
        items=notification.list_inbox(user['id'])
        assert len(items)==1, 'Approval journal has no Inbox notification wiring'
        item=items[0]
        assert item['run_id']==rid and item['request_id']=='native-action'
        assert item['approval_id']==runtime.approvals(user)['items'][0]['id']
        assert 'do not expose' not in json.dumps(item)
        assert notification.list_inbox('stranger')==[]
        with notification._db() as db:
            assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==1
        with app.state.journal.connect() as db:
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]=='sent'
        assert not calls


def test_cross_database_failure_leaves_retryable_intent_not_historical_sweep(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        original=runtime.approval_notifier
        def failure(*args):
            original(*args)  # Simulate process loss after notification commit, before intent ACK.
            raise RuntimeError('isolated cross-db gap')
        runtime.approval_notifier=failure
        event={'event':'approval.request','request_id':'new-action','run_id':'native-run'}
        runtime._approval(user,runtime.get(user,rid),event)
        with app.state.journal.connect() as db:
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]=='pending'
            db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                       ('historical',rid,'historical-request','{}','pending',time.time()+60))
        runtime.approval_notifier=original
        runtime.drain_approval_notifications()
        assert len(app.state.notifications.list_inbox(user['id']))==1
        with app.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0]==1
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]=='sent'


def test_resolved_and_unowned_approval_validation_fails_closed(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{'event':'approval.request','request_id':'action','run_id':'native-run'})
        item=app.state.notifications.list_inbox(user['id'])[0]
        validator=app.state.notifications.approval_validator
        identity=(user['id'],rid,'action',item['approval_id'])
        assert validator(*identity)
        assert not validator('stranger',*identity[1:])
        assert not validator(user['id'],rid,'other',item['approval_id'])
        app.state.journal.finish(user['id'],rid,'completed')
        assert not validator(*identity)


def test_existing_worker_retries_pending_intents_without_browser(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        original=runtime.approval_notifier
        runtime.approval_notifier=lambda *args: (_ for _ in ()).throw(RuntimeError('temporary'))
        runtime._approval(user,runtime.get(user,rid),{'event':'approval.request','request_id':'retry','run_id':'native-run'})
        assert app.state.notifications.list_inbox(user['id'])==[]
        runtime.approval_notifier=original
        deadline=time.monotonic()+18
        while time.monotonic()<deadline and not app.state.notifications.list_inbox(user['id']):
            time.sleep(.02)
        assert len(app.state.notifications.list_inbox(user['id']))==1


def test_mismatched_native_run_approval_never_creates_notification_intent(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'foreign-action','run_id':'foreign-run'})
        assert app.state.notifications.list_inbox(user['id'])==[]
        assert runtime.approvals(user)['items']==[]
        with app.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0]==0
