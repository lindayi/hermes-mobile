"""Native regression tests. Run with native venv + temporary HERMES_HOME.
NATIVE_PATCH_ROOT optionally points to isolated staged copies (never live writes).
Only compiled methods are swapped; model execution is replaced by a capture seam.
"""
import ast
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest

NATIVE = Path('/usr/local/lib/hermes-agent')
sys.path.insert(0, str(NATIVE))
from gateway.platforms.api_server import APIServerAdapter
from run_agent import AIAgent


def load_method(cls, filename, name):
    source = Path(os.environ.get('NATIVE_PATCH_ROOT', str(NATIVE)), filename).read_text()
    tree = ast.parse(source)
    node = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == cls.__name__
                for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    namespace = dict(getattr(cls, name).__globals__)
    exec(compile('from __future__ import annotations\n' + textwrap.dedent(ast.get_source_segment(source, node)), filename, 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('exists,wait', [(True, False), (True, True), (False, False)])
def test_native_history_reloaded_after_lease_except_fresh_seed(exists, wait):
    fixture = runpy.run_path(str(NATIVE / 'tests/run_agent/test_cross_process_turn_lease.py'))
    db = fixture['_DB'](session_exists=exists)
    agent = fixture['_agent_with_db'](db)
    if wait:
        original = db.acquire_session_turn_lease
        def acquire(*args, **kwargs):
            kwargs['on_wait'](0)
            return original(*args, **kwargs)
        db.acquire_session_turn_lease = acquire
    observed = {}
    def loop(agent, message, system, history, *args, **kwargs):
        observed.update(history=history, session_id=agent.session_id)
        return {'final_response': 'fixture', 'messages': history, 'failed': False}
    seed = [{'role': 'user', 'content': 'caller seed'}]
    method = load_method(AIAgent, 'run_agent.py', 'run_conversation')
    with patch('agent.conversation_loop.run_conversation', loop):
        method(agent, 'next', conversation_history=seed)
    if exists:
        assert observed == {'history': [{'role': 'user', 'content': 'durable latest'}], 'session_id': 'compressed-tip'}
        assert [e[0] for e in db.events] == ['acquire', 'resolve', 'reload', 'release']
    else:
        assert observed['history'] is seed
        assert db.events == []


class Request:
    match_info = {'run_id': 'fixture-run'}
    def __init__(self, body):
        self.body = body
    async def json(self):
        return self.body


@pytest.mark.parametrize('explicit', [True, False])
def test_runs_preserves_tool_call_and_multimodal_history(explicit):
    history = [
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
        {'role': 'tool', 'content': 'result', 'tool_call_id': 'call-1'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]},
    ]
    class Captured(Exception):
        pass
    def route(*args):
        raise Captured()
    adapter = SimpleNamespace(_parse_session_key_header=lambda r: (None, None),
        _concurrency_limited_response=lambda: None, _resolve_route=route)
    method = load_method(APIServerAdapter, 'gateway/platforms/api_server.py', '_handle_runs')
    try:
        supplied = [dict(msg, _session_db_row_id=999) for msg in history]
        body = {'input': 'next', 'conversation_history': supplied} if explicit else {'input': supplied + [{'role': 'user', 'content': 'next'}]}
        asyncio.run(method(adapter, Request(body)))
    except Captured as exc:
        tb = exc.__traceback__
        while tb.tb_frame.f_code.co_name != '_handle_runs':
            tb = tb.tb_next
        assert tb.tb_frame.f_locals['conversation_history'] == history
    else:
        pytest.fail('run did not reach route seam')


@pytest.mark.parametrize('choice', ['once', 'deny'])
def test_approval_resolves_exact_identity_once_and_echoes_it(choice):
    import tools.approval as approvals
    older = approvals._ApprovalEntry({'request_id': 'older'})
    target = approvals._ApprovalEntry({'request_id': 'reviewed'})
    q = asyncio.Queue()
    adapter = SimpleNamespace(_check_auth=lambda r: None,
        _run_statuses={'fixture-run': {'status': 'waiting_for_approval'}},
        _run_approval_sessions={'fixture-run': 'isolated-queue'},
        _run_streams={'fixture-run': q}, _set_run_status=lambda *a, **kw: None)
    method = load_method(APIServerAdapter, 'gateway/platforms/api_server.py', '_handle_run_approval')
    with patch.object(approvals, '_gateway_queues', {'isolated-queue': [older, target]}):
        response = asyncio.run(method(adapter, Request({'choice': choice, 'request_id': 'reviewed'})))
        assert target.result == choice and older.result is None
        assert response.status == 200
        assert json.loads(response.text)['request_id'] == 'reviewed'
        assert q.get_nowait()['request_id'] == 'reviewed'
        response = asyncio.run(method(adapter, Request({'choice': choice, 'request_id': 'reviewed'})))
        assert response.status == 409
        assert older.result is None


@pytest.mark.parametrize('body', [[], {}, {'choice': 'once'},
    *[{'choice': 'once', 'request_id': rid} for rid in (None, '', ' ', 123, {}, 'x' * 201)],
    {'choice': 'once', 'request_id': 'target', 'all': True},
    {'choice': 'once', 'request_id': 'target', 'resolve_all': True}])
def test_approval_invalid_identity_never_calls_resolver(body):
    adapter = SimpleNamespace(_check_auth=lambda r: None,
        _run_statuses={'fixture-run': {'status': 'waiting_for_approval'}},
        _run_approval_sessions={'fixture-run': 'isolated-queue'}, _run_streams={},
        _set_run_status=lambda *a, **kw: None)
    method = load_method(APIServerAdapter, 'gateway/platforms/api_server.py', '_handle_run_approval')
    with patch('tools.approval.resolve_gateway_approval', return_value=1) as resolve:
        response = asyncio.run(method(adapter, Request(body)))
        assert response.status == 400
        resolve.assert_not_called()


def test_action_specific_approval_capability_is_explicit():
    adapter = SimpleNamespace(_check_auth=lambda r: None, _model_name='fixture', _api_key='fixture', _cors_origins=[])
    method = load_method(APIServerAdapter, 'gateway/platforms/api_server.py', '_handle_capabilities')
    response = asyncio.run(method(adapter, Request({})))
    assert json.loads(response.text)['features'].get('run_approval_request_id') is True


def test_session_messages_binds_resolved_tip_to_requested_source():
    async def existing(sid):
        assert sid == 'old'
        return {'id': sid}, None
    db = SimpleNamespace(resolve_resume_session_id=lambda sid: 'tip', get_messages=lambda *a, **kw: [])
    async def ensure():
        return db
    adapter = SimpleNamespace(_check_auth=lambda r: None, _get_existing_session_or_404=existing,
        _ensure_session_db_async=ensure, _message_response=lambda row: row)
    request = Request({})
    request.match_info = {'session_id': 'old'}
    request.query = {'limit': '500', 'offset': '0', 'order': 'oldest'}
    method = load_method(APIServerAdapter, 'gateway/platforms/api_server.py', '_handle_session_messages')
    result = json.loads(asyncio.run(method(adapter, request)).text)
    assert result['session_id'] == 'tip'
    assert result.get('requested_session_id') == 'old'


@pytest.mark.parametrize('marker', ['_reset_from', '_branched_from', '_delegate_from'])
def test_native_resolution_does_not_redirect_into_new_or_branch(tmp_path, marker):
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / 'isolated.db')
    try:
        db.create_session('old', source='cli')
        db.append_message('old', role='user', content='original')
        db.end_session('old', 'reset')
        db.create_session('new', source='cli', parent_session_id='old')
        db._conn.execute('UPDATE sessions SET model_config=? WHERE id=?', (json.dumps({marker: 'old'}), 'new'))
        db._conn.commit()
        db.append_message('new', role='user', content='separate conversation')
        assert db.resolve_resume_session_id('old') == 'old'
    finally:
        db.close()


