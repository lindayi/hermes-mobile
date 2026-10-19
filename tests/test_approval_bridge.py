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


def test_owned_cron_approval_keeps_inbox_destination_and_projection(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        with sqlite3.connect(tmp_path/'home'/'state.db') as db:
            db.execute("UPDATE sessions SET source='cron' WHERE id='wa-1'")
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                       ('chat-session','Synthetic chat','whatsapp',5,6,None))
        notification=app.state.notifications
        from types import SimpleNamespace
        sent=[]
        notification.vapid_private_key='synthetic-test-key'
        notification.vapid_public_key='synthetic-test-key'
        notification.send_push=lambda **payload:(sent.append(payload) or SimpleNamespace(status_code=201))
        with app.state.auth.store.transaction() as db:
            device=db.execute('SELECT id FROM sessions WHERE user_id=?',(user['id'],)).fetchone()[0]
        notification.subscribe(user['id'],device,subscription())
        runtime=app.state.orchestrator
        runtime.gateway.execution_ready=True
        runtime.gateway.action_approvals_ready=True
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'cron-action','run_id':'native-run',
            'command':'echo synthetic cron approval'})
        chat_run,_=app.state.journal.submit(user['id'],'default','chat-session','Synthetic chat request','chat-key')
        app.state.journal.set_upstream(user['id'],chat_run['id'],'native-chat-run')
        runtime._approval(user,runtime.get(user,chat_run['id']),{
            'event':'approval.request','request_id':'chat-action','run_id':'native-chat-run',
            'command':'echo synthetic chat approval'})
        approvals={item['request_id']:item for item in runtime.approvals(user)['items']}
        with notification._db() as db:
            notices={row['delivery_id']:row['id'] for row in db.execute(
                'SELECT id,delivery_id FROM inbox WHERE user_id=?',(user['id'],))}
        notice_id=notices['approval:'+json.dumps([rid,'cron-action'],separators=(',',':'))]
        chat_notice_id=notices['approval:'+json.dumps([chat_run['id'],'chat-action'],separators=(',',':'))]

        page=client.get(BASE+'/inbox')
        assert page.status_code==200
        assert [item['id'] for item in page.json()['items']]==[notice_id]
        assert page.json()['read_count']==0
        assert client.get(BASE+'/inbox?limit=1&offset=1').json()['items']==[]
        target=client.get(BASE+f"/inbox/{notice_id}/target")
        assert target.status_code==200
        assert target.json()=={'kind':'inbox'}
        chat_target=client.get(BASE+f"/inbox/{chat_notice_id}/target")
        assert chat_target.status_code==200 and chat_target.json()['kind']=='approval'
        listed={item['request_id']:item for item in client.get(BASE+'/approvals').json()['items']}
        assert listed['cron-action']['id']==approvals['cron-action']['id']
        assert listed['cron-action']['session_id']=='wa-1'
        assert listed['cron-action']['inbox_visible'] is True
        assert listed['chat-action']['inbox_visible'] is False
        for request_id in ('cron-action','chat-action'):
            item=listed[request_id]
            detail=client.get(BASE+f"/approvals/{item['id']}")
            assert detail.status_code==200
            assert detail.json()==item
            assert item['command']==f"echo synthetic {'cron' if request_id=='cron-action' else 'chat'} approval"
            assert item['executable'] is True and item['choices']==['once','deny']
        assert notification.flush()['sent']==2
        assert len(sent)==2
        with notification._db() as db:
            receipts_before=[tuple(row) for row in db.execute('SELECT * FROM approval_notifications ORDER BY approval_id')]
            assert {row[0] for row in db.execute('SELECT status FROM outbox')}=={'sent'}
        assert notification.mark_read(user['id'],notice_id)
        assert notification.read_count(user['id'])==1
        assert client.post(BASE+'/inbox/clear-read').json()=={'ok':True,'dismissed':1}
        assert notification.list_inbox(user['id'])==[]
        with notification._db() as db:
            assert [tuple(row) for row in db.execute('SELECT * FROM approval_notifications ORDER BY approval_id')]==receipts_before
            assert db.execute('SELECT count(*) FROM inbox').fetchone()[0]==2
            assert {row[0] for row in db.execute('SELECT status FROM outbox')}=={'sent'}


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
        assert client.get(BASE+'/inbox').json()['items']==[]
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
        listing=client.get(BASE+'/approvals').json()['items'][0]
        assert listing==details
        assert listing['command']=='echo alias'


