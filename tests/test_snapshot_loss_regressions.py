"""Loss regressions against temporary native and app-journal SQLite databases."""
import pytest
from test_chat_snapshot import chat, admit, persist, latest
from test_auth import BASE


def test_prior_legacy_persisted_turn_does_not_duplicate_native_history(chat):
    app, _, user, _ = chat
    legacy, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'Legacy input', 'legacy')
    persist(chat, [('user', 'Legacy input', None), ('assistant', 'Legacy answer', None)])
    app.state.journal.finish(user['id'], legacy['id'], 'completed', output='Legacy answer')
    admit(chat, text='New anchored input')
    body = latest(chat)
    assert [item['content'] for item in body['items']] == ['Retained answer', 'Legacy input', 'Legacy answer']
    assert body['run']['input'] == 'New anchored input'


def test_active_turn_with_171_later_native_tool_rows_keeps_input_overlay(chat):
    app, _, user, _ = chat
    run = admit(chat, text='Long running prompt')
    app.state.journal.set_upstream(user['id'], run['id'], 'upstream-fixture')
    persist(chat, [('user', 'Long running prompt', None)] + [('tool', 'fixture tool result', None)] * 171)
    body = latest(chat, '&limit=100')
    assert body['run']['id'] == run['id']
    assert body['run']['input'] == 'Long running prompt'
    assert [item['content'] for item in body['items']] == ['Retained answer']
    assert body['total'] == 1, 'current-turn tool rows are replayed once, not a misleading latest history page'


def test_identical_unpersisted_admissions_stay_distinct(chat):
    app, _, user, _ = chat
    first = admit(chat, text='Again')
    app.state.journal.finish(user['id'], first['id'], 'failed')
    second = admit(chat, text='Again', key='second')
    body = latest(chat)
    assert len([item for item in body['items'] if item['content'] == 'Again']) == 1
    assert body['run']['input'] == 'Again' and body['run']['id'] == second['id']


def test_offset_pages_use_same_composed_sequence(chat):
    app, client, user, _ = chat
    first = admit(chat, text='Saved input')
    app.state.journal.finish(user['id'], first['id'], 'failed')
    admit(chat, text='New input', key='two')
    persist(chat, [('user', 'New input', None)])
    full = latest(chat)
    pages = [client.get(BASE + '/sessions/wa-1/messages?limit=1&offset=' + str(i)).json()
             for i in range(full['total'])]
    assert [page['items'][0] for page in pages] == full['items']
    assert all(page['total'] == full['total'] for page in pages)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'unknown'])
@pytest.mark.parametrize('persist_input', [False, True])
def test_external_turn_after_unsuccessful_run_survives(chat, status, persist_input):
    app, _, user, db = chat
    run = admit(chat)
    app.state.journal.finish(user['id'], run['id'], status, error='Stopped')
    if persist_input:
        persist(chat, [('user', 'Next turn', None), ('assistant', 'Partial', None)])
    persist(chat, [('user', 'External CLI/WA input', None), ('assistant', 'External answer', None)])
    before = db.read_bytes()
    body = latest(chat)
    contents = [item['content'] for item in body['items']]
    assert 'External CLI/WA input' in contents
    assert 'External answer' in contents
    assert body['run']['input'] == 'Next turn'
    assert body['snapshot']['reconciliation'] == 'conservative-union'
    assert db.read_bytes() == before


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'completed'])
def test_prior_unpersisted_input_survives_next_admission_and_reopen(chat, status):
    app, _, user, db = chat
    first = admit(chat, text='Accepted first input')
    app.state.journal.finish(user['id'], first['id'], status, output='Saved answer' if status == 'completed' else None)
    second = admit(chat, text='Second input', key='second')
    before = db.read_bytes()
    body = latest(chat)
    saved = [item for item in body['items'] if item['content'] == 'Accepted first input']
    assert len(saved) == 1
    assert saved[0]['id'] == 'journal:' + first['id'] + ':user'
    assert saved[0]['run_status'] == status
    if status == 'completed':
        assert any(item['content'] == 'Saved answer' for item in body['items'])
    assert body['run']['id'] == second['id']
    assert latest(chat) == body
    assert db.read_bytes() == before
