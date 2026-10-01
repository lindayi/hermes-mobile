"""Public conversation search uses synthetic native and private app state only."""
import json
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_native_catalog import create_native_db


def test_body_only_matches_beyond_first_page_count_and_dedupe(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)', [
            (f's{i}', 'Unrelated title', 'cli', i + 10, i + 10, None) for i in range(110)])
        c.execute("UPDATE messages SET content='The brass otter answer' WHERE id=1")
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('wa-1','user','brass otter question')")
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('s0','user','brass otter second session')")
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    first = catalog.sessions('default', q='brass otter', limit=1)
    assert first['total'] == 2
    assert [row['id'] for row in first['items']] == ['s0']
    assert catalog.sessions('default', q='brass otter', limit=1, offset=1)['items'][0]['id'] == 'wa-1'
    assert catalog.sessions('default', q='brass otter', offset=2) == {'items': [], 'total': 2}
    assert db.read_bytes() == before


@pytest.mark.parametrize('row', [
    {'role': 'system', 'content': 'secretneedle'},
    {'role': 'tool', 'content': 'secretneedle'},
    {'role': 'assistant', 'content': 'Public', 'reasoning_content': 'secretneedle'},
    {'role': 'assistant', 'content': 'Public', 'tool_calls': '[{"arguments":"secretneedle"}]'},
    {'role': 'user', 'content': 'secretneedle', 'display_kind': 'hidden'},
    {'role': 'assistant', 'content': '<think>secretneedle</think>Public'},
    {'role': 'assistant', 'content': '<think>secretneedle'},
    {'role': 'user', 'content': 'secretneedle', 'active': 0, 'compacted': 0},
    {'role': 'user', 'content': '[CONTEXT SUMMARY]:\nsecretneedle', 'display_kind': 'hidden'},
    {'role': 'user', 'content': '[ASYNC DELEGATION BATCH COMPLETE — batch]\nsecretneedle'},
])
def test_hidden_private_and_scaffolding_never_create_search_matches(tmp_path, row):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        for sql in ('active INTEGER DEFAULT 1', 'compacted INTEGER DEFAULT 0', 'display_kind TEXT'):
            c.execute('ALTER TABLE messages ADD COLUMN ' + sql)
        row = dict(row, session_id='wa-1')
        c.execute('INSERT INTO messages (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    assert NativeCatalog({'default': tmp_path}).sessions('default', q='secretneedle')['total'] == 0


def test_archived_public_content_and_literal_queries_remain_searchable(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    text = "Literal 20%_\\' OR 1=1 -- and archive-only"
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
        c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        c.execute('UPDATE messages SET active=0,compacted=1,content=? WHERE id=1', (text,))
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('cli-1','user','Literal 200anything')")
    catalog = NativeCatalog({'default': tmp_path})
    for q in ("20%_\\' OR 1=1 --", 'archive-only'):
        assert [row['id'] for row in catalog.sessions('default', q=q)['items']] == ['wa-1']
    assert catalog.sessions('default', q="not found' OR 1=1 --")['total'] == 0


def test_runtime_marker_search_respects_explicit_human_provenance(tmp_path):
    from test_runtime_notice_catalog import NUDGE
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN platform_message_id TEXT')
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('wa-1','user',?)", (NUDGE,))
        c.execute("INSERT INTO messages(session_id,role,content,platform_message_id) VALUES('cli-1','user',?,'human')", (NUDGE,))
    rows = NativeCatalog({'default': tmp_path}).sessions('default', q='maximum number of tool-calling')['items']
    assert [row['id'] for row in rows] == ['cli-1']


def test_only_public_multimodal_text_and_commentary_are_searchable(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    blocks = [{'type': 'text', 'text': 'Visible blocks'},
              {'type': 'image_url', 'image_url': {'url': 'https://secretneedle.invalid'}},
              {'type': 'thinking', 'thinking': 'secretneedle'}]
    commentary = [{'type': 'message', 'phase': 'commentary', 'content': [
        {'type': 'output_text', 'text': 'Visible sidecar'},
        {'type': 'reasoning_text', 'text': 'secretneedle'}]},
        {'type': 'reasoning', 'text': 'secretneedle'}]
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN codex_message_items TEXT')
        c.execute('UPDATE messages SET content=?,codex_message_items=? WHERE id=1', (json.dumps(blocks), json.dumps(commentary)))
    catalog = NativeCatalog({'default': tmp_path})
    assert catalog.sessions('default', q='secretneedle')['total'] == 0
    for term in ('Visible blocks', 'Visible sidecar'):
        assert catalog.sessions('default', q=term)['items'][0]['id'] == 'wa-1'


def test_search_projects_each_eligible_body_at_most_once(tmp_path, monkeypatch):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.executemany("INSERT INTO messages(session_id,role,content) VALUES('wa-1','user',?)",
                      [('needle public ' + str(i),) for i in range(100)])
        c.execute("INSERT INTO sessions VALUES('worker','worker','subagent',9,9,NULL)")
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('worker','user','hidden worker')")
    seen = []
    original = search._public_body
    def project(*args):
        seen.append(args[1])
        return original(*args)
    monkeypatch.setattr(search, '_public_body', project)
    page = NativeCatalog({'default': tmp_path}).sessions('default', q='needle', limit=1)
    assert page['total'] == 1
    assert len(seen) <= 101
    assert 'hidden worker' not in seen


def test_search_budget_aborts_instead_of_partial_count(tmp_path, monkeypatch):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.executemany("INSERT INTO messages(session_id,role,content) VALUES('wa-1','user',?)",
                      [('needle ' + str(i),) for i in range(400)])
    monkeypatch.setattr(search, 'MAX_SEARCH_STEPS', 1, raising=False)
    with pytest.raises(sqlite3.OperationalError):
        NativeCatalog({'default': tmp_path}).sessions('default', q='needle')


@pytest.mark.parametrize('field', ['content', 'codex_message_items'])
def test_oversized_public_field_aborts_without_passing_huge_values_to_python(tmp_path, monkeypatch, field):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN codex_message_items TEXT')
        c.execute('UPDATE messages SET ' + field + '=? WHERE id=1', ('needle ' * 100,))
    monkeypatch.setattr(search, 'MAX_SEARCH_FIELD_BYTES', 64, raising=False)
    original = search._public_body
    sizes = []
    def bounded(*args):
        sizes.extend(len(arg) for arg in args if isinstance(arg, str))
        return original(*args)
    monkeypatch.setattr(search, '_public_body', bounded)
    with pytest.raises(sqlite3.OperationalError):
        NativeCatalog({'default': tmp_path}).sessions('default', q='needle')
    assert all(size <= 64 for size in sizes)


@pytest.mark.parametrize('schema', ['none', 'partial', 'legacy', 'active', 'archive'])
def test_search_optional_message_schema_falls_back_safely(tmp_path, schema):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        if schema in ('none', 'partial'):
            c.execute('DROP TABLE messages')
            if schema == 'partial':
                c.execute('CREATE TABLE messages(session_id TEXT, role TEXT)')
        elif schema in ('active', 'archive'):
            c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            c.execute('UPDATE messages SET active=0')
            if schema == 'archive':
                c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 1')
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    assert catalog.sessions('default', q='CLI')['items'][0]['id'] == 'cli-1'
    assert catalog.sessions('default', q='Retained answer')['total'] == (schema in ('legacy', 'archive'))
    assert db.read_bytes() == before


def test_search_deadline_fails_closed_and_query_cap_is_compatible(tmp_path, monkeypatch):
    import backend.catalog_search as search
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET content=?', ('z' * 200,))
    catalog = NativeCatalog({'default': tmp_path})
    assert catalog.sessions('default', q='z' * 201)['total'] == 1
    monkeypatch.setattr(search, 'MAX_SEARCH_SECONDS', -1)
    with pytest.raises(sqlite3.OperationalError):
        catalog.sessions('default', q='anything')


def test_search_never_reads_reasoning_api_content_or_raw_tool_arguments(tmp_path, monkeypatch):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN api_content TEXT')
    catalog = NativeCatalog({'default': tmp_path})
    connect = catalog._connect
    reads = []
    def audited(profile):
        c = connect(profile)
        def authorizer(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == 'messages':
                reads.append(column)
                if column in ('reasoning', 'reasoning_content', 'api_content', 'tool_calls'):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c
    monkeypatch.setattr(catalog, '_connect', audited)
    assert catalog.sessions('default', q='Retained')['total'] == 1
    assert 'content' in reads


def test_kind_owner_profile_and_deleted_filters_apply_before_count(tmp_path):
    from backend.runs import RunJournal
    from backend.session_deletion import SessionDeletion
    profiles = {}
    for name in ('default', 'member'):
        home = tmp_path / name
        home.mkdir()
        profiles[name] = home
        create_native_db(home / 'state.db')
        with sqlite3.connect(home / 'state.db') as c:
            c.execute('ALTER TABLE sessions ADD COLUMN model_config TEXT')
            c.execute('UPDATE messages SET content=?', (name + ' needle',))
            for source in ('cron', 'agent_test', 'subagent', 'delegate'):
                c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)', (source, 'Other', source, 10, 10, None, None))
                c.execute('INSERT INTO messages(session_id,role,content) VALUES(?,?,?)', (source, 'user', 'needle'))
            c.execute("INSERT INTO sessions VALUES('hidden','Other','cli',11,11,NULL,?)", (json.dumps({'_delegate_from':'s'}),))
            c.execute("INSERT INTO messages(session_id,role,content) VALUES('hidden','user','needle')")
    catalog = NativeCatalog(profiles)
    journal = RunJournal(tmp_path / 'journal.sqlite')
    journal.claim_deletion('owner', 'default', 'wa-1')
    deletion = SessionDeletion(journal)
    owner = dict(id='owner', profile='default', role='owner')
    member = dict(id='member', profile='member', role='member')
    assert deletion.sessions(catalog, owner, 1, 0, 'needle', 'chats')['total'] == 0
    assert deletion.sessions(catalog, member, 1, 0, 'needle', 'chats')['total'] == 1
    assert deletion.sessions(catalog, member, 1, 0, 'default needle', 'all')['total'] == 0
    assert deletion.sessions(catalog, dict(owner, id='another'), 1, 0, 'needle', 'chats')['total'] == 1
    for kind, expected in [('cron', 1), ('tests', 1), ('all', 2)]:
        assert deletion.sessions(catalog, owner, 1, 0, 'needle', kind)['total'] == expected


def test_representative_search_streams_thirty_thousand_messages(tmp_path):
    import time
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute('CREATE INDEX messages_session ON messages(session_id)')
        c.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                      ((f's{i}', 'Title', 'cli', i + 10, i + 10, None) for i in range(300)))
        c.executemany('INSERT INTO messages(session_id,role,content) VALUES(?,?,?)',
                      ((f's{i % 300}', 'assistant' if i % 2 else 'user', 'Public history ' * 20 + (' needle' if i % 300 == 0 else ''))
                       for i in range(30_000)))
    started = time.monotonic()
    result = NativeCatalog({'default': tmp_path}).sessions('default', q='needle')
    elapsed = time.monotonic() - started
    assert result['total'] == 1 and result['items'][0]['id'] == 's0'
    print(f' synthetic_search: 300 sessions / 30000 messages / {elapsed:.3f}s')
    db.unlink()