def test_pending_list_batches_real_proofs_and_repeated_sessions(tmp_path, monkeypatch):
    import asyncio
    from contextlib import contextmanager

    with environment(tmp_path) as (app, client, user, existing_run, _):
        journal=app.state.journal
        runtime=app.state.orchestrator
        journal.finish(user['id'],existing_run,'completed')
        with sqlite3.connect(tmp_path/'home'/'state.db') as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                       ('cron-session','Synthetic scheduled session','cron',3,4,None))
        identities=[]
        for owner,profile,session in [
                (user['id'],'default','wa-1'),(user['id'],'default','old-alias'),
                (user['id'],'default','cron-session'),('foreign','default','foreign-session'),
                (user['id'],'foreign-profile','foreign-profile-session')]:
            run,_=journal.submit(owner,profile,session,'Synthetic request',f'pending-{len(identities)}',
                history_anchor=(lambda: {'session_id':'old-alias',
                    'canonical_session_id':'wa-1','message_id':1}) if session=='old-alias' else None)
            journal.finish(owner,run['id'],'completed')
            identities.append(run['id'])
        with journal.connect() as db, db:
            db.execute("UPDATE runs SET status='waiting_for_approval' WHERE id IN (?,?,?,?,?)",identities)
            for group,run_id in enumerate(identities):
                for index in range(70):
                    db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                        (f'pending-{group}-{index}',run_id,f'request-{group}-{index}',
                         '{"command":"echo synthetic"}','pending',time.time()+600))

        original_approvals=runtime.approvals
        observed={}

        def approvals(owner):
            try:
                asyncio.get_running_loop()
                observed['event_loop']=True
            except RuntimeError:
                observed['event_loop']=False
            result=original_approvals(owner)
            assert len(result['items'])==210
            first=result['items'][0]
            observed['invalid']=[
                {**first,'request_id':'wrong-request'},
                {**first,'session_id':None},
                {**first,'session_id':'foreign-session'},
                {**first,'run_id':identities[3],'id':'pending-3-0','request_id':'request-3-0'},
                {**first,'run_id':identities[4],'id':'pending-4-0','request_id':'request-4-0'},
            ]
            return {'items':result['items']+observed['invalid']}

        monkeypatch.setattr(runtime,'approvals',approvals)
        work={'auth':0,'journal':0,'native':0,'lineage':0,'proofs':0}
        original_transaction=app.state.auth.store.transaction

        @contextmanager
        def transaction(*args,**kwargs):
            work['auth']+=1
            with original_transaction(*args,**kwargs) as db:
                yield db

        original_connect=journal.connect

        def connect(*args,**kwargs):
            work['journal']+=1
            db=original_connect(*args,**kwargs)
            def trace(sql):
                if 'JOIN orchestration_approvals a ON a.run_id=' in sql:
                    work['proofs']+=1
            db.set_trace_callback(trace)
            return db

        original_native=app.state.catalog._connect
        original_lineage=app.state.background_delivery._lineage

        def native(*args,**kwargs):
            work['native']+=1
            return original_native(*args,**kwargs)

        def lineage(*args,**kwargs):
            work['lineage']+=1
            return original_lineage(*args,**kwargs)

        monkeypatch.setattr(app.state.auth.store,'transaction',transaction)
        monkeypatch.setattr(journal,'connect',connect)
        monkeypatch.setattr(app.state.catalog,'_connect',native)
        monkeypatch.setattr(app.state.background_delivery,'_lineage',lineage)
        response=client.get(BASE+'/approvals')
        assert response.status_code==200
        items=response.json()['items']
        assert len(items)==210
        for group in range(3):
            selected=[item for item in items[:210] if item['id'].startswith(f'pending-{group}-')]
            assert len(selected)==70
            assert all(item['session_id']==('cron-session' if group==2 else 'wa-1') for item in selected)
            assert all(item['inbox_visible'] is (group==2) for item in selected)
            if group!=2:
                assert all(item['run_session_id']==('old-alias' if group==1 else 'wa-1') for item in selected)
        assert not any(item in items for item in observed['invalid'])
        assert all(item['request_id']!='wrong-request' and item['session_id'] is not None for item in items)
        assert work['auth']<=4, work
        assert work['proofs']==3, work
        assert work['journal']<=10, work
        assert work['native']<=12, work
        assert work['lineage']==3, work
        assert observed['event_loop'] is False
        print('Pending-list real work:',work,'event_loop:',observed['event_loop'])
        before=work.copy()
        assert client.get(BASE+'/approvals').status_code==200
        assert work['lineage']-before['lineage']==3,'classification must be request-scoped, not cached'


