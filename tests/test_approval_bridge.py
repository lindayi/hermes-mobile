"""Approval ingress -> real journal intent -> real owned Inbox/outbox."""
import json
import sqlite3
import time

from fastapi import HTTPException
import pytest

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
        assert target=={'kind':'approval','session_id':'wa-1','run_session_id':'wa-1','run_id':rid,
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


def test_approval_target_and_pending_card_resolve_compression_tip(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, _):
        with sqlite3.connect(tmp_path/'home'/'state.db') as db:
            db.execute('ALTER TABLE sessions ADD COLUMN end_reason TEXT')
            db.execute("UPDATE sessions SET end_reason='compression' WHERE id='wa-1'")
            db.execute("INSERT INTO sessions VALUES('tip','Canonical chat','whatsapp',3,4,'wa-1',NULL)")
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'compressed-action','run_id':'native-run',
            'command':'echo compressed'})
        with app.state.notifications._db() as db:
            notice=db.execute('SELECT id FROM inbox WHERE user_id=?',(user['id'],)).fetchone()

        target=client.get(BASE+f"/inbox/{notice['id']}/target")
        assert target.status_code==200
        assert target.json()=={
            'kind':'approval','session_id':'tip','run_session_id':'wa-1','run_id':rid,
            'approval_id':runtime.approvals(user)['items'][0]['id'],'request_id':'compressed-action'
        }
        listing=client.get(BASE+'/approvals').json()['items'][0]
        assert listing['session_id']=='tip' and listing['run_session_id']=='wa-1'
        details=client.get(BASE+f"/approvals/{listing['id']}").json()
        assert details['session_id']=='tip' and details['run_session_id']=='wa-1'
        assert details['status']=='pending'

        with app.state.notifications._db() as db, db:
            db.execute('UPDATE inbox SET session_id=? WHERE id=?',('unproven-alias',notice['id']))
        assert client.get(BASE+f"/inbox/{notice['id']}/target").status_code==404
        with pytest.raises(HTTPException) as error:
            app.state.notifications.notification_target('stranger',notice['id'])
        assert error.value.status_code==404


def test_approval_target_resolves_absent_run_alias_through_verified_lineage(tmp_path):
    with environment(tmp_path) as (app, client, user, existing_run_id, _):
        app.state.journal.finish(user['id'],existing_run_id,'completed')
        runtime=app.state.orchestrator
        run,_=app.state.journal.submit(
            user['id'],'default','old-alias','Private request','alias-approval',
            history_anchor=lambda: {
                'session_id':'old-alias','canonical_session_id':'wa-1','message_id':1
            })
        app.state.journal.set_upstream(user['id'],run['id'],'native-run')
        runtime._approval(user,runtime.get(user,run['id']),{
            'event':'approval.request','request_id':'alias-action','run_id':'native-run',
            'command':'echo alias'})
        approval=runtime.approvals(user)['items'][0]
        with app.state.notifications._db() as db:
            notice=db.execute('SELECT inbox_id AS id FROM approval_notifications WHERE approval_id=?',
                               (approval['id'],)).fetchone()

        target=client.get(BASE+f"/inbox/{notice['id']}/target")
        assert target.status_code==200
        assert target.json()=={
            'kind':'approval','session_id':'wa-1','run_session_id':'old-alias',
            'run_id':run['id'],'approval_id':approval['id'],'request_id':'alias-action'
        }
        details=client.get(BASE+f"/approvals/{approval['id']}").json()
        assert details['session_id']=='wa-1' and details['run_session_id']=='old-alias'
        assert details['status']=='pending'


def test_expired_approval_target_keeps_authorized_chat_but_has_no_decision(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, _):
        runtime=app.state.orchestrator
        runtime.gateway.execution_ready=True
        runtime.gateway.action_approvals_ready=True
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'expired-action','run_id':'native-run',
            'command':'echo expired'})
        with app.state.notifications._db() as db:
            notice=db.execute('SELECT id FROM inbox WHERE user_id=?',(user['id'],)).fetchone()
        approval_id=client.get(BASE+f"/inbox/{notice['id']}/target").json()['approval_id']
        pending=client.get(BASE+f'/approvals/{approval_id}')
        assert pending.status_code==200
        assert pending.json()['status']=='pending'
        assert pending.json()['executable'] is True
        assert pending.json()['choices']==['once','deny']
        with app.state.journal.connect() as db, db:
            db.execute('UPDATE orchestration_approvals SET expires_at=? WHERE request_id=?',
                       (time.time()-1,'expired-action'))
        target=client.get(BASE+f"/inbox/{notice['id']}/target")
        assert target.status_code==200
        approval=client.get(BASE+f"/approvals/{target.json()['approval_id']}")
        assert approval.status_code==200 and approval.json()['status']=='expired'
        assert approval.json()['executable'] is False
        assert approval.json()['choices']==[]


@pytest.mark.parametrize('decision', ['once','deny'])
def test_expired_verification_rejects_approval_before_native_decision(tmp_path, decision):
    with environment(tmp_path) as (app,client,user,rid,calls):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'fresh-action','run_id':'native-run',
            'command':'echo fresh'})
        approval_id=runtime.approvals(user)['items'][0]['id']
        with app.state.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET last_verified_at=? WHERE user_id=?',
                       (time.time()-301,user['id']))

        response=client.post(BASE+f'/approvals/{approval_id}/decision',json={'decision':decision})

        assert response.status_code==403
        assert app.state.journal.get(user['id'],rid)['status']=='waiting_for_approval'
        with app.state.journal.connect() as db:
            assert db.execute('SELECT status FROM orchestration_approvals WHERE id=?',
                              (approval_id,)).fetchone()['status']=='pending'
        assert not [request for request in calls if request.method=='POST']


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
