"""Deterministic terminal-tail regressions through the real authenticated ASGI app.

Only temporary databases and the existing virtual authenticator are used. The send
hook models a producer committing events while StreamingResponse yields a batch;
no timing sleeps, live gateway, or model calls are involved.
"""
import asyncio
import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from test_auth import BASE, ORIGIN, enroll
from test_security_regressions import app as sse_fixture, assert_security_headers


@pytest.fixture
def live_stream(sse_fixture):
    app = sse_fixture
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        _, result = enroll(client)
        uid = result.json()['user']['id']
        journal = app.state.journal
        run, _ = journal.submit(uid, 'default', 'synthetic-session', 'test', 'test-key')
        journal.set_upstream(uid, run['id'], 'synthetic-upstream')
        yield app, client, journal, uid, run['id']


def expected_sse_events(recorded):
    """Build the wire contract independently, retaining every event and payload field."""
    expected = []
    for event in recorded:
        data = event['data']
        if event['name'] in ('tool', 'commentary', 'delta') and isinstance(data, dict):
            data = dict(data, observed_at=event['observed_at'])
        elif event['name'] == 'done' and isinstance(data, dict):
            data = dict(data, updated_at=event['observed_at'])
        # Receipt metadata is inside data, not an SSE envelope field.
        expected.append({'id': event['id'], 'name': event['name'], 'data': data})
    return expected


def capture_sse(app, client, url, *, on_body=None, headers=None):
    """Exercise routing, auth, middleware and streaming with a controlled send seam."""
    request = client.build_request('GET', url, headers=headers)
    parsed = urlsplit(str(request.url))
    sent = []

    async def run():
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'GET', 'scheme': 'https', 'path': parsed.path,
                 'raw_path': parsed.path.encode(), 'query_string': parsed.query.encode(),
                 'root_path': '', 'server': ('lindayi.me', 443),
                 'client': ('127.0.0.1', 1234),
                 'headers': [(key.lower(), value) for key, value in request.headers.raw]}
        pending = True

        async def receive():
            nonlocal pending
            if pending:
                pending = False
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)
            if on_body and message['type'] == 'http.response.body':
                on_body(message.get('body', b''))

        await asyncio.wait_for(app(scope, receive, send), timeout=5)

    asyncio.run(run())
    start = next(message for message in sent if message['type'] == 'http.response.start')
    response_headers = {k.decode(): v.decode() for k, v in start['headers']}
    assert_security_headers(response_headers)
    body = b''.join(message.get('body', b'') for message in sent
                    if message['type'] == 'http.response.body').decode()
    if start['status'] != 200:
        return start['status'], body
    assert response_headers['content-type'].startswith('text/event-stream')
    assert response_headers['x-accel-buffering'] == 'no'
    events = []
    for frame in body.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in frame.splitlines() if ': ' in line)
        if 'id' in fields:
            events.append({'id': int(fields['id']), 'name': fields['event'],
                           'data': json.loads(fields['data'])})
    return start['status'], events


@pytest.mark.parametrize('terminal_status', ['completed', 'failed', 'cancelled', 'unknown'])
@pytest.mark.parametrize('with_delta', [False, True])
def test_final_answer_committed_while_tool_batch_is_sent_arrives_without_reopen(
        live_stream, terminal_status, with_delta):
    app, client, journal, uid, rid = live_stream
    journal.event(uid, rid, 'tool', {'status': 'completed', 'name': 'terminal'})
    finished = False

    def finish_during_tool_send(body):
        nonlocal finished
        if b'event: tool\n' in body and not finished:
            finished = True
            if with_delta:
                journal.event(uid, rid, 'delta', {'text': 'The final answer.'})
            journal.finish(uid, rid, terminal_status, output='The final answer.')

    status, events = capture_sse(app, client, BASE+'/runs/'+rid+'/events',
                                 on_body=finish_during_tool_send)
    assert finished, 'The producer must finish after the tool frame was emitted'
    assert journal.get(uid, rid)['output'] == 'The final answer.'
    assert status == 200
    assert [event['name'] for event in events] == (['tool', 'delta', 'done'] if with_delta else ['tool', 'done'])
    assert events == expected_sse_events(journal.events(uid, rid)), 'Every persisted event arrives once, in order'
    assert events[-1]['data']['output'] == 'The final answer.'


def test_done_projects_recorded_receipt_time_without_mutating_payload(live_stream):
    from contextlib import closing
    app, client, journal, uid, rid = live_stream
    journal.event(uid, rid, 'delta', {'text': 'PUBLIC A'})
    journal.event(uid, rid, 'delta', {'text': 'DRAFT B'})
    journal.finish(uid, rid, 'completed', output='DEFINITIVE F')
    recorded = journal.events(uid, rid)
    with closing(journal.connect()) as connection, connection:
        for event, stamp in zip(recorded, [110, 120, 130]):
            connection.execute('UPDATE events SET created_at=? WHERE id=?', (stamp, event['id']))
    status, events = capture_sse(app, client, BASE+'/runs/'+rid+'/events')
    assert status == 200
    assert events[-1]['data'].get('updated_at') == 130
    assert [event['name'] for event in events] == ['delta', 'delta', 'done']
    assert journal.events(uid, rid)[-1]['data'] == recorded[-1]['data']
    assert 'updated_at' not in recorded[-1]['data']


