"""Read-only presentation timestamps from temporary journals, never live state."""
import json
from contextlib import closing

from test_auth import BASE
from test_chat_snapshot import chat, admit, latest


def test_replay_and_sse_expose_recorded_receipt_time_not_payload_claims(chat):
    app, client, user, _ = chat
    journal = app.state.journal
    run = admit(chat)
    ids = [journal.event(user['id'], run['id'], name, data) for name, data in [
        ('tool', {'event': 'tool.started', 'tool': 'read_file', 'summary': 'read_file: guide.md', 'observed_at': 999}),
        ('commentary', {'text': 'Public progress', 'observed_at': 999}),
        ('delta', {'text': 'Continuing', 'observed_at': 999}),
    ]]
    with closing(journal.connect()) as db, db:
        for eid, stamp in zip(ids, (101.5, 102.5, 103.5)):
            db.execute('UPDATE events SET created_at=? WHERE id=?', (stamp, eid))
    journal.finish(user['id'], run['id'], 'completed', output='Done')
    events = journal.events(user['id'], run['id'])
    assert [event.get('observed_at') for event in events[:3]] == [101.5, 102.5, 103.5]
    replay = latest(chat)['tool_replay']['events']
    assert [event.get('observed_at') for event in replay] == [101.5, 102.5, 103.5]
    assert all('observed_at' not in event['data'] for event in replay), 'payload claims are not timestamp authority'
    response = client.get(BASE + '/runs/' + run['id'] + '/events')
    assert response.status_code == 200
    public = [json.loads(block.split('data: ', 1)[1]) for block in response.text.split('\n\n')
              if any('event: ' + name + '\n' in block for name in ('tool', 'commentary', 'delta'))]
    assert [data.get('observed_at') for data in public] == [101.5, 102.5, 103.5]
    # Merely projecting a timestamp must not rewrite an existing event payload.
    assert journal.events(user['id'], run['id'])[0]['data']['observed_at'] == 999


def test_materialized_turn_keeps_per_segment_times_and_tool_start_position():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=160, input='Ask', output='Answer', status='completed', error=None)
    events = [
        dict(id=1, name='tool', observed_at=110, data=dict(event='tool.started', tool='read_file', tool_call_id='t', summary='read_file: guide.md')),
        dict(id=2, name='commentary', observed_at=120, data=dict(text='Progress')),
        dict(id=3, name='tool', observed_at=140, data=dict(event='tool.completed', tool='read_file', tool_call_id='t', duration=30, error=False)),
        dict(id=4, name='delta', observed_at=150, data=dict(text='More progress')),
        dict(id=5, name='delta', observed_at=151, data=dict(text=' continues')),
        dict(id=6, name='tool', observed_at=155, data=dict(event='tool.started', tool='web_search', tool_call_id='u')),
    ]
    items = _journal_turn(run, events)
    assert [item['timestamp'] for item in items] == [100, 110, 120, 150, 155, 160]
    assert [item.get('observed_at') for item in items[1:]] == [110, 120, 150, 155, 160]
    assert items[1]['duration'] == 30
    assert items[1]['status'] == 'completed'
    assert items[3]['content'] == 'More progress continues'
    assert items[3]['timed_chunks'] == [
        {'text': 'More progress', 'observed_at': 150},
        {'text': ' continues', 'observed_at': 151},
    ], 'collapsed presentation must retain each recorded delta boundary for late background placement'
    assert items[4]['status'] == 'unknown'
    assert [item['id'] for item in items] == ['journal:fixture:user', 'journal:fixture:1', 'journal:fixture:text:2', 'journal:fixture:text:4', 'journal:fixture:6', 'journal:fixture:assistant']


def test_materialization_keeps_committed_delta_boundaries_before_authoritative_final():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=140, input='Ask', output='Final answer', status='completed', error=None)
    events = [dict(id=1, name='delta', observed_at=110, data=dict(text='EARLIER ')),
              dict(id=2, name='delta', observed_at=120, data=dict(text='LATER')),
              dict(id=3, name='tool', observed_at=130, data=dict(name='read_file'))]
    items = _journal_turn(run, events)
    assert items[1]['content'] == 'EARLIER LATER'
    assert items[1]['timed_chunks'] == [{'text': 'EARLIER ', 'observed_at': 110}, {'text': 'LATER', 'observed_at': 120}]
    assert items[-1]['content'] == 'Final answer'
    assert items[-1]['observed_at'] == 140


