"""Deterministic snapshot tests: actual app/journal/native SQLite, no model calls."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db


@pytest.fixture
def chat(tmp_path):
    home = tmp_path / 'native'
    home.mkdir()
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home}, bootstrap_secret=BOOTSTRAP))
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        user = client.get(BASE + '/auth/me').json()['user']
        yield app, client, user, home / 'state.db'


def admit(chat, text='Next turn', key='one', sid='wa-1'):
    app, _, user, _ = chat
    return app.state.journal.submit(user['id'], 'default', sid, text, key,
        history_anchor=lambda: app.state.catalog.history_anchor('default', sid))[0]


def persist(chat, rows, sid='wa-1'):
    with sqlite3.connect(chat[3]) as c:
        c.executemany('INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)',
                      [(sid, role, content, calls, 100) for role, content, calls in rows])


def latest(chat, query=''):
    response = chat[1].get(BASE + '/sessions/wa-1/messages?latest=true' + query)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('status', ['queued', 'running', 'stopping', 'waiting_for_approval', 'unknown', 'completed', 'failed', 'cancelled'])
def test_partial_native_turn_is_excluded_from_journal_overlay(chat, status):
    app, _, user, _ = chat
    run = admit(chat)
    if status == 'running':
        app.state.journal.set_upstream(user['id'], run['id'], 'upstream')
    elif status != 'queued':
        app.state.journal.finish(user['id'], run['id'], status, output='Answer' if status == 'completed' else None)
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Working', '[{"id":"call","function":{"name":"read_file","arguments":"{}"}}]'), ('tool', 'Result', None)])
    body = latest(chat)
    assert body['run'] is not None, 'Partially persisted input/tools must not hide the durable run'
    assert body['run']['id'] == run['id']
    assert body['run']['status'] == status
    assert [row['content'] for row in body['items']] == ['Retained answer']
    assert (body['total'], body['offset']) == (1, 0)
    assert body['snapshot'] == {'mode': 'overlay', 'anchored': True}


@pytest.mark.parametrize('later_turn', [False, True])
def test_completed_persisted_turn_becomes_authoritative_native_history(chat, later_turn):
    app, _, user, _ = chat
    run = admit(chat)
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    persist(chat, [('user', 'Next turn', None), ('assistant', None, '[{"id":"t","function":{"name":"read_file","arguments":"{}"}}]'), ('tool', 'Result', None), ('assistant', 'Answer', None)])
    if later_turn:
        persist(chat, [('user', 'External later turn', None), ('assistant', 'Later answer', None)])
    body = latest(chat)
    assert body['run'] is None, 'Persisted terminal turn must not also replay as an overlay'
    assert body['last_run']['id'] == run['id']
    assert body['snapshot'] == {'mode': 'history', 'anchored': True}
    assert [row['content'] for row in body['items']].count('Next turn') == 1
    assert [row['content'] for row in body['items']].count('Answer') == 1
    assert sum(row['role'] == 'tool' for row in body['items']) == 1
    assert body['total'] == (7 if later_turn else 5)


def test_repeated_identical_turns_are_not_globally_deduplicated(chat):
    app, _, user, _ = chat
    first = admit(chat, text='Again')
    persist(chat, [('user', 'Again', None), ('assistant', 'Same answer', None)])
    app.state.journal.finish(user['id'], first['id'], 'completed', output='Same answer')
    second = admit(chat, text='Again', key='second')
    persist(chat, [('user', 'Again', None)])
    body = latest(chat)
    assert body['run']['id'] == second['id']
    assert [row['content'] for row in body['items']] == ['Retained answer', 'Again', 'Same answer']
    persist(chat, [('assistant', 'Same answer', None)])
    app.state.journal.finish(user['id'], second['id'], 'completed', output='Same answer')
    body = latest(chat)
    assert body['run'] is None
    assert [row['content'] for row in body['items']].count('Again') == 2
    assert [row['content'] for row in body['items']].count('Same answer') == 2


@pytest.mark.parametrize('persist_final', [False, True])
def test_completion_between_journal_and_native_reads_is_reconciled(chat, monkeypatch, persist_final):
    app, _, user, _ = chat
    run = admit(chat)
    original = app.state.catalog.messages
    calls = []
    def racing_read(*args, **kwargs):
        if not calls:
            calls.append(True)
            if persist_final:
                persist(chat, [('user', 'Next turn', None), ('assistant', 'Answer', None)])
            app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.catalog, 'messages', racing_read)
    body = latest(chat)
    assert body['last_run']['status'] == 'completed', 'Do not mix an old journal state with a newer native page'
    assert (body['run'] is None) == persist_final
    assert [row['content'] for row in body['items']].count('Next turn') == int(persist_final)


def test_admission_during_snapshot_read_retries_before_returning(chat, monkeypatch):
    app, _, _, _ = chat
    original = app.state.catalog.messages
    admitted = []
    def racing_read(*args, **kwargs):
        if not admitted:
            admitted.append(admit(chat))
            persist(chat, [('user', 'Next turn', None)])
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.catalog, 'messages', racing_read)
    body = latest(chat)
    assert body['run'] is not None, 'A newly admitted turn cannot be swallowed into native-only history'
    assert body['run']['id'] == admitted[0]['id']
    assert [row['content'] for row in body['items']] == ['Retained answer']


def test_unstable_journal_fails_bounded_instead_of_returning_mixed_snapshot(chat, monkeypatch):
    app, client, user, _ = chat
    run = admit(chat)
    original = app.state.catalog.messages
    reads = []
    def racing_read(*args, **kwargs):
        reads.append(True)
        app.state.journal.finish(user['id'], run['id'], 'unknown', error=str(len(reads)))
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.catalog, 'messages', racing_read)
    response = client.get(BASE + '/sessions/wa-1/messages?latest=true')
    assert response.status_code == 503
    assert len(reads) == 3


@pytest.mark.asyncio
async def test_admission_persists_watermark_before_dispatch_and_keeps_it_on_retry(tmp_path):
    from test_orchestration import runtime_at, USER, BODY
    runtime, journal, gateway = runtime_at(tmp_path)
    db = runtime.catalog.profiles['default'] / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO messages VALUES(7,'native','assistant','Prior',NULL,1)")
    try:
        run = await runtime.submit(USER, BODY)
        with closing(journal.connect()) as c:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE name='run_history_anchors'").fetchone(), 'Admission needs durable app-only history anchors'
            anchor = dict(c.execute('SELECT * FROM run_history_anchors WHERE run_id=?', (run['id'],)).fetchone())
        assert anchor['message_id'] == 7
        assert anchor['session_id'] == 'native'
        assert not gateway.starts, 'Anchor must exist before background dispatch'
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO messages VALUES(8,'native','user','hello',NULL,2)")
        assert (await runtime.submit(USER, BODY))['id'] == run['id']
        with closing(journal.connect()) as c:
            assert c.execute('SELECT message_id FROM run_history_anchors').fetchone()[0] == 7
    finally:
        await runtime.close()


def test_reopen_empty_conversation_discovers_queued_run_without_browser_storage(chat):
    app, client, user, _ = chat
    run, _ = app.state.journal.submit(user['id'], 'default', 'cli-1', 'Still here', 'one')
    response = client.get(BASE + '/sessions/cli-1/messages?latest=true')
    assert response.status_code == 200
    body = response.json()
    assert body.get('run') == run, 'Latest messages must discover the durable pending input'
    assert body['last_run'] == run
    assert body['items'] == []
    assert body['snapshot'] == {'mode': 'overlay', 'anchored': False}


@pytest.mark.parametrize('status', ['queued', 'running', 'waiting_for_approval'])
def test_restart_recovery_remains_discoverable_without_resubmission(chat, status):
    app, _, user, _ = chat
    run = admit(chat)
    if status == 'running':
        app.state.journal.set_upstream(user['id'], run['id'], 'upstream')
    elif status == 'waiting_for_approval':
        app.state.journal.finish(user['id'], run['id'], status)
    app.state.journal.recover()
    body = latest(chat)
    assert body['run']['id'] == run['id']
    assert body['run']['input'] == 'Next turn'
    assert body['run']['status'] == 'unknown'
    assert not app.state.orchestrator._tasks


@pytest.mark.parametrize('foreign', ['user', 'profile', 'session'])
def test_discovery_never_exposes_foreign_journal_input(chat, foreign):
    app, _, user, _ = chat
    app.state.journal.submit('stranger' if foreign == 'user' else user['id'],
        'other' if foreign == 'profile' else 'default',
        'cli-1' if foreign == 'session' else 'wa-1', 'FOREIGN SECRET', 'foreign')
    body = latest(chat)
    assert body['run'] is None and body['last_run'] is None
    assert 'FOREIGN SECRET' not in str(body)


def test_latest_scopes_before_ordering_and_uses_admission_order_not_update_time(chat):
    app, _, user, _ = chat
    first = admit(chat, key='first')
    app.state.journal.finish(user['id'], first['id'], 'completed', output='First')
    second = admit(chat, key='second')
    app.state.journal.finish(user['id'], second['id'], 'completed', output='Second')
    app.state.journal.submit('stranger', 'default', 'wa-1', 'FOREIGN SECRET', 'foreign')
    app.state.journal.finish(user['id'], first['id'], 'completed', output='Updated first')
    body = latest(chat)
    assert body['run']['id'] == second['id']
    assert 'FOREIGN SECRET' not in str(body)


def test_default_and_offset_pagination_share_composed_prefix_with_latest(chat):
    app, client, _, _ = chat
    persist(chat, [('user', 'Earlier', None), ('assistant', 'Earlier answer', None)])
    admit(chat)
    persist(chat, [('user', 'Next turn', None)])
    ordinary = client.get(BASE + '/sessions/wa-1/messages?limit=1&offset=1').json()
    assert ordinary['items'][0]['content'] == 'Earlier'
    assert ordinary['total'] == 3 and ordinary['offset'] == 1
    assert ordinary['run']['id'] == ordinary['last_run']['id']
    assert ordinary['snapshot'] == {'mode': 'overlay', 'anchored': True}
    body = latest(chat, '&limit=1&offset=999')
    assert body['items'][0]['content'] == 'Earlier answer'
    assert body['total'] == 3 and body['offset'] == 2
    assert body['run']['input'] == 'Next turn'


def test_legacy_nonempty_history_is_never_cut_by_matching_input(chat):
    app, _, user, _ = chat
    persist(chat, [('user', 'Same', None), ('assistant', 'Answer', None)])
    run, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'Same', 'legacy')
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat)
    assert body['run'] is None
    assert body['last_run']['id'] == run['id']
    assert body['total'] == 3
    assert body['snapshot'] == {'mode': 'legacy-unanchored', 'anchored': False}


@pytest.mark.parametrize('suffix', [[], [('user', 'Next turn', None)],
    [('user', 'Other', None), ('assistant', 'Answer', None)],
    [('user', 'Next turn', None), ('assistant', 'Different', None)],
    [('user', 'Next turn', None), ('assistant', 'Answer', '[{"id":"tool"}]')],
    [('user', 'Next turn', None), ('user', 'Another input', None), ('assistant', 'Answer', None)]])
def test_completion_proof_requires_this_anchored_turn_and_final_answer(chat, suffix):
    app, _, user, _ = chat
    run = admit(chat)
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    persist(chat, suffix)
    body = latest(chat)
    assert body['run']['id'] == run['id']
    retained = ['Retained answer']
    if suffix and suffix[0][1] == 'Other':
        retained += ['Other', 'Answer']
    elif len(suffix) > 1 and suffix[1][1] == 'Another input':
        retained += ['Another input', 'Answer']
    assert [row['content'] for row in body['items']] == retained


def test_delegation_envelope_is_not_a_new_turn_boundary(chat):
    app, _, user, _ = chat
    run = admit(chat)
    persist(chat, [('user', 'Next turn', None),
        ('user', '[ASYNC DELEGATION BATCH COMPLETE — batch-1]\nResult', None),
        ('assistant', 'Answer', None)])
    app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat)
    assert body['run'] is None
    assert body['items'][2]['kind'] == 'delegation'


def test_native_snapshot_count_and_rows_share_read_transaction(chat, monkeypatch):
    app, _, _, db = chat
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA journal_mode=WAL')
    original = app.state.catalog._connect
    writes = []
    def connect(profile):
        c = original(profile)
        def trace(sql):
            if 'SELECT count(*) FROM messages' in sql and not writes:
                writes.append(True)
                persist(chat, [('user', 'Concurrent native input', None)])
        c.set_trace_callback(trace)
        return c
    monkeypatch.setattr(app.state.catalog, '_connect', connect)
    body = latest(chat)
    assert writes
    assert body['total'] == len(body['items']) == 1
    assert body['items'][0]['content'] == 'Retained answer'
    assert latest(chat)['total'] == 2


def test_anchor_failure_rolls_back_admission_and_never_dispatches(chat):
    app, _, user, _ = chat
    def unavailable():
        raise sqlite3.OperationalError('native temporarily unavailable')
    with pytest.raises(sqlite3.OperationalError):
        app.state.journal.submit(user['id'], 'default', 'wa-1', 'Must not dispatch', 'bad', history_anchor=unavailable)
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM run_history_anchors').fetchone()[0] == 0
    assert not app.state.orchestrator._tasks


def test_legacy_journal_upgrade_preserves_runs_columns_and_records(chat):
    from backend.runs import RunJournal
    app, _, user, _ = chat
    run, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'Legacy input', 'legacy')
    with closing(app.state.journal.connect()) as c, c:
        columns = list(c.execute('PRAGMA table_info(runs)'))
        c.execute('DROP TABLE run_history_anchors')
    reopened = RunJournal(app.state.journal.path)
    assert reopened.get(user['id'], run['id']) == run
    with closing(reopened.connect()) as c:
        assert list(c.execute('PRAGMA table_info(runs)')) == columns
    assert reopened.latest(user['id'], 'default', 'wa-1') == {'run': run, 'anchor': None}


def test_snapshot_route_remains_authenticated_and_native_read_only(chat):
    app, client, _, db = chat
    admit(chat)
    before = db.read_bytes()
    for _ in range(3):
        assert latest(chat)['run']
    assert db.read_bytes() == before
    client.cookies.clear()
    assert client.get(BASE + '/sessions/wa-1/messages?latest=true').status_code == 401


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'unknown'])
def test_legacy_last_run_preserves_status_and_error_when_no_overlay(chat, status):
    app, client, user, _ = chat
    run, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'Legacy input', 'legacy-error')
    app.state.journal.finish(user['id'], run['id'], status, output='Saved output', error='Saved diagnostic')
    body = latest(chat)
    direct = client.get(BASE + '/runs/' + run['id']).json()
    assert body['run'] is None
    assert body['last_run'] == direct
    assert body['last_run']['status'] == status
    assert body['last_run']['error'] == 'Saved diagnostic'
    assert body['snapshot']['mode'] == 'legacy-unanchored'


def test_anchor_includes_hidden_ids_without_dropping_visible_prior_equal_input(chat):
    app, _, _, db = chat
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Earlier answer', None)])
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
        c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        c.execute("INSERT INTO messages(id,session_id,role,content,active,compacted) VALUES(50,'wa-1','user','Rewound',0,0)")
    run = admit(chat)
    persist(chat, [('user', 'Next turn', None)])
    body = latest(chat)
    assert body['run']['id'] == run['id']
    assert [row['content'] for row in body['items']] == ['Retained answer', 'Next turn', 'Earlier answer']
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT message_id FROM run_history_anchors').fetchone()[0] == 50
