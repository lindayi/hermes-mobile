import asyncio
import importlib.util
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest
from aiohttp import web


class Base:
    def __init__(self):
        self._run_statuses = {}
        self._active_run_agents = {}
        self._run_streams = {}
        self._run_approval_sessions = {}
        self._stopping_run_ids = set()
    async def _handle_capabilities(self, request):
        return web.json_response({'features': {'runs': True}})
    def _http_route_table(self):
        return []
    async def _handle_get_run(self, request):
        run_id = request.match_info['run_id']
        return web.json_response(self._run_statuses.get(run_id, {}))
    def _check_auth(self, request):
        return None
    async def _read_json_body(self, request):
        if getattr(request, 'during_body', None):
            request.during_body()
        return request.body, None
    def _set_run_status(self, run_id, status, **fields):
        current = self._run_statuses.setdefault(run_id, {})
        current.update(status=status, **fields)
        return current
    def _make_run_event_callback(self, run_id, loop):
        return lambda *args, **kwargs: None
    def _sweep_orphaned_runs_once(self, now=None):
        pass
    def _create_agent(self, **kwargs):
        return self.agent_factory(**kwargs)


@pytest.fixture
def registry(monkeypatch):
    module = ModuleType('tools.approval')
    module._lock = threading.Lock()  # Native is NOT reentrant.
    module._gateway_queues = {}
    def listing(key):
        with module._lock:
            return [dict(entry.data) for entry in module._gateway_queues.get(key, [])]
    module.list_gateway_approvals = listing
    package = ModuleType('tools')
    package.approval = module
    monkeypatch.setitem(sys.modules, 'tools', package)
    monkeypatch.setitem(sys.modules, 'tools.approval', module)
    return module


def bind_run(a):
    # Native binds the run-specific approval identity before creating callbacks.
    a._run_streams['r'] = asyncio.Queue()
    a._run_approval_sessions['r'] = 'r'
    return a._make_run_event_callback('r', asyncio.get_running_loop())


def adapter():
    assert importlib.util.find_spec('backend.native_run_controls'), 'Dedicated adapter missing'
    from backend.native_run_controls import run_controls_adapter
    return run_controls_adapter(Base)()


def test_capabilities_are_explicit_and_base_unchanged():
    async def check():
        a = adapter()
        caps = json.loads((await a._handle_capabilities(None)).text)
        assert caps['mobile_run_controls'] == {
            'version': 1, 'steering': True, 'live_commentary': True, 'clarifications': True}
        assert 'mobile_run_controls' not in json.loads((await Base()._handle_capabilities(None)).text)
    asyncio.run(check())


def request(text='note', key='k', during_body=None):
    return SimpleNamespace(match_info={'run_id': 'r'}, body={'input': text, 'idempotency_key': key}, during_body=during_body)


def test_steer_dedup_exact_input_and_post_body_status_gate(registry):
    async def check():
        a = adapter()
        calls = []
        bind_run(a)
        a._active_run_agents['r'] = SimpleNamespace(steer=lambda text: calls.append(text) or True)
        a._set_run_status('r', 'running')
        first = await a._handle_steer_run(request())
        assert first.status == 200
        receipt = json.loads(first.text)
        assert receipt == {'object':'hermes.run.steer','run_id':'r','steer_id':'k','status':'accepted_unconfirmed','accepted':True}
        assert json.loads((await a._handle_steer_run(request())).text) == receipt
        assert (await a._handle_steer_run(request('different'))).status == 409
        assert calls == ['note']
        for status in ('waiting_for_approval', 'stopping', 'completed', 'failed', 'cancelled'):
            # Each case is a fresh run, not an illegal terminal-to-running reset.
            a._run_statuses.pop('r', None)
            a._controls.pop('r', None)
            bind_run(a)
            a._set_run_status('r', 'running')
            result = await a._handle_steer_run(request(key=status, during_body=lambda: a._set_run_status('r', status)))
            assert result.status == 409
            assert json.loads(result.text)['status'] == 'not_delivered'
            assert a._run_statuses['r']['status'] == status
        assert calls == ['note']
        for text, key in [('x'*32769,'long'), ('x','x'*129), ('  ','blank')]:
            assert (await a._handle_steer_run(request(text,key))).status == 400
    asyncio.run(check())



