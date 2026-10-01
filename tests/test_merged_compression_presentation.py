"""Empty native merge-into-tail carriers: synthetic copies of observed shapes."""
import json
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_context_compression_presentation import PREFIXES, END
from test_native_catalog import create_native_db

EMPTY_PREFIX = ('[PRIOR CONTEXT — for reference only; not a new message]\n\n\n'
                '[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n\n')
CONTENT = EMPTY_PREFIX + PREFIXES[0] + '\nMerged scaffold needle\n\n' + END


def calls(arguments='original arguments', *, call_id='call-carrier'):
    return json.dumps([dict(id=call_id, call_id=call_id, response_item_id='item-carrier',
                            type='function', function=dict(name='delegate_task', arguments=arguments))])


def merged_db(tmp_path):
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        for column in ('active INTEGER DEFAULT 1', 'compacted INTEGER DEFAULT 0',
                       'display_kind TEXT', 'display_metadata TEXT', 'platform_message_id TEXT',
                       'tool_call_id TEXT', 'tool_name TEXT', 'codex_message_items TEXT', 'api_content TEXT'):
            c.execute('ALTER TABLE messages ADD COLUMN ' + column)
        c.executemany('INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp,active,compacted) '
                      "VALUES(?,'cli-1',?,?,?,?,?,?)", [
            (2, 'assistant', '', calls(), 2, 0, 1),
            (3, 'user', 'Real retained request', None, 3, 1, 0),
            (4, 'tool', 'Prior result', None, 4, 1, 0),
            (5, 'assistant', CONTENT, calls('pruned arguments'), 2, 1, 0),
            (6, 'tool', '{"success":true}', None, 5, 1, 0),
            (7, 'assistant', 'Real final answer', None, 6, 1, 0),
        ])
        c.execute("UPDATE messages SET tool_call_id='call-carrier' WHERE id=6")
        c.execute("UPDATE messages SET reasoning='PRIVATE REASONING', reasoning_content='PRIVATE REASONING', "
                  "api_content='PRIVATE API REPLAY' WHERE id=5")
    return db, NativeCatalog({'default': tmp_path})


def test_empty_merged_assistant_carrier_folds_with_archived_identity(tmp_path):
    db, catalog = merged_db(tmp_path)
    before = db.read_bytes()
    result = catalog.messages('default', 'cli-1', latest=True, limit=1, turn_boundary=True)
    row = next(item for item in result['items'] if item['id'] == 5)
    assert row.get('kind') == 'context_compression'
    assert (row['role'], row['name'], row['status']) == ('tool', 'Context compression', 'completed')
    assert row['content'] == CONTENT
    assert row['tool_calls'][0]['id'] == 'call-carrier'
    tool = next(item for item in result['items'] if item['id'] == 6)
    assert tool['name'] == 'delegate_task' and tool['tool_call_id'] == 'call-carrier'
    assert result['items'][0]['id'] == 3 and result['turn_boundary']['complete']
    assert all(secret not in json.dumps(result) for secret in ('PRIVATE REASONING', 'PRIVATE API REPLAY'))
    assert db.read_bytes() == before
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT role,content,tool_calls FROM messages WHERE id=5').fetchone() == (
            'assistant', CONTENT, calls('pruned arguments'))


