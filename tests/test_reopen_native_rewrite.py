"""Native archive_and_compact seam; real fixture DBs, never live model actions."""
import sqlite3

import pytest

from test_chat_snapshot import chat, admit, persist, latest


def compact(chat):
    """Native compaction archives and reinserts protected rows with timestamps."""
    with sqlite3.connect(chat[3]) as c:
        c.row_factory = sqlite3.Row
        columns = {row[1] for row in c.execute('PRAGMA table_info(messages)')}
        if 'active' not in columns:
            c.execute('ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1')
            c.execute('ALTER TABLE messages ADD COLUMN compacted INTEGER DEFAULT 0')
        rows = c.execute("SELECT role,content,tool_calls,timestamp FROM messages WHERE session_id='wa-1' AND active=1 ORDER BY id").fetchall()
        c.execute("UPDATE messages SET active=0,compacted=1 WHERE session_id='wa-1' AND active=1")
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES('wa-1','user','Fixture compaction reference summary',900)")
        c.executemany('INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)',
                      [('wa-1', *row) for row in rows])


def test_reopen_after_compaction_relocates_anchor_without_swallowing_equal_old_turn(chat):
    app, _, user, db = chat
    persist(chat, [('user', 'Again', None), ('assistant', 'Earlier answer', None)])
    run = admit(chat, text='Again')
    app.state.journal.set_upstream(user['id'], run['id'], 'fixture-upstream')
    compact(chat)
    persist(chat, [('user', 'Again', None),
        ('assistant', 'Working', '[{"id":"live-call","function":{"name":"read_file","arguments":"{}"}}]'),
        ('tool', 'Frozen result', None)])
    # Distinct turns can have equal content, but native preserved timestamps
    # distinguish the reinserted historical turn from this new turn.
    with sqlite3.connect(db) as c:
        c.execute("UPDATE messages SET timestamp=101 WHERE id=(SELECT max(id)-2 FROM messages)")
    before = db.read_bytes()
    body = latest(chat)
    assert body['run']['id'] == run['id']
    assert [row['content'] for row in body['items']].count('Again') == 1
    assert not any(row['content'] == 'Frozen result' for row in body['items'])
    assert not any(row['content'] == 'Working' for row in body['items'])
    assert body['total'] == len(body['items'])
    assert db.read_bytes() == before


@pytest.mark.parametrize('role,content', [
    ('system', 'Fixture system boundary'),
    ('user', '[ASYNC DELEGATION BATCH COMPLETE — earlier-batch]\nEarlier result'),
])
@pytest.mark.parametrize('completed', [False, True])
def test_non_turn_prefix_does_not_defeat_current_turn_identity(chat, role, content, completed):
    app, _, user, _ = chat
    run = admit(chat)
    persist(chat, [(role, content, None), ('user', 'Next turn', None), ('assistant', 'Answer', None)])
    if completed:
        app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    body = latest(chat)
    assert (body['run'] is None) == completed
    assert [item['content'] for item in body['items']].count('Next turn') == int(completed)
    assert content in [item['content'] for item in body['items']], 'Non-turn prefix is not owned by the new run'


@pytest.mark.parametrize('epochs', [1, 2])
@pytest.mark.parametrize('completed', [False, True])
def test_rewritten_current_turn_has_one_authority_and_keeps_later_equal_cli_turn(chat, epochs, completed):
    app, client, user, _ = chat
    run = admit(chat, text='Again')
    persist(chat, [('user', 'Again', None), ('assistant', 'Answer', None)])
    for _ in range(epochs):
        compact(chat)
    if completed:
        app.state.journal.finish(user['id'], run['id'], 'completed', output='Answer')
    persist(chat, [('user', 'Again', None), ('assistant', 'External answer', None)])
    with sqlite3.connect(chat[3]) as c:
        c.execute('UPDATE messages SET timestamp=200 WHERE id>=(SELECT max(id)-1 FROM messages)')
    body = latest(chat)
    assert (body['run'] is None) == completed
    assert [item['content'] for item in body['items']].count('Again') == 1 + int(completed)
    assert [item['content'] for item in body['items']].count('Answer') == int(completed)
    assert [item['content'] for item in body['items']].count('External answer') == 1
    from test_auth import BASE
    pages = [client.get(BASE + '/sessions/wa-1/messages?limit=1&offset=' + str(i)).json()
             for i in range(body['total'])]
    assert [page['items'][0] for page in pages] == body['items']
    assert all(page['total'] == body['total'] for page in pages)


def test_prior_rewritten_completed_run_does_not_create_synthetic_duplicates(chat):
    app, _, user, _ = chat
    first = admit(chat)
    compact(chat)
    persist(chat, [('user', 'Next turn', None), ('assistant', 'Saved answer', None)])
    app.state.journal.finish(user['id'], first['id'], 'completed', output='Saved answer')
    second = admit(chat, text='New turn', key='second')
    persist(chat, [('user', 'New turn', None), ('tool', 'Current frozen tool', None)])
    compact(chat)
    body = latest(chat)
    assert body['run']['id'] == second['id']
    assert not any(item.get('source') == 'journal' for item in body['items'])
    assert [item['content'] for item in body['items']].count('Next turn') == 1
    assert [item['content'] for item in body['items']].count('Saved answer') == 1
    assert not any(item['content'] in ('New turn', 'Current frozen tool') for item in body['items'])


