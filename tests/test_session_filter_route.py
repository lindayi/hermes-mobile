"""Assembled session filters share count/page predicates and preserve direct history."""
import sqlite3
from fastapi.testclient import TestClient
from backend.app import Settings,create_app
from test_auth import BASE,BOOTSTRAP,ORIGIN,enroll
from test_native_catalog import create_native_db


def test_session_filter_route_defaults_counts_search_pages_and_direct_open(tmp_path):
    home=tmp_path/'native';home.mkdir();create_native_db(home/'state.db')
    with sqlite3.connect(home/'state.db') as db:
        for sid,title,source in [('cron-a','Scheduled result','cron'),('test-a','Verification fixture','agent_test'),('child-a','Worker fixture','subagent')]:
            db.execute('INSERT INTO sessions(id,title,source,started_at,last_activity_at) VALUES(?,?,?,?,?)',(sid,title,source,200,200))
        db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES('cron-a','assistant','Fixture cron result',200)")
    app=create_app(Settings(state_dir=tmp_path/'app',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as c:
        assert c.get(BASE+'/sessions?kind=all').status_code==401
        c.headers['Origin']=ORIGIN;enroll(c)
        default=c.get(BASE+'/sessions').json()
        assert default['total']==2
        assert not {'cron-a','test-a','child-a'} & {r['id'] for r in default['items']}
        for kind,sid in [('cron','cron-a'),('tests','test-a')]:
            result=c.get(BASE+'/sessions',params={'kind':kind}).json()
            assert result['total']==1
            assert [r['id'] for r in result['items']]==[sid]
        all_pages=[c.get(BASE+'/sessions',params={'kind':'all','limit':1,'offset':i}).json() for i in range(4)]
        assert all(p['total']==4 for p in all_pages)
        assert len({r['id'] for p in all_pages for r in p['items']})==4
        assert c.get(BASE+'/sessions?kind=all&q=Scheduled').json()['total']==1
        assert c.get(BASE+'/sessions?q=Scheduled').json()['total']==0
        assert c.get(BASE+'/sessions?kind=not-valid').status_code==422
        result=c.get(BASE+'/sessions/cron-a/messages?latest=true')
        assert result.status_code==200
        assert result.json()['items'][0]['content']=='Fixture cron result'
