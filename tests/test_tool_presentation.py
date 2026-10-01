import json
import sqlite3
import pytest


@pytest.mark.parametrize('name,args,expected', [
    ('web_search', {'query': 'password=hunter2'}, 'web_search'),
    ('web_search', {'query': 'Authorization: Bearer abc'}, 'web_search'),
    ('web_search', {'query': 'sk-12345678901234567890'}, 'web_search'),
    ('web_search', {'query': 'https://user:secret@example.com/a?token=secret#secret'}, 'web_search: https://example.com/a'),
    ('web_extract', {'urls': ['https://u:p@example.com/docs?q=secret#secret']}, 'web_extract: https://example.com/docs'),
    ('write_file', {'path': 'src/app.py', 'content': 'SECRET'}, 'write_file: src/app.py'),
    ('read_file', {'path': '.env'}, 'read_file'),
    ('browser_navigate', {'url': 'https://u:p@example.com/docs?key=abc'}, 'browser_navigate: https://example.com/docs'),
    ('patch', {'path': 'src/app.py', 'new_string': 'SECRET'}, 'patch: src/app.py'),
    ('search_files', {'path': 'src', 'pattern': 'SECRET'}, 'search_files: src'),
    ('skill_view', {'name': 'pdf'}, 'skill_view: pdf'),
    ('web_search', {'query': 'https://example.com/%70assword/abc'}, 'web_search'),
    ('web_search', {'query': 'HTTPS://u:p@example.com/docs?x=abc'}, 'web_search: https://example.com/docs'),
    ('terminal', {'command': 'echo SECRET > ~/.ssh/id_rsa'}, 'terminal'),
    ('execute_code', {'code': 'SECRET'}, 'execute_code: Run Python script'),
    ('unknown_tool', {'query': 'SECRET'}, 'unknown_tool'),
    ('read_file', {'path': {'secret': 'SECRET'}}, 'read_file'),
    ('web_search', {'query': 'hello\nworld'}, 'web_search: hello world'),
])
def test_allowlisted_summaries_fail_closed(name, args, expected):
    from backend.tool_presentation import tool_summary
    assert tool_summary(name, args) == expected


@pytest.mark.parametrize('name,args,preview,expected', [
    ('terminal', {'command': 'pytest tests/test_auth.py -q'}, None, 'terminal: pytest tests/test_auth.py -q'),
    ('terminal', None, '/usr/bin/python3 -m pytest tests', 'terminal: /usr/bin/python3 -m pytest tests'),
    ('functions.terminal', {'command': 'git status --short'}, None, 'functions.terminal: git status --short'),
    ('terminal', {'command': 'echo HIDDEN > private.key'}, None, 'terminal'),
    ('terminal', {'command': 'curl -H "Authorization: Bearer HIDDEN" https://example.com'}, None, 'terminal'),
    ('process', {'action': 'poll', 'data': 'HIDDEN'}, None, 'process: Poll process'),
    ('delegate_task', {'tasks': [{'goal': 'Inspect UI'}, {'goal': 'Run tests'}]}, None, 'delegate_task: 2 tasks: Inspect UI · Run tests'),
    ('execute_code', {'code': 'PRIVATE'}, None, 'execute_code: Run Python script'),
    ('functions.read_file', {'path': 'docs/guide.md'}, None, 'functions.read_file: docs/guide.md'),
])
def test_action_previews_are_useful_without_raw_code(name, args, preview, expected):
    from backend.tool_presentation import tool_summary
    assert tool_summary(name, args, preview=preview) == expected


def test_summary_is_bounded_and_malformed_input_falls_back():
    from backend.tool_presentation import tool_summary
    assert len(tool_summary('web_search', {'query': 'weather ' * 100})) <= 360
    assert tool_summary('read_file', '{bad') == 'read_file'
    assert tool_summary('bad name\nSECRET', {}) == 'tool'

from backend.native_catalog import NativeCatalog


@pytest.mark.asyncio
async def test_live_events_only_journal_safe_preview_and_lifecycle_metadata(tmp_path):
    from test_orchestration import runtime_at, USER, BODY, until
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        await g.queue.put({'event': 'tool.started', 'tool': 'read_file', 'preview': 'docs/guide.md', 'tool_call_id': 'c', 'run_id': 'upstream', 'args': {'secret': 'PRIVATE'}, 'result': 'PRIVATE'})
        await g.queue.put({'event': 'tool.completed', 'tool': 'read_file', 'tool_call_id': 'c', 'status': 'failed', 'is_error': True, 'duration': 1, 'result': 'PRIVATE'})
        await g.queue.put({'event': 'run.completed', 'run_id': 'upstream', 'output': 'done'})
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        events = [e['data'] for e in j.events('owner', run['id']) if e['name'] == 'tool']
        assert events[0].get('summary') == 'read_file: docs/guide.md'
        assert 'PRIVATE' not in str(events)
        assert 'preview' not in events[0]
        assert events[1]['event'] == 'tool.completed'
        assert events[1]['tool_call_id'] == 'c'
        assert events[1]['is_error'] is True
        assert events[1]['status'] == 'failed'
        assert 'summary' not in events[1]  # UI retains the started summary.
    finally:
        await o.close()


def test_live_previews_are_not_trusted_as_arbitrary_text():
    from backend import tool_presentation
    normalizer = getattr(tool_presentation, 'normalize_tool_event', None)
    assert normalizer is not None
    for name, preview in [('terminal', 'echo PRIVATE > key'), ('read_file', '{"password":"PRIVATE"}'), ('web_search', 'password PRIVATE'), ('unknown', 'PRIVATE')]:
        data = normalizer({'event': 'tool.started', 'tool': name, 'preview': preview})
        assert data['summary'] == name



def test_saved_summaries_match_call_ids_across_pages_without_writing(tmp_path):
    db = tmp_path / 'state.db'
    calls = [{'id': 'a', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'docs/guide.md'})}},
             {'id': 'b', 'function': {'name': 'web_search', 'arguments': '{"query":"weather Toronto"}'}}]
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE sessions(id TEXT); CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, tool_call_id TEXT, reasoning_content TEXT, api_content TEXT);')
        c.execute("INSERT INTO sessions VALUES ('s')")
        c.execute('INSERT INTO messages VALUES (1,?,?,?,?,?,?,?,?)', ('s', 'assistant', 'Checking sources', json.dumps(calls), 1, None, 'PRIVATE', 'PRIVATE'))
        c.execute('INSERT INTO messages VALUES (2,?,?,?,?,?,?,?,?)', ('s', 'tool', 'raw result', None, 2, 'b', None, None))
        c.execute('INSERT INTO messages VALUES (3,?,?,?,?,?,?,?,?)', ('s', 'tool', 'raw result', None, 3, 'a', None, None))
    before = db.read_bytes()
    catalog = NativeCatalog({'default': tmp_path})
    items = catalog.messages('default', 's')['items']
    assert items[0]['tool_calls'][0].get('summary') == 'read_file: docs/guide.md'
    assert items[1]['summary'] == 'web_search: weather Toronto'
    assert catalog.messages('default', 's', limit=1, latest=True)['items'][0]['summary'] == 'read_file: docs/guide.md'
    assert 'PRIVATE' not in str(items)
    assert items[2]['content'] == 'raw result'
    assert db.read_bytes() == before
