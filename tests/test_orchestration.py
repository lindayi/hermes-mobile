"""Real SQLite journal; only the external gateway is faked."""
import asyncio
import importlib.util
import sqlite3

import pytest

from backend.hermes_client import GatewayClient, IntegrationUnavailable
from backend.native_catalog import NativeCatalog
from backend.runs import RunJournal, RunConflict

class FakeGateway:
    execution_ready = True

    def __init__(self):
        self.starts = []
        self.queue = asyncio.Queue()
        self.started = asyncio.Event()
        self.status = {'run_id': 'upstream', 'status': 'running'}
        self.requests = []
        self.stops = []

    def require_execution(self):
        if not self.execution_ready:
            raise IntegrationUnavailable('disabled')

    async def start(self, session_id, text, history=None):
        self.starts.append((session_id, text, history))
        self.started.set()
        return {'run_id': 'upstream'}

    async def events(self, rid):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            if isinstance(event, Exception):
                raise event
            yield event

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return self.status

    async def stop(self, rid):
        self.stops.append(rid)
        return {'status': 'stopping'}


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


USER = {'id': 'owner', 'profile': 'default'}
BODY = {'session_id': 'native', 'input': 'hello', 'idempotency_key': 'key'}


def catalog_at(tmp_path):
    home = tmp_path / 'native-home'
    home.mkdir(exist_ok=True)
    with sqlite3.connect(home / 'state.db') as c:
        c.executescript('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY);'
                        'CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT,timestamp REAL);')
        c.execute('INSERT OR IGNORE INTO sessions VALUES (?)', ('native',))
    return NativeCatalog({'default': home})


@pytest.mark.asyncio
async def test_execution_gate_is_closed_before_admission(tmp_path):
    assert importlib.util.find_spec('backend.orchestration'), 'Orchestrator missing'
    from backend.orchestration import Orchestrator
    journal = RunJournal(tmp_path / 'runs.db')
    gateway = GatewayClient('http://localhost:8642', 'token')
    runtime = Orchestrator(journal, gateway, catalog_at(tmp_path))
    try:
        with pytest.raises(IntegrationUnavailable):
            await runtime.submit(USER, BODY)
        with journal.connect() as c:
            assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_background_run_preserves_complete_native_history_and_durable_events(tmp_path):
    from backend.orchestration import Orchestrator
    journal = RunJournal(tmp_path / 'runs.db')
    gateway = FakeGateway()
    history = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 't1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
               {'role': 'tool', 'tool_call_id': 't1', 'content': 'native result'}]
    async def load(profile, sid):
        assert (profile, sid) == ('default', 'native')
        return {'session_id': sid, 'profile': profile, 'history': history, 'complete': True}
    runtime = Orchestrator(journal, gateway, catalog_at(tmp_path), history_loader=load)
    run = await runtime.submit(USER, BODY)
    assert run['status'] == 'queued'
    await gateway.started.wait()
    assert gateway.starts == [('native', 'hello', history)]
    await gateway.queue.put({'event': 'message.delta', 'delta': 'answer'})
    await gateway.queue.put({'event': 'run.completed', 'run_id': 'upstream', 'output': 'answer'})
    await until(lambda: journal.get('owner', run['id'])['status'] == 'completed')
    reopened = RunJournal(journal.path)
    assert reopened.get('owner', run['id'])['output'] == 'answer'
    assert any(e['name'] == 'delta' and e['data']['text'] == 'answer' for e in reopened.events('owner', run['id']))
    assert runtime.get(USER, run['id'])['status'] == 'completed'
    await runtime.close()