@pytest.mark.parametrize('endpoint', ['list','detail'])
@pytest.mark.parametrize('change', ['role','profile','status'])
@pytest.mark.parametrize('stage', ['classification','runtime'])
def test_pending_list_revalidates_authorization_after_classification(tmp_path, monkeypatch, change, endpoint, stage):
    with environment(tmp_path) as (app, client, user, rid, _):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'auth-change','run_id':'native-run','command':'echo synthetic'})
        approval_id=runtime.approvals(user)['items'][0]['id']
        target,name=(app.state.background_delivery,'_lineage') if stage=='classification' else (
            runtime,'approval' if endpoint=='detail' else 'approvals')
        original=getattr(target,name)

        def lineage(*args,**kwargs):
            result=original(*args,**kwargs)
            value={'role':'member','profile':'foreign-profile','status':'pending'}[change]
            with app.state.auth.store.transaction() as db:
                db.execute(f'UPDATE users SET {change}=? WHERE id=?',(value,user['id']))
            return result

        monkeypatch.setattr(target,name,lineage)
        response=client.get(BASE+'/approvals'+(f'/{approval_id}' if endpoint=='detail' else ''))
        assert response.status_code==(404 if endpoint=='detail' else 200)
        if endpoint=='list':
            assert response.json()=={'items':[]}
        assert 'echo synthetic' not in response.text
        assert 'executable' not in response.text and 'choices' not in response.text
        assert 'request_id' not in response.text


@pytest.mark.parametrize('endpoint', ['list','detail'])
@pytest.mark.parametrize('invalid', ['request','approval','run','null-session','foreign-session','missing-native','ambiguous-alias'])
def test_approval_reads_omit_unverified_action_details(tmp_path, monkeypatch, endpoint, invalid):
    with environment(tmp_path) as (app, client, user, rid, _):
        runtime=app.state.orchestrator
        runtime.gateway.execution_ready=True
        runtime.gateway.action_approvals_ready=True
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'bound-action','run_id':'native-run',
            'command':'echo private synthetic action','target':'private synthetic target'})
        item=runtime.approvals(user)['items'][0]
        assert item['executable'] is True and item['choices']==['once','deny']
        changed={**item}
        if invalid in ('request','approval','run','null-session','foreign-session'):
            field,value={
                'request':('request_id','wrong-request'),'approval':('id','wrong-approval'),
                'run':('run_id','foreign-run'),'null-session':('session_id',None),
                'foreign-session':('session_id','foreign-session')}[invalid]
            changed[field]=value
        else:
            with sqlite3.connect(tmp_path/'home'/'state.db') as db:
                db.execute("DELETE FROM sessions WHERE id='wa-1'")
            if invalid=='ambiguous-alias':
                other,_=app.state.journal.submit(user['id'],'default','other-alias','Synthetic','other-alias')
                with app.state.journal.connect() as db, db:
                    db.execute('INSERT INTO run_history_anchors(run_id,session_id,canonical_session_id,message_id) VALUES(?,?,?,?)',
                               (rid,'wa-1','first-tip',1))
                    db.execute('INSERT INTO run_history_anchors(run_id,session_id,canonical_session_id,message_id) VALUES(?,?,?,?)',
                               (other['id'],'wa-1','second-tip',1))
        monkeypatch.setattr(runtime,'approvals',lambda owner:{'items':[changed]})
        monkeypatch.setattr(runtime,'approval',lambda owner,aid:changed)
        response=client.get(BASE+'/approvals'+(f"/{item['id']}" if endpoint=='detail' else ''))
        assert response.status_code==(404 if endpoint=='detail' else 200)
        if endpoint=='list':
            assert response.json()=={'items':[]}
        for text in ('private synthetic','executable','choices','request_id'):
            assert text not in response.text


