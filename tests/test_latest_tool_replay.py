"""Latest tools use real temporary SQLite and authenticated HTTP; no native calls."""
import json
import sqlite3

import pytest

from test_auth import BASE
from test_chat_snapshot import chat, admit, persist, latest
from test_reopen_native_rewrite import compact


def record_tool(chat, run, name='read_file', call_id=None):
    journal, uid = chat[0].state.journal, chat[2]['id']
    common = {'tool': name}
    if call_id is not None:
        common['tool_call_id'] = call_id
    first = journal.event(uid, run['id'], 'tool', dict(common, event='tool.started', summary=name))
    last = journal.event(uid, run['id'], 'tool', dict(common, event='tool.completed', error=None, duration=0.1))
    return [first, last]


def test_terminal_overlay_reopens_with_saved_idless_tools_after_compaction(chat):
    app, client, user, db = chat
    # Page boundaries and equal old inputs must not be mistaken for this turn.
    persist(chat, [('user', 'Again', None), ('assistant', 'Earlier answer', None)]
            + [('tool', 'Historical result', None)] * 105)
    run = admit(chat, text='Again')
    persist(chat, [('user', 'Again', None), ('assistant', 'Working', '[{"id":"native-call","function":{"name":"read_file","arguments":"{}"}}]'),
                   ('tool', 'Partial native result', None)])
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET timestamp=200 WHERE id>=(SELECT max(id)-2 FROM messages)')
    compact(chat)
    ids = record_tool(chat, run)
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Saved final answer')
    before = db.read_bytes()
    body = latest(chat, '&limit=1')
    assert body['run']['id'] == run['id']
    replay = body.get('tool_replay')
    assert replay is not None, 'Terminal overlays must include durable tools; terminal UI does not open SSE'
    assert replay['run_id'] == run['id']
    assert [event['id'] for event in replay['events']] == ids
    assert [event['data']['event'] for event in replay['events']] == ['tool.started', 'tool.completed']
    assert all(event['name'] == 'tool' for event in replay['events'])
    assert not any(row['content'] in ('Again', 'Working', 'Partial native result', 'Saved final answer') for row in body['items'])
    assert body['offset'] > 0
    # The recorded SSE seam has the same stable IDs; seeding is not execution.
    response = client.get(BASE + '/runs/' + run['id'] + '/events')
    assert response.status_code == 200
    assert all(f'id: {event_id}\nevent: tool\n' in response.text for event_id in ids)
    assert db.read_bytes() == before
    assert not app.state.orchestrator._tasks


@pytest.mark.parametrize('native_tools', [False, True])
def test_recorded_tools_keep_one_overlay_authority_after_native_final_arrives(chat, native_tools):
    app, _, user, _ = chat
    run = admit(chat)
    ids = record_tool(chat, run)
    rows = [('user', 'Next turn', None)]
    if native_tools:
        rows += [('assistant', None, '[{"id":"native-call","function":{"name":"read_file","arguments":"{}"}}]'),
                 ('tool', 'Native result', None)]
    persist(chat, rows + [('assistant', 'Answer', None)])
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat)
    assert body['run'] is not None, 'Native final text alone cannot retire durable journal tool activity'
    assert body['run']['id'] == run['id']
    assert body['run']['input'] == 'Next turn' and body['run']['output'] == 'Answer'
    assert [event['id'] for event in body['tool_replay']['events']] == ids
    assert [row['content'] for row in body['items']] == ['Retained answer']


def test_active_replay_is_ordered_public_activity_with_transaction_highwater(chat):
    app, client, user, _ = chat
    run = admit(chat)
    journal = app.state.journal
    uid, rid = user['id'], run['id']
    journal.set_upstream(uid, rid, 'fixture-upstream')
    commentary = journal.event(uid, rid, 'commentary', {'text': 'Inspecting fixture', 'id': 'public-1'})
    tools = record_tool(chat, run)
    delta = journal.event(uid, rid, 'delta', {'text': 'Public progress'})
    journal.event(uid, rid, 'commentary', {'text': 'PRIVATE ANALYSIS', 'channel': 'analysis'})
    journal.event(uid, rid, 'delta', {'text': 'PRIVATE REASONING', 'reasoning_content': 'PRIVATE'})
    cursor = journal.event(uid, rid, 'approval', {'id': 'pending-private-id', 'arguments': 'PRIVATE ARGS'})
    body = latest(chat)
    replay = body['tool_replay']
    assert [event['id'] for event in replay['events']] == [commentary, *tools, delta]
    assert [event['name'] for event in replay['events']] == ['commentary', 'tool', 'tool', 'delta']
    assert replay['cursor'] == cursor
    assert 'PRIVATE' not in json.dumps(replay)
    assert replay['events'][0]['data'] == {'text': 'Inspecting fixture', 'id': 'public-1'}
    assert body['run']['status'] == 'running'
    # An event arriving after the snapshot is recovered exactly by the cursor.
    new_tools = record_tool(chat, run, name='web_search')
    journal.finish(uid, rid, 'completed', output='Final')
    response = client.get(BASE + '/runs/' + rid + '/events?after=' + str(replay['cursor']))
    assert response.status_code == 200
    assert all(f'id: {event_id}\nevent: tool\n' in response.text for event_id in new_tools)
    assert all(f'id: {event_id}\n' not in response.text for event_id in [commentary, *tools, delta, cursor])