def native_agent():
    sys.path.insert(0, '/usr/local/lib/hermes-agent')
    # Compile exact installed methods without importing the CLI's dotenv/plugin startup.
    import ast
    import re
    from pathlib import Path
    tree = ast.parse(Path('/usr/local/lib/hermes-agent/run_agent.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AIAgent')
    names = {'steer', '_drain_pending_steer', 'clear_interrupt', '_strip_think_blocks',
             '_fire_streamed_codex_commentary', '_interim_text_was_delivered',
             '_record_delivered_interim_text', '_normalize_interim_visible_text'}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls.bases = []
    ns = {'threading': threading, 're': re, 'redact_sensitive_text': lambda text: text,
          'logger': SimpleNamespace(debug=lambda *a, **k: None)}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<installed AIAgent methods>', 'exec'), ns)
    agent = object.__new__(ns['AIAgent'])
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    agent._execution_thread_id = None
    agent.show_commentary = True
    return agent


def attach(a, agent):
    callback = bind_run(a)
    a._set_run_status('r', 'running')
    a.agent_factory = lambda **kwargs: agent
    result = a._create_agent(tool_progress_callback=callback, stream_delta_callback=lambda text: None)
    a._active_run_agents['r'] = result
    return result


def test_clarification_callback_waits_for_answer_and_returns_tool_result(registry):
    async def check():
        a = adapter()
        agent = SimpleNamespace(steer=lambda text: True, clear_interrupt=lambda: True,
                                run_conversation=lambda: {})
        attach(a, agent)
        assert callable(agent.clarify_callback)
        result = {}
        started = threading.Event()

        def tool_call():
            started.set()
            answer = agent.clarify_callback(
                'Which option?', ['Keep current', 'Change it'], multi_select=False)
            result['value'] = json.dumps({
                'question': 'Which option?', 'choices_offered': ['Keep current', 'Change it'],
                'user_response': answer})

        worker = threading.Thread(target=tool_call)
        worker.start()
        assert started.wait(1)
        q = a._run_streams['r']
        event = await asyncio.wait_for(q.get(), timeout=1)
        assert event['event'] == 'run.clarification'
        assert event['status'] == 'pending'
        assert event['question'] == 'Which option?'
        assert event['choices'] == ['Keep current', 'Change it']
        assert not result
        response = await a._handle_clarification_answer(SimpleNamespace(
            match_info={'run_id': 'r', 'question_id': event['question_id']},
            body={'answer': 'Change it', 'other': False}))
        assert json.loads(response.text)['status'] == 'answered'
        await asyncio.to_thread(worker.join, 1)
        assert not worker.is_alive()
        assert json.loads(result['value'])['user_response'] == 'Change it'
        assert a._run_statuses['r']['status'] == 'running'
        duplicate = await a._handle_clarification_answer(SimpleNamespace(
            match_info={'run_id': 'r', 'question_id': event['question_id']},
            body={'answer': 'Change it', 'other': False}))
        assert duplicate.status == 200
        assert json.loads(duplicate.text)['answer'] == 'Change it'
        conflict = await a._handle_clarification_answer(SimpleNamespace(
            match_info={'run_id': 'r', 'question_id': event['question_id']},
            body={'answer': 'Keep current', 'other': False}))
        assert conflict.status == 409
        assert len(a._run_streams) == 1
    asyncio.run(check())


def test_pinned_clarify_tool_dispatches_to_the_run_bound_waiter(registry):
    async def check():
        import hashlib
        from pathlib import Path
        from deploy.native_controls_release import NATIVE_DEPENDENCIES
        tool_path=Path('/usr/local/lib/hermes-agent/tools/clarify_tool.py')
        if not tool_path.is_file():
            pytest.skip('Pinned native runtime is provisioned only in hosted native tests')
        assert hashlib.sha256(tool_path.read_bytes()).hexdigest()==NATIVE_DEPENDENCIES[tool_path]
        spec=importlib.util.spec_from_file_location('pinned_clarify_tool',tool_path)
        tool=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        a=adapter()
        agent=SimpleNamespace(steer=lambda text: True,clear_interrupt=lambda: True,
                              run_conversation=lambda: {})
        attach(a,agent)
        result={}
        worker=threading.Thread(target=lambda: result.update(value=tool.clarify_tool(
            'Which plan?', ['Keep current','Change it'], multi_select=True,
            callback=agent.clarify_callback)))
        worker.start()
        event=await asyncio.wait_for(a._run_streams['r'].get(),timeout=1)
        assert event['choices']==['Keep current (Recommended)','Change it']
        response=await a._handle_clarification_answer(SimpleNamespace(
            match_info={'run_id':'r','question_id':event['question_id']},
            body={'answer':['Keep current (Recommended)','A new plan'],'other':True}))
        assert response.status==200
        await asyncio.to_thread(worker.join,1)
        assert not worker.is_alive()
        assert json.loads(result['value'])=={
            'question':'Which plan?','choices_offered':['Keep current','Change it'],
            'user_response':['Keep current','A new plan']}
    asyncio.run(check())


@pytest.mark.parametrize(('ending', 'expected'), [('timeout', 'expired'), ('stop', 'cancelled')])
def test_clarification_timeout_and_stop_release_waiter_truthfully(registry, ending, expected):
    async def check():
        a = adapter()
        agent = SimpleNamespace(steer=lambda text: True, clear_interrupt=lambda: True,
                                run_conversation=lambda: {}, clarify_timeout=0.02)
        attach(a, agent)
        failure = []

        def ask():
            try:
                agent.clarify_callback('Question?', ['A', 'B'])
            except RuntimeError as exc:
                failure.append(str(exc))

        worker = threading.Thread(target=ask)
        worker.start()
        event = await asyncio.wait_for(a._run_streams['r'].get(), timeout=1)
        if ending == 'stop':
            a._set_run_status('r', 'stopping')
        await asyncio.to_thread(worker.join, 1)
        assert not worker.is_alive()
        item = a._controls['r']['clarifications'][event['question_id']]
        assert item['status'] == expected
        assert failure == [f'Clarification {expected}.']
        assert a._run_statuses['r']['status'] == ('running' if ending == 'timeout' else 'stopping')
    asyncio.run(check())


@pytest.mark.parametrize(('choices', 'multi_select', 'answer', 'other'), [
    (['A', 'B'], True, ['A', 'B'], False),
    (['A', 'B'], True, ['A', 'typed answer'], True),
    (None, False, 'typed answer', False),
])
def test_clarification_answer_validates_single_multi_other_and_open_ended(
        registry, choices, multi_select, answer, other):
    async def check():
        a = adapter()
        agent = SimpleNamespace(steer=lambda text: True, clear_interrupt=lambda: True,
                                run_conversation=lambda: {})
        attach(a, agent)
        result = {}

        def ask():
            result['answer'] = agent.clarify_callback(
                'Choose or write an answer', choices, multi_select=multi_select)

        worker = threading.Thread(target=ask)
        worker.start()
        request = await asyncio.wait_for(a._run_streams['r'].get(), timeout=1)
        body = {'answer': answer, 'other': other}
        response = await a._handle_clarification_answer(SimpleNamespace(
            match_info={'run_id': 'r', 'question_id': request['question_id']}, body=body))
        assert response.status == 200
        await asyncio.to_thread(worker.join, 1)
        assert not worker.is_alive()
        assert result['answer'] == answer
    asyncio.run(check())


@pytest.mark.parametrize('ending', ['completed', 'failed', 'cancelled', 'exception'])
def test_worker_close_retains_final_drain_gap_and_stop_pending(ending, registry):
    async def check():
        a = adapter()
        agent = native_agent()
        def run(**kwargs):
            # The native finalizer drains, then a HTTP request arrives before return.
            assert agent._drain_pending_steer() == 'first'
            assert agent.steer('late')
            if ending == 'exception':
                raise RuntimeError('fixture')
            return {'pending_steer': 'first', 'final_response': 'final'}
        agent.run_conversation = run
        attach(a, agent)
        await a._handle_steer_run(request('first'))
        try:
            result = await asyncio.to_thread(agent.run_conversation)
        except RuntimeError:
            result = None
        assert not agent.steer('too late')
        assert json.loads((await a._handle_steer_run(request('too late','late'))).text)['status'] == 'not_delivered'
        a._set_run_status('r', ending if ending != 'exception' else 'failed')
        assert 'late' in a._run_statuses['r']['pending_steer']
        assert a._run_statuses['r']['steer_receipts'][0]['status'] == 'unknown'
        if result:
            assert 'first' in result['pending_steer'] and 'late' in result['pending_steer']
        b = adapter()
        other = native_agent()
        other.run_conversation = lambda **kw: {}
        attach(b, other)
        await b._handle_steer_run(request('keep on stop'))
        other.clear_interrupt()
        b._set_run_status('r', 'cancelled')
        assert 'keep on stop' in b._run_statuses['r']['pending_steer']
    asyncio.run(check())



def test_actual_codex_consumer_to_created_agent_commentary_queue():
    async def check():
        a = adapter()
        agent = native_agent()
        agent.run_conversation = lambda **kw: {}
        attach(a, agent)
        assert callable(getattr(agent, 'interim_assistant_callback', None))
        from agent.codex_runtime import _consume_codex_event_stream
        events = []
        for phase, text in [('analysis','PRIVATE'), ('commentary','PUBLIC'), ('commentary','PUBLIC'), ('final_answer','FINAL')]:
            item = {'id':phase,'type':'message','phase':phase,'content':[{'type':'output_text','text':text}]}
            events += [{'type':'response.output_item.added','item':item},
                       {'type':'response.output_text.delta','delta':text},
                       {'type':'response.output_item.done','item':item}]
        public, private = [], []
        await asyncio.to_thread(_consume_codex_event_stream, iter(events), model='fixture',
            on_text_delta=public.append, on_reasoning_delta=private.append,
            on_commentary_message=agent._fire_streamed_codex_commentary)
        await asyncio.sleep(0)
        q = a._run_streams['r']
        result = []
        while not q.empty(): result.append(q.get_nowait())
        assert [e['text'] for e in result if e['event'] == 'message.commentary'] == ['PUBLIC']
        assert public == ['FINAL']
        assert 'PRIVATE' not in json.dumps(result)
        agent.interim_assistant_callback('already ordinary streamed', already_streamed=True)
        await asyncio.sleep(0)
        assert q.empty()
        agent.interim_assistant_callback('late queue')
        a._run_streams.pop('r')
        await asyncio.sleep(0)
        assert q.empty()
    asyncio.run(check())



def test_launcher_installs_controls_only_on_owner(monkeypatch, tmp_path):
    # The composed native launcher uses the installed dependency-free daemon pool.
    monkeypatch.syspath_prepend('/usr/local/lib/hermes-agent')
    from backend import native_controls_service as service
    assert hasattr(service, 'listener_adapter'), 'Launcher selection seam missing'
    async def check():
        owner = service.listener_adapter(Base, tmp_path/'owner-home', member=False,
                                         state_dir=tmp_path/'app-state')()
        member = service.listener_adapter(Base, service.PROFILE_ROOT/'member_fixture', member=True)()
        request = SimpleNamespace(path='/v1/capabilities', match_info={})
        owner_caps = json.loads((await owner._handle_capabilities(request)).text)
        assert owner_caps['mobile_run_controls']['steering']
        assert owner_caps['features']['mobile_session_delete_version'] == 1
        member_caps = json.loads((await member._handle_capabilities(request)).text)
        assert 'mobile_session_delete_version' not in member_caps.get('features', {})
        assert 'mobile_run_controls' not in member_caps
        assert member_caps['mobile_runtime']['home'].endswith('member_fixture')
    asyncio.run(check())



@pytest.mark.parametrize('event', ['run.completed', 'run.failed', 'run.cancelled'])
def test_terminal_transport_retains_guidance_before_status_publication(event, registry):
    async def check():
        a = adapter()
        agent = native_agent()
        agent.run_conversation = lambda **kw: {'pending_steer': agent._drain_pending_steer()}
        attach(a, agent)
        await a._handle_steer_run(request('retain'))
        await asyncio.to_thread(agent.run_conversation)
        q = a._run_streams['r']
        q.put_nowait({'event': event, 'run_id':'r'})
        terminal = q.get_nowait()
        assert terminal['pending_steer'] == 'retain'
        assert terminal['steer_receipts'][0]['status'] == 'unknown'
    asyncio.run(check())



def test_pending_merge_is_not_duplicated_on_terminal_republication():
    a = adapter()
    state = a._state('r')
    a._retain(state, 'first')
    a._retain(state, 'late')
    a._retain(state, 'first\nlate')
    assert a._snapshot('r')['pending_steer'] == 'first\nlate'


def test_receipts_expire_with_native_status_and_attempt_count_is_bounded(registry):
    async def check():
        a = adapter()
        a._set_run_status('r', 'running')
        bind_run(a)
        a._active_run_agents['r'] = SimpleNamespace(steer=lambda text: True)
        for i in range(256):
            assert (await a._handle_steer_run(request(key=str(i)))).status == 200
        assert (await a._handle_steer_run(request(key='overflow'))).status == 429
        assert (await a._handle_steer_run(request(key='0'))).status == 200
        a._run_statuses.clear()
        a._sweep_orphaned_runs_once()
        assert 'r' not in a._controls
    asyncio.run(check())



def _installed_native_probe(tmp_path, scenario):
    """Real native import in a fresh process/home; never construct an agent/server."""
    import os
    import subprocess
    from pathlib import Path
    script = r'''
import asyncio, threading, sys
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from gateway.platforms.api_server import APIServerAdapter
from backend.native_run_controls import run_controls_adapter
C = run_controls_adapter(APIServerAdapter)
a = object.__new__(C)
a._controls_lock = threading.RLock()
a._controls = {}
a._run_statuses = {}
a._run_streams = {}
a._active_run_agents = {}
a._stopping_run_ids = set()
a._run_approval_sessions = {'r': 'r'}
a._active_run_tasks = {}
loop = asyncio.new_event_loop()
q = a._run_streams['r'] = asyncio.Queue()
cb = a._make_run_event_callback('r', loop)
a._set_run_status('r', 'queued')
a._set_run_status('r', 'running')
scenario = sys.argv[1]
if scenario.startswith('approval_timeout:'):
    import json
    from types import SimpleNamespace
    from contextlib import nullcontext
    from tools import approval
    # Only the approval timer/hooks are fixtures. Execute the installed timeout,
    # registry, tool callback and native GET; never construct a model agent.
    approval._get_approval_timeout = lambda: 0
    approval.human_wait_window = lambda key: nullcontext()
    approval._fire_approval_hook = lambda *args, **kwargs: None
    approval.is_interrupted = lambda: False
    a._check_auth = lambda request: None
    async def read_body(request):
        return request.body, None
    a._read_json_body = read_body
    calls = []
    agent = SimpleNamespace(steer=lambda text: calls.append(text) or True)
    a._active_run_agents['r'] = agent
    a._controls['r']['agent'] = agent
    a._active_run_tasks['r'] = loop.create_future()
    def notify(data):
        a._set_run_status('r', 'waiting_for_approval', last_event='approval.request')
    outcome = approval._await_gateway_decision('r', notify, {'request_id': 'expired'})
    assert outcome['resolved'] is False and outcome['choice'] is None
    assert approval.list_gateway_approvals('r') == []
    cb('tool.completed', tool_name='synthetic')
    assert a._run_statuses['r']['status'] == 'waiting_for_approval'
    request = SimpleNamespace(match_info={'run_id': 'r'},
        body={'input': 'guidance', 'idempotency_key': 'synthetic-key'})
    async def check():
        if scenario.endswith(':get'):
            result = json.loads((await a._handle_get_run(request)).text)
            assert result['status'] == 'running', result
            assert result['pending_approvals'] == []
        response = await a._handle_steer_run(request)
        assert response.status == 200, response.text
        assert json.loads(response.text)['status'] == 'accepted_unconfirmed'
        await a._handle_steer_run(request)
        assert calls == ['guidance'], calls
    loop.run_until_complete(check())
elif scenario.startswith('race:'):
    ending = scenario.split(':')[1]
    attempted = threading.Event()
    lock = threading.RLock()
    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is not threading.main_thread():
                attempted.set()
            lock.acquire()
        def __exit__(self, *args):
            lock.release()
    a._controls_lock = ObservedLock()
    with a._controls_lock:
        worker = threading.Thread(target=lambda: cb('tool.completed', tool_name='late'))
        worker.start()
        assert attempted.wait(2), 'worker did not reach lock'
        a._set_run_status('r', ending)
    worker.join(2)
    assert not worker.is_alive()
    assert a._run_statuses['r']['status'] == ending, a._run_statuses
elif scenario == 'monotonic':
    a._set_run_status('r', 'waiting_for_approval')
    a._set_run_status('r', 'queued')
    a._set_run_status('r', 'running')
    assert a._run_statuses['r']['status'] == 'running'
    a._set_run_status('r', 'stopping')
    a._set_run_status('r', 'running')
    assert a._run_statuses['r']['status'] == 'stopping'
    a._set_run_status('r', 'completed')
    a._set_run_status('r', 'waiting_for_approval')
    assert a._run_statuses['r']['status'] == 'completed'
elif scenario == 'expired_callback':
    a._set_run_status('r', 'completed')
    a._run_statuses.clear()
    a._controls.clear()
    cb('tool.completed', tool_name='late')
    assert not a._run_statuses, a._run_statuses
    assert not a._controls, a._controls
elif scenario == 'detached_queue':
    a._run_statuses.clear()
    a._controls.clear()
    a._run_streams.clear()
    q.put_nowait({'event': 'run.completed', 'run_id': 'r'})
    assert not a._controls, a._controls
    assert not a._run_statuses
    assert q.empty()
elif scenario == 'generation':
    a._run_statuses.clear()
    a._controls.clear()
    replacement = a._run_streams['r'] = asyncio.Queue()
    fresh = a._make_run_event_callback('r', loop)
    a._set_run_status('r', 'queued')
    cb('tool.completed', tool_name='stale')
    assert 'last_event' not in a._run_statuses['r']
    fresh('tool.started', tool_name='fresh')
    assert a._run_statuses['r']['last_event'] == 'tool.started'
elif scenario == 'closed_worker':
    a._controls['r']['closed'] = True
    cb('tool.started', tool_name='late')
    assert 'last_event' not in a._run_statuses['r']
else:
    cb('tool.started', tool_name='ordinary')
    cb('tool.completed', tool_name='ordinary')
    loop.run_until_complete(asyncio.sleep(0))
    assert [q.get_nowait()['event'], q.get_nowait()['event']] == ['tool.started', 'tool.completed']
loop.run_until_complete(asyncio.sleep(0))
loop.close()
'''
    native_python = Path('/usr/local/lib/hermes-agent/venv/bin/python')
    result = subprocess.run([str(native_python), '-c', script, scenario],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'HERMES_HOME': str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['race:completed', 'race:stopping', 'monotonic', 'ordinary',
                                      'approval_timeout:get', 'approval_timeout:steer'])
def test_installed_native_lifecycle_is_monotonic(tmp_path, scenario):
    _installed_native_probe(tmp_path, scenario)


@pytest.mark.parametrize('scenario', ['expired_callback', 'detached_queue', 'generation', 'closed_worker'])
def test_installed_native_stale_owners_are_discarded(tmp_path, scenario):
    _installed_native_probe(tmp_path, scenario)


def test_nonclearing_interrupt_path_keeps_native_pending_slot(registry):
    async def check():
        a = adapter()
        agent = native_agent()
        agent.run_conversation = lambda **kw: {}
        attach(a, agent)
        await a._handle_steer_run(request('still pending'))
        assert agent.clear_interrupt(preserve_redirect=True) is False
        assert agent._drain_pending_steer() == 'still pending'
    asyncio.run(check())