@pytest.mark.asyncio
async def test_proven_local_photo_rejection_fails_run_and_releases_dispatch_slot(tmp_path):
    from backend.hermes_client import NativeRunRejected
    from backend.orchestration import Orchestrator

    class RejectingGateway(FakeGateway):
        async def start(self, session_id, text, history=None):
            raise NativeRunRejected('Synthetic request exceeded the native size limit')

    journal, gateway = RunJournal(tmp_path / 'runs.db'), RejectingGateway()
    runtime = Orchestrator(journal, gateway, catalog_at(tmp_path), history_loader=complete_context)
    try:
        run = await runtime.submit(USER, dict(BODY, idempotency_key='oversized-photo'))
        await until(lambda: journal.get('owner', run['id'])['status'] == 'failed')
        assert runtime.get(USER, run['id'])['upstream_id'] is None
        assert run['id'] not in runtime._dispatching
        assert not gateway.requests
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('context', [None, {}, {'complete': False},
    {'complete': False, 'profile': 'default', 'session_id': 'native', 'history': []},
    {'complete': True, 'profile': 'other', 'session_id': 'native', 'history': [{'role': 'user', 'content': 'hi'}]},
    {'complete': True, 'profile': 'default', 'session_id': 'native', 'history': [{'role': 'tool', 'content': 'lost ID'}]}])
async def test_incomplete_or_wrong_native_context_never_dispatches(tmp_path, context):
    from backend.orchestration import Orchestrator
    j, g = RunJournal(tmp_path / 'r.db'), FakeGateway()
    loader = None if context is None else lambda profile, sid: context
    o = Orchestrator(j, g, catalog_at(tmp_path), history_loader=loader)
    try:
        with pytest.raises(IntegrationUnavailable):
            await o.submit(USER, BODY)
        assert not g.starts
    finally:
        await o.close()


def complete_context(profile, sid):
    return {'profile': profile, 'session_id': sid, 'complete': True,
            'history': [{'role': 'user', 'content': 'earlier turn'}]}


def runtime_at(tmp_path, **kwargs):
    from backend.orchestration import Orchestrator
    j, g = RunJournal(tmp_path / 'r.db'), FakeGateway()
    return Orchestrator(j, g, catalog_at(tmp_path), history_loader=complete_context, **kwargs), j, g


