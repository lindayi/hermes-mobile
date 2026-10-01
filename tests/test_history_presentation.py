"""Display-only native history contract; all databases here are synthetic."""
import json
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_native_catalog import create_native_db


def tool_database(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN tool_name TEXT')
        c.execute('ALTER TABLE messages ADD COLUMN tool_call_id TEXT')
        c.execute('ALTER TABLE messages ADD COLUMN effect_disposition TEXT')
    return db


def test_tool_names_match_call_ids_across_page_boundary_without_guessing(tmp_path):
    db = tool_database(tmp_path)
    calls = [{'id': 'call-a', 'function': {'name': 'terminal', 'arguments': '{}'}},
             {'id': 'call-b', 'function': {'name': 'read_file', 'arguments': '{}'}}]
    with sqlite3.connect(db) as c:
        c.execute("UPDATE messages SET tool_calls=? WHERE id=1", (json.dumps(calls),))
        c.executemany('INSERT INTO messages(id,session_id,role,content,tool_call_id,tool_name) VALUES(?,?,?,?,?,?)', [
            (2, 'wa-1', 'tool', 'file data', 'call-b', None),
            (3, 'wa-1', 'tool', 'command output', 'call-a', 'explicit_name'),
            (4, 'wa-1', 'tool', 'unmatched', 'foreign', None),
            (5, 'wa-1', 'tool', 'no identity', None, None),
        ])
        c.execute("INSERT INTO messages(id,session_id,role,tool_calls) VALUES(6,'cli-1','assistant',?)",
                  (json.dumps([{'id': 'foreign', 'function': {'name': 'secret_foreign_tool'}}]),))
    before = db.read_bytes()
    result = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1', limit=4, latest=True)
    assert [item.get('name') for item in result['items']] == ['read_file', 'explicit_name', 'tool', 'tool']
    assert result['offset'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize(('content', 'expected'), [
    ('{"success":true}', 'success'),
    ('{"exit_code":0,"error":null}', 'success'),
    ('{"status":"success"}', 'success'),
    ('{"status":"ok"}', 'success'),
    ('{"success":false}', 'failed'),
    ('{"exit_code":2}', 'failed'),
    ('{"error":"denied","success":true}', 'failed'),
    ('{"status":"failed"}', 'failed'),
    ('{"status":"timeout"}', 'failed'),
    ('{"status":"cancelled"}', 'failed'),
    ('{"isError":true}', 'failed'),
    ('{"status":"completed"}', 'completed'),
    ('{"status":"running"}', 'unknown'),
    ('{"error":null}', 'unknown'),
    ('{"output":"success"}', 'unknown'),
    ('{"exit_code":false}', 'unknown'),
    ('{"success":"false"}', 'unknown'),
    ('{"status":[]}', 'unknown'),
    ('[]', 'unknown'),
    ('null', 'unknown'),
    ('successfully completed', 'unknown'),
    ('not json', 'unknown'),
    ('', 'unknown'),
    (None, 'unknown'),
])
def test_tool_status_uses_explicit_structured_evidence_only(tmp_path, content, expected):
    db = tool_database(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content,tool_name) VALUES(2,'wa-1','tool',?,'terminal')", (content,))
    item = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1', latest=True, limit=1)['items'][0]
    assert item.get('status') == expected
    assert item['content'] == content
    assert item['name'] == 'terminal'


@pytest.mark.parametrize(('headers', 'expected'), [
    ('--- ✓ TASK 1/1: Work  (status=completed, api_calls=2, 4s) ---\nReport', 'completed'),
    ('--- ✗ TASK 1/1: Work  (status=error) ---\nFailure report', 'failed'),
    ('--- ✗ TASK 1/1: Work\nwith a multiline goal  (status=error) ---\nFailure report', 'failed'),
    ('--- ✗ TASK 1/1: Work  (status=timeout) ---\nFailure report', 'failed'),
    ('--- ✓ TASK 1/2: A  (status=success) ---\nA\n--- ✗ TASK 2/2: B  (status=failed) ---\nB', 'mixed'),
    ('--- ⚠ TASK 1/1: Work  (status=completed, TRUNCATED: hit max_iterations — work may be incomplete) ---', 'mixed'),
    ('--- ✗ TASK 1/1: Work  (status=?) ---', 'mixed'),
    ('Role: leaf   Model: ?   Total duration: 0s\n--- ERROR ---\nThe batch did not complete successfully: interrupted', 'failed'),
    ('No per-task status retained', 'completed'),
])
def test_system_batch_envelope_is_tool_report_with_honest_status(tmp_path, headers, expected):
    db = tmp_path / 'state.db'
    create_native_db(db)
    content = '[ASYNC DELEGATION BATCH COMPLETE — deleg_fixture]\n' + headers
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'wa-1','user',?)", (content,))
    before = db.read_bytes()
    item = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1', latest=True, limit=1)['items'][0]
    assert item['role'] == 'tool'
    assert item['name'] == 'delegate_task'
    assert item['kind'] == 'delegation'
    assert item['status'] == expected
    assert item['content'] == content
    assert db.read_bytes() == before


@pytest.mark.parametrize('content', [
    'Please explain delegation completion',
    'I saw [ASYNC DELEGATION BATCH COMPLETE — deleg_fixture]\nWhat does this mean?',
    '[ASYNC DELEGATION BATCH COMPLETE — deleg_fixture] is a quoted example',
    ' [ASYNC DELEGATION BATCH COMPLETE — deleg_fixture]\nIndented quote',
    '[ASYNC DELEGATION BATCH COMPLETE — ]\nNot a valid header',
    '[ASYNC DELEGATION BATCH COMPLETE — unfinished',
])
def test_ordinary_user_mentions_are_not_reclassified(tmp_path, content):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'wa-1','user',?)", (content,))
    item = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1', latest=True, limit=1)['items'][0]
    assert item['role'] == 'user'
    assert item['content'] == content
    assert 'kind' not in item


