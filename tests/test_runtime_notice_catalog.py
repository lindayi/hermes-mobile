"""Exact installed runtime marker; synthetic databases, no SDK imports."""
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog
from test_context_compression_presentation import database

NUDGE = ("You've reached the maximum number of tool-calling iterations allowed. "
         "Please provide a final response summarizing what you've found and accomplished so far, "
         "without calling any more tools.")


def test_exact_native_nudge_is_process_without_mutating_history(tmp_path):
    catalog, db = database(tmp_path, [dict(role='user', content=NUDGE)])
    before = db.read_bytes()
    page = catalog.messages('default', 's')
    item = page['items'][0]
    assert (item['role'], item.get('kind'), item.get('name'), item.get('status')) == (
        'tool', 'runtime_notice', 'Tool limit reached', 'completed')
    assert item['content'] == NUDGE and item['id'] == 1 and item['timestamp'] == 1
    assert page['total'] == 1 and page['offset'] == 0
    assert db.read_bytes() == before
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT role,content FROM messages').fetchone() == ('user', NUDGE)


def test_nudge_is_not_turn_boundary_or_completed_overlay_identity(tmp_path):
    catalog, _ = database(tmp_path, [
        dict(role='assistant', content='Earlier'),
        dict(role='user', content='Request'),
        dict(role='assistant', content='Working'),
        dict(role='user', content=NUDGE),
        dict(role='assistant', content='Done'),
    ])
    page = catalog.messages('default', 's', limit=1, latest=True, turn_boundary=True)
    assert page['offset'] == 1
    assert page['count'] == 4
    run = dict(id='r', input='Request', output='Done', status='completed', created_at=1, error=None)
    snapshot = dict(run=run, anchor=dict(session_id='s', canonical_session_id='s', message_id=1))
    result = catalog.messages('default', 's', snapshot=snapshot)
    assert result['run'] is None
    assert result['snapshot']['mode'] == 'history'


@pytest.mark.parametrize('generation', [False, True])
def test_guidance_overlay_keeps_nudge_identity_across_native_rewrites(tmp_path, generation):
    rows = [dict(role='assistant', content='Earlier'), dict(role='user', content='Request'),
            dict(role='assistant', content='Working'), dict(role='user', content=NUDGE),
            dict(role='assistant', content='Done'), dict(role='user', content='External'),
            dict(role='assistant', content='External answer')]
    if generation:
        rows = [dict(row, active=0, compacted=1, timestamp=i) for i, row in enumerate(rows, 1)]
        rows += [dict(row, active=1, compacted=0) for row in rows]
    catalog, _ = database(tmp_path, rows)
    run = dict(id='r', input='Request', output='Done', status='completed', created_at=1, error=None)
    events = [dict(id=1, name='steering', data=dict(id='steer', input='Guidance',
               idempotency_key='key', status='accepted', created_at=2))]
    snapshot = dict(run=run, anchor=dict(session_id='s', canonical_session_id='s', message_id=1),
                    replay_events=events)
    result = catalog.messages('default', 's', snapshot=snapshot, turn_boundary=True)
    assert result['run'] is None
    for text in ('Done', 'Request', 'Guidance', NUDGE):
        assert [row['content'] for row in result['items']].count(text) == 1
    assert sum(row.get('kind') == 'runtime_notice' for row in result['items']) == 1
    assert result['items'][-1]['content'] == 'External answer'
    assert result['count'] == len(result['items']) - 1


@pytest.mark.parametrize('changes', [
    {'display_kind': 'user'}, {'display_kind': 'guidance'}, {'display_kind': 'future'},
    {'display_metadata': '{"origin":"user"}'}, {'display_metadata': '{}'},
    {'display_metadata': 'malformed'}, {'platform_message_id': 'human-1'},
    {'tool_calls': '[]'}, {'role': 'assistant'}, {'role': 'tool'},
    {'content': ' ' + NUDGE}, {'content': NUDGE + '\n'},
    {'content': '"' + NUDGE + '"'}, {'content': '```\n' + NUDGE + '\n```'},
    {'content': NUDGE + '\nPlease explain this.'}, {'content': NUDGE[:80]},
    {'content': 'Please explain: ' + NUDGE}, {'content': NUDGE.upper()},
])
def test_conflicting_provenance_quotes_mixed_asks_are_not_notices(tmp_path, changes):
    catalog, _ = database(tmp_path, [dict(dict(role='user', content=NUDGE), **changes)])
    row = catalog.messages('default', 's')['items'][0]
    assert row.get('kind') != 'runtime_notice'
    assert row['role'] == changes.get('role', 'user')
    assert row['content'] == changes.get('content', NUDGE)


@pytest.mark.parametrize('schema', ['legacy', 'active', 'archive'])
def test_exact_marker_optional_schema_and_archived_visibility(tmp_path, schema):
    from test_native_catalog import create_native_db
    db = tmp_path / 'state.db'
    create_native_db(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages(id,session_id,role,content) VALUES(2,'wa-1','user',?)", (NUDGE,))
        if schema != 'legacy':
            c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            c.execute('UPDATE messages SET active=0 WHERE id=2')
        if schema == 'archive':
            c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
            c.execute('UPDATE messages SET compacted=1 WHERE id=2')
    rows = NativeCatalog({'default': tmp_path}).messages('default', 'wa-1')['items']
    assert sum(row.get('kind') == 'runtime_notice' for row in rows) == (schema != 'active')


def test_human_provenance_not_aliased_to_archived_runtime_marker(tmp_path):
    catalog, _ = database(tmp_path, [
        dict(role='user', content=NUDGE, timestamp=7, active=0, compacted=1),
        dict(role='user', content=NUDGE, timestamp=7, platform_message_id='human'),
    ])
    rows = catalog.messages('default', 's')['items']
    assert [row['id'] for row in rows] == [1, 2]
    assert [row['role'] for row in rows] == ['tool', 'user']
