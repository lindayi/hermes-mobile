"""Synthetic assembled finance/auth and public-build acceptance."""
import json
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db


def test_nested_finance_index_stays_addressable_and_dependencies_versioned(tmp_path):
    from deploy.frontend_release import build_frontend
    source=tmp_path/'source';source.mkdir()
    (source/'index.html').write_text('<a href="/hermes/finance/">Finance</a>')
    (source/'finance').mkdir()
    (source/'finance/index.html').write_text('<link href="./styles.css"><script src="./app.mjs"></script>')
    (source/'finance/styles.css').write_text('body{color:teal}')
    (source/'finance/app.mjs').write_text('export const privateData=false;')
    out=build_frontend(source,tmp_path/'public')
    assert (out/'finance/index.html').exists(), 'DirectoryIndex must stay stable for companion pages'
    page=(out/'finance/index.html').read_text()
    assert './styles.css' not in page and './app.mjs' not in page
    assert len(list((out/'finance').glob('styles.*.css')))==1
    assert len(list((out/'finance').glob('app.*.mjs')))==1
    assert '/hermes/finance/' in (out/'index.html').read_text()


def test_assembled_finance_requires_existing_owner_session_before_private_io(tmp_path):
    from backend.app import create_app,Settings
    home=tmp_path/'home';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'app-state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        for name in ['meta','overview','transactions','transfers','investments','compensation','obligations','quotes']:
            response=client.get(BASE+'/finance/'+name)
            assert response.status_code==401, (name,response.status_code)
            assert response.headers['cache-control']=='no-store'
        client.headers['Origin']=ORIGIN;enroll(client)
        response=client.get(BASE+'/finance/meta')
        assert response.status_code==503, response.text
        assert str(home) not in response.text
        assert not (home/'finance').exists(), 'Reads must not create a missing finance DB/directory'
        uid=client.get(BASE+'/auth/me').json()['user']['id']
        with app.state.auth.store.transaction() as db:
            db.execute("UPDATE users SET role='member' WHERE id=?",(uid,))
        for name in ['meta','overview','transactions','transfers','investments','compensation','obligations','quotes']:
            assert client.get(BASE+'/finance/'+name).status_code==403
        assert client.get('/hermes/finance/finance.sqlite3').status_code==404


def test_assembled_owner_reads_fresh_sqlite_and_revocation_blocks_same_cookie(tmp_path):
    from backend.app import create_app,Settings
    home=tmp_path/'home';home.mkdir();create_native_db(home/'state.db')
    finance=home/'finance';finance.mkdir();database=finance/'finance.sqlite3'
    schema=json.loads((Path(__file__).resolve().parents[1]/'docs/finance-schema-contract.json').read_text())
    with sqlite3.connect(database) as db:
        for name,columns in schema.items():
            ddl=','.join('"'+column['name']+'" '+column['type'] for column in columns)
            db.execute(f'CREATE TABLE "{name}" ({ddl})')
        db.execute("INSERT INTO hierarchical_transactions(entry_id,record_id,dataset,account,date,currency,category_id,flow,amount_minor,income_minor,expense_minor,included,record_status) VALUES('fixture','fixture','bank','Checking','2030-03-14','CAD','income.pay','income',10000,10000,0,1,'posted')")
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN;enroll(client)
        first=client.get(BASE+'/finance/overview')
        assert first.status_code==200,first.text
        assert first.json()['totals'][0]['income_minor']==10000
        with sqlite3.connect(database) as db:
            db.execute('UPDATE hierarchical_transactions SET amount_minor=12000,income_minor=12000')
        second=client.get(BASE+'/finance/overview')
        assert second.json()['totals'][0]['income_minor']==12000
        assert second.headers['cache-control']=='no-store'
        assert second.headers['vary']=='Cookie'
        assert 'raw_json' not in second.json()
        with app.state.auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1')
        assert client.get(BASE+'/finance/overview').status_code==401
        assert client.get(BASE+'/finance/quotes').status_code==401
