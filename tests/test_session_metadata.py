import sqlite3
from fastapi.testclient import TestClient
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db
from backend.app import create_app, Settings
from test_member_runtime import family


def test_authorized_session_metadata_member_binding_does_not_nest_auth_writers(family, monkeypatch):
    import json
    from test_runtime_binding import setup_stale, login
    old, calls, result=setup_stale(family, monkeypatch)
    with TestClient(old,base_url=ORIGIN) as client:
        login(client,old)
        assert client.get(BASE+'/sessions/'+result['smoke_session']).status_code == 409
    app=create_app(Settings(**json.loads(family['config'].read_text())))
    with TestClient(app,base_url=ORIGIN) as client:
        login(client,app)
        response=client.get(BASE+'/sessions/'+result['smoke_session'])
        assert response.status_code == 200, response.text
        assert response.json()['id'] == result['smoke_session']
        assert client.get(BASE+'/sessions/wa-1').status_code == 404
    assert calls == []


def test_authorized_session_metadata_exact_read_only_and_deletion_guarded(tmp_path):
    home = tmp_path/'native'; home.mkdir(); create_native_db(home/'state.db')
    foreign = tmp_path/'foreign'; foreign.mkdir(); create_native_db(foreign/'state.db')
    with sqlite3.connect(foreign/'state.db') as db:
        db.execute("UPDATE sessions SET id='foreign-only',title='Foreign secret' WHERE id='wa-1'")
    before = (home/'state.db').read_bytes()
    app = create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home,'foreign':foreign},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        assert client.get(BASE+'/sessions/wa-1').status_code == 401
        client.headers['Origin']=ORIGIN;enroll(client)
        response=client.get(BASE+'/sessions/wa-1')
        assert response.status_code == 200, response.text
        assert response.json() == {'id':'wa-1','title':'WhatsApp conversation'}
        assert response.headers['cache-control']=='no-store'
        assert client.get(BASE+'/sessions/foreign-only').status_code == 404
        assert client.get(BASE+'/sessions/not-found').status_code == 404
        user=client.get(BASE+'/auth/me').json()['user']
        with app.state.journal.connect() as db:
            db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',(user['id'],'default','wa-1','op','deleted',1))
        assert client.get(BASE+'/sessions/wa-1').status_code == 409
        assert (home/'state.db').read_bytes() == before


def test_authorized_session_metadata_rechecks_deletion_after_read(tmp_path, monkeypatch):
    import asyncio
    home=tmp_path/'native';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN;enroll(client)
        user=client.get(BASE+'/auth/me').json()['user']
        original=asyncio.to_thread
        observed=[]
        async def raced_read(fn,*args,**kwargs):
            result=await original(fn,*args,**kwargs)
            if fn.__name__=='read_metadata':
                observed.append(True)
                with app.state.journal.connect() as db:
                    db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',(user['id'],'default','wa-1','op','prepared',1))
            return result
        monkeypatch.setattr(asyncio,'to_thread',raced_read)
        assert client.get(BASE+'/sessions/wa-1').status_code == 409
        assert observed == [True]


def test_authorized_session_metadata_rechecks_readiness_after_read(tmp_path, monkeypatch):
    import asyncio
    home=tmp_path/'native';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN;enroll(client)
        user=client.get(BASE+'/auth/me').json()['user']
        original=asyncio.to_thread
        async def raced_read(fn,*args,**kwargs):
            result=await original(fn,*args,**kwargs)
            if fn.__name__=='read_metadata':
                with app.state.auth.store.transaction() as db:
                    db.execute("UPDATE users SET status='pending' WHERE id=?",(user['id'],))
            return result
        monkeypatch.setattr(asyncio,'to_thread',raced_read)
        assert client.get(BASE+'/sessions/wa-1').status_code == 409


def test_authorized_session_metadata_requires_ready_profile(tmp_path):
    home=tmp_path/'native';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN;enroll(client)
        user=client.get(BASE+'/auth/me').json()['user']
        with app.state.auth.store.transaction() as db:
            db.execute("UPDATE users SET status='pending' WHERE id=?",(user['id'],))
        assert client.get(BASE+'/sessions/wa-1').status_code == 409
