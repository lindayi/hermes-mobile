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
        assert notification.list_inbox(user['id'])==[]
        with notification._db() as db:
            item=db.execute('SELECT id,body,session_id FROM inbox WHERE user_id=?',(user['id'],)).fetchone()
        assert item['session_id']=='wa-1'
        target=notification.notification_target(user['id'],item['id'])
        assert target=={'kind':'approval','session_id':'wa-1','run_id':rid,
                        'approval_id':runtime.approvals(user)['items'][0]['id'],'request_id':'native-action'}
        approval=client.get(BASE+f"/approvals/{target['approval_id']}")
        assert approval.status_code==200
        assert approval.json()['status']=='pending' and approval.json()['session_id']=='wa-1'
        assert approval.json()['command']=='do not expose full command'
        assert client.get(BASE+'/approvals/not-owned').status_code==404
        assert 'do not expose' not in json.dumps(dict(item))
        assert notification.list_inbox('stranger')==[]
        with notification._db() as db:
            assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM inbox').fetchone()[0]==1
        with app.state.journal.connect() as db:
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]=='sent'
        assert not calls


def test_approval_details_respect_run_session_deletion_fence(tmp_path):
    from contextlib import closing

    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'delete-fence','run_id':'native-run',
            'command':'private action details'})
        approval_id=runtime.approvals(user)['items'][0]['id']
        with closing(app.state.journal.connect()) as db, db:
            db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                       (user['id'],user['profile'],'wa-1','deletion-operation','deleted',1))

        response=client.get(BASE+f'/approvals/{approval_id}')

        assert response.status_code==409
        assert 'private action details' not in response.text


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
        with app.state.notifications._db() as db:
            assert db.execute('SELECT count(*) FROM inbox WHERE user_id=?',(user['id'],)).fetchone()[0]==1
        with app.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0]==1
            assert db.execute('SELECT status FROM approval_notification_intents').fetchone()[0]=='sent'


def test_resolved_and_unowned_approval_validation_fails_closed(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{'event':'approval.request','request_id':'action','run_id':'native-run'})
        with app.state.notifications._db() as db:
            item=db.execute('SELECT id FROM inbox WHERE user_id=?',(user['id'],)).fetchone()
        item=app.state.notifications.notification_target(user['id'],item['id'])
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
        while time.monotonic()<deadline:
            with app.state.notifications._db() as db:
                if db.execute('SELECT 1 FROM inbox WHERE user_id=?',(user['id'],)).fetchone():
                    break
            time.sleep(.02)
        with app.state.notifications._db() as db:
            assert db.execute('SELECT count(*) FROM inbox WHERE user_id=?',(user['id'],)).fetchone()[0]==1


def test_mismatched_native_run_approval_never_creates_notification_intent(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'foreign-action','run_id':'foreign-run'})
        assert app.state.notifications.list_inbox(user['id'])==[]
        assert runtime.approvals(user)['items']==[]
        with app.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0]==0