def test_unknown_native_effect_is_not_upgraded_to_success(tmp_path):
    db = tool_database(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content,tool_name,effect_disposition) VALUES(2,'wa-1','tool',?, 'terminal','unknown')", ('{"success":true}',))
    item = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1', limit=1, latest=True)['items'][0]
    assert item['status'] == 'unknown'


def test_chat_inventory_excludes_worker_sources_before_search_count_and_paging(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)', [
            ('worker-subagent', 'CLI worker', 'subagent', 10, 10, 'cli-1'),
            ('worker-delegate', 'CLI worker', 'delegate', 11, 11, None),
            ('continuation', 'CLI continued', 'cli', 9, 9, 'cli-1'),
        ])
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    page = catalog.sessions('default', limit=1, q='CLI')
    assert page['total'] == 2
    assert [item['id'] for item in page['items']] == ['continuation']
    assert catalog.sessions('default', limit=1, offset=1, q='CLI')['items'][0]['id'] == 'cli-1'
    assert catalog.sessions('default', q='worker')['total'] == 0
    assert catalog.sessions('default')['total'] == 3
    assert catalog.messages('default', 'worker-subagent')['items'] == []
    assert db.read_bytes() == before


def test_messages_endpoint_accepts_latest_without_changing_default_pages(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import Settings, create_app
    from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll

    home = tmp_path / 'hermes'
    home.mkdir()
    create_native_db(home / 'state.db')
    with sqlite3.connect(home / 'state.db') as c:
        c.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'wa-1','user','Latest')")
    app = create_app(Settings(state_dir=tmp_path/'app', profiles={'default': home}, bootstrap_secret=BOOTSTRAP))
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        assert client.get(BASE + '/sessions/wa-1/messages?latest=true').status_code == 401
        enroll(client)
        result = client.get(BASE + '/sessions/wa-1/messages?limit=1&latest=true')
        assert result.status_code == 200
        assert result.json()['items'][0]['content'] == 'Latest'
        assert result.json()['offset'] == 1
        assert result.json()['total'] == 2
        assert client.get(BASE + '/sessions/wa-1/messages?limit=1').json()['items'][0]['id'] == 1
        assert client.get(BASE + '/sessions/wa-1/messages?limit=1&offset=1&latest=false').json()['items'][0]['id'] == 2
        assert client.get(BASE + '/sessions/wa-1/messages?latest=not-a-bool').status_code == 422


def test_chat_inventory_honors_optional_native_delegation_marker(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE sessions ADD COLUMN model_config TEXT')
        c.execute("UPDATE sessions SET model_config=? WHERE id='cli-1'", (json.dumps({'_delegate_from': 'wa-1'}),))
        c.execute("UPDATE sessions SET model_config='malformed legacy config' WHERE id='wa-1'")
    catalog = NativeCatalog({'default': tmp_path})
    assert catalog.sessions('default', limit=1)['total'] == 1
    assert catalog.sessions('default', limit=1)['items'][0]['id'] == 'wa-1'
    assert catalog.sessions('default', q='cli-1')['total'] == 0
    assert catalog.messages('default', 'cli-1')['total'] == 0


@pytest.mark.parametrize('visibility', ['legacy', 'active', 'compacted'])
def test_latest_messages_returns_chronological_tail_with_raw_pagination_offset(tmp_path, visibility):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.executemany('INSERT INTO messages(id,session_id,role,content,timestamp) VALUES(?,?,?,?,?)',
                      [(i, 'wa-1', 'user', str(i), 100 - i) for i in range(2, 9)])
        if visibility != 'legacy':
            c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            c.execute('UPDATE messages SET active=0 WHERE id IN (2,8)')
        if visibility == 'compacted':
            c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
            c.execute('UPDATE messages SET compacted=1 WHERE id=2')
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    ids = {'legacy': list(range(1, 9)), 'active': [1,3,4,5,6,7], 'compacted': list(range(1,8))}[visibility]
    result = catalog.messages('default', 'wa-1', limit=3, offset=999, latest=True)
    assert [item['id'] for item in result['items']] == ids[-3:]
    assert result['offset'] == len(ids) - 3
    assert result['total'] == len(ids)
    ordinary = catalog.messages('default', 'wa-1', limit=3, offset=1)
    assert [item['id'] for item in ordinary['items']] == ids[1:4]
    assert ordinary['offset'] == 1
    assert catalog.messages('default', 'wa-1', limit=500, latest=True)['offset'] == 0
    assert catalog.messages('default', 'cli-1', latest=True) == {'items': [], 'total': 0, 'offset': 0}
    assert db.read_bytes() == before
