"""Synthetic native lifecycle and bridge recovery; no model, service or live state."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from aiohttp import web

from backend.native_run_controls import run_controls_adapter
from test_native_run_controls import Base, registry


class NativeBase(Base):
    def __init__(self):
        super().__init__()
        self._active_run_tasks = {}
        self._run_approval_sessions = {}

    async def _handle_get_run(self, request):
        error = self._check_auth(request)
        if error is not None:
            return error
        return web.json_response(self._run_statuses[request.match_info['run_id']])


def live_run(registry, run_id='r'):
    a = run_controls_adapter(NativeBase)()
    calls = []
    agent = SimpleNamespace(steer=lambda text: calls.append(text) or True,
        run_conversation=lambda **kw: {}, clear_interrupt=lambda: False,
        _drain_pending_steer=lambda: None)
    a._run_streams[run_id] = asyncio.Queue()
    a._run_approval_sessions[run_id] = run_id
    a._set_run_status(run_id, 'running')
    a._run_statuses[run_id]['run_id'] = run_id
    callback = a._make_run_event_callback(run_id, asyncio.get_running_loop())
    a.agent_factory = lambda **kw: agent
    a._active_run_agents[run_id] = a._create_agent(tool_progress_callback=callback)
    a._active_run_tasks[run_id] = asyncio.get_running_loop().create_future()
    with registry._lock:
        registry._gateway_queues[run_id] = [SimpleNamespace(data={'request_id': 'expired'})]
    a._set_run_status(run_id, 'waiting_for_approval', last_event='approval.request')
    return a, calls


def req(run_id='r', key='guidance'):
    return SimpleNamespace(match_info={'run_id': run_id},
        body={'input': 'keep working', 'idempotency_key': key})


def expire(registry, run_id='r'):
    # The native timeout _drop_entry removes the queue, with no status update.
    with registry._lock:
        registry._gateway_queues.pop(run_id)


@pytest.mark.asyncio
async def test_get_recovers_exact_live_run_after_timeout(registry):
    a, calls = live_run(registry)
    before = json.loads((await a._handle_get_run(req())).text)
    assert before['status'] == 'waiting_for_approval'
    assert before['pending_approvals'] == [{'run_id': 'r', 'request_id': 'expired'}]
    expire(registry)
    a._set_run_status('r', 'waiting_for_approval', last_event='tool.completed')
    result = json.loads((await a._handle_get_run(req())).text)
    assert result['status'] == 'running'
    assert result['pending_approvals'] == []
    assert result['last_event'] == 'tool.completed'
    assert a._run_statuses['r']['status'] == 'running'
    assert not calls


@pytest.mark.asyncio
async def test_steer_recovers_without_prior_get_and_preserves_receipts(registry):
    a, calls = live_run(registry)
    expire(registry)
    first = await a._handle_steer_run(req())
    assert first.status == 200
    receipt = json.loads(first.text)
    assert receipt['status'] == 'accepted_unconfirmed'
    assert receipt['accepted'] is True
    assert json.loads((await a._handle_steer_run(req())).text) == receipt
    snapshot = json.loads((await a._handle_get_run(req())).text)
    assert snapshot['steer_receipts'] == [receipt]
    assert snapshot['status'] == 'running'
    assert calls == ['keep working']


@pytest.mark.asyncio
async def test_new_approval_between_recovery_and_notify_blocks_steering(registry):
    a, calls = live_run(registry)
    expire(registry)
    assert json.loads((await a._handle_get_run(req())).text)['status'] == 'running'
    # Native inserts under its registry lock BEFORE publishing waiting status.
    with registry._lock:
        registry._gateway_queues['r'] = [SimpleNamespace(data={'request_id': 'new'})]
    response = await a._handle_steer_run(req())
    assert response.status == 409
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', [None, 'foreign'])
@pytest.mark.parametrize('evidence', ['empty', 'pending', 'unavailable'])
async def test_running_without_matching_callback_binding_rejects_new_steer(registry, binding, evidence):
    a, calls = live_run(registry)
    expire(registry)
    a._set_run_status('r', 'running')
    a._controls['r']['approval_session'] = binding
    if evidence == 'pending':
        with registry._lock:
            registry._gateway_queues['r'] = [SimpleNamespace(data={'request_id': 'new'})]
    elif evidence == 'unavailable':
        def unavailable(key):
            raise RuntimeError('unavailable')
        registry.list_gateway_approvals = unavailable
    response = await a._handle_steer_run(req())
    assert response.status == 409
    assert json.loads(response.text)['status'] == 'not_delivered'
    assert json.loads(response.text)['accepted'] is False
    assert calls == []


@pytest.mark.asyncio
async def test_existing_receipt_replays_without_binding_or_registry(registry):
    a, calls = live_run(registry)
    expire(registry)
    first = await a._handle_steer_run(req())
    assert first.status == 200
    receipt = json.loads(first.text)
    a._controls['r'].pop('approval_session')
    a._run_approval_sessions.pop('r')
    del registry._gateway_queues
    replay = await a._handle_steer_run(req())
    assert replay.status == 200
    assert json.loads(replay.text) == receipt
    assert (await a._handle_steer_run(req(key='new'))).status == 409
    assert calls == ['keep working']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [
    'missing_task', 'done_task', 'missing_agent', 'changed_agent', 'missing_queue',
    'changed_queue', 'missing_session', 'foreign_session', 'closed', 'stopping',
    'completed', 'failed', 'cancelled', 'missing_identity', 'wrong_identity',
    'unavailable', 'malformed', 'missing_registry', 'malformed_queue', 'new_approval',
    'changed_task', 'changed_state', 'changed_status',
])
@pytest.mark.parametrize('endpoint', ['get', 'steer'])
async def test_recovery_fails_closed_on_missing_evidence_or_races(registry, fault, endpoint):
    a, calls = live_run(registry)
    expire(registry)
    original = registry.list_gateway_approvals
    def listing(key):
        snapshot = original(key)
        if fault == 'missing_task': a._active_run_tasks.pop('r')
        if fault == 'done_task': a._active_run_tasks['r'].set_result(None)
        if fault == 'missing_agent': a._active_run_agents.pop('r')
        if fault == 'changed_agent': a._active_run_agents['r'] = SimpleNamespace(steer=lambda _: calls.append('wrong'))
        if fault == 'missing_queue': a._run_streams.pop('r')
        if fault == 'changed_queue': a._run_streams['r'] = asyncio.Queue()
        if fault == 'missing_session': a._run_approval_sessions.pop('r')
        if fault == 'foreign_session': a._run_approval_sessions['r'] = 'foreign'
        if fault == 'closed': a._controls['r']['closed'] = True
        if fault == 'stopping': a._stopping_run_ids.add('r')
        if fault in ('completed', 'failed', 'cancelled'): a._set_run_status('r', fault)
        if fault == 'missing_identity': a._run_statuses['r'].pop('run_id')
        if fault == 'wrong_identity': a._run_statuses['r']['run_id'] = 'foreign'
        if fault == 'unavailable': raise RuntimeError('unavailable')
        if fault == 'malformed': return [{'request_id': None}]
        if fault == 'missing_registry': del registry._gateway_queues
        if fault == 'malformed_queue': registry._gateway_queues['r'] = None
        if fault == 'new_approval':
            with registry._lock:
                registry._gateway_queues['r'] = [SimpleNamespace(data={'request_id': 'new'})]
        if fault == 'changed_task': a._active_run_tasks['r'] = asyncio.get_running_loop().create_future()
        if fault == 'changed_state': a._controls['r'] = dict(a._controls['r'])
        if fault == 'changed_status': a._run_statuses['r'] = dict(a._run_statuses['r'])
        return snapshot
    registry.list_gateway_approvals = listing
    response = await (a._handle_get_run(req()) if endpoint == 'get' else a._handle_steer_run(req()))
    assert a._run_statuses['r']['status'] != 'running'
    if endpoint == 'steer':
        assert response.status == 409
    else:
        assert json.loads(response.text)['status'] != 'running'
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['get', 'steer'])
async def test_registry_lock_covers_recovery_publication_and_admission(registry, endpoint):
    a, calls = live_run(registry)
    expire(registry)
    attempted, inserted = threading.Event(), threading.Event()
    def insert():
        attempted.set()
        with registry._lock:
            registry._gateway_queues['r'] = [SimpleNamespace(data={'request_id': 'new'})]
            inserted.set()
        # Native notify releases the registry lock before acquiring controls.
        a._set_run_status('r', 'waiting_for_approval')
    worker = threading.Thread(target=insert)
    original = a._set_run_status
    def publish(run_id, status, **fields):
        if status == 'running':
            assert registry._lock.locked()
            worker.start()
            assert attempted.wait(2)
            assert not inserted.is_set()
        return original(run_id, status, **fields)
    a._set_run_status = publish
    original_steer = a._active_run_agents['r'].steer
    def steer(text):
        assert registry._lock.locked() and not inserted.is_set()
        return original_steer(text)
    a._active_run_agents['r'].steer = steer
    response = await (a._handle_get_run(req()) if endpoint == 'get' else a._handle_steer_run(req()))
    assert response.status == 200
    await asyncio.to_thread(worker.join, 2)
    assert not worker.is_alive() and inserted.is_set()
    assert a._run_statuses['r']['status'] == 'waiting_for_approval'
    assert calls == (['keep working'] if endpoint == 'steer' else [])


def test_bridge_recovers_only_bound_approval_and_steers_without_resubmitting(tmp_path, registry):
    import httpx
    from test_steering import environment, BASE, BODY
    holder = {}
    async def upstream(request):
        if request.url.path == '/v1/runs/native-run':
            response = await holder['native']._handle_get_run(req('native-run'))
        elif request.url.path == '/v1/runs/native-run/steer':
            r = req('native-run')
            r.body = json.loads(request.content)
            response = await holder['native']._handle_steer_run(r)
        else:
            assert request.method == 'GET', 'No new run or approval decision permitted'
            return None
        return httpx.Response(response.status, json=json.loads(response.text))
    with environment(tmp_path, upstream) as (app, client, user, rid, requests):
        async def setup():
            holder['native'], holder['calls'] = live_run(registry, 'native-run')
        client.portal.call(setup)
        journal, runtime = app.state.journal, app.state.orchestrator
        runtime._approval(user, runtime.get(user, rid),
            {'event': 'approval.request', 'run_id': 'native-run', 'request_id': 'expired'})
        other, _ = journal.submit('another-owner', 'other-profile', 'other-session', 'untouched', 'other-key')
        with journal.connect() as db:
            db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                ('foreign', other['id'], 'expired', '{}', 'pending', 0))
        journal.finish(user['id'], rid, 'unknown')
        expire(registry, 'native-run')
        recovered = client.portal.call(runtime.refresh, user, rid)
        assert recovered['status'] == 'running'
        assert (recovered['id'], recovered['input'], recovered['idempotency_key'], recovered['upstream_id']) == (
            rid, 'original', 'start-key', 'native-run')
        with journal.connect() as db:
            rows = dict(db.execute('SELECT run_id,status FROM orchestration_approvals'))
            assert rows == {rid: 'resolved_external', other['id']: 'pending'}
        assert client.get(BASE+f'/runs/{rid}/controls').json()['steering'] is True
        response = client.post(BASE+f'/runs/{rid}/steer', json=BODY)
        assert response.status_code == 200
        assert response.json()['status'] == 'accepted_unconfirmed'
        assert holder['calls'] == [BODY['input']]
        assert [(r.method, r.url.path) for r in requests if r.method != 'GET'] == [
            ('POST', '/v1/runs/native-run/steer')]
