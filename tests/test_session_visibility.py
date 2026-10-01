"""Metadata-only list filtering. Fixtures never touch native/live state."""
import importlib.util
import sqlite3

import pytest


@pytest.mark.parametrize('kind,expected', [
    ('chats', ['chat', 'compaction', 'invalid-json']),
    ('all', ['chat', 'compaction', 'cron', 'invalid-json', 'marked', 'test']),
    ('cron', ['cron']),
    ('tests', ['marked', 'test']),
])
def test_modes_honor_explicit_metadata_and_always_hide_delegates(kind, expected):
    with database([
        {'id': 'chat', 'source': 'api_server'},
        {'id': 'compaction', 'source': 'cli', 'parent_session_id': 'parent'},
        {'id': 'cron', 'source': 'cron', 'model_config': '{"_hermes_mobile": {"purpose": "agent_test"}}'},
        {'id': 'test', 'source': 'agent_test'},
        {'id': 'marked', 'source': 'api_server', 'model_config': '{"_hermes_mobile": {"purpose": "agent_test"}}'},
        {'id': 'invalid-json', 'source': 'cli', 'model_config': '{broken'},
        {'id': 'delegate', 'source': 'cli', 'model_config': '{"_delegate_from": "parent"}'},
        {'id': 'child', 'source': 'subagent'},
        {'id': 'cron-child', 'source': 'cron', 'model_config': '{"_delegate_from": "parent"}'},
        {'id': 'test-child', 'source': 'agent_test', 'model_config': '{"_delegate_from": "parent"}'},
    ]) as connection:
        assert select(connection, kind=kind) == (len(expected), expected)


def test_verified_historical_probe_ids_not_title_substrings():
    known = [
        'api_1790570594_9efc871d', 'api_1790571743_1d1c5208',
        'api_1790575010_8e1e6d88', 'api_1790572001_2d1ae99c',
        'api_1790572140_79f45e95',
    ]
    rows = [{'id': key, 'source': 'api_server', 'title': 'Renamed known probe'} for key in known]
    rows += [
        {'id': 'user-title', 'source': 'api_server', 'title': 'Hermes Mobile deployment verification aeba22fd'},
        {'id': 'user-marker', 'source': 'cli', 'title': 'Verify deployment with PUBLIC_HERMES_APP_OK'},
        {'id': 'test-probe-user', 'source': 'whatsapp', 'title': 'test my probe'},
        {'id': 'health', 'source': 'cli', 'title': 'Run migration health check'},
    ]
    with database(rows) as connection:
        assert select(connection, kind='tests') == (5, sorted(known))
        assert select(connection) == (4, ['health', 'test-probe-user', 'user-marker', 'user-title'])


@pytest.mark.parametrize('kind,expected', [
    ('chats', ['a', 'b']), ('all', ['a', 'b', 'c']), ('cron', ['c']), ('tests', []),
])
def test_search_literal_wildcards_counts_and_pagination_use_one_predicate(kind, expected):
    with database([
        {'id': 'a', 'source': 'cli', 'title': '100%_\\\\ ready'},
        {'id': 'b', 'source': 'whatsapp', 'title': '100%_\\\\ ready too'},
        {'id': 'c', 'source': 'cron', 'title': '100%_\\\\ ready cron'},
        {'id': 'd', 'source': 'cli', 'title': '100xx ready'},
        {'id': 'hidden', 'source': 'subagent', 'title': '100%_\\\\ ready'},
    ]) as connection:
        for offset in range(len(expected) + 1):
            assert select(connection, kind=kind, q='100%_\\\\', limit=1, offset=offset) == (
                len(expected), expected[offset:offset + 1])
        assert select(connection, kind=kind, q="' OR 1=1 --") == (0, [])
        assert select(connection, kind=kind, q='unmatched') == (0, [])


@pytest.mark.parametrize('kind', ['', 'hidden', 'TESTS', "all' OR 1=1 --", None])
def test_invalid_kind_is_rejected(kind):
    with database([]) as connection:
        with pytest.raises(ValueError, match='kind'):
            select(connection, kind=kind)


def test_legacy_schema_searches_ids_and_null_titles():
    with database([
        {'id': 'cli-match', 'source': None, 'title': None},
        {'id': 'cron-match', 'source': 'cron', 'title': None},
        {'id': 'child-match', 'source': 'delegate'},
        {'id': 'test-match', 'source': 'agent_test'},
        {'id': 'other', 'source': 'cli', 'title': 'ordinary'},
    ], model_config=False) as connection:
        assert select(connection, q='match') == (1, ['cli-match'])
        assert select(connection, kind='all', q='match') == (3, ['cli-match', 'cron-match', 'test-match'])


def test_exact_healthcheck_ids_require_cli_source_not_similar_titles():
    known = [
        '20260905_153550_864153', '20260905_153626_d6a777',
        '20260905_153637_05468c', '20260905_153713_191770', '20260905_153759_639642',
    ]
    rows = [{'id': key, 'source': 'cli', 'title': 'Renamed'} for key in known]
    rows.append({'id': 'human', 'source': 'cli', 'title': 'This is a migration health check. Reply with…'})
    with database(rows) as connection:
        assert select(connection, kind='tests') == (5, sorted(known))
        assert select(connection) == (1, ['human'])
    with database([{'id': key, 'source': 'whatsapp'} for key in known]) as connection:
        assert select(connection, kind='tests') == (0, [])
        assert select(connection)[0] == 5