def test_empty_merged_compression_search_uses_same_process_identity(tmp_path):
    db, catalog = merged_db(tmp_path)
    before = db.read_bytes()
    assert catalog.sessions('default', q='Merged scaffold needle')['total'] == 0
    assert catalog.sessions('default', q='Real retained request')['total'] == 1
    assert catalog.sessions('default', q='Real final answer')['total'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize('case', [
    'no-source', 'source-rewound', 'source-active', 'source-human-content',
    'timestamp', 'call-id', 'call-name', 'call-type', 'response-item',
    'platform', 'metadata', 'display-kind', 'source-platform', 'source-metadata',
    'source-display-kind', 'user-role', 'nonempty-prior', 'quoted', 'unknown-header',
    'suffix-live-ask', 'missing-end', 'extra-delimiter', 'text-blocks',
    'malformed-calls', 'empty-calls', 'scalar-calls', 'primitive-call',
    'missing-call-id', 'duplicate-call-id', 'ambiguous-source', 'ambiguous-active',
])
def test_merged_lookalikes_and_conflicting_carriers_stay_visible(tmp_path, case):
    db, catalog = merged_db(tmp_path)
    changes = {
        'source-rewound': (2, 'compacted', 0), 'source-active': (2, 'active', 1),
        'source-human-content': (2, 'content', 'Preserved assistant words'),
        'timestamp': (5, 'timestamp', 200),
        'call-id': (5, 'tool_calls', calls(call_id='different-call')),
        'platform': (5, 'platform_message_id', 'human-message'),
        'metadata': (5, 'display_metadata', '{"origin":"human"}'),
        'display-kind': (5, 'display_kind', 'hidden'),
        'source-platform': (2, 'platform_message_id', 'human-message'),
        'source-metadata': (2, 'display_metadata', '{"origin":"human"}'),
        'source-display-kind': (2, 'display_kind', 'guidance'),
        'user-role': (5, 'role', 'user'),
        'nonempty-prior': (5, 'content', CONTENT.replace('\n\n\n', '\nReal human words\n\n', 1)),
        'quoted': (5, 'content', '```\n' + CONTENT + '\n```'),
        'unknown-header': (5, 'content', CONTENT.replace(PREFIXES[0], '[CONTEXT COMPACTION — future]')),
        'suffix-live-ask': (5, 'content', CONTENT + '\nLive human request'),
        'missing-end': (5, 'content', CONTENT.removesuffix(END)),
        'extra-delimiter': (5, 'content', EMPTY_PREFIX + CONTENT),
        'text-blocks': (5, 'content', json.dumps([dict(type='text', text=CONTENT)])),
        'malformed-calls': (5, 'tool_calls', '{broken'),
        'empty-calls': (5, 'tool_calls', '[]'),
        'scalar-calls': (5, 'tool_calls', '42'),
        'primitive-call': (5, 'tool_calls', '["not a call"]'),
    }
    with sqlite3.connect(db) as c:
        if case == 'no-source':
            c.execute('DELETE FROM messages WHERE id=2')
        elif case.startswith('ambiguous-'):
            original = 2 if case == 'ambiguous-source' else 5
            c.execute('INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp,active,compacted) '
                      'SELECT 8,session_id,role,content,tool_calls,timestamp,active,compacted '
                      'FROM messages WHERE id=?', (original,))
        elif case in ('call-name', 'call-type', 'response-item', 'missing-call-id', 'duplicate-call-id'):
            value = json.loads(calls())
            if case == 'call-name':
                value[0]['function']['name'] = 'other_tool'
            elif case == 'call-type':
                value[0]['type'] = 'other'
            elif case == 'response-item':
                value[0]['response_item_id'] = 'other-item'
            elif case == 'missing-call-id':
                del value[0]['id']
            else:
                value *= 2
            c.execute('UPDATE messages SET tool_calls=? WHERE id=5', (json.dumps(value),))
        else:
            row_id, column, value = changes[case]
            c.execute(f'UPDATE messages SET {column}=? WHERE id=?', (value, row_id))
    before = db.read_bytes()
    row = next(item for item in catalog.messages('default', 'cli-1')['items'] if item['id'] == 5)
    assert row.get('kind') != 'context_compression'
    assert row['role'] == ('user' if case == 'user-role' else 'assistant')
    # Existing explicitly hidden rows are intentionally not in public search.
    if case != 'display-kind':
        assert catalog.sessions('default', q='Merged scaffold needle')['total'] == 1
    assert db.read_bytes() == before


@pytest.mark.parametrize('missing', ['display_kind', 'display_metadata', 'platform_message_id',
                                    'timestamp', 'active', 'compacted', 'tool_calls'])
def test_merged_optional_and_legacy_schema_fail_closed(tmp_path, missing):
    from backend.context_compression_presentation import compression_ids
    db, catalog = merged_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute(f'ALTER TABLE messages DROP COLUMN {missing}')
    before = db.read_bytes()
    if missing != 'tool_calls':
        with sqlite3.connect(db) as c:
            c.row_factory = sqlite3.Row
            columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
            assert (5 in compression_ids(c, 'cli-1', columns)) == (missing.startswith('display_') or missing == 'platform_message_id')
    expected = int(missing in ('timestamp', 'active', 'compacted', 'tool_calls'))
    assert catalog.sessions('default', q='Merged scaffold needle')['total'] == expected
    assert db.read_bytes() == before


@pytest.mark.parametrize('prefix', PREFIXES, ids=['current', 'pre80622', 'pre69619', 'july', 'carveout', 'oldest'])
@pytest.mark.parametrize('archived', [False, True])
def test_merged_exact_headers_and_archived_generation(tmp_path, prefix, archived):
    db, catalog = merged_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET content=?,active=?,compacted=? WHERE id=5',
                  (CONTENT.replace(PREFIXES[0], prefix), int(not archived), int(archived)))
    row = next(item for item in catalog.messages('default', 'cli-1')['items'] if item['id'] == 5)
    assert row['kind'] == 'context_compression'
    assert catalog.sessions('default', q='Merged scaffold needle')['total'] == 0


def test_merged_carrier_is_not_the_first_human_turn_in_overlay(tmp_path):
    db, catalog = merged_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM messages WHERE id IN (3,4,6)')
        c.execute("INSERT INTO messages(id,session_id,role,content,timestamp) VALUES(6,'cli-1','user','Real request',5)")
    run = dict(id='r', input='Real request', output='Real final answer', status='completed', created_at=1, error=None)
    snapshot = dict(run=run, anchor=dict(session_id='cli-1', canonical_session_id='cli-1', message_id=2))
    result = catalog.messages('default', 'cli-1', snapshot=snapshot)
    assert result['run'] is None and result['snapshot']['mode'] == 'history'
    assert next(item for item in result['items'] if item['id'] == 5)['kind'] == 'context_compression'


def test_merged_classification_does_not_load_arguments_or_private_sidecars(tmp_path):
    from backend.context_compression_presentation import _merged_carrier_ids
    db, _ = merged_db(tmp_path)
    seen = []
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        class ReadBoundary:
            def execute(self, query, bindings):
                assert all(name not in query for name in ('reasoning', 'api_content', 'codex_message_items'))
                rows = list(c.execute(query, bindings))
                seen.extend(dict(row) for row in rows)
                return rows
        assert _merged_carrier_ids(ReadBoundary(), 'cli-1', columns) == {5}
    assert 'original arguments' not in json.dumps(seen)
    assert 'pruned arguments' not in json.dumps(seen)
