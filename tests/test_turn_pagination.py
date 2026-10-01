"""Real SQLite pagination regressions; all writes are isolated test fixtures."""
import json
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_chat_snapshot import chat


ENVELOPE = '[ASYNC DELEGATION BATCH COMPLETE — fixture]\n--- ✓ TASK 1/1: Task  (status=completed) ---\nReport'


def database(tmp_path, rows, *, legacy=False):
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY);
            INSERT INTO sessions VALUES('s');
            CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT DEFAULT 's',
                role TEXT, content TEXT, tool_calls TEXT, timestamp REAL,
                tool_call_id TEXT, reasoning_content TEXT DEFAULT 'PRIVATE REASONING');
        ''')
        if not legacy:
            c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        c.executemany('INSERT INTO messages(role,content,tool_calls,tool_call_id,timestamp) VALUES(?,?,?,?,?)',
                      [(role, content, calls, call_id, index) for index, (role, content, calls, call_id) in enumerate(rows)])
    return NativeCatalog({'default': tmp_path}), db


def turn(name, tools):
    rows = [('user', name, None, None)]
    for index in range(tools):
        call_id = f'{name}-{index}'
        calls = json.dumps([{'id': call_id, 'function': {'name': 'read_file', 'arguments': '{"path":"docs/file.md"}'}}])
        rows.extend([('assistant', '', calls, None), ('tool', '{"success":true}', None, call_id)])
    rows.append(('assistant', name + ' done', None, None))
    return rows


def page(catalog, **kwargs):
    return catalog.messages('default', 's', **kwargs, turn_boundary=True)


@pytest.mark.parametrize(('tools', 'limit'), [(70, 100), (310, 100), (310, 500)])
def test_latest_expands_to_user_before_long_tool_sequence(tmp_path, tools, limit):
    earlier = turn('earlier', 0)
    newest = turn('newest', tools)
    catalog, db = database(tmp_path, earlier + newest)
    before = db.read_bytes()
    result = page(catalog, latest=True, limit=limit)
    assert result['offset'] == len(earlier), 'fixed-row latest pages must expand to the genuine user'
    assert [item['id'] for item in result['items']] == list(range(len(earlier) + 1, len(earlier + newest) + 1))
    assert result['count'] == len(newest)
    assert result['turn_boundary']['complete'] is True
    assert result['turn_boundary']['truncated'] is False
    assert 'PRIVATE REASONING' not in str(result)
    calls = {call['id'] for item in result['items'] for call in item.get('tool_calls') or []}
    results = {item['tool_call_id'] for item in result['items'] if item['role'] == 'tool'}
    assert calls == results
    assert all(item['name'] == 'read_file' for item in result['items'] if item['role'] == 'tool')
    assert db.read_bytes() == before


def test_pathological_turn_has_bounded_contiguous_suffix_and_explicit_flags(tmp_path):
    rows = turn('huge', 5100)
    catalog, db = database(tmp_path, rows)
    result = page(catalog, latest=True, limit=500)
    assert result['count'] <= 10_000, 'whole-turn expansion must not load an unbounded transcript'
    assert result['count'] == result['turn_boundary']['max_rows']
    assert result['offset'] + result['count'] == len(rows)
    assert result['turn_boundary']['truncated'] is True
    assert result['turn_boundary']['complete'] is False
    assert result['turn_boundary']['reason'] == 'row_cap'
    older = page(catalog, offset=0, limit=result['offset'])
    ids = [item['id'] for item in older['items'] + result['items']]
    assert ids == list(range(1, len(rows) + 1))
    assert older['turn_boundary']['complete'] is True


def test_expansion_byte_budget_flags_partial_turn_without_dropping_bodies(tmp_path):
    huge = 'x' * (9 * 1024 * 1024)
    rows = [('user', 'big result', None, None), ('tool', huge, None, 'big')]
    rows += [('assistant', str(index), None, None) for index in range(120)]
    catalog, _ = database(tmp_path, rows)
    result = page(catalog, latest=True, limit=100)
    assert result['offset'] == 2, 'oversized expansion payload must stay outside this bounded suffix'
    assert result['count'] == 120
    assert result['turn_boundary']['reason'] == 'byte_cap'
    assert result['turn_boundary']['complete'] is False
    older = page(catalog, offset=0, limit=2)
    assert older['items'][1]['content'] == huge  # requested-row semantics are unchanged
    assert older['turn_boundary']['complete'] is True


def test_composed_journal_turn_is_aligned_in_the_filtered_sequence(chat):
    from test_chat_snapshot import admit
    app, _, user, db = chat
    run = admit(chat, text='Journal-only earlier')
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Earlier answer')
    active = admit(chat, text='Active now', key='next')
    state = app.state.journal.snapshot_state(user['id'], 'default', 'wa-1')
    before = db.read_bytes()
    result = app.state.catalog.messages('default', 'wa-1', limit=1, latest=True,
                                        snapshot=state, turn_boundary=True)
    assert result['offset'] == 1, 'journal-only answer must expand to its journal user'
    assert [item['content'] for item in result['items']] == ['Journal-only earlier', 'Earlier answer']
    assert all(item['source'] == 'journal' for item in result['items'])
    assert result['count'] == 2 and result['total'] == 3
    assert result['run']['id'] == active['id']
    assert result['snapshot'] == {'mode': 'overlay', 'anchored': True, 'reconciliation': 'conservative-union'}
    assert db.read_bytes() == before


def test_snapshot_expands_later_external_turn_without_swallowing_active_overlay(chat):
    from backend.chat_snapshot import conversation_snapshot
    from test_chat_snapshot import admit, persist
    from test_latest_tool_replay import record_tool
    app, _, user, db = chat
    earlier = turn('Earlier', 70)
    persist(chat, [row[:3] for row in earlier])
    active = admit(chat, text='Active web')
    app.state.journal.set_upstream(user['id'], active['id'], 'fixture-upstream')
    event_ids = record_tool(chat, active, call_id='web-tool')
    persist(chat, [('user', 'Active web', None), ('tool', 'Active partial', None)])
    external = turn('Later CLI', 310)
    persist(chat, [row[:3] for row in external])
    before = db.read_bytes()
    result = conversation_snapshot(app.state.catalog, app.state.journal, user, 'wa-1',
                                   limit=100, latest=True, turn_boundary=True)
    assert result['offset'] == 1 + len(earlier), 'latest external CLI tools need their own real user'
    assert result['items'][0]['content'] == 'Later CLI'
    assert result['count'] == len(external)
    assert result['run']['id'] == active['id'] and result['run']['status'] == 'running'
    assert [event['id'] for event in result['tool_replay']['events']] == event_ids
    assert result['snapshot'] == {'mode': 'overlay', 'anchored': True, 'reconciliation': 'conservative-union'}
    assert 'Active partial' not in str(result)
    older = conversation_snapshot(app.state.catalog, app.state.journal, user, 'wa-1',
                                  limit=100, offset=result['offset'] - 100, turn_boundary=True)
    assert older['offset'] == 1
    assert older['count'] == len(earlier)
    assert older['offset'] + older['count'] == result['offset']
    assert not {item['id'] for item in older['items']} & {item['id'] for item in result['items']}
    assert older['run'] == result['run'] and older['tool_replay'] == result['tool_replay']
    assert db.read_bytes() == before


def test_older_pages_preserve_complete_turns_and_ignore_delegation_user_envelopes(tmp_path):
    prefix = [('system', 'context', None, None), ('assistant', 'retained', None, None)]
    first, middle, newest = turn('First', 70), turn('Middle', 310), turn('Newest', 70)
    middle.insert(len(middle) - 110, ('user', ENVELOPE, None, None))
    rows = prefix + first + middle + newest
    catalog, db = database(tmp_path, rows)
    before = db.read_bytes()
    current = page(catalog, latest=True, limit=100)
    seen = current['items']
    starts = [current['offset']]
    while current['offset']:
        limit = min(100, current['offset'])
        previous = page(catalog, limit=limit, offset=current['offset'] - limit)
        assert previous['offset'] + previous['count'] == current['offset']
        assert previous['turn_boundary']['complete'] is True
        seen = previous['items'] + seen
        starts.append(previous['offset'])
        current = previous
    assert starts == [len(prefix + first + middle), len(prefix + first), len(prefix), 0]
    assert [item['id'] for item in seen] == list(range(1, len(rows) + 1))
    report = next(item for item in seen if item['content'] == ENVELOPE)
    assert report['role'] == 'tool' and report['kind'] == 'delegation'
    assert db.read_bytes() == before


@pytest.mark.parametrize('content', ['Please explain delegation', 'I saw ' + ENVELOPE,
                                     ' ' + ENVELOPE, ENVELOPE.split('\n')[0] + ' is a quote'])
def test_ordinary_user_mentions_are_boundaries_not_agent_rows(tmp_path, content):
    rows = turn('Old', 0) + [('user', content, None, None)]
    rows += [('assistant', 'agent commentary', None, None), ('tool', 'result', None, None)]
    catalog, _ = database(tmp_path, rows)
    result = page(catalog, latest=True, limit=1)
    assert result['offset'] == 2
    assert result['items'][0]['content'] == content
    assert result['items'][0]['role'] == 'user'
    assert result['count'] == 3


@pytest.mark.parametrize('legacy', [False, True])
def test_no_user_prefix_and_empty_legacy_streams_are_complete(tmp_path, legacy):
    rows = [('system', 'context', None, None), ('assistant', 'retained', None, None),
            ('user', ENVELOPE, None, None)] + [('tool', 'result', None, None)] * 120
    catalog, db = database(tmp_path, rows, legacy=legacy)
    result = page(catalog, latest=True, limit=100)
    assert result['offset'] == 0 and result['count'] == len(rows)
    assert result['turn_boundary']['complete'] is True
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO sessions VALUES('empty')")
    empty = catalog.messages('default', 'empty', latest=True, turn_boundary=True)
    assert empty['items'] == [] and empty['offset'] == empty['count'] == empty['total'] == 0
    assert empty['turn_boundary']['complete'] is True
    beyond = page(catalog, offset=1000)
    assert beyond['items'] == [] and beyond['offset'] == 1000 and beyond['count'] == 0


def test_raw_latest_and_offset_clients_keep_exact_row_slices(tmp_path):
    catalog, _ = database(tmp_path, turn('Long', 310))
    health = catalog.messages('default', 's', latest=True, limit=1)
    assert list(health) == ['items', 'total', 'offset']
    assert len(health['items']) == 1 and health['offset'] == 621
    assert health == catalog.messages('default', 's', limit=1, offset=621)
    assert [row['id'] for row in catalog.messages('default', 's', limit=2, offset=5)['items']] == [6, 7]


def test_hidden_users_and_compaction_copies_do_not_shift_expanded_coordinates(tmp_path):
    rows = turn('Compacted', 310)
    catalog, db = database(tmp_path, rows)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET active=0,compacted=1')
        c.execute("INSERT INTO messages(session_id,role,content,tool_calls,timestamp,tool_call_id) "
                  'SELECT session_id,role,content,tool_calls,timestamp,tool_call_id FROM messages ORDER BY id')
        c.execute("INSERT INTO messages(role,content,active,compacted) VALUES('user','rewound',0,0)")
        c.execute("INSERT INTO messages(role,content,session_id) VALUES('user','foreign','other')")
    before = db.read_bytes()
    result = page(catalog, latest=True, limit=100)
    assert result['offset'] == 0 and result['count'] == result['total'] == len(rows)
    assert [row['id'] for row in result['items']] == list(range(len(rows) + 1, 2 * len(rows) + 1))
    assert 'rewound' not in str(result) and 'foreign' not in str(result)
    assert db.read_bytes() == before


@pytest.mark.parametrize('reason', ['row_cap', 'byte_cap'])
def test_composed_native_and_journal_pages_share_expansion_safety_budgets(chat, reason):
    from backend.chat_snapshot import conversation_snapshot
    from test_chat_snapshot import admit, persist
    app, _, user, _ = chat
    first = admit(chat, text='Unpersisted previous')
    app.state.journal.finish(user['id'], first['id'], 'completed', output='Previous final')
    active = admit(chat, text='Active web', key='active')
    if reason == 'row_cap':
        rows = turn('External', 5100)
        expected_count = 10_000
    else:
        rows = [('user', 'External', None, None), ('tool', 'x' * (9 * 1024 * 1024), None, None)]
        rows += [('assistant', str(i), None, None) for i in range(120)]
        expected_count = 120
    persist(chat, [row[:3] for row in rows])
    result = conversation_snapshot(app.state.catalog, app.state.journal, user, 'wa-1',
                                   limit=100, latest=True, turn_boundary=True)
    assert result['count'] == expected_count
    assert result['turn_boundary']['reason'] == reason
    assert result['turn_boundary']['truncated'] is True
    assert result['offset'] + result['count'] == result['total'] == len(rows) + 3
    assert result['run']['id'] == active['id']
    seen = result['items']
    while result['offset']:
        limit = min(100, result['offset'])
        previous = conversation_snapshot(app.state.catalog, app.state.journal, user, 'wa-1',
                                         offset=result['offset'] - limit, limit=limit, turn_boundary=True)
        assert previous['offset'] + previous['count'] == result['offset']
        seen = previous['items'] + seen
        result = previous
    assert len(seen) == len({item['id'] for item in seen}) == len(rows) + 3
    assert [item['content'] for item in seen[:4]] == ['Retained answer', 'Unpersisted previous',
                                                    'Previous final', 'External']