def test_failed_journal_only_equal_input_survives_relocated_shared_anchor(chat):
    app, _, user, _ = chat
    first = admit(chat, text='Again')
    app.state.journal.finish(user['id'], first['id'], 'failed', error='Fixture failure')
    second = admit(chat, text='Again', key='second')
    compact(chat)
    persist(chat, [('user', 'Again', None), ('tool', 'Frozen tool', None)])
    body = latest(chat)
    saved = [row for row in body['items'] if row['content'] == 'Again']
    assert len(saved) == 1
    assert saved[0]['id'] == 'journal:' + first['id'] + ':user'
    assert body['run']['id'] == second['id']
    assert not any(row['content'] == 'Frozen tool' for row in body['items'])


def test_mismatched_real_user_before_equal_input_is_never_searched_past(chat):
    run = admit(chat)
    compact(chat)
    persist(chat, [('system', 'Fixture system row', None), ('user', 'External input', None),
                   ('assistant', 'External answer', None), ('user', 'Next turn', None),
                   ('tool', 'Unattributed tool', None)])
    body = latest(chat)
    assert body['run']['id'] == run['id']
    assert body['snapshot']['reconciliation'] == 'conservative-union'
    assert all(text in [row['content'] for row in body['items']]
               for text in ('External input', 'External answer', 'Next turn', 'Unattributed tool'))


def test_legacy_unanchored_rewritten_history_stays_native_authoritative(chat):
    app, _, user, _ = chat
    run, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'Again', 'legacy')
    persist(chat, [('user', 'Again', None), ('assistant', 'Earlier answer', None)])
    compact(chat)
    body = latest(chat)
    assert body['run'] is None and body['last_run']['id'] == run['id']
    assert body['snapshot']['mode'] == 'legacy-unanchored'
    assert [row['content'] for row in body['items']].count('Again') == 1


def test_ambiguous_multiple_live_anchor_copies_are_not_relocated(chat):
    admit(chat)
    compact(chat)
    with sqlite3.connect(chat[3]) as c:
        c.execute("INSERT INTO messages(session_id,role,content,tool_calls,timestamp) SELECT session_id,role,content,tool_calls,timestamp FROM messages WHERE id=1")
    persist(chat, [('user', 'Next turn', None), ('tool', 'Unattributed tool', None)])
    body = latest(chat)
    assert body['run'] is not None
    assert [row['content'] for row in body['items']].count('Retained answer') == 3
    assert any(row['content'] == 'Unattributed tool' for row in body['items'])


def test_rewrite_identity_includes_tool_call_id_and_never_collapses_live_equal_turns(chat):
    with sqlite3.connect(chat[3]) as c:
        c.execute('ALTER TABLE messages ADD COLUMN tool_call_id TEXT')
    persist(chat, [('tool', 'Equal result', None)])
    with sqlite3.connect(chat[3]) as c:
        c.execute("UPDATE messages SET tool_call_id='archived-call' WHERE role='tool'")
    compact(chat)
    with sqlite3.connect(chat[3]) as c:
        c.execute("UPDATE messages SET tool_call_id='different-call' WHERE role='tool' AND active=1")
    persist(chat, [('user', 'Equal input', None), ('user', 'Equal input', None)])
    body = latest(chat)
    assert [row['content'] for row in body['items']].count('Equal result') == 2
    assert [row['content'] for row in body['items']].count('Equal input') == 2


def build_reopen_fixture(directory):
    """Return REAL composed snapshot + durable replay events for browser tests.

    Standalone CLI below serializes this; only writes under the supplied temp
    directory. Includes archived/reinserted current input and frozen tools.
    """
    from pathlib import Path
    from types import SimpleNamespace
    from backend.native_catalog import NativeCatalog
    from backend.runs import RunJournal
    from backend.chat_snapshot import conversation_snapshot
    from test_native_catalog import create_native_db

    directory = Path(directory)
    home = directory / 'native'
    home.mkdir(parents=True)
    create_native_db(home / 'state.db')
    journal = RunJournal(directory / 'runs.sqlite')
    catalog = NativeCatalog({'default': home})
    user = {'id': 'fixture-owner', 'profile': 'default'}
    local = (SimpleNamespace(state=SimpleNamespace(journal=journal, catalog=catalog)), None, user, home / 'state.db')
    run = admit(local, text='Reopen this running turn')
    journal.set_upstream(user['id'], run['id'], 'fixture-upstream')
    persist(local, [('user', run['input'], None),
        ('assistant', 'Reading fixture', '[{"id":"fixture-read","function":{"name":"read_file","arguments":"{}"}}]'),
        ('tool', 'Frozen fixture result', None)])
    compact(local)
    journal.event(user['id'], run['id'], 'delta', {'text': 'Reading fixture'})
    journal.event(user['id'], run['id'], 'tool', {
        'event': 'tool.started', 'tool': 'read_file', 'tool_call_id': 'fixture-read', 'summary': 'read_file'})
    journal.event(user['id'], run['id'], 'tool', {
        'event': 'tool.completed', 'tool': 'read_file', 'tool_call_id': 'fixture-read', 'status': 'success'})
    journal.event(user['id'], run['id'], 'delta', {'text': 'Continuing after reopen'})
    journal.event(user['id'], run['id'], 'tool', {
        'event': 'tool.started', 'tool': 'web_search', 'tool_call_id': 'fixture-live', 'summary': 'web_search'})
    snapshot = conversation_snapshot(catalog, journal, user, 'wa-1', latest=True)
    return {'snapshot': snapshot, 'events': journal.events(user['id'], run['id']),
            'session_id': 'wa-1', 'run_id': run['id']}