@pytest.mark.parametrize('epochs', [0, 1, 2])
def test_exact_native_result_id_coverage_retires_replay_without_losing_history(chat, epochs):
    app, _, user, db = chat
    run = admit(chat)
    record_tool(chat, run, call_id='current-call')
    persist(chat, [('user', 'Next turn', None),
                   ('assistant', None, '[{"id":"current-call","function":{"name":"read_file","arguments":"{}"}}]'),
                   ('tool', '{"success":true}', None), ('assistant', 'Answer', None)])
    for _ in range(epochs):
        compact(chat)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN tool_call_id TEXT')
        c.execute("UPDATE messages SET tool_call_id='current-call' WHERE role='tool'")
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat)
    assert body['run'] is None, 'Exact result identity covers durable recorded calls in this admitted native turn'
    assert body['last_run']['id'] == run['id']
    assert body['tool_replay'] is None
    assert sum(row['role'] == 'tool' for row in body['items']) == 1
    assert [row['content'] for row in body['items']].count('Next turn') == 1
    assert [row['content'] for row in body['items']].count('Answer') == 1


@pytest.mark.parametrize('epochs', [0, 1, 2])
@pytest.mark.parametrize('persist_final', [False, True])
def test_terminal_journal_tools_stay_before_later_equal_external_turn(chat, epochs, persist_final):
    app, client, user, db = chat
    persist(chat, [('user', 'Again', None), ('assistant', 'Earlier answer', None)])
    run = admit(chat, text='Again')
    record_tool(chat, run)
    persist(chat, [('user', 'Again', None), ('tool', 'Partial native tool', None)]
            + ([('assistant', 'Answer', None)] if persist_final else []))
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET timestamp=200 WHERE id>3')
    for _ in range(epochs):
        compact(chat)
    persist(chat, [('user', 'Again', None), ('assistant', 'Later external answer', None)])
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET timestamp=300 WHERE id>=(SELECT max(id)-1 FROM messages)')
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    before = db.read_bytes()
    body = latest(chat)
    assert body['run'] is None, 'A completed older web turn must not be appended after a newer native CLI turn'
    assert body['tool_replay'] is None
    contents = [row['content'] for row in body['items']]
    assert contents.count('Again') == 3
    tools = [row for row in body['items'] if row['role'] == 'tool']
    assert len(tools) == 1
    assert tools[0]['source'] == 'journal' and tools[0]['status'] == 'completed'
    assert body['items'].index(tools[0]) < contents.index('Answer') < contents.index('Later external answer')
    assert contents.count('Earlier answer') == contents.count('Answer') == contents.count('Later external answer') == 1
    pages = [client.get(BASE + '/sessions/wa-1/messages?limit=1&offset=' + str(i)).json()
             for i in range(body['total'])]
    assert [page['items'][0] for page in pages] == body['items']
    assert db.read_bytes() == before


@pytest.mark.parametrize('tail', ['Answer', 'Partial answer'])
def test_materialized_older_turn_preserves_public_text_and_anonymous_tool_occurrences(chat, tail):
    app, _, user, _ = chat
    run = admit(chat)
    journal, uid, rid = app.state.journal, user['id'], run['id']
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Answer', None),
                   ('user', 'Later CLI input', None), ('assistant', 'Later answer', None)])
    journal.event(uid, rid, 'delta', {'text': 'First '})
    journal.event(uid, rid, 'delta', {'text': 'progress'})
    first = record_tool(chat, run)
    journal.event(uid, rid, 'commentary', {'text': 'Second progress', 'id': 'note'})
    second = record_tool(chat, run)
    journal.event(uid, rid, 'tool', {'event': 'tool.started', 'tool': 'read_file', 'summary': 'Unfinished'})
    journal.event(uid, rid, 'delta', {'text': tail})
    journal.finish(uid, rid, 'completed', output='Answer')
    body = latest(chat)
    assert body['run'] is None
    owned = [row for row in body['items'] if row.get('run_id') == rid]
    assert [row['role'] for row in owned] == ['user', 'assistant', 'tool', 'assistant', 'tool', 'tool', 'assistant']
    assert [row['content'] for row in owned if row['role'] == 'assistant'] == ['First progress', 'Second progress', 'Answer']
    assert [row.get('channel') for row in owned if row['role'] == 'assistant'] == ['commentary', 'commentary', None]
    tools = [row for row in owned if row['role'] == 'tool']
    assert [row['status'] for row in tools] == ['completed', 'completed', 'unknown']
    assert [row['id'] for row in tools[:2]] == ['journal:' + rid + ':' + str(first[0]), 'journal:' + rid + ':' + str(second[0])]
    assert [row['content'] for row in body['items']].count('Answer') == 1