def test_assembled_approval_detail_work_runs_off_event_loop(tmp_path, monkeypatch):
    import asyncio

    with environment(tmp_path) as (app, client, user, rid, _):
        runtime=app.state.orchestrator
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'threaded-detail','run_id':'native-run',
            'command':'echo threaded detail'})
        approval_id=runtime.approvals(user)['items'][0]['id']
        observed=[]
        for target,name in [(runtime,'approval'),(app.state.journal,'connect'),
                            (app.state.background_delivery,'_lineage')]:
            original=getattr(target,name)

            def probe(*args,_original=original,_name=name,**kwargs):
                try:
                    asyncio.get_running_loop()
                    observed.append((_name,True))
                except RuntimeError:
                    observed.append((_name,False))
                return _original(*args,**kwargs)

            monkeypatch.setattr(target,name,probe)
        response=client.get(BASE+f'/approvals/{approval_id}')
        assert response.status_code==200
        assert response.json()['command']=='echo threaded detail'
        assert {'approval','connect','_lineage'}<={name for name,_ in observed}
        assert all(not on_loop for _,on_loop in observed),observed
        assert client.get(BASE+'/approvals/not-owned').status_code==404


def test_terminal_run_exact_detail_retires_unavailable_placeholder_without_stream_event(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        journal=app.state.journal
        journal.finish(user['id'],rid,'waiting_for_approval')
        run=journal.get(user['id'],rid)
        runtime._missing_approval(user,run,'interrupted-action')
        item=runtime.approvals(user)['items'][0]
        assert item['status']=='details_unavailable'
        with journal.connect() as db:
            assert db.execute('SELECT count(*) FROM events WHERE run_id=? AND name=?',
                              (rid,'approval')).fetchone()[0]==1

        journal.finish(user['id'],rid,'completed')

        response=client.get(BASE+f"/approvals/{item['id']}")
        assert response.status_code==200
        detail=response.json()
        assert detail['status']=='unavailable'
        assert detail['executable'] is False and detail['choices']==[]
        assert not detail.get('details_unavailable',False)
        assert 'native client' not in detail.get('error','').lower()


def test_session_approval_enumeration_precedes_global_cap_and_marks_truncation(tmp_path):
    from contextlib import closing

    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        journal=app.state.journal
        journal.finish(user['id'],rid,'completed')
        with sqlite3.connect(tmp_path/'home'/'state.db') as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                       ('other-chat','Other synthetic chat','whatsapp',3,4,None))

        older,_=journal.submit(user['id'],user['profile'],'other-chat','Older request','older-cap')
        with closing(journal.connect()) as db, db:
            db.execute("UPDATE runs SET status='waiting_for_approval' WHERE id=?",(older['id'],))
        older=journal.get(user['id'],older['id'])
        runtime._missing_approval(user,older,'older-action-0')
        with closing(journal.connect()) as db, db:
            for index in range(1,1001):
                approval_id=f'older-{index:032x}'
                request_id=f'older-action-{index}'
                action='{"details_unavailable":true}'
                db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                           (approval_id,older['id'],request_id,action,'details_unavailable',0))
                row=dict(id=approval_id,run_id=older['id'],request_id=request_id,
                         action=action,status='details_unavailable',expires_at=0,
                         session_id='other-chat')
                db.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                           (older['id'],'approval',json.dumps(runtime._approval_view(row)),time.time()))

        target,_=journal.submit(user['id'],user['profile'],'old-alias',
            'Canonical chat request','target-cap',
            history_anchor=lambda:{'session_id':'old-alias','canonical_session_id':'wa-1','message_id':1})
        with closing(journal.connect()) as db, db:
            db.execute("UPDATE runs SET status='waiting_for_approval',upstream_id=? WHERE id=?",
                       ('native-target',target['id']))
        target=journal.get(user['id'],target['id'])
        runtime._approval(user,target,{
            'event':'approval.request','run_id':'native-target',
            'request_id':'target-action','command':'echo target'})
        with journal.connect() as db:
            target_item=db.execute('SELECT id,request_id FROM orchestration_approvals '
                                   'WHERE run_id=? AND request_id=?',
                                   (target['id'],'target-action')).fetchone()
        assert any(event['name']=='approval' for event in journal.events(user['id'],target['id']))

        global_items=client.get(BASE+'/approvals').json()['items']
        assert len(global_items)==1000
        assert all(item['id']!=target_item['id'] for item in global_items)

        response=client.get(BASE+'/approvals?session_id=wa-1')
        assert response.status_code==200
        result=response.json()
        assert result['complete'] is True
        assert [item['id'] for item in result['items']]==[target_item['id']]
        assert result['items'][0]['session_id']=='wa-1'
        assert result['items'][0]['run_session_id']=='old-alias'

        truncated=client.get(BASE+'/approvals?session_id=other-chat')
        assert truncated.status_code==200
        assert truncated.json()['complete'] is False
        assert len(truncated.json()['items'])==1000

        foreign=client.get(BASE+'/approvals?session_id=foreign-session')
        assert foreign.status_code==200
        assert foreign.json()=={'items':[],'complete':True}