def test_finish_after_empty_journal_read_does_not_close_before_tail(live_stream, monkeypatch):
    app, client, journal, uid, rid = live_stream
    read_events = journal.events
    finished = False

    def finish_after_read(user_id, run_id, after=0):
        nonlocal finished
        batch = read_events(user_id, run_id, after)
        if not finished:
            assert batch == []
            finished = True
            journal.finish(uid, rid, 'completed', output='Answer after empty read')
        return batch

    monkeypatch.setattr(journal, 'events', finish_after_read)
    status, events = capture_sse(app, client, BASE+'/runs/'+rid+'/events')
    assert status == 200
    assert events == expected_sse_events(read_events(uid, rid))
    assert events[-1]['data']['output'] == 'Answer after empty read'


@pytest.mark.parametrize('tail_size', [999, 1000, 1001])
def test_terminal_tail_spanning_batch_boundary_is_ordered_and_resumable(live_stream, tail_size):
    from contextlib import closing

    app, client, journal, uid, rid = live_stream
    tool_id = journal.event(uid, rid, 'tool', {'status': 'completed'})
    finished = False

    def finish_during_tool_send(body):
        nonlocal finished
        if b'event: tool\n' in body and not finished:
            finished = True
            # Bulk fixture setup keeps boundary coverage fast; reads use real journal SQL.
            with closing(journal.connect()) as connection, connection:
                connection.executemany(
                    'INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                    [(rid, 'delta', json.dumps({'text': str(index)}), 0)
                     for index in range(tail_size)])
            journal.finish(uid, rid, 'completed', output='Complete answer')

    url = BASE+'/runs/'+rid+'/events'
    status, events = capture_sse(app, client, url, on_body=finish_during_tool_send)
    assert status == 200
    expected = []
    while batch := journal.events(uid, rid, expected[-1]['id'] if expected else 0):
        expected.extend(batch)
    assert events == expected_sse_events(expected)
    assert len(events) == tail_size + 2
    assert events[-1]['name'] == 'done'
    assert [event['data']['observed_at'] for event in events[1:-1]] == [0] * tail_size
    assert [event['data'] for event in expected[1:-1]] == [
        {'text': str(index)} for index in range(tail_size)
    ], 'Projecting recorded timestamps must not rewrite stored delta payloads'
    assert capture_sse(app, client, url, headers={'Last-Event-ID': str(tool_id)}) == (200, events[1:])
    assert capture_sse(app, client, url+'?after='+str(tool_id),
                       headers={'Last-Event-ID': str(events[-1]['id'])}) == (200, events[1:])
    assert capture_sse(app, client, url, headers={'Last-Event-ID': str(events[-1]['id'])}) == (200, [])


@pytest.mark.parametrize('foreign_scope', ['owner', 'profile'])
def test_terminal_stream_cannot_read_another_owner_or_profile(live_stream, foreign_scope):
    app, client, journal, uid, rid = live_stream
    journal.finish(uid, rid, 'completed')
    other_uid = 'other-owner' if foreign_scope == 'owner' else uid
    other_profile = 'other-profile' if foreign_scope == 'profile' else 'default'
    foreign, _ = journal.submit(other_uid, other_profile, 'other-session', 'secret', 'other-key')
    journal.finish(other_uid, foreign['id'], 'completed', output='Secret answer')
    status, body = capture_sse(app, client, BASE+'/runs/'+foreign['id']+'/events')
    assert status == 404
    assert 'Secret answer' not in body


def test_revoked_session_stops_before_reading_new_terminal_tail(live_stream):
    app, client, journal, uid, rid = live_stream
    journal.event(uid, rid, 'tool', {'status': 'completed'})

    def revoke_during_tool_send(body):
        if b'event: tool\n' in body:
            journal.finish(uid, rid, 'completed', output='Must not be delivered')
            with app.state.auth.store.transaction() as database:
                database.execute('UPDATE sessions SET revoked=1 WHERE user_id=?', (uid,))

    status, events = capture_sse(app, client, BASE+'/runs/'+rid+'/events',
                                 on_body=revoke_during_tool_send)
    assert status == 200
    assert [event['name'] for event in events] == ['tool']


def test_recovered_unknown_without_done_event_closes_after_draining(live_stream):
    app, client, journal, uid, rid = live_stream
    journal.event(uid, rid, 'tool', {'status': 'completed'})
    journal.recover()  # Recovery deliberately has no done event.
    assert journal.get(uid, rid)['status'] == 'unknown'
    assert capture_sse(app, client, BASE+'/runs/'+rid+'/events') == (
        200, expected_sse_events(journal.events(uid, rid)))
