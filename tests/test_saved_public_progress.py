import json
import sqlite3
from backend.native_catalog import NativeCatalog
from test_native_catalog import create_native_db


def test_review_catalogue_unicode_assignment_sentinel_absent(tmp_path):
    path = tmp_path / 'state.db'
    create_native_db(path)
    sidecar = [{'type': 'message', 'phase': 'commentary', 'content': [
        {'type': 'output_text', 'text': '“password”: “SYNTHETIC_PRIVATE_VALUE”'}]}]
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE messages ADD COLUMN codex_message_items TEXT')
        db.execute('UPDATE messages SET content=?, codex_message_items=? WHERE id=1',
                   ('', json.dumps(sidecar)))
    before = path.read_bytes()
    item = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1')['items'][0]
    assert 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(item)
    assert item['public_commentary'][0]['id'] == 'native:1:commentary:0'
    assert 'codex_message_items' not in item
    assert not any(key.startswith('reasoning') for key in item)
    assert path.read_bytes() == before


def test_saved_native_sidecar_projects_public_progress_without_altering_raw_offsets(tmp_path):
    path=tmp_path/'state.db';create_native_db(path)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE messages ADD COLUMN codex_message_items TEXT')
        db.execute('DELETE FROM messages')
        items=[{'type':'message','phase':phase,'content':[{'type':'output_text','text':text}]} for phase,text in [('analysis','PRIVATE ANALYSIS'),('commentary','Checking saved public progress'),('final_answer','Not commentary')]]
        db.execute("INSERT INTO messages(id,session_id,role,content,tool_calls,codex_message_items,reasoning_content) VALUES(1,'wa-1','assistant','',?,?,?)",(json.dumps([{'id':'call','function':{'name':'terminal','arguments':'{"command":"pwd"}'}}]),json.dumps(items),'PRIVATE SIDECAR'))
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'wa-1','tool','result')")
    before=path.read_bytes();cat=NativeCatalog({'default':tmp_path})
    page=cat.messages('default','wa-1',latest=True,limit=1,turn_boundary=True)
    assert page['offset']==0 and page['count']==2 and page['total']==2
    first=page['items'][0]
    assert [x['content'] for x in first.get('public_commentary',[])]==['Checking saved public progress']
    assert first['public_commentary'][0]['id']=='native:1:commentary:1'
    assert 'codex_message_items' not in first
    assert 'PRIVATE' not in json.dumps(page) and 'Not commentary' not in json.dumps(page)
    assert cat.messages('default','wa-1',limit=1)['items'][0]['public_commentary']==first['public_commentary']
    assert path.read_bytes()==before