@pytest.mark.parametrize('final_status', ['expired','resolved','unavailable'])
def test_final_approval_target_keeps_authorized_chat_but_has_no_decision(tmp_path, final_status):
    with environment(tmp_path) as (app, client, user, rid, _):
        runtime=app.state.orchestrator
        runtime.gateway.execution_ready=True
        runtime.gateway.action_approvals_ready=True
        runtime._approval(user,runtime.get(user,rid),{
            'event':'approval.request','request_id':'final-action','run_id':'native-run',
            'command':'echo final'})
        with app.state.notifications._db() as db:
            notice=db.execute('SELECT id FROM inbox WHERE user_id=?',(user['id'],)).fetchone()
        approval_id=client.get(BASE+f"/inbox/{notice['id']}/target").json()['approval_id']
        pending=client.get(BASE+f'/approvals/{approval_id}')
        assert pending.status_code==200
        assert pending.json()['status']=='pending'
        assert pending.json()['executable'] is True
        assert pending.json()['choices']==['once','deny']
        with app.state.journal.connect() as db, db:
            if final_status=='expired':
                db.execute('UPDATE orchestration_approvals SET expires_at=? WHERE request_id=?',
                           (time.time()-1,'final-action'))
            elif final_status=='resolved':
                db.execute("UPDATE orchestration_approvals SET status='resolved' WHERE request_id=?",
                           ('final-action',))
        if final_status=='unavailable':
            app.state.journal.finish(user['id'],rid,'completed')
        approval=client.get(BASE+f'/approvals/{approval_id}')
        assert approval.status_code==200 and approval.json()['status']==final_status
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
