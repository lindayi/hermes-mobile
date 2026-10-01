"""Retained process identity must agree with history, not merely equal text."""
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_native_catalog import create_native_db


CONTENT = ('[CONTEXT SUMMARY]:\nRetained process search needle\n'
           '--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---')


def retained_db(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        for column in ('active INTEGER DEFAULT 1', 'compacted INTEGER DEFAULT 0',
                       'display_kind TEXT', 'display_metadata TEXT', 'platform_message_id TEXT',
                       'tool_call_id TEXT', 'tool_name TEXT'):
            c.execute('ALTER TABLE messages ADD COLUMN ' + column)
        c.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active,compacted) VALUES(2,'cli-1','user',?,2,0,1)", (CONTENT,))
        c.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active,compacted) VALUES(3,'cli-1','assistant','Prior answer',3,0,1)")
        c.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active,compacted) VALUES(4,'cli-1','user',?,2,1,0)", (CONTENT,))
    return db, NativeCatalog({'default': tmp_path})


def test_retained_compression_copy_is_not_searchable(tmp_path):
    db, catalog = retained_db(tmp_path)
    before = db.read_bytes()
    rows = catalog.messages('default', 'cli-1')['items']
    assert [row['id'] for row in rows] == [3, 4]
    assert rows[-1]['kind'] == 'context_compression'
    assert rows[-1]['content'] == CONTENT
    assert catalog.sessions('default', q='Retained process search needle')['total'] == 0
    assert catalog.sessions('default', q='Prior answer')['total'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize(('column', 'value'), [
    ('timestamp', 20), ('timestamp', None), ('role', 'assistant'),
    ('display_kind', 'human'), ('display_metadata', '{"source":"human"}'),
    ('platform_message_id', 'human-message-4'), ('tool_calls', '[]'),
    ('tool_call_id', 'distinct-tool-call'), ('tool_name', 'distinct-tool'),
])
def test_equal_body_with_distinct_identity_remains_searchable(tmp_path, column, value):
    db, catalog = retained_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute(f'UPDATE messages SET {column}=? WHERE id=4', (value,))
    before = db.read_bytes()
    row = catalog.messages('default', 'cli-1')['items'][-1]
    assert row['id'] == 4 and row.get('kind') != 'context_compression'
    assert row['content'] == CONTENT
    result = catalog.sessions('default', q='Retained process search needle')
    assert result['total'] == 1
    assert [item['id'] for item in result['items']] == ['cli-1']
    assert db.read_bytes() == before


def test_ambiguous_active_copies_remain_searchable(tmp_path):
    db, catalog = retained_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content,timestamp) VALUES(5,'cli-1','user',?,2)", (CONTENT,))
    before = db.read_bytes()
    rows = catalog.messages('default', 'cli-1')['items']
    assert [row['id'] for row in rows] == [2, 3, 4, 5]
    assert all(row.get('kind') != 'context_compression' for row in rows[-2:])
    assert catalog.sessions('default', q='Retained process search needle')['total'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize('missing', ['timestamp', 'tool_calls'])
def test_partial_schema_does_not_attempt_unprovable_copy_identity(tmp_path, missing):
    db, catalog = retained_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages DROP COLUMN ' + missing)
    before = db.read_bytes()
    # Without a complete native identity, search must not guess or fail SQL.
    assert catalog.sessions('default', q='Retained process search needle')['total'] == 1
    assert db.read_bytes() == before


def test_copy_lookup_only_for_eligible_process_sessions(tmp_path, monkeypatch):
    import backend.native_catalog as native
    db, catalog = retained_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)', [
            ('worker', 'Other', 'subagent', 5, 5, None),
            ('deleted', 'Other', 'cli', 5, 5, None)])
        c.executemany('INSERT INTO messages(session_id,role,content,timestamp,active,compacted) VALUES(?,?,?,?,0,1)', [
            ('worker', 'user', CONTENT, 2), ('deleted', 'user', CONTENT, 2)])
    original = native._native_rewrites
    calls = []

    def rewrites(c, sid, columns):
        calls.append(sid)
        result = original(c, sid, columns)
        assert all(isinstance(old, int) and isinstance(new, int) for old, new in result.items())
        return result

    def forbidden(*args, **kwargs):
        pytest.fail('Search must not materialize a transcript')

    monkeypatch.setattr(native, '_native_rewrites', rewrites)
    monkeypatch.setattr(catalog, 'messages', forbidden)
    before = db.read_bytes()
    assert catalog.sessions('default', q='Retained process search needle', excluded_ids=['deleted'])['total'] == 0
    assert calls == ['cli-1']
    assert db.read_bytes() == before
