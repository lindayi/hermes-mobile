"""Guidance is read-only presentation of durable acceptance, never a new turn."""
import json
import sqlite3

import pytest

from test_chat_snapshot import chat, admit, persist, latest
from test_latest_tool_replay import record_tool


def attempt(run, **changes):
    return dict(dict(id='attempt-a', run_id=run['id'], idempotency_key='guidance-key',
                     input='Focus on the tests', status='accepted_unconfirmed',
                     steer_id='guidance-key', created_at=120.0, updated_at=121.0), **changes)


def guidance(chat, run, **changes):
    data = attempt(run, **changes)
    return chat[0].state.journal.event(chat[2]['id'], run['id'], 'steering', data)


def test_snapshot_replays_one_allowlisted_acceptance_in_tool_order(chat):
    app, _, user, db = chat
    run = admit(chat)
    first = record_tool(chat, run)
    guidance(chat, run, status='sending')
    accepted = guidance(chat, run, private_token='PRIVATE', reasoning_content='PRIVATE')
    second = record_tool(chat, run, name='web_search')
    guidance(chat, run, updated_at=122.0)
    journal_before, native_before = app.state.journal.path.read_bytes(), db.read_bytes()
    body = latest(chat)
    events = body['tool_replay']['events']
    assert [event['id'] for event in events] == [*first, accepted, *second]
    assert events[2] == {'id': accepted, 'name': 'steering', 'data': attempt(run, updated_at=122.0)}
    assert 'PRIVATE' not in json.dumps(body)
    assert db.read_bytes() == native_before
    assert app.state.journal.path.read_bytes() == journal_before


@pytest.mark.parametrize('status', ['not_delivered', 'unknown'])
def test_later_negative_receipt_updates_only_previously_accepted_message(chat, status):
    run = admit(chat)
    accepted = guidance(chat, run)
    guidance(chat, run, status=status, updated_at=123.0)
    # A stale acceptance cannot undo known nondelivery.
    if status == 'not_delivered':
        guidance(chat, run, updated_at=122.0)
    for unaccepted in ('rejected', 'sending', 'unknown', 'not_delivered'):
        guidance(chat, run, id=unaccepted, status=unaccepted)
    guidance(chat, run, id='wrong-run', run_id='someone-elses-run')
    events = latest(chat)['tool_replay']['events']
    assert events == [{'id': accepted, 'name': 'steering',
                       'data': attempt(run, status=status, updated_at=123.0)}]


def test_accepted_guidance_survives_new_admission_with_interleaved_tools(chat):
    app, _, user, db = chat
    run = admit(chat)
    first = record_tool(chat, run)
    guidance(chat, run)
    second = record_tool(chat, run, name='web_search')
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    active = admit(chat, text='Next real turn', key='second')
    before = db.read_bytes()
    body = latest(chat, '&turn_boundary=true')
    owned = [row for row in body['items'] if row.get('run_id') == run['id']]
    assert [row['role'] for row in owned] == ['user', 'tool', 'user', 'tool', 'assistant']
    item = owned[2]
    assert item == dict(id=f"journal:{run['id']}:steering:attempt-a", role='user',
                        kind='guidance', content='Focus on the tests', tool_calls=None,
                        timestamp=120.0, run_id=run['id'], source='journal',
                        steering_id='attempt-a', idempotency_key='guidance-key',
                        steer_id='guidance-key', steering_status='accepted_unconfirmed',
                        turn_boundary=False)
    assert body['run']['id'] == active['id']
    assert body['total'] == body['count'] == len(body['items']) - 1
    assert db.read_bytes() == before


@pytest.mark.parametrize('next_admission', [False, True])
def test_completed_guided_turn_stays_before_later_native_turn(chat, next_admission):
    app, _, user, db = chat
    run = admit(chat)
    guidance(chat, run)
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Answer', None),
                   ('user', 'Later CLI', None), ('assistant', 'Later answer', None)])
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    if next_admission:
        admit(chat, text='Next web', key='next-web')
    body = latest(chat, '&turn_boundary=true')
    assert [item['content'] for item in body['items']] == [
        'Retained answer', 'Next turn', 'Focus on the tests', 'Answer', 'Later CLI', 'Later answer']
    assert body['total'] == body['count'] == 5
    assert body['items'][2]['kind'] == 'guidance'
    assert body['tool_replay'] is None or not body['tool_replay']['events']


OOB_OPEN = ('[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered '
            'once at this position; not tool output and not a new delivery when replayed '
            'from conversation history]')
OOB = OOB_OPEN + '\nFocus on the tests\n[/OUT-OF-BAND USER MESSAGE]'


@pytest.mark.parametrize('next_admission', [False, True])
def test_native_oob_does_not_split_or_duplicate_guided_turn(chat, next_admission):
    app, _, user, db = chat
    run = admit(chat)
    record_tool(chat, run)
    guidance(chat, run)
    record_tool(chat, run, name='web_search')
    persist(chat, [('user', 'Next turn', None), ('tool', 'Result\n' + OOB, None),
                   ('user', OOB, None), ('assistant', 'Answer', None),
                   ('user', 'Later CLI', None), ('assistant', 'Later answer', None)])
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    if next_admission:
        admit(chat, text='Next web', key='next-web')
    body = latest(chat, '&turn_boundary=true')
    assert OOB_OPEN not in json.dumps(body, ensure_ascii=False)
    assert [item['content'] for item in body['items']].count('Focus on the tests') == 1
    assert [item['content'] for item in body['items']].count('Answer') == 1
    assert [item['content'] for item in body['items']][-2:] == ['Later CLI', 'Later answer']


