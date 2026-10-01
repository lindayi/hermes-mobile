"""Search must follow raw public projection; only synthetic native state."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.native_catalog import NativeCatalog
from backend.runtime_notice_presentation import MAX_ITERATIONS_SUMMARY_REQUEST as NUDGE
from test_native_catalog import create_native_db


@pytest.mark.parametrize('form', ['tool_calls', 'text_blocks'])
def test_visible_exact_marker_is_searchable_without_changing_bytes(tmp_path, form):
    db = tmp_path / 'state.db'
    create_native_db(db)
    content = json.dumps([{'type': 'text', 'text': NUDGE}]) if form == 'text_blocks' else NUDGE
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content,tool_calls) VALUES(2,'cli-1','user',?,?)",
                  (content, '[]' if form == 'tool_calls' else None))
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    row = catalog.messages('default', 'cli-1')['items'][0]
    assert row['role'] == 'user' and row.get('kind') != 'runtime_notice'
    assert row['content'] == content
    result = catalog.sessions('default', q='maximum number of tool-calling')
    assert result['total'] == 1
    assert [item['id'] for item in result['items']] == ['cli-1']
    assert db.read_bytes() == before


@pytest.mark.parametrize('schema', ['missing', 'partial', 'empty'])
def test_expired_title_only_fallback_raises(tmp_path, monkeypatch, schema):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('DROP TABLE messages')
        if schema == 'partial':
            c.execute('CREATE TABLE messages(session_id TEXT,role TEXT)')
        elif schema == 'empty':
            c.execute('CREATE TABLE messages(session_id TEXT,role TEXT,content TEXT)')
    monkeypatch.setattr(search, 'MAX_SEARCH_SECONDS', -1)
    with pytest.raises(sqlite3.OperationalError):
        NativeCatalog({'default': tmp_path}).sessions('default', q='CLI')


@pytest.mark.parametrize('boundary', ['count', 'page'])
def test_deadline_crossed_during_final_count_or_page_raises(tmp_path, monkeypatch, boundary):
    import backend.catalog_search as search
    create_native_db(tmp_path / 'state.db')
    now = [0.0]
    monkeypatch.setattr(search, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    catalog = NativeCatalog({'default': tmp_path})
    connect = catalog._connect

    def delayed(profile):
        c = connect(profile)
        def trace(sql):
            target = 'SELECT count(*) FROM sessions' if boundary == 'count' else 'SELECT id,title,source'
            if sql.startswith(target):
                now[0] = search.MAX_SEARCH_SECONDS + 1
        c.set_trace_callback(trace)
        return c

    monkeypatch.setattr(catalog, '_connect', delayed)
    with pytest.raises(sqlite3.OperationalError):
        catalog.sessions('default', q='CLI')


@pytest.mark.parametrize('form', ['standalone', 'complete', 'text_blocks'])
def test_visible_human_compression_paste_is_searchable(tmp_path, form):
    db = tmp_path / 'state.db'
    create_native_db(db)
    content = '[CONTEXT SUMMARY]:\nHuman alignment needle'
    if form == 'complete':
        content += '\n--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---'
    if form == 'text_blocks':
        content = json.dumps([{'type': 'text', 'text': content}])
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'cli-1','user',?)", (content,))
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    row = catalog.messages('default', 'cli-1')['items'][0]
    assert row['role'] == 'user' and row.get('kind') != 'context_compression'
    assert row['content'] == content
    assert catalog.sessions('default', q='Human alignment needle')['total'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize('generation', ['frontier', 'archived'])
def test_positive_raw_compression_process_is_not_searchable(tmp_path, generation):
    db = tmp_path / 'state.db'
    create_native_db(db)
    content = ('[CONTEXT SUMMARY]:\nPrivate process needle\n'
               '--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---')
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
        c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        c.execute("INSERT INTO messages(id,session_id,role,content,active,compacted) VALUES(2,'cli-1','user','Earlier human',0,1)")
        c.execute("INSERT INTO messages(id,session_id,role,content,active,compacted) VALUES(3,'cli-1','user',?,?,?)",
                  (content, int(generation == 'frontier'), int(generation == 'archived')))
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    rows = catalog.messages('default', 'cli-1')['items']
    assert rows[-1]['kind'] == 'context_compression'
    assert rows[-1]['content'] == content
    assert catalog.sessions('default', q='Private process needle')['total'] == 0
    assert catalog.sessions('default', q='Earlier human')['total'] == 1
    assert db.read_bytes() == before


def test_tool_conflict_only_crosses_sql_boundary_as_structural_fact(tmp_path, monkeypatch):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    secret = 'PRIVATE_TOOL_ARGUMENT_SENTINEL'
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content,tool_calls) VALUES(2,'cli-1','user',?,?)",
                  (NUDGE, json.dumps([{'arguments': secret}])))
    original = search._public_body
    seen = []
    def project(*args):
        seen.extend(args)
        return original(*args)
    monkeypatch.setattr(search, '_public_body', project)
    catalog = NativeCatalog({'default': tmp_path})
    assert catalog.sessions('default', q='maximum number of tool-calling')['total'] == 1
    assert catalog.sessions('default', q=secret)['total'] == 0
    assert secret not in repr(seen)


def test_process_candidate_classification_obeys_field_budget(tmp_path, monkeypatch):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    content = ('[CONTEXT SUMMARY]:\n' + 'Large process body ' * 30 + '\n'
               '--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---')
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
        c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        c.execute("INSERT INTO messages(id,session_id,role,content,active,compacted) VALUES(2,'cli-1','user',?,0,1)", (content,))
    monkeypatch.setattr(search, 'MAX_SEARCH_FIELD_BYTES', 64)
    with pytest.raises(sqlite3.OperationalError):
        NativeCatalog({'default': tmp_path}).sessions('default', q='Large process body')
