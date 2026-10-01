"""Presentation-only compression regressions; exclusively synthetic native state."""
import json
import sqlite3

import pytest

from backend.native_catalog import NativeCatalog

LEGACY = '[CONTEXT SUMMARY]:\nEarlier work, retained verbatim.'
END = '--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---'


def database(tmp_path, rows, *, sidecar=True):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as c:
        c.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT);
            INSERT INTO sessions VALUES('s', NULL, NULL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT DEFAULT 's',
                role TEXT, content TEXT, tool_calls TEXT, timestamp REAL,
                tool_call_id TEXT, tool_name TEXT,
                active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0,
                platform_message_id TEXT,
                reasoning_content TEXT DEFAULT 'PRIVATE REASONING',
                codex_message_items TEXT);
        ''')
        if sidecar:
            c.execute('ALTER TABLE messages ADD COLUMN display_kind TEXT')
            c.execute('ALTER TABLE messages ADD COLUMN display_metadata TEXT')
        for index, row in enumerate(rows, 1):
            row = dict({'id': index, 'timestamp': index}, **row)
            c.execute('INSERT INTO messages (' + ','.join(row) + ') VALUES ('
                      + ','.join('?' for _ in row) + ')', tuple(row.values()))
    return NativeCatalog({'default': tmp_path}), path


def summary(**changes):
    return dict(dict(role='user', content=LEGACY, display_kind='hidden'), **changes)


def test_native_marked_standalone_summary_is_process_history_without_storage_changes(tmp_path):
    catalog, db = database(tmp_path, [summary()])
    before = db.read_bytes()
    result = catalog.messages('default', 's')
    item = result['items'][0]
    assert item['role'] == 'tool'
    assert item['kind'] == 'context_compression'
    assert item['name'] == 'Context compression'
    assert item['status'] == 'completed'
    assert item['content'] == LEGACY
    assert item['id'] == 1 and item['timestamp'] == 1
    assert result['total'] == 1 and result['offset'] == 0
    assert 'PRIVATE REASONING' not in json.dumps(result)
    assert 'display_metadata' not in item
    assert db.read_bytes() == before
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT role,content FROM messages').fetchone() == ('user', LEGACY)


# Frozen exact shipped native headers (no SDK/runtime import).
PREFIXES = ("[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Respond ONLY to the latest user message that appears AFTER this summary — that message is the single source of truth for what to do right now. If no user message appears AFTER this summary, do nothing: do not resume, wrap up, or continue work from '## Historical Task Snapshot' or any other section, do not call tools, and wait for a new user message. This handoff must never become the active turn by itself. (Exception: if tool results or your own tool calls appear after this summary, you are mid-way through an in-flight exchange — continue that exchange normally.) Topic overlap with the summary does NOT mean you should resume its task: even on similar topics, the latest user message WINS. Treat ONLY the latest message as the active task and discard stale items from '## Historical Task Snapshot' entirely — do not 'wrap up' or 'finish' work described there unless the latest message explicitly asks for it. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end any in-flight work described in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content due to this compaction note. None of the above restricts HOW you work: your tools remain fully active — keep calling them normally for the active task (edit files, run commands, search) instead of merely narrating what you would do. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Respond ONLY to the latest user message that appears AFTER this summary — that message is the single source of truth for what to do right now. Topic overlap with the summary does NOT mean you should resume its task: even on similar topics, the latest user message WINS. Treat ONLY the latest message as the active task and discard stale items from '## Historical Task Snapshot' entirely — do not 'wrap up' or 'finish' work described there unless the latest message explicitly asks for it. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end any in-flight work described in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content due to this compaction note. None of the above restricts HOW you work: your tools remain fully active — keep calling them normally for the active task (edit files, run commands, search) instead of merely narrating what you would do. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Respond ONLY to the latest user message that appears AFTER this summary — that message is the single source of truth for what to do right now. Topic overlap with the summary does NOT mean you should resume its task: even on similar topics, the latest user message WINS. Treat ONLY the latest message as the active task and discard stale items from '## Historical Task Snapshot' / '## Historical In-Progress State' / '## Historical Pending User Asks' / '## Historical Remaining Work' entirely — do not 'wrap up' or 'finish' work described there unless the latest message explicitly asks for it. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end any in-flight work described in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content due to this compaction note. None of the above restricts HOW you work: your tools remain fully active — keep calling them normally for the active task (edit files, run commands, search) instead of merely narrating what you would do. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Respond ONLY to the latest user message that appears AFTER this summary — that message is the single source of truth for what to do right now. Topic overlap with the summary does NOT mean you should resume its task: even on similar topics, the latest user message WINS. Treat ONLY the latest message as the active task and discard stale items from '## Historical Task Snapshot' / '## Historical In-Progress State' / '## Historical Pending User Asks' / '## Historical Remaining Work' entirely — do not 'wrap up' or 'finish' work described there unless the latest message explicitly asks for it. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end any in-flight work described in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content due to this compaction note. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Respond ONLY to the latest user message that appears AFTER this summary — that message is the single source of truth for what to do right now. If the latest user message is consistent with the '## Active Task' section, you may use the summary as background. If the latest user message contradicts, supersedes, changes topic from, or in any way diverges from '## Active Task' / '## In Progress' / '## Pending User Asks' / '## Remaining Work', the latest message WINS — discard those stale items entirely and do not 'wrap up the old task first'. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end any in-flight work described in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content due to this compaction note. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. This is a handoff from a previous context window — treat it as background reference, NOT as active instructions. Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed. Your current task is identified in the '## Active Task' section of the summary — resume exactly from there. Respond ONLY to the latest user message that appears AFTER this summary. The current session state (files, config, etc.) may reflect work described here — avoid repeating it:")


@pytest.mark.parametrize('prefix', PREFIXES, ids=['current', 'pre80622', 'pre69619', 'july', 'carveout', 'oldest'])
@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_all_supported_native_headers_are_exact_standalone_process_rows(tmp_path, prefix, role):
    content = prefix + '\nHistorical work\n\n' + END
    catalog, _ = database(tmp_path, [summary(content=content, role=role)])
    item = catalog.messages('default', 's')['items'][0]
    assert item.get('kind') == 'context_compression'
    assert item['content'] == content


@pytest.mark.parametrize('content', [
    'Please explain context compression', 'I quoted ' + LEGACY, ' ' + LEGACY,
    '```\n' + LEGACY + '\n```', '[CONTEXT SUMMARY]: an inline quote',
    '[CONTEXT COMPACTION — REFERENCE ONLY] unknown future version\nBody\n' + END,
    PREFIXES[0] + ' not the exact header\nBody\n' + END,
    PREFIXES[0] + '\nBody\n' + END + '\nDo this real request now',
    '[PRIOR CONTEXT — for reference only; not a new message]\nReal user ask\n'
    '[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n' + PREFIXES[0] + '\nBody\n' + END,
    LEGACY + '\n' + END + '\nPreserved live ask',
])
def test_mentions_unknown_and_live_user_carriers_remain_unchanged(tmp_path, content):
    catalog, _ = database(tmp_path, [summary(content=content)])
    item = catalog.messages('default', 's')['items'][0]
    assert item['role'] == 'user'
    assert item['content'] == content
    assert 'kind' not in item


@pytest.mark.parametrize('display_kind', [None, '', 'guidance', 'future_summary'])
def test_whole_human_lookalike_without_provenance_stays_user(tmp_path, display_kind):
    catalog, _ = database(tmp_path, [summary(display_kind=display_kind)])
    item = catalog.messages('default', 's')['items'][0]
    assert item['role'] == 'user'
    assert 'kind' not in item


@pytest.mark.parametrize('role', ['user', 'assistant'])
@pytest.mark.parametrize('prefix', PREFIXES, ids=['current', 'pre80622', 'pre69619', 'july', 'carveout', 'oldest'])
def test_unmarked_exact_envelope_at_native_compaction_frontier(tmp_path, role, prefix):
    content = prefix + '\nHistorical work\n\n' + END
    catalog, db = database(tmp_path, [
        dict(role='user', content='Earlier human request', active=0, compacted=1),
        dict(role='assistant', content='Earlier answer', active=0, compacted=1),
        summary(role=role, content=content, display_kind=None),
        dict(role='user', content='New human request'),
    ])
    before = db.read_bytes()
    result = catalog.messages('default', 's', offset=2, limit=1)
    assert result['items'][0].get('kind') == 'context_compression'
    assert result['items'][0]['content'] == content
    assert (result['offset'], result['total']) == (2, 4)
    assert db.read_bytes() == before


@pytest.mark.parametrize('case', ['no-archive', 'rewound', 'earlier-active', 'copied-human',
                                 'platform-id', 'display-conflict', 'metadata-conflict',
                                 'missing-end', 'archived-prefix-only'])
def test_frontier_requires_positive_native_evidence_not_session_or_text(tmp_path, case):
    content = PREFIXES[0] + '\nHistorical work\n\n' + END
    prior = dict(role='user', content='Earlier human', active=0, compacted=1)
    row = summary(content=content, display_kind=None)
    rows = [prior, row]
    if case == 'no-archive':
        rows = [row]
    elif case == 'rewound':
        prior['compacted'] = 0
    elif case == 'earlier-active':
        prior.update(active=1, compacted=0)
    elif case == 'copied-human':
        prior['content'] = content
    elif case == 'platform-id':
        row['platform_message_id'] = 'human-123'
    elif case == 'display-conflict':
        row['display_kind'] = 'guidance'
    elif case == 'metadata-conflict':
        row['display_metadata'] = '{"origin":"user"}'
    elif case == 'missing-end':
        row['content'] = PREFIXES[0] + '\nBody without end marker'
    elif case == 'archived-prefix-only':
        row.update(active=0, compacted=1, content=PREFIXES[0])
    catalog, db = database(tmp_path, rows)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO sessions VALUES('parent',NULL,'compression')")
        c.execute("UPDATE sessions SET parent_session_id='parent' WHERE id='s'")
    item = catalog.messages('default', 's')['items'][-1]
    assert item['role'] == 'user'
    assert 'kind' not in item


def test_paging_expands_past_summary_to_genuine_human_turn(tmp_path):
    catalog, _ = database(tmp_path, [
        dict(role='user', content='Real request'),
        dict(role='assistant', content='Working'),
        summary(),
        dict(role='assistant', content='Done'),
    ])
    result = catalog.messages('default', 's', latest=True, limit=1, turn_boundary=True)
    assert result['offset'] == 0
    assert result['count'] == result['total'] == 4
    assert result['items'][2]['kind'] == 'context_compression'


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_summary_before_admitted_turn_does_not_leave_duplicate_overlay(tmp_path, role):
    catalog, _ = database(tmp_path, [
        summary(role=role), dict(role='user', content='Real request'),
        dict(role='assistant', content='Done'),
    ])
    run = dict(id='r', input='Real request', output='Done', status='completed', created_at=1, error=None)
    snapshot = dict(run=run, anchor=dict(session_id='s', canonical_session_id='s', message_id=0))
    result = catalog.messages('default', 's', snapshot=snapshot)
    assert result['run'] is None
    assert result['snapshot']['mode'] == 'history'
    assert result['items'][0]['kind'] == 'context_compression'


@pytest.mark.parametrize('role', ['user', 'assistant'])
@pytest.mark.parametrize('prefix', PREFIXES, ids=['current', 'pre80622', 'pre69619', 'july', 'carveout', 'oldest'])
def test_archived_generations_use_exact_reserved_envelope_and_native_archive_marker(tmp_path, role, prefix):
    content = prefix + '\nOlder generation retained body\n\n' + END
    catalog, _ = database(tmp_path, [
        summary(role=role, content=content, display_kind=None, active=0, compacted=1),
        dict(role='user', content='Real request', active=0, compacted=1),
        summary(content=PREFIXES[0] + '\nNew generation\n\n' + END, display_kind=None),
    ])
    result = catalog.messages('default', 's')
    assert [row.get('kind') for row in result['items']] == ['context_compression', None, 'context_compression']
    assert result['items'][0]['content'] == content


def test_human_and_summary_with_equal_content_timestamp_are_not_rewrite_aliases(tmp_path):
    catalog, _ = database(tmp_path, [
        dict(role='user', content=LEGACY, timestamp=7, active=0, compacted=1),
        summary(timestamp=7),
    ])
    result = catalog.messages('default', 's')
    assert [row['id'] for row in result['items']] == [1, 2]
    assert [row['role'] for row in result['items']] == ['user', 'tool']


@pytest.mark.parametrize('generation', [False, True])
def test_guidance_overlay_keeps_process_row_without_treating_it_as_a_later_user(tmp_path, generation):
    rows = [dict(role='assistant', content='Earlier'), dict(role='user', content='Request'),
            dict(role='assistant', content='Working'), summary(),
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
    assert [row['content'] for row in result['items']].count('Done') == 1
    assert [row['content'] for row in result['items']].count('Request') == 1
    assert [row['content'] for row in result['items']].count('Guidance') == 1
    assert sum(row.get('kind') == 'context_compression' for row in result['items']) == 1
    assert result['items'][-1]['content'] == 'External answer'
    assert result['count'] == len(result['items']) - 1


def test_recognized_archive_summary_keeps_identity_through_retained_native_copy(tmp_path):
    content = PREFIXES[0] + '\nBody\n\n' + END
    catalog, _ = database(tmp_path, [
        summary(content=content, display_kind=None, timestamp=2, active=0, compacted=1),
        dict(role='assistant', content='Prior answer', active=0, compacted=1),
        summary(content=content, display_kind=None, timestamp=2),
    ])
    result = catalog.messages('default', 's')
    assert [row['id'] for row in result['items']] == [2, 3]
    assert result['items'][-1].get('kind') == 'context_compression'


@pytest.mark.parametrize('field,value', [('platform_message_id', 'human-123'),
                                         ('display_metadata', '{"origin":"user"}')])
def test_explicit_human_provenance_overrides_hidden_lookalike(tmp_path, field, value):
    catalog, _ = database(tmp_path, [summary(**{field: value})])
    item = catalog.messages('default', 's')['items'][0]
    assert item['role'] == 'user'
    assert 'kind' not in item


@pytest.mark.asyncio
async def test_raw_gateway_history_preserves_summary_roles_and_bound_alias_paging():
    import httpx
    from backend.hermes_client import GatewayClient
    content = PREFIXES[0] + '\nBody\n\n' + END
    calls = []
    def handler(request):
        calls.append(request.url.params['offset'])
        rows = ([dict(role='user', content=content, display_kind='hidden')] * 500
                if calls[-1] == '0' else [dict(role='assistant', content='Raw answer')])
        return httpx.Response(200, json=dict(session_id='tip', requested_session_id='old', data=rows))
    client = GatewayClient('http://localhost:8642', 'fixture', transport=httpx.MockTransport(handler))
    try:
        result = await client.history('default', 'old')
    finally:
        await client.close()
    assert calls == ['0', '500']
    assert result['canonical_session_id'] == 'tip'
    assert result['history'] == [dict(role='user', content=content)] * 500 + [dict(role='assistant', content='Raw answer')]


def retained_head_rows(role='assistant'):
    """Synthetic producer shape: three retained head rows, fresh summary, old tail."""
    call = json.dumps([dict(id='head-call', type='function',
                           function=dict(name='read_file', arguments='{}'))])
    head = [dict(role='user', content='Opening genuine request', timestamp=10),
            dict(role='assistant', content='', tool_calls=call, timestamp=11),
            dict(role='tool', content='Original long tool output', tool_call_id='head-call',
                 tool_name='read_file', timestamp=12)]
    tail = dict(role='assistant', content='Retained recent answer', timestamp=30)
    archived = head + [dict(role='user', content='Compressed middle', timestamp=20), tail]
    return ([dict(row, active=0, compacted=1) for row in archived]
            + [dict(row) for row in head]
            + [dict(role=role, content=PREFIXES[0] + '\nFresh retained-head needle\n\n' + END,
                    timestamp=100), dict(tail),
               dict(role='assistant', content='Latest answer', timestamp=110)])


@pytest.mark.parametrize('role', ['user', 'assistant'])
@pytest.mark.parametrize('sidecar', [False, True])
def test_retained_head_summary_is_process_with_pruned_paired_tool_result(tmp_path, role, sidecar):
    rows = retained_head_rows(role)
    rows[7]['content'] = '[read_file] synthetic compacted tool result'
    catalog, db = database(tmp_path, rows, sidecar=sidecar)
    before = db.read_bytes()
    result = catalog.messages('default', 's')
    item = next(row for row in result['items'] if row['id'] == 9)
    assert item.get('kind') == 'context_compression'
    assert item['role'] == 'tool' and item['name'] == 'Context compression'
    assert item['content'] == rows[8]['content']
    assert sum(row.get('kind') == 'context_compression' for row in result['items']) == 1
    assert next(row for row in result['items'] if row['id'] == 6)['role'] == 'user'
    assert db.read_bytes() == before
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT role,content FROM messages WHERE id=9').fetchone() == (role, rows[8]['content'])


def test_retained_head_summary_survives_later_genuine_human_lookalike(tmp_path):
    rows = retained_head_rows('user')
    human = '[CONTEXT SUMMARY]:\nA human quotation\n' + END
    rows.append(dict(role='user', content=human, timestamp=120, platform_message_id='human'))
    catalog, _ = database(tmp_path, rows)
    items = catalog.messages('default', 's')['items']
    assert next(row for row in items if row['id'] == 9).get('kind') == 'context_compression'
    assert items[-1]['role'] == 'user' and items[-1]['content'] == human


@pytest.mark.parametrize('case', [
    'no-archive', 'rewound-source', 'changed-head', 'changed-tail', 'head-timestamp',
    'tail-timestamp', 'summary-timestamp', 'head-order', 'missing-middle',
    'duplicate-source', 'duplicate-tail-source', 'duplicate-active', 'copied-human',
    'platform', 'display', 'metadata', 'calls', 'call-id', 'tool-name',
    'quoted', 'trailing-ask', 'missing-end', 'unknown-header', 'json-content',
    'unpaired-pruned-tool', 'source-metadata', 'second-candidate',
    'unrelated-archive-prefix', 'object-calls', 'duplicate-call-id',
])
def test_retained_head_proof_fails_closed(tmp_path, case):
    rows = retained_head_rows('user')
    candidate = rows[8]
    if case == 'no-archive':
        rows = rows[5:]
    elif case == 'rewound-source':
        rows[1]['compacted'] = 0
    elif case == 'changed-head':
        rows[5]['content'] += ' a genuine new request'
    elif case == 'changed-tail':
        rows[9]['content'] += ' new content'
    elif case == 'head-timestamp':
        rows[5]['timestamp'] = None
    elif case == 'tail-timestamp':
        rows[9]['timestamp'] = 101
    elif case == 'summary-timestamp':
        candidate['timestamp'] = 29
    elif case == 'head-order':
        rows[5], rows[6] = rows[6], rows[5]
    elif case == 'missing-middle':
        del rows[3]
    elif case == 'duplicate-source':
        rows.insert(3, dict(rows[1]))
    elif case == 'duplicate-tail-source':
        rows.insert(5, dict(rows[4]))
    elif case == 'duplicate-active':
        rows.append(dict(rows[5]))
    elif case == 'copied-human':
        rows[3]['content'] = candidate['content']
    elif case in ('platform', 'display', 'metadata', 'calls', 'call-id', 'tool-name'):
        key, value = {'platform': ('platform_message_id', 'human'),
                      'display': ('display_kind', 'guidance'),
                      'metadata': ('display_metadata', '{"origin":"human"}'),
                      'calls': ('tool_calls', '[]'), 'call-id': ('tool_call_id', 'conflict'),
                      'tool-name': ('tool_name', 'conflict')}[case]
        candidate[key] = value
    elif case == 'quoted':
        candidate['content'] = '```\n' + candidate['content'] + '\n```'
    elif case == 'trailing-ask':
        candidate['content'] += '\nDo this live request'
    elif case == 'missing-end':
        candidate['content'] = candidate['content'].replace(END, '')
    elif case == 'unknown-header':
        candidate['content'] = candidate['content'].replace(PREFIXES[0], '[CONTEXT COMPACTION unknown]')
    elif case == 'json-content':
        candidate['content'] = json.dumps([dict(type='text', text=candidate['content'])])
    elif case == 'unpaired-pruned-tool':
        rows[7].update(content='Pruned result', tool_call_id='unpaired')
        rows[2]['tool_call_id'] = 'unpaired'
    elif case == 'source-metadata':
        rows[0]['platform_message_id'] = 'not-the-same-origin'
    elif case == 'second-candidate':
        rows.extend([dict(role='user', content=LEGACY + '\n' + END, timestamp=130),
                     dict(role='assistant', content='Old-looking tail', timestamp=40)])
    elif case == 'unrelated-archive-prefix':
        rows.insert(0, dict(role='user', content='Not the retained opening', timestamp=1,
                            active=0, compacted=1))
    elif case in ('object-calls', 'duplicate-call-id'):
        call = json.loads(rows[1]['tool_calls'])[0]
        calls = json.dumps({'wrong-shape': call} if case == 'object-calls' else [call, call])
        rows[1]['tool_calls'] = rows[6]['tool_calls'] = calls
        rows[7]['content'] = 'Pruned result'
    candidate_id = next(i for i, row in enumerate(rows, 1) if row is candidate)
    _, db = database(tmp_path, rows)
    from backend.context_compression_presentation import compression_ids
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        assert candidate_id not in compression_ids(c, 's', columns)


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_retained_head_search_and_turn_boundaries_share_projection(tmp_path, role):
    catalog, db = database(tmp_path, retained_head_rows(role))
    with sqlite3.connect(db) as c:
        for column in ('title TEXT', 'source TEXT', 'started_at REAL', 'last_activity_at REAL'):
            c.execute('ALTER TABLE sessions ADD COLUMN ' + column)
    before = db.read_bytes()
    assert catalog.sessions('default', q='Fresh retained-head needle')['total'] == 0
    assert catalog.sessions('default', q='Opening genuine request')['total'] == 1
    page = catalog.messages('default', 's', latest=True, limit=1, turn_boundary=True)
    assert page['items'][0]['id'] == 6
    assert page['turn_boundary']['complete']
    assert next(row for row in page['items'] if row['id'] == 9)['kind'] == 'context_compression'
    all_rows = catalog.messages('default', 's')['items']
    paged = []
    for offset in range(len(all_rows)):
        paged += catalog.messages('default', 's', offset=offset, limit=1)['items']
    assert [row['id'] for row in paged] == [row['id'] for row in all_rows]
    assert db.read_bytes() == before


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_retained_head_multiple_archive_generations(tmp_path, role):
    first = retained_head_rows(role)
    # Archive the previous active generation, then insert a new retained head,
    # new summary and its older tail. Repeated identities are distinct epochs.
    rows = first[:5] + [dict(row, active=0, compacted=1) for row in first[5:]]
    fresh = dict(first[8], content=PREFIXES[0] + '\nSecond generation\n\n' + END, timestamp=200)
    rows += [dict(row) for row in first[5:8]] + [fresh, dict(first[9]), dict(first[10])]
    catalog, _ = database(tmp_path, rows)
    items = catalog.messages('default', 's')['items']
    assert sum(row.get('kind') == 'context_compression' for row in items) == 2
    assert next(row for row in items if row['content'] == fresh['content'])['kind'] == 'context_compression'
    assert sum(row['content'] == 'Opening genuine request' for row in items) == 1


def test_retained_head_classifier_never_reads_private_sidecars(tmp_path):
    from backend.context_compression_presentation import compression_ids
    _, db = database(tmp_path, retained_head_rows())
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        c.execute('ALTER TABLE messages ADD COLUMN api_content TEXT')
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        forbidden = {'api_content', 'codex_message_items', 'reasoning_content'}
        reads = set()
        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == 'messages':
                reads.add(column)
                return sqlite3.SQLITE_DENY if column in forbidden else sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorize)
        assert 9 in compression_ids(c, 's', columns)
        assert not reads & forbidden


@pytest.mark.parametrize('target', ['summary', 'head', 'tail', 'archive-middle'])
def test_retained_head_nonfinite_chronology_fails_closed(tmp_path, target):
    from backend.context_compression_presentation import compression_ids
    rows = retained_head_rows()
    if target == 'summary':
        rows[8]['timestamp'] = float('inf')
    elif target == 'head':
        rows[0]['timestamp'] = rows[5]['timestamp'] = float('-inf')
    elif target == 'tail':
        rows[4]['timestamp'] = rows[9]['timestamp'] = float('-inf')
    else:
        rows[3]['timestamp'] = float('-inf')
    _, db = database(tmp_path, rows)
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        assert 9 not in compression_ids(c, 's', columns)


@pytest.mark.parametrize('missing', ['timestamp', 'tool_calls', 'active', 'compacted'])
def test_retained_head_missing_required_identity_column_fails_closed(tmp_path, missing):
    from backend.context_compression_presentation import compression_ids
    _, db = database(tmp_path, retained_head_rows())
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        c.execute('ALTER TABLE messages DROP COLUMN ' + missing)
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        assert 9 not in compression_ids(c, 's', columns)


@pytest.mark.parametrize('bound', ['head', 'archive', 'generations', 'envelope', 'calls'])
def test_retained_head_proof_budgets_fail_closed(tmp_path, bound):
    from backend.context_compression_presentation import compression_ids
    rows = retained_head_rows()
    candidate = rows[8]
    if bound == 'head':
        extras = [dict(role='assistant', content=f'Retained head {i}', timestamp=13 + i / 100)
                  for i in range(30)]
        rows = (rows[:3] + [dict(row, active=0, compacted=1) for row in extras]
                + rows[3:8] + extras + rows[8:])
    elif bound == 'archive':
        rows[3:3] = [dict(role='assistant', content='Archived middle', timestamp=20,
                          active=0, compacted=1) for _ in range(8192)]
    elif bound == 'generations':
        rows = [dict(row) for _ in range(65) for row in rows[:5]] + rows[5:]
    elif bound == 'envelope':
        candidate['content'] = PREFIXES[0] + '\n' + 'x' * 8388608 + '\n' + END
    elif bound == 'calls':
        call = json.loads(rows[1]['tool_calls'])
        call[0]['function']['arguments'] = 'x' * 8388608
        rows[1]['tool_calls'] = rows[6]['tool_calls'] = json.dumps(call)
        rows[7]['content'] = 'Pruned tool output'
    candidate_id = next(i for i, row in enumerate(rows, 1) if row is candidate)
    _, db = database(tmp_path, rows)
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        assert candidate_id not in compression_ids(c, 's', columns)


def test_legacy_schema_without_provenance_keeps_full_reserved_text(tmp_path):
    content = PREFIXES[0] + '\nBody\n\n' + END
    catalog, _ = database(tmp_path, [dict(role='user', content=content)], sidecar=False)
    assert catalog.messages('default', 's')['items'][0]['role'] == 'user'
