import importlib.util
import sqlite3


def create_native_db(path):
    c = sqlite3.connect(path)
    c.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,source TEXT,started_at REAL,last_activity_at REAL,parent_session_id TEXT);
    CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT,timestamp REAL,reasoning TEXT,reasoning_content TEXT);''')
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', ('wa-1','WhatsApp conversation','whatsapp',1,2,None))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', ('cli-1','CLI conversation','cli',3,4,None))
    c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(1,'wa-1','assistant','Retained answer',None,2,'PRIVATE REASONING','PRIVATE REASONING'))
    c.commit(); c.close()


def test_native_catalog_lists_cli_and_whatsapp_without_writing(tmp_path):
    assert importlib.util.find_spec('backend.native_catalog') is not None, 'Native profile catalogue not implemented'
    from backend.native_catalog import NativeCatalog
    db = tmp_path / 'state.db'; create_native_db(db)
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    result = catalog.sessions('default')
    assert result['total'] == 2
    assert {i['source'] for i in result['items']} == {'cli','whatsapp'}
    assert result['items'][0]['id'] == 'cli-1'
    assert db.read_bytes() == before


def test_catalog_paginates_searches_and_filters_private_reasoning(tmp_path):
    from backend.native_catalog import NativeCatalog
    create_native_db(tmp_path / 'state.db')
    cat = NativeCatalog({'default': tmp_path})
    page = cat.sessions('default', limit=1, offset=1)
    assert len(page['items']) == 1 and page['items'][0]['id'] == 'wa-1'
    assert cat.sessions('default', q='CLI')['total'] == 1
    messages = cat.messages('default', 'wa-1')
    assert messages['items'][0]['content'] == 'Retained answer'
    assert 'PRIVATE REASONING' not in str(messages)
    assert 'reasoning' not in messages['items'][0]


def test_catalog_never_resolves_unregistered_profile_or_unknown_session(tmp_path):
    import pytest
    from backend.native_catalog import NativeCatalog
    create_native_db(tmp_path / 'state.db')
    cat = NativeCatalog({'default': tmp_path})
    with pytest.raises(KeyError):
        cat.sessions('../../default')
    with pytest.raises(KeyError):
        cat.messages('default', 'not-found')


def test_native_job_inventory_does_not_expose_routing_credentials(tmp_path):
    import json
    from backend.native_catalog import NativeCatalog
    cron = tmp_path/'cron'; cron.mkdir()
    (cron/'jobs.json').write_text(json.dumps({'jobs':[{'id':'job-1','name':'Price watch','prompt':'Monitor price','schedule':{'kind':'interval','minutes':360},'enabled':True,'deliver':'whatsapp:private-recipient','origin':{'secret':'do-not-render'},'last_status':'ok'}]}))
    cat=NativeCatalog({'default':tmp_path})
    result=cat.jobs('default')
    assert result['items'][0]['id']=='job-1'
    assert 'private-recipient' not in str(result)
    assert 'do-not-render' not in str(result)


def test_history_hides_rewound_rows_but_keeps_compacted_display_history(tmp_path):
    from backend.native_catalog import NativeCatalog
    db=tmp_path/'state.db'; create_native_db(db)
    c=sqlite3.connect(db)
    c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
    c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
    c.execute("UPDATE messages SET active=0,compacted=1 WHERE id=1")
    c.execute("INSERT INTO messages(id,session_id,role,content,active,compacted) VALUES(2,'wa-1','user','Undone message',0,0)")
    c.commit(); c.close()
    rows=NativeCatalog({'default':tmp_path}).messages('default','wa-1')['items']
    assert [row['content'] for row in rows]==['Retained answer']
