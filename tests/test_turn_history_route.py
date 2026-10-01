import sqlite3
from fastapi.testclient import TestClient
from test_model_controls import fixture
from test_auth import ORIGIN, BASE, enroll


def test_actual_messages_route_opts_into_turn_boundary_but_health_probe_does_not(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    with sqlite3.connect(app.state.catalog.profiles['default']/'state.db') as db:
        db.execute('DELETE FROM messages')
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES(1,'wa-1','user','Question')")
        db.executemany("INSERT INTO messages(id,session_id,role,content) VALUES(?,'wa-1','tool','result')",[(i,) for i in range(2,154)])
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        page=c.get(BASE+'/sessions/wa-1/messages?latest=true&limit=100&turn_boundary=true').json()
        assert page['offset']==0
        assert page['items'][0]['role']=='user'
        assert page['turn_boundary']['complete']
        raw=c.get(BASE+'/sessions/wa-1/messages?latest=true&limit=1').json()
        assert len(raw['items'])==1 and raw['offset']==152
        assert 'turn_boundary' not in raw
    assert not calls