def test_authoritative_final_discards_uncommitted_tail_even_when_different_or_empty():
    from backend.native_catalog import _journal_turn
    events = [dict(id=1, name='delta', observed_at=110, data=dict(text='Partial ')),
              dict(id=2, name='delta', observed_at=120, data=dict(text='answer'))]
    for output in ('Partial answer', 'Authoritative replacement', ''):
        run = dict(id='fixture', created_at=100, updated_at=140, input='Ask',
                   output=output, status='completed', error=None)
        items = _journal_turn(run, events)
        assert [item['content'] for item in items] == ['Ask', output]
        assert items[-1]['observed_at'] == 140
        assert not any(item.get('channel') == 'commentary' for item in items)


def test_deferred_public_tail_is_bounded_and_never_visible_without_receipt():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=130, input='Ask', output='Final', status='completed', error=None)
    for chunks in ([dict(text='A', observed_at=110), dict(text='B', observed_at=120)],
                   [dict(text='x', observed_at=110)] * 8193,
                   [dict(text='x' * 262145, observed_at=110)]):
        items = _journal_turn(run, [dict(id=i+1, name='delta', observed_at=part['observed_at'],
                                        data=dict(text=part['text'])) for i, part in enumerate(chunks)])
        assert [item['content'] for item in items] == ['Ask', 'Final']
        if len(chunks) <= 8192 and sum(len(part['text']) for part in chunks) <= 262144:
            assert items[-1]['public_tail'] == chunks
        else:
            assert 'public_tail' not in items[-1]


def test_terminal_without_authoritative_output_preserves_available_tail():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=140, input='Ask',
               output=None, status='unknown', error=None)
    items = _journal_turn(run, [dict(id=1, name='delta', observed_at=110, data=dict(text='Available partial'))])
    assert items[1]['content'] == 'Available partial'
    assert items[1]['timed_chunks'] == [dict(text='Available partial', observed_at=110)]
    assert items[1]['observed_at'] == 110


def test_identical_streamed_final_is_not_an_extra_progress_row():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=140, input='Ask', output='EARLIER LATER', status='completed', error=None)
    items = _journal_turn(run, [dict(id=1, name='delta', observed_at=110, data=dict(text='EARLIER ')),
                                dict(id=2, name='delta', observed_at=120, data=dict(text='LATER'))])
    assert len(items) == 2
    assert items[-1]['content'] == 'EARLIER LATER'
    assert 'public_commentary' not in items[-1]
    assert items[-1]['observed_at'] == 140


def test_materialized_chunk_metadata_is_bounded_and_keeps_full_public_content():
    from backend.native_catalog import _journal_turn
    run = dict(id='fixture', created_at=100, updated_at=9000, input='Ask', output='Final', status='completed', error=None)
    events = [dict(id=i+1, name='delta', observed_at=110+i, data=dict(text='x')) for i in range(8193)]
    events.append(dict(id=9000, name='tool', observed_at=8900, data=dict(name='last_tool')))
    items = _journal_turn(run, events)
    assert items[1]['content'] == 'x' * 8193
    assert items[1]['timed_chunks'] == [], 'oversized metadata is explicitly unavailable, not truncated or first-time imputed'


def test_timed_chunk_projection_accepts_only_public_replay_text(chat):
    from backend.native_catalog import _journal_turn
    app, _, user, _ = chat
    run = admit(chat)
    journal = app.state.journal
    for data in [dict(text='PRIVATE', channel='analysis'), dict(text='PRIVATE', phase='reasoning'),
                 dict(text='PRIVATE', reasoning_content='hidden'),
                 dict(text='Public', observed_at=999, timed_chunks=[dict(text='PRIVATE')], arguments='PRIVATE')]:
        journal.event(user['id'], run['id'], 'delta', data)
    journal.event(user['id'], run['id'], 'tool', dict(name='last_tool'))
    replay = latest(chat)['tool_replay']['events']
    items = _journal_turn({**run, 'status': 'completed', 'output': 'Final', 'error': None}, replay)
    assert items[1]['content'] == 'Public'
    assert items[1]['timed_chunks'] == [{'text': 'Public', 'observed_at': replay[0]['observed_at']}]
    assert 'PRIVATE' not in json.dumps(items)
    assert items[1]['timed_chunks'][0]['observed_at'] != 999
    journal.finish(user['id'], run['id'], 'completed', output='Final')
    from test_chat_snapshot import persist
    persist(chat, [('user', run['input'], None), ('assistant', 'Final', None),
                   ('user', 'Later CLI turn', None), ('assistant', 'Later answer', None)])
    materialized = latest(chat)['items']
    progress = next(item for item in materialized if item.get('channel') == 'commentary')
    assert progress['timed_chunks'] == items[1]['timed_chunks']
    assert 'PRIVATE' not in json.dumps(materialized)