@pytest.mark.asyncio
async def test_one_same_session_active_run_and_idempotent_retry(tmp_path):
    o, j, g = runtime_at(tmp_path)
    try:
        first = await o.submit(USER, BODY)
        assert (await o.submit(USER, BODY))['id'] == first['id']
        with pytest.raises(RunConflict):
            await o.submit(USER, dict(BODY, input='changed'))
        with pytest.raises(RunConflict):
            await o.submit(USER, dict(BODY, idempotency_key='second'))
        await g.started.wait()
        assert len(g.starts) == 1
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_profile_and_user_scope_never_fall_back_to_default_gateway(tmp_path):
    o, j, g = runtime_at(tmp_path)
    try:
        with pytest.raises(IntegrationUnavailable):
            await o.submit({'id': 'member', 'profile': 'member_1'}, BODY)
        run = await o.submit(USER, BODY)
        for stranger in ({'id': 'stranger', 'profile': 'default'}, {'id': 'owner', 'profile': 'member_1'}):
            with pytest.raises(KeyError):
                o.get(stranger, run['id'])
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'running'])
async def test_stream_interruption_observes_positive_native_status(tmp_path, status):
    o, j, g = runtime_at(tmp_path)
    run = await o.submit(USER, BODY)
    await g.started.wait()
    g.status = {'run_id': 'upstream', 'status': status, 'output': 'final'}
    await g.queue.put(IntegrationUnavailable('secret must not be reflected'))
    expected = status
    try:
        await until(lambda: bool(g.requests) and j.get('owner', run['id'])['status'] == expected)
        assert g.requests == [('GET', '/v1/runs/upstream', {})]
        assert 'secret' not in str(o.get(USER, run['id']))
        if expected == 'running':
            with pytest.raises(RunConflict):
                await o.submit(USER, dict(BODY, idempotency_key='again'))
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_close_marks_unresolved_run_and_rejects_new_submissions(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = await o.submit(USER, BODY)
    await g.started.wait()
    await o.close()
    assert o.get(USER, run['id'])['status'] == 'unknown'
    assert not o._tasks
    with pytest.raises(IntegrationUnavailable):
        await o.submit(USER, dict(BODY, idempotency_key='next'))


@pytest.mark.asyncio
async def test_execution_timeout_bounds_background_work(tmp_path):
    o, j, g = runtime_at(tmp_path, run_timeout=0.01)
    try:
        run = await o.submit(USER, BODY)
        await until(lambda: bool(g.requests))
        assert o.get(USER, run['id'])['status'] == 'running'
        assert len(g.starts) == 1 and not g.stops
        assert o._tasks  # Only the inactive stream ends, not observation of the job.
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_stop_is_cooperative_idempotent_and_owner_scoped(tmp_path):
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        with pytest.raises(KeyError):
            await o.stop({'id': 'foreign', 'profile': 'default'}, run['id'])
        assert (await o.stop(USER, run['id']))['status'] == 'stopping'
        assert (await o.stop(USER, run['id']))['status'] == 'stopping'
        assert g.stops == ['upstream']
        await g.queue.put({'event': 'run.cancelled', 'run_id': 'upstream'})
        await until(lambda: o.get(USER, run['id'])['status'] == 'cancelled')
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_stop_before_dispatch_does_not_start_gateway(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = await o.submit(USER, BODY)
    assert (await o.stop(USER, run['id']))['status'] == 'cancelled'
    await o.close()
    assert not g.starts


@pytest.mark.asyncio
async def test_stop_during_ambiguous_dispatch_waits_for_upstream_id(tmp_path):
    class SlowGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
        async def start(self, *args, **kwargs):
            result = await super().start(*args, **kwargs)
            await self.release.wait()
            return result
    o, j, _ = runtime_at(tmp_path)
    g = o.gateway = SlowGateway()
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        assert (await o.stop(USER, run['id']))['status'] == 'stopping'
        g.release.set()
        await until(lambda: bool(g.stops))
        assert g.stops == ['upstream']
        assert o.get(USER, run['id'])['status'] == 'stopping'
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_approval_mapping_is_durable_action_bound_and_deduplicated(tmp_path):
    from backend.orchestration import Orchestrator
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        event = {'event': 'approval.request', 'request_id': 'native-approval', 'run_id': 'upstream',
                 'command': 'rm /tmp/example', 'tool': 'terminal'}
        await g.queue.put(event)
        await g.queue.put(dict(event, timestamp=123))
        await until(lambda: bool(o.approvals(USER)['items']))
        await asyncio.sleep(0.01)
        items = o.approvals(USER)['items']
        assert len(items) == 1
        assert items[0]['run_id'] == run['id']
        assert items[0]['command'] == 'rm /tmp/example'
        assert items[0]['request_id'] == 'native-approval'
        assert items[0]['executable'] is False
        assert len([e for e in j.events('owner', run['id']) if e['name'] == 'approval']) == 1
        reopened = Orchestrator(RunJournal(j.path), g, o.catalog)
        assert reopened.approvals(USER)['items'][0]['id'] == items[0]['id']
        assert o.approvals({'id': 'other', 'profile': 'default'}) == {'items': []}
        with pytest.raises(KeyError):
            await o.decide({'id': 'other', 'profile': 'default'}, items[0]['id'], 'once')
        # Installed Runs API accepts only run-level choice: cannot bind a decision
        # atomically to request_id/action. Never fake a safe upstream success.
        for decision in ('once', 'deny'):
            with pytest.raises(IntegrationUnavailable):
                await o.decide(USER, items[0]['id'], decision)
        assert not g.requests
        await reopened.close()
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', ['changed', 'expired', 'responded'])
async def test_stale_approval_decisions_are_rejected_without_upstream_send(tmp_path, invalidate):
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        event = {'event': 'approval.request', 'request_id': 'approval', 'command': 'exact action', 'run_id': 'upstream'}
        if invalidate == 'expired':
            event['expires_at'] = 1
        await g.queue.put(event)
        await until(lambda: any(e['name'] == 'approval' for e in j.events('owner', run['id'])))
        aid = next(e['data']['id'] for e in j.events('owner', run['id']) if e['name'] == 'approval')
        with pytest.raises(ValueError):
            await o.decide(USER, aid, 'always')
        if invalidate == 'changed':
            await g.queue.put(dict(event, command='different action'))
            await until(lambda: o.get(USER, run['id'])['status'] == 'unknown')
        if invalidate == 'responded':
            await g.queue.put({'event': 'approval.responded', 'run_id': 'upstream', 'request_id': 'approval', 'choice': 'once', 'resolved': 1})
            await until(lambda: o.get(USER, run['id'])['status'] == 'running')
        assert o.approvals(USER) == {'items': []}
        with pytest.raises(RunConflict):
            await o.decide(USER, aid, 'once')
        assert all(method != 'POST' for method, _, _ in g.requests)
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_tool_progress_and_status_are_durable(tmp_path):
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        await g.queue.put({'event': 'tool.started', 'run_id': 'upstream', 'tool': 'read_file', 'preview': 'file.txt'})
        await g.queue.put({'event': 'tool.completed', 'run_id': 'upstream', 'tool': 'read_file', 'duration': 1})
        await g.queue.put({'event': 'run.completed', 'run_id': 'upstream', 'output': 'done'})
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        events = j.events('owner', run['id'])
        assert [e['data']['status'] for e in events if e['name'] == 'status'] == ['queued', 'running']
        assert [e['data']['event'] for e in events if e['name'] == 'tool'] == ['tool.started', 'tool.completed']
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_restart_retry_returns_unknown_without_reloading_or_redispatch(tmp_path):
    from backend.orchestration import Orchestrator
    o, j, g = runtime_at(tmp_path)
    run = await o.submit(USER, BODY)
    await g.started.wait()
    await o.close()
    reopened_journal = RunJournal(j.path)
    reopened_journal.recover()
    reopened = Orchestrator(reopened_journal, g, o.catalog)
    try:
        retry = await reopened.submit(USER, BODY)
        assert retry['id'] == run['id'] and retry['status'] == 'unknown'
        assert len(g.starts) == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_complete_native_loader_may_attest_fresh_empty_session(tmp_path):
    o, j, g = runtime_at(tmp_path)
    o.history_loader = lambda profile, sid: {'profile': profile, 'session_id': sid, 'complete': True, 'history': []}
    try:
        await o.submit(USER, BODY)
        await g.started.wait()
        assert g.starts[0][2] == []
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, dict(BODY, profile='member'), dict(BODY, input=''), dict(BODY, input='x' * 100001), dict(BODY, idempotency_key=42)])
async def test_invalid_submission_is_rejected_before_journaling(tmp_path, body):
    o, j, g = runtime_at(tmp_path)
    try:
        with pytest.raises(ValueError):
            await o.submit(USER, body)
        with j.connect() as c:
            assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_close_during_native_history_load_does_not_admit_work(tmp_path):
    o, j, g = runtime_at(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def load(profile, sid):
        entered.set()
        await release.wait()
        return complete_context(profile, sid)
    o.history_loader = load
    pending = asyncio.create_task(o.submit(USER, BODY))
    await entered.wait()
    await o.close()
    release.set()
    with pytest.raises(IntegrationUnavailable):
        await pending
    assert not g.starts


@pytest.mark.asyncio
async def test_late_stop_failure_does_not_overwrite_confirmed_completion(tmp_path):
    o, j, g = runtime_at(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_stop(rid):
        entered.set()
        await release.wait()
        raise IntegrationUnavailable('stop failed')
    g.stop = slow_stop
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        stopping = asyncio.create_task(o.stop(USER, run['id']))
        await entered.wait()
        await g.queue.put({'event': 'run.completed', 'run_id': 'upstream', 'output': 'completed before stop'})
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        release.set()
        assert (await stopping)['status'] == 'completed'
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_interrupted_stream_reconciles_only_once_outside_run_deadline(tmp_path):
    o, j, g = runtime_at(tmp_path, run_timeout=0.01)
    async def delayed_status(method, path, **kwargs):
        g.requests.append((method, path, kwargs))
        await asyncio.sleep(0.03)
        return {'run_id': 'upstream', 'status': 'completed', 'output': 'done'}
    g.request = delayed_status
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        await g.queue.put(None)
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        assert len(g.requests) == 1
    finally:
        await o.close()