@pytest.mark.parametrize('later_native', [False, True])
def test_background_only_public_tail_survives_native_final_without_visible_draft(chat, later_native):
    from contextlib import closing
    app, _, user, db = chat
    run = admit(chat)
    journal = app.state.journal
    ids = [journal.event(user['id'], run['id'], 'delta', data) for data in
           [dict(text='PUBLIC A'), dict(text='DRAFT B'), dict(text='PRIVATE', channel='analysis'),
            dict(text='PRIVATE', reasoning_content='hidden')]]
    with closing(journal.connect()) as connection, connection:
        for eid, stamp in zip(ids, [110, 120, 121, 122]):
            connection.execute('UPDATE events SET created_at=? WHERE id=?', (stamp, eid))
    journal.finish(user['id'], run['id'], 'completed', output='DEFINITIVE F')
    persist(chat, [('user', run['input'], None), ('assistant', 'DEFINITIVE F', None)]
            + ([('user', 'Later CLI', None), ('assistant', 'Later answer', None)] if later_native else []))
    before = db.read_bytes()
    body = latest(chat)
    if later_native:
        assert body['run'] is None
        final = next(row for row in body['items'] if row.get('run_id') == run['id'] and row['content'] == 'DEFINITIVE F')
        assert final['public_tail'] == [dict(text='PUBLIC A', observed_at=110), dict(text='DRAFT B', observed_at=120)]
        assert [row['content'] for row in body['items']].count('DEFINITIVE F') == 1
        assert not any(row.get('channel') == 'commentary' for row in body['items'])
        assert body['items'].index(final) < next(i for i, row in enumerate(body['items']) if row['content'] == 'Later CLI')
    else:
        assert body['run'] is not None, 'Native final alone must not retire deferred public-tail boundaries'
        assert body['run']['id'] == run['id']
        assert [event['name'] for event in body['tool_replay']['events']] == ['delta', 'delta']
    assert 'PRIVATE' not in json.dumps(body)
    assert db.read_bytes() == before
    assert not app.state.orchestrator._tasks


def test_replay_has_no_thousand_event_truncation_or_raw_tool_arguments(chat):
    app, _, user, _ = chat
    run = admit(chat)
    journal = app.state.journal
    # Batch fixture writes, not thousands of independent fsyncs or real tools.
    with journal.connect() as c:
        c.executemany('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                      [(run['id'], 'tool', json.dumps({'event': 'tool.started' if i % 2 == 0 else 'tool.completed',
                        'tool': 'read_file', 'arguments': 'PRIVATE ARGS', 'result': 'PRIVATE RESULT',
                        'reasoning_content': 'PRIVATE REASONING'}), 1) for i in range(1002)])
    journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat, '&limit=1')
    replay = body['tool_replay']
    assert len(replay['events']) == 1002
    assert replay['cursor'] > replay['events'][-1]['id']
    assert 'PRIVATE' not in json.dumps(replay)
    assert replay['events'] == sorted(replay['events'], key=lambda row: row['id'])


def test_replay_ownership_precedes_latest_run_and_cursor_selection(chat):
    app, client, user, _ = chat
    run = admit(chat)
    owned_ids = record_tool(chat, run)
    journal = app.state.journal
    journal.finish(user['id'], run['id'], 'completed', output='Answer')
    for owner, profile, session in [('foreign', 'default', 'wa-1'),
                                    (user['id'], 'other', 'wa-1'),
                                    (user['id'], 'default', 'cli-1')]:
        foreign, _ = journal.submit(owner, profile, session, 'PRIVATE INPUT', profile + session)
        journal.event(owner, foreign['id'], 'tool', {'tool': 'PRIVATE TOOL', 'event': 'tool.started'})
        journal.finish(owner, foreign['id'], 'completed', output='PRIVATE OUTPUT')
    body = latest(chat)
    assert body['tool_replay']['run_id'] == run['id']
    assert [event['id'] for event in body['tool_replay']['events']] == owned_ids
    assert 'PRIVATE' not in json.dumps(body)
    client.cookies.clear()
    assert client.get(BASE + '/sessions/wa-1/messages?latest=true').status_code == 401


def test_event_arriving_during_native_read_retries_even_when_run_status_is_unchanged(chat, monkeypatch):
    app, _, user, _ = chat
    run = admit(chat)
    original = app.state.catalog.messages
    reads, event_ids = [], []
    def racing(*args, **kwargs):
        page = original(*args, **kwargs)
        if not reads:
            event_ids.extend(record_tool(chat, run))
        reads.append(True)
        return page
    monkeypatch.setattr(app.state.catalog, 'messages', racing)
    body = latest(chat)
    assert len(reads) == 2
    assert [event['id'] for event in body['tool_replay']['events']] == event_ids
    assert body['tool_replay']['cursor'] == event_ids[-1]