def test_prior_guidance_compaction_preserves_external_answer_without_user_copy(chat):
    from test_auth import BASE
    from test_reopen_native_rewrite import compact

    app, client, user, db = chat
    run = admit(chat)
    guidance(chat, run)
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Answer', None),
                   ('user', 'External input', None), ('assistant', 'External answer', None)])
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    active = admit(chat, text='New web turn', key='second')
    compact(chat)
    # Keep the external user's archived identity, but do not reinsert its boundary.
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT role,content,tool_calls,timestamp FROM messages "
            "WHERE session_id='wa-1' AND active=1 AND content NOT IN "
            "('External input','Fixture compaction reference summary') ORDER BY id"
        ).fetchall()
        c.execute("UPDATE messages SET active=0,compacted=1 WHERE session_id='wa-1' AND active=1")
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) "
                  "VALUES('wa-1','user','Second summary',901)")
        c.executemany('INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)',
                      [('wa-1', *row) for row in rows])
        external_ids = {row[0] for row in c.execute(
            "SELECT max(id) FROM messages WHERE session_id='wa-1' "
            "AND content IN ('External input','External answer') GROUP BY content")}
    before = db.read_bytes(), app.state.journal.path.read_bytes()
    body = latest(chat, '&turn_boundary=true')
    contents = [row['content'] for row in body['items']]
    assert 'External input' in contents
    assert 'External answer' in contents
    assert external_ids <= {row['id'] for row in body['items']}
    assert all(contents.count(text) == 1 for text in ('Next turn', 'Focus on the tests', 'Answer'))
    assert body['run']['id'] == active['id']
    assert body['total'] == body['count'] == len(body['items']) - 1
    pages = [client.get(BASE + f'/sessions/wa-1/messages?limit=1&offset={i}').json()
             for i in range(body['total'])]
    assert [row for page in pages for row in page['items']] == body['items']
    assert all(page['total'] == body['total'] and page['offset'] == i
               and page.get('count', len(page['items'])) == 1 for i, page in enumerate(pages))
    assert before == (db.read_bytes(), app.state.journal.path.read_bytes())


def test_guidance_presentation_expansion_keeps_raw_page_arithmetic(chat):
    from test_auth import BASE
    app, client, user, _ = chat
    run = admit(chat)
    record_tool(chat, run)
    guidance(chat, run)
    guidance(chat, run, id='attempt-b', idempotency_key='other-key', input='Keep it concise')
    record_tool(chat, run, name='web_search')
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    admit(chat, text='Next real turn', key='second')
    whole = latest(chat, '&turn_boundary=true')
    pages = [client.get(BASE + f'/sessions/wa-1/messages?limit=1&offset={i}').json()
             for i in range(whole['total'])]
    assert [item for page in pages for item in page['items']] == whole['items']
    expanded = next(page for page in pages if len(page['items']) > 1)
    assert expanded['count'] == 1, 'presentation bubbles must not move the raw cursor'
    aligned = latest(chat, '&limit=1&turn_boundary=true')
    assert aligned['items'][0]['content'] == 'Next turn'
    assert aligned['offset'] + aligned['count'] == whole['total']


def test_foreign_owner_profile_session_and_unaccepted_attempts_never_project(chat):
    app, _, user, _ = chat
    journal = app.state.journal
    run = admit(chat)
    guidance(chat, run)
    journal.finish(user['id'], run['id'], 'completed', output='Answer')
    for owner, profile, session in [('foreign', 'default', 'wa-1'),
                                     (user['id'], 'other', 'wa-1'),
                                     (user['id'], 'default', 'cli-1')]:
        foreign, _ = journal.submit(owner, profile, session, 'PRIVATE input', profile + session)
        journal.event(owner, foreign['id'], 'steering', attempt(foreign, input='PRIVATE guidance'))
        journal.finish(owner, foreign['id'], 'completed', output='PRIVATE output')
    next_run = admit(chat, text='New turn', key='next')
    for status in ('rejected', 'sending', 'unknown', 'not_delivered'):
        guidance(chat, next_run, id=status, status=status, input='PRIVATE unaccepted')
    body = latest(chat)
    assert 'PRIVATE' not in json.dumps(body)
    assert sum(item.get('kind') == 'guidance' for item in body['items']) == 1
    assert body['run']['id'] == next_run['id']
    assert not body['tool_replay']['events']


@pytest.mark.parametrize('changes', [
    {'steer_id': {'private': 'PRIVATE'}},
    {'created_at': {'private': 'PRIVATE'}},
    {'updated_at': ['PRIVATE']},
    {'input': 'PRIVATE conflicting input'},
    {'idempotency_key': 'PRIVATE conflicting identity'},
])
def test_malformed_or_conflicting_receipts_cannot_replace_accepted_identity(chat, changes):
    run = admit(chat)
    accepted = guidance(chat, run)
    guidance(chat, run, **changes)
    events = latest(chat)['tool_replay']['events']
    assert events == [{'id': accepted, 'name': 'steering', 'data': attempt(run)}]
