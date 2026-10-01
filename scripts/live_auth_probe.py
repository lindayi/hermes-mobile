"""Public-HTTPS auth verification with an ephemeral virtual authenticator.

Only a uniquely labelled deployment-test account is created, then removed. Refuses
if any owner already exists. Appends model turns only to an explicitly selected
session already classified as Testing; never creates unmarked native sessions.
The optional --cron check retains its existing explicit execution behavior.
"""
import json
import sqlite3
import sys
from pathlib import Path
import uuid
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_auth import VirtualAuthenticator
from backend.manage import issue_owner_code

def main(test_session_id=None):
    if not test_session_id:
        raise RuntimeError('Pass --test-session-id for an existing verified testing session; unmarked creation is disabled')
    state=Path('/home/lindayi/.local/share/hermes-mobile-live')
    dbpath=state/'auth.sqlite'
    with sqlite3.connect(dbpath) as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='owner'").fetchone()[0]==0,'Owner already exists; refusing deployment fixture'
    label='deployment-test-'+uuid.uuid4().hex
    code=issue_owner_code(state/'config.json')
    key=VirtualAuthenticator()
    base='https://lindayi.me/hermes/app-api'
    try:
        with httpx.Client(headers={'Origin':'https://lindayi.me'},timeout=30,trust_env=False) as c:
            response=c.post(base+'/auth/register/options',json={'code':code,'display_name':label});response.raise_for_status();start=response.json()
            response=c.post(base+'/auth/register/verify',json={'enrollment_id':start['enrollment_id'],'credential':key.register(start['options'])});response.raise_for_status()
            info=response.json();assert info['user']['profile']=='default' and info['user']['role']=='owner'
            c.headers['X-CSRF-Token']=info['csrf_token']
            assert c.get(base+'/auth/me').status_code==200
            sessions=c.get(base+'/sessions');sessions.raise_for_status()
            assert sessions.json()['total']>0
            jobs=c.get(base+'/jobs');jobs.raise_for_status()
            # Real model turn through the PUBLIC web bridge, not directly to native API.
            checked=c.get(base+'/sessions',params={'kind':'tests','q':test_session_id,'limit':100})
            checked.raise_for_status()
            if not any(item.get('id') == test_session_id for item in checked.json()['items']):
                raise RuntimeError('Refusing to run outside a verified testing session')
            sid=test_session_id
            submitted=c.post(base+'/runs',json={'session_id':sid,'input':'Deployment verification only: reply exactly PUBLIC_HERMES_APP_OK. Do not use tools, update memory, or change anything.','idempotency_key':label})
            submitted.raise_for_status();rid=submitted.json()['id']
            import time
            deadline=time.monotonic()+180
            while True:
                result=c.get(base+'/runs/'+rid);result.raise_for_status();result=result.json()
                if result['status'] in ('completed','failed','unknown','cancelled'):break
                if time.monotonic()>deadline:raise TimeoutError('Public run did not finish; do not resubmit')
                time.sleep(1)
            assert result['status']=='completed' and 'PUBLIC_HERMES_APP_OK' in str(result.get('output')),result
            stream=c.get(base+'/runs/'+rid+'/events');stream.raise_for_status()
            assert 'event: done' in stream.text and 'PUBLIC_HERMES_APP_OK' in stream.text
            duplicate=c.post(base+'/runs',json={'session_id':sid,'input':'Deployment verification only: reply exactly PUBLIC_HERMES_APP_OK. Do not use tools, update memory, or change anything.','idempotency_key':label});duplicate.raise_for_status()
            assert duplicate.json()['id']==rid
            print('PASS: public HTTPS chat, persisted completion, SSE result and duplicate-submit protection.')
            previous=c.cookies.get('hermes_session')
            options=c.post(base+'/auth/verify/options',json={});options.raise_for_status();options=options.json()
            rotated=c.post(base+'/auth/verify/finish',json={'challenge_id':options['challenge_id'],'credential':key.assert_(options['options'])});rotated.raise_for_status()
            assert c.cookies.get('hermes_session')!=previous
            if '--cron' in sys.argv:
                import subprocess
                probe=subprocess.run(['/usr/local/lib/hermes-agent/venv/bin/python',str(Path(__file__).with_name('live_cron_probe.py'))],capture_output=True,text=True,timeout=120)
                if probe.returncode:
                    raise RuntimeError('Cron probe failed: '+probe.stdout+' '+probe.stderr)
                inbox=c.get(base+'/inbox');inbox.raise_for_status()
                assert any('MOBILE_CRON_DELIVERY_OK' in item['body'] for item in inbox.json()['items'])
                print('PASS: real script-only cron execution -> native platform adapter -> HTTPS-account inbox.')
            invalid=httpx.get(base+'/auth/me',headers={'Cookie':'hermes_session='+previous},trust_env=False,timeout=30)
            assert invalid.status_code==401
            assert c.post(base+'/auth/logout').status_code==200
            assert c.get(base+'/auth/me').status_code==401
            print(json.dumps({'https_auth':'passed','native_profile':'default','native_session_count':sessions.json()['total'],'native_job_count':len(jobs.json()['items']),'stepup_rotation':'passed','old_token_rejected':True,'logout':'passed'}))
    finally:
        with sqlite3.connect(dbpath) as db:
            ids=[r[0] for r in db.execute('SELECT id FROM users WHERE display_name=?',(label,))]
            for uid in ids:
                for table in ('credentials','sessions','recovery_codes'):
                    db.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
                db.execute('DELETE FROM users WHERE id=? AND display_name=?',(uid,label))
            db.commit()
        print('Removed only the uniquely labelled deployment-test account. Owner slot is available for your signup.')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-session-id', required=True,
                        help='Explicit consent to append probe turns to an existing verified testing session')
    parser.add_argument('--cron', action='store_true', help='Also run the existing cron delivery probe')
    main(parser.parse_args().test_session_id)