def visibility_module():
    assert importlib.util.find_spec('backend.session_visibility'), 'Session visibility helper is missing'
    from backend import session_visibility
    return session_visibility


def database(rows, *, model_config=True):
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, '
                       'parent_session_id TEXT' + (', model_config TEXT' if model_config else '') + ')')
    for row in rows:
        fields = list(row)
        connection.execute('INSERT INTO sessions (' + ','.join(fields) + ') VALUES ('
                           + ','.join('?' for _ in fields) + ')', tuple(row.values()))
    return connection


def select(connection, *, kind='chats', q='', limit=100, offset=0):
    module = visibility_module()
    columns = {row[1] for row in connection.execute('PRAGMA table_info(sessions)')}
    where, params = module.session_filter(columns, kind=kind, q=q)
    total = connection.execute('SELECT count(*) FROM sessions ' + where, params).fetchone()[0]
    rows = connection.execute('SELECT id FROM sessions ' + where + ' ORDER BY id LIMIT ? OFFSET ?',
                              (*params, limit, offset)).fetchall()
    return total, [row[0] for row in rows]


def test_live_probe_requires_explicit_testing_session_before_any_request(monkeypatch):
    import asyncio
    import httpx
    from scripts import live_probe

    monkeypatch.setattr(live_probe.Path, 'read_text', lambda *_: (
        '{"upstream_url":"http://offline.test","upstream_token":"fixture"}'))

    def handler(request):
        pytest.fail('An unclassified probe must stop before any native request')

    client = httpx.AsyncClient(base_url='http://offline.test', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(live_probe.httpx, 'AsyncClient', lambda **_: client)
    with pytest.raises(RuntimeError, match='--test-session-id'):
        asyncio.run(live_probe.main())


@pytest.mark.parametrize('session_id,returned_id,source,allowed', [
    ('api_1790638979_bfc85b3e', 'api_1790638979_bfc85b3e', 'api_server', True),
    ('marked-fixture', 'marked-fixture', 'agent_test', True),
    ('human-fixture', 'human-fixture', 'api_server', False),
    ('api_1790638979_bfc85b3e', 'api_1790638979_bfc85b3e', 'whatsapp', False),
    ('requested-fixture', 'different-fixture', 'agent_test', False),
])
def test_live_probe_only_runs_in_explicit_verified_session(
        monkeypatch, session_id, returned_id, source, allowed):
    import asyncio
    import json
    import httpx
    from scripts import live_probe

    requests = []
    marker = None
    monkeypatch.setattr(live_probe.Path, 'read_text', lambda *_: (
        '{"upstream_url":"http://offline.test","upstream_token":"fixture"}'))

    def handler(request):
        nonlocal marker
        requests.append((request.method, request.url.path))
        if request.method == 'POST':
            assert request.url.path == '/v1/runs', 'Probe must not create unmarked sessions'
            assert json.loads(request.content)['session_id'] == session_id
            prompt = json.loads(request.content)['input']
            if 'printf ' in prompt:
                marker = prompt.split('printf ', 1)[1].split()[0]
            return httpx.Response(200, json={'run_id': 'offline-run'})
        if request.url.path == '/api/sessions/' + session_id:
            return httpx.Response(200, json={'session': {
                'id': returned_id, 'source': source, 'title': 'Native repair verification'}})
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'features': {'run_approval_request_id': True}})
        if request.url.path == '/v1/runs/offline-run':
            return httpx.Response(200, json={'status': 'completed', 'output': marker})
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'session_id': session_id, 'data': [
                {'id': 1, 'role': 'tool', 'content': marker}] if marker else []})
        pytest.fail('Unexpected offline request: ' + request.url.path)

    client = httpx.AsyncClient(base_url='http://offline.test', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(live_probe.httpx, 'AsyncClient', lambda **_: client)
    if allowed:
        asyncio.run(live_probe.main(session_id))
        assert requests.count(('POST', '/v1/runs')) == 2
    else:
        with pytest.raises(RuntimeError, match='verified testing session'):
            asyncio.run(live_probe.main(session_id))
        assert not any(method == 'POST' for method, _ in requests)


def test_live_probe_cli_forwards_explicit_testing_session(monkeypatch):
    import asyncio
    import runpy
    import sys
    from pathlib import Path

    received = []

    def run(coroutine):
        received.append(coroutine.cr_frame.f_locals.get('test_session_id'))
        coroutine.close()

    monkeypatch.setattr(asyncio, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['live_probe.py', '--test-session-id', 'selected-test'])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/live_probe.py'), run_name='__main__')
    assert received == ['selected-test']


def test_auth_probe_import_and_missing_id_do_not_touch_state(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Auth probe must require a selected test session before touching live state')

    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    from scripts import live_auth_probe
    with pytest.raises(RuntimeError, match='--test-session-id'):
        live_auth_probe.main()


@pytest.mark.parametrize('listed_id,allowed', [('selected-test', True), ('other-test', False), (None, False)])
def test_auth_probe_uses_exact_testing_filter_match_before_model_run(monkeypatch, listed_id, allowed):
    import json
    from types import SimpleNamespace
    import httpx
    from scripts import live_auth_probe

    class VerifiedRunReached(Exception):
        pass

    # The authentication lifecycle is external to this regression; all state is
    # in-memory, all HTTP is MockTransport, and no model run can be submitted.
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE users (id TEXT, role TEXT, display_name TEXT)')
    monkeypatch.setattr(live_auth_probe.sqlite3, 'connect', lambda *_: db)
    monkeypatch.setattr(live_auth_probe, 'issue_owner_code', lambda *_: 'offline-code')
    monkeypatch.setattr(live_auth_probe, 'VirtualAuthenticator', lambda: SimpleNamespace(register=lambda _: {}))
    seen_testing = []
    mutations = []

    def handler(request):
        path = request.url.path.rsplit('/app-api', 1)[-1]
        if request.method == 'POST':
            mutations.append(path)
            assert path != '/sessions', 'Public probe must not create an unmarked session'
        if path == '/auth/register/options':
            return httpx.Response(200, json={'enrollment_id': 'offline', 'options': {}})
        if path == '/auth/register/verify':
            return httpx.Response(200, json={'user': {'profile': 'default', 'role': 'owner'}, 'csrf_token': 'offline'})
        if path == '/auth/me':
            return httpx.Response(200)
        if path == '/sessions':
            if request.url.params.get('kind') == 'tests':
                assert request.url.params['q'] == 'selected-test'
                seen_testing.append(True)
                return httpx.Response(200, json={'total': int(listed_id is not None),
                    'items': [{'id': listed_id}] if listed_id else []})
            return httpx.Response(200, json={'total': 1, 'items': []})
        if path == '/jobs':
            return httpx.Response(200, json={'items': []})
        if path == '/runs':
            assert seen_testing, 'Must verify Testing classification before submitting'
            assert json.loads(request.content)['session_id'] == 'selected-test'
            raise VerifiedRunReached
        pytest.fail('Unexpected offline request: ' + path)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(live_auth_probe.httpx, 'Client', lambda **_: client)
    if allowed:
        with pytest.raises(VerifiedRunReached):
            live_auth_probe.main('selected-test')
    else:
        with pytest.raises(RuntimeError, match='verified testing session'):
            live_auth_probe.main('selected-test')
        assert '/runs' not in mutations
    assert seen_testing == [True]


@pytest.mark.parametrize('arguments', [[], ['--test-session-id', 'selected-test'],
                                        ['--test-session-id', 'selected-test', '--cron']])
def test_auth_probe_cli_requires_and_forwards_testing_session(monkeypatch, arguments):
    import runpy
    import sys
    from pathlib import Path

    def stop_before_state(*args, **kwargs):
        raise RuntimeError('Selected-session preflight reached; offline stop')

    monkeypatch.setattr(sqlite3, 'connect', stop_before_state)
    monkeypatch.setattr(sys, 'argv', ['live_auth_probe.py', *arguments])
    expected = RuntimeError if arguments else SystemExit
    with pytest.raises(expected) as caught:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/live_auth_probe.py'), run_name='__main__')
    if arguments:
        assert 'Selected-session preflight reached' in str(caught.value)
    else:
        assert caught.value.code == 2


def test_verified_native_repair_probe_is_testing_without_hiding_human_lookalikes():
    probe_id = 'api_1790638979_bfc85b3e'
    rows = [
        {'id': probe_id, 'source': 'api_server', 'title': 'Renamed probe'},
        {'id': 'human-title', 'source': 'api_server',
         'title': 'Native repair verification bd837190d6e3'},
        {'id': 'human-testing', 'source': 'cli', 'title': 'Testing deployment repair'},
        {'id': 'api_example_human_session', 'source': 'api_server', 'title': 'Human-authored example'},
    ]
    with database(rows) as connection:
        assert select(connection, kind='tests') == (1, [probe_id])
        assert select(connection) == (3, ['api_example_human_session', 'human-testing', 'human-title'])
        assert select(connection, kind='all')[0] == 4
        assert select(connection, kind='tests', q='Renamed', limit=1, offset=1) == (1, [])
    with database([{'id': probe_id, 'source': 'whatsapp'}]) as connection:
        assert select(connection, kind='tests') == (0, [])
        assert select(connection) == (1, [probe_id])


def test_default_chats_excludes_explicit_cron_tests_and_subagents():
    with database([
        {'id': 'chat', 'source': 'cli', 'title': 'Testing my probe software'},
        {'id': 'whatsapp', 'source': 'whatsapp', 'parent_session_id': 'older-chat'},
        {'id': 'cron', 'source': 'cron'},
        {'id': 'test', 'source': 'agent_test'},
        {'id': 'child', 'source': 'subagent'},
        {'id': 'delegate', 'source': 'delegate'},
    ]) as connection:
        assert select(connection) == (2, ['chat', 'whatsapp'])