def test_browser_fixture_uses_real_composition_for_midrun_rewrite(tmp_path):
    fixture = build_reopen_fixture(tmp_path)
    body = fixture['snapshot']
    assert body['run']['status'] == 'running'
    assert body['run']['input'] == 'Reopen this running turn'
    assert not any(row['content'] in ('Reopen this running turn', 'Frozen fixture result', 'Reading fixture')
                   for row in body['items'])
    assert body['total'] == len(body['items']) == 2
    assert len(fixture['events']) == 5


def test_second_compaction_drops_anchor_but_not_overlay_owned_rows(tmp_path):
    from backend.native_catalog import NativeCatalog
    from backend.runs import RunJournal
    from backend.chat_snapshot import conversation_snapshot

    fixture = build_reopen_fixture(tmp_path)
    db = tmp_path / 'native' / 'state.db'
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT role,content,tool_calls,timestamp FROM messages WHERE session_id='wa-1' AND active=1 AND content NOT IN ('Retained answer','Fixture compaction reference summary') ORDER BY id").fetchall()
        c.execute("UPDATE messages SET active=0,compacted=1 WHERE session_id='wa-1' AND active=1")
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES('wa-1','user','Second compaction summary',901)")
        c.executemany('INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)', [('wa-1', *row) for row in rows])
    before = db.read_bytes()
    body = conversation_snapshot(NativeCatalog({'default': tmp_path / 'native'}),
        RunJournal(tmp_path / 'runs.sqlite'), {'id': 'fixture-owner', 'profile': 'default'}, 'wa-1', latest=True)
    assert body['run']['id'] == fixture['run_id']
    assert not any(row['content'] in ('Reopen this running turn', 'Reading fixture', 'Frozen fixture result') for row in body['items'])
    assert 'Second compaction summary' in [row['content'] for row in body['items']]
    assert body['total'] == len(body['items'])
    assert db.read_bytes() == before


def test_dropped_later_user_boundary_does_not_assign_its_answer_to_overlay(chat):
    run = admit(chat)
    persist(chat, [('user', 'Next turn', None), ('tool', 'Owned tool', None),
                   ('user', 'External input', None), ('assistant', 'External answer', None)])
    compact(chat)
    with sqlite3.connect(chat[3]) as c:
        rows = c.execute("SELECT role,content,tool_calls,timestamp FROM messages WHERE active=1 AND content NOT IN ('Retained answer','Fixture compaction reference summary','External input') ORDER BY id").fetchall()
        c.execute('UPDATE messages SET active=0,compacted=1 WHERE active=1')
        c.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES('wa-1','user','Second summary',901)")
        c.executemany('INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)', [('wa-1', *row) for row in rows])
    body = latest(chat)
    assert body['run']['id'] == run['id']
    contents = [row['content'] for row in body['items']]
    assert 'Next turn' not in contents and 'Owned tool' not in contents
    assert contents.count('External input') == 1
    assert contents.count('External answer') == 1


def test_rewrite_resolution_has_bounded_sql_work_for_6000_rows():
    import time
    from backend.native_catalog import _native_rewrites

    with sqlite3.connect(':memory:') as c:
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL,tool_calls TEXT,active INTEGER,compacted INTEGER)')
        c.execute('CREATE INDEX idx_messages_session_id ON messages(session_id)')
        for active in (0, 1):
            c.executemany('INSERT INTO messages(session_id,role,content,timestamp,active,compacted) VALUES(?,?,?,?,?,?)',
                [('s', 'assistant', 'row-' + str(i), i, active, 1-active) for i in range(3000)])
        calls = 0
        def budget():
            nonlocal calls
            calls += 1
            return calls > 2000
        c.set_progress_handler(budget, 1000)
        started = time.perf_counter()
        try:
            rewrites = _native_rewrites(c, 's', {row[1] for row in c.execute('PRAGMA table_info(messages)')})
        except sqlite3.OperationalError as exc:
            pytest.fail('Rewrite exceeded 2 million SQLite VM instructions: ' + str(exc))
        elapsed = time.perf_counter() - started
        assert rewrites == {i: i + 3000 for i in range(1, 3001)}
        print(f'6000 rows: {elapsed:.4f}s, fewer than {calls * 1000 + 1000} VM instructions')


if __name__ == '__main__':
    import json
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix='reopen-native-fixture-') as directory:
        print(json.dumps(build_reopen_fixture(directory)))
