import asyncio
import importlib.util
import json
import os
import queue
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from aiohttp import web


def module():
    assert importlib.util.find_spec('backend.native_maintenance'), 'maintenance module missing'
    from backend import native_maintenance
    return native_maintenance


@pytest.fixture(autouse=True)
def isolated_daemon_pool(monkeypatch):
    """Load only the dependency-free installed pool, never native registries."""
    import sys
    spec = importlib.util.spec_from_file_location(
        'maintenance_test_daemon_pool', '/usr/local/lib/hermes-agent/tools/daemon_pool.py')
    daemon_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon_module)
    monkeypatch.setitem(sys.modules, 'tools.daemon_pool', daemon_module)
    return daemon_module.DaemonThreadPoolExecutor


def test_idle_real_delegation_pool_does_not_block_next_release(tmp_path, isolated_daemon_pool):
    registry, delegations, path = components(tmp_path)
    a = module().maintenance_adapter(Base, sources=lambda: (registry, delegations, path))()
    with isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate') as pool:
        assert pool.submit(lambda: 'completed').result(timeout=2) == 'completed'
        assert any(t.is_alive() for t in pool._threads), 'regression needs an idle live worker'
        evidence = module().maintenance_snapshot(a, registry, delegations, path)
        assert evidence['status'] == 'ok', evidence
        assert all(count == 0 for count in evidence['work'].values()), evidence


def test_queued_cancellation_releases_once_but_running_cancellation_does_not(tmp_path, isolated_daemon_pool):
    a = module().maintenance_adapter(Base)()
    stop, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        stop.wait(5)
    with isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate') as pool:
        running = pool.submit(blocked)
        try:
            assert entered.wait(2)
            queued = pool.submit(lambda: pytest.fail('cancelled task must never run'))
            assert a._maintenance_delegations.count() == 2
            assert not running.cancel()
            assert a._maintenance_delegations.count() == 2
            assert queued.cancel()
            assert queued.cancel()  # Repeated cancellation must not undercount.
            assert a._maintenance_delegations.count() == 1
        finally:
            stop.set()
        running.result(timeout=2)
        assert a._maintenance_delegations.count() == 0


@pytest.mark.parametrize('started', [False, True])
def test_submit_failure_after_enqueue_never_hides_live_work(tmp_path, isolated_daemon_pool, monkeypatch, started):
    a = module().maintenance_adapter(Base)()
    stop, entered = threading.Event(), threading.Event()
    calls = []
    def work():
        calls.append('ran')
        entered.set()
        stop.wait(5)
    with isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate') as pool:
        adjust = pool._adjust_thread_count
        def broken_adjust():
            if started:
                adjust()
                assert entered.wait(2)
            raise RuntimeError('thread start failed after enqueue')
        monkeypatch.setattr(pool, '_adjust_thread_count', broken_adjust)
        try:
            with pytest.raises(RuntimeError, match='thread start failed'):
                pool.submit(work)
            assert a._maintenance_delegations.count() == int(started)
            monkeypatch.setattr(pool, '_adjust_thread_count', adjust)
        finally:
            stop.set()
        assert pool.submit(lambda: 'next').result(timeout=2) == 'next'
        assert calls == (['ran'] if started else [])
        assert a._maintenance_delegations.count() == 0


@pytest.mark.parametrize('prefix', ['async-delegate', '', 'tool-executor'])
@pytest.mark.parametrize('state', ['unused', 'idle', 'running', 'foreign_thread'])
def test_late_tracker_installation_is_sticky_unknown(tmp_path, isolated_daemon_pool, state, prefix):
    registry, delegations, path = components(tmp_path)
    stop, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        stop.wait(5)
    pool = isolated_daemon_pool(max_workers=1, thread_name_prefix=prefix)
    worker = None
    try:
        if state == 'idle':
            pool.submit(lambda: None).result(timeout=2)
        elif state == 'running':
            pool.submit(blocked)
            assert entered.wait(2)
        elif state == 'foreign_thread':
            pool.shutdown()
            del pool
            pool = None
            worker = threading.Thread(target=blocked, name='async-delegate_legacy', daemon=True)
            worker.start()
            assert entered.wait(2)
        a = module().maintenance_adapter(Base)()
        evidence = module().maintenance_snapshot(a, registry, delegations, path)
        assert evidence['status'] == 'unknown' and 'work' not in evidence
    finally:
        stop.set()
        if pool is not None:
            pool.shutdown(wait=True)
        if worker is not None:
            worker.join(timeout=2)
    assert module().maintenance_snapshot(a, registry, delegations, path)['status'] == 'unknown'


@pytest.mark.parametrize('prefix', ['', 'tool-executor'])
def test_late_install_rejects_abandoned_pool_even_after_object_collected(tmp_path, isolated_daemon_pool, prefix):
    import gc
    import weakref
    registry, delegations, path = components(tmp_path)
    stop, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        stop.wait(5)
    pool = isolated_daemon_pool(max_workers=1, thread_name_prefix=prefix)
    running = pool.submit(blocked)
    try:
        assert entered.wait(2)
        workers = list(pool._threads)
        ref = weakref.ref(pool)
        pool.shutdown(wait=False)
        del pool
        gc.collect()
        assert ref() is None
        a = module().maintenance_adapter(Base)()
        assert module().maintenance_snapshot(a, registry, delegations, path)['status'] == 'unknown'
    finally:
        stop.set()
        running.result(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
    assert module().maintenance_snapshot(a, registry, delegations, path)['status'] == 'unknown'


@pytest.mark.parametrize('when', ['before_install', 'after_install'])
def test_changed_submit_implementation_cannot_claim_readiness(tmp_path, isolated_daemon_pool, monkeypatch, when):
    registry, delegations, path = components(tmp_path)
    original = isolated_daemon_pool.submit
    def changed(executor, fn, /, *args, **kwargs):
        return original(executor, fn, *args, **kwargs)
    if when == 'before_install':
        monkeypatch.setattr(isolated_daemon_pool, 'submit', changed)
    a = module().maintenance_adapter(Base)()
    if when == 'after_install':
        monkeypatch.setattr(isolated_daemon_pool, 'submit', changed)
    evidence = module().maintenance_snapshot(a, registry, delegations, path)
    assert evidence['status'] == 'unknown' and 'work' not in evidence


def test_work_exceptions_and_rejected_shutdown_submissions_balance(tmp_path, isolated_daemon_pool):
    a = module().maintenance_adapter(Base)()
    def failed():
        assert a._maintenance_delegations.count() == 1
        raise ValueError('worker failed')
    with isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate') as pool:
        for _ in range(3):
            with pytest.raises(ValueError, match='worker failed'):
                pool.submit(failed).result(timeout=2)
            assert a._maintenance_delegations.count() == 0
    with pytest.raises(RuntimeError, match='shutdown'):
        pool.submit(lambda: pytest.fail('rejected work ran'))
    assert a._maintenance_delegations.count() == 0


@pytest.mark.parametrize('prefix', ['async-delegate', '', 'tool-executor'])
def test_install_precedes_base_constructor_counts_all_daemon_pools_not_stdlib(isolated_daemon_pool, prefix):
    from concurrent.futures import ThreadPoolExecutor
    stdlib_submit = ThreadPoolExecutor.submit
    stop, entered = threading.Event(), threading.Event()
    def work():
        entered.set()
        stop.wait(5)
    # Stdlib work is unaffected; every daemon-pool prefix is now in scope.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='stdlib') as other:
        untracked = other.submit(work)
        assert entered.wait(2)
        class ConstructingBase(Base):
            def __init__(self):
                super().__init__()
                assert self._maintenance_delegations.count() == 0
                with isolated_daemon_pool(max_workers=1, thread_name_prefix=prefix) as pool:
                    def task(*, fn):
                        assert self._maintenance_delegations.count() == 1
                        return fn
                    assert pool.submit(task, fn='keyword forwarding').result(timeout=2) == 'keyword forwarding'
                    assert any(t.is_alive() for t in pool._threads)
                    assert self._maintenance_delegations.count() == 0
        try:
            a = module().maintenance_adapter(ConstructingBase)()
            assert a._maintenance_delegations.count() == 0
            assert ThreadPoolExecutor.submit is stdlib_submit
        finally:
            stop.set()
        untracked.result(timeout=2)


def test_shutdown_cancels_queued_futures_without_releasing_running_work(isolated_daemon_pool):
    a = module().maintenance_adapter(Base)()
    stop, entered = threading.Event(), threading.Event()
    def work():
        entered.set()
        stop.wait(5)
    pool = isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate')
    running = pool.submit(work)
    try:
        assert entered.wait(2)
        queued = [pool.submit(lambda: pytest.fail('cancelled queue entry ran')) for _ in range(4)]
        assert a._maintenance_delegations.count() == 5
        pool.shutdown(wait=False, cancel_futures=True)
        assert all(future.cancelled() for future in queued)
        assert a._maintenance_delegations.count() == 1
    finally:
        stop.set()
        pool.shutdown(wait=True)
    running.result(timeout=2)
    assert a._maintenance_delegations.count() == 0


def durable(tmp_path, events):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE async_delegations (delegation_id TEXT PRIMARY KEY, state TEXT, event_json TEXT, result_json TEXT, delivery_state TEXT)')
        for event in events:
            db.execute('INSERT INTO async_delegations VALUES (?,?,?,?,?)',
                       (event['delegation_id'], 'completed', json.dumps(event), json.dumps({'summary': 'result'}), 'pending'))
    return path


def test_durable_backlog_inspection_preserves_queue_and_database(tmp_path):
    event = {'type': 'async_delegation', 'delegation_id': 'd', 'session_key': 'foreign-owner', 'summary': 'sensitive'}
    path = durable(tmp_path, [event])
    q = queue.Queue()
    q.put({**event, 'restored': True})
    before = path.read_bytes()
    result = module().notification_evidence(q, path)
    assert result == {'status': 'ok', 'backlog': 1, 'durable_retained': 1, 'unpreserved': 0,
                      'policy': 'retain-durable-no-drain-no-replay'}
    assert q.qsize() == 1 and q.unfinished_tasks == 1
    assert list(q.queue) == [{**event, 'restored': True}]
    assert path.read_bytes() == before
    assert 'sensitive' not in json.dumps(result)


class Base:
    def __init__(self):
        self._pending_agent_requests = 0
        self._inflight_agent_runs = 0
        self._active_run_tasks = {}
        self._run_statuses = {}
        self._active_run_agents = {}
        self._shutdown_interruptible_agents = {}
        self._stopping_run_ids = set()
    def _check_auth(self, request):
        return None if request == 'secret' else web.json_response({'error': 'unauthorized'}, status=401)
    async def _handle_health_detailed(self, request):
        return web.json_response({'pid': os.getpid(), 'gateway_busy': True})
    async def _handle_capabilities(self, request):
        return web.json_response({'features': {'runs': True}})
    def _create_agent(self, **kwargs):
        return self.agent_factory(**kwargs)
    def _set_run_status(self, run_id, status, **fields):
        self._run_statuses[run_id] = {'status': status, **fields}
    async def _run_agent(self):
        await self.work()


def components(tmp_path):
    registry = SimpleNamespace(_lock=threading.Lock(), _running={}, completion_queue=queue.Queue())
    delegations = SimpleNamespace(_records_lock=threading.Lock(), _records={})
    return registry, delegations, tmp_path / 'absent.db'


def test_listener_snapshot_all_work_categories_are_positive(tmp_path):
    registry, delegations, path = components(tmp_path)
    a = module().maintenance_adapter(Base)()
    result = module().maintenance_snapshot(a, registry, delegations, path)
    assert result['version'] == 1 and result['scope'] == 'dedicated-listener'
    assert result['pid'] == os.getpid() and type(result['start_ticks']) is int
    assert result['status'] == 'ok'
    assert set(result['work']) == {'pending_admissions', 'inflight_agent_calls', 'active_run_tasks',
                                  'nonterminal_runs', 'active_run_agents', 'shutdown_agents',
                                  'stopping_runs', 'agent_workers', 'cancellation_uncertain',
                                  'live_subprocesses', 'active_delegations', 'delegation_executor_threads', 'session_deletion_workers'}
    assert all(value == 0 for value in result['work'].values())
    a._pending_agent_requests = 1
    a._inflight_agent_runs = 2
    a._active_run_tasks['r'] = SimpleNamespace(done=lambda: False)
    a._run_statuses = {s: {'status': s} for s in (
        'queued', 'running', 'stopping', 'waiting_for_approval',
        'waiting_for_clarification', 'completed')}
    a._active_run_agents['r'] = object()
    a._shutdown_interruptible_agents[1] = object()
    a._stopping_run_ids.add('r')
    a._maintenance_workers = 2
    a._session_deletion_workers = 1
    a._maintenance_uncertain = True
    registry._running['p'] = object()
    delegations._records['d'] = {'status': 'finalizing'}
    result = module().maintenance_snapshot(a, registry, delegations, path)
    assert result['status'] == 'ok'
    assert all(value > 0 for key, value in result['work'].items() if key != 'delegation_executor_threads')
    assert result['work']['nonterminal_runs'] == 5
    assert not path.exists()


@pytest.mark.parametrize('corruption', ['pending_bool', 'inflight_negative', 'task_done_string', 'missing_tasks',
                                        'run_status', 'delegation_status', 'missing_registry', 'broken_database'])
def test_unknown_state_never_becomes_zero(tmp_path, corruption):
    registry, delegations, path = components(tmp_path)
    a = module().maintenance_adapter(Base)()
    if corruption == 'pending_bool':
        a._pending_agent_requests = False
    elif corruption == 'inflight_negative':
        a._inflight_agent_runs = -1
    elif corruption == 'task_done_string':
        a._active_run_tasks['r'] = SimpleNamespace(done=lambda: 'yes')
    elif corruption == 'missing_tasks':
        del a._active_run_tasks
    elif corruption == 'run_status':
        a._run_statuses['r'] = {'status': 'unrecognized'}
    elif corruption == 'delegation_status':
        delegations._records['r'] = {'status': 'unrecognized'}
    elif corruption == 'missing_registry':
        registry = None
    else:
        registry.completion_queue.put({'type': 'async_delegation', 'delegation_id': 'missing'})
    result = module().maintenance_snapshot(a, registry, delegations, path)
    assert result['status'] == 'unknown'
    assert 'work' not in result
    assert not path.exists()


@pytest.mark.parametrize('owner,attribute', [('adapter', '_active_run_agents'), ('adapter', '_shutdown_interruptible_agents'),
                                           ('adapter', '_stopping_run_ids'), ('registry', '_running'),
                                           ('delegations', '_records')])
def test_malformed_empty_container_is_not_positive_zero(tmp_path, owner, attribute):
    registry, delegations, path = components(tmp_path)
    a = module().maintenance_adapter(Base, sources=lambda: (registry, delegations, path))()
    setattr({'adapter': a, 'registry': registry, 'delegations': delegations}[owner], attribute, [])
    assert module().maintenance_snapshot(a, registry, delegations, path)['status'] == 'unknown'


@pytest.mark.parametrize('record_state', ['running', 'stalled', 'pruned'])
def test_real_daemon_worker_survives_record_finalization_and_pool_resize(tmp_path, isolated_daemon_pool, record_state):
    import gc
    import weakref
    registry, delegations, path = components(tmp_path)
    Adapter = module().maintenance_adapter(Base, sources=lambda: (registry, delegations, path))
    a = Adapter()
    submit = isolated_daemon_pool.submit
    second = module().maintenance_adapter(Base)()
    assert second._maintenance_delegations is a._maintenance_delegations
    assert isolated_daemon_pool.submit is submit, 'adapter construction must not nest wrappers'
    if record_state != 'pruned':
        delegations._records['d'] = {'status': record_state}
    stop, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        stop.wait(5)
    old = isolated_daemon_pool(max_workers=1, thread_name_prefix='async-delegate')
    old_ref = weakref.ref(old)
    running = old.submit(blocked)
    workers = list(old._threads)
    try:
        assert entered.wait(2)
        assert all(t.daemon for t in workers)
        assert not running.cancel()
        old.shutdown(wait=False)
        del old
        gc.collect()
        assert old_ref() is None, 'test must abandon the old pool just as native resize does'
        with isolated_daemon_pool(max_workers=3, thread_name_prefix='async-delegate') as new:
            assert new.submit(lambda: 'new generation').result(timeout=2) == 'new generation'
            result = module().maintenance_snapshot(a, registry, delegations, path)
            assert result['status'] == 'ok'
            assert result['work']['active_delegations'] == int(record_state == 'running')
            assert result['work']['delegation_executor_threads'] == 1
            assert second._maintenance_delegations.count() == 1
            delegations._records.clear()
            assert module().maintenance_snapshot(a, registry, delegations, path)['work']['delegation_executor_threads'] == 1
    finally:
        stop.set()
        running.result(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
    assert a._maintenance_delegations.count() == 0


def test_adapter_auth_and_capability_are_explicit(tmp_path):
    calls = []
    def sources():
        calls.append('read')
        return components(tmp_path)
    a = module().maintenance_adapter(Base, sources=sources)()
    async def check():
        assert (await a._handle_health_detailed('wrong')).status == 401
        assert (await a._handle_capabilities('wrong')).status == 401
        assert calls == []
        data = json.loads((await a._handle_health_detailed('secret')).text)
        assert data['gateway_busy'] is True  # Never use shared gateway busy for this decision.
        assert data['native_maintenance']['status'] == 'ok'
        caps = json.loads((await a._handle_capabilities('secret')).text)
        assert caps['mobile_native_maintenance'] == {'version': 1, 'scope': 'dedicated-listener', 'atomic_drain': False}
        assert 'native_maintenance' not in json.loads((await Base()._handle_health_detailed('secret')).text)
    asyncio.run(check())


def test_agent_construction_and_execution_reservation_survives_native_cleanup(tmp_path):
    a = module().maintenance_adapter(Base, sources=lambda: components(tmp_path))()
    def work():
        assert a._maintenance_workers == 1
        a._active_run_agents.clear()
        a._active_run_tasks.clear()
        a._set_run_status('r', 'cancelled')
        assert a._maintenance_workers == 1
        return 'done'
    def factory(**kwargs):
        assert a._maintenance_workers == 1
        return SimpleNamespace(run_conversation=work)
    a.agent_factory = factory
    agent = a._create_agent()
    assert a._maintenance_workers == 1
    assert agent.run_conversation() == 'done'
    assert a._maintenance_workers == 0 and a._maintenance_uncertain is True
    def broken(**kwargs):
        raise ValueError('creation failed')
    a.agent_factory = broken
    with pytest.raises(ValueError):
        a._create_agent()
    assert a._maintenance_workers == 0


def test_chat_coroutine_cancellation_is_sticky_uncertainty(tmp_path):
    a = module().maintenance_adapter(Base, sources=lambda: components(tmp_path))()
    async def check():
        entered = asyncio.Event()
        async def work():
            entered.set()
            await asyncio.Event().wait()
        a.work = work
        task = asyncio.create_task(a._run_agent())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert a._maintenance_uncertain is True
    asyncio.run(check())


def test_default_sources_never_import_native_modules(tmp_path, monkeypatch):
    import sys
    registry, delegations, path = components(tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setitem(sys.modules, 'tools.process_registry', SimpleNamespace(process_registry=registry))
    monkeypatch.setitem(sys.modules, 'tools.async_delegation', delegations)
    a = module().maintenance_adapter(Base)()
    async def check():
        result = json.loads((await a._handle_health_detailed('secret')).text)['native_maintenance']
        assert result['status'] == 'ok'
        monkeypatch.delitem(sys.modules, 'tools.process_registry')
        result = json.loads((await a._handle_health_detailed('secret')).text)['native_maintenance']
        assert result['status'] == 'unknown'
        assert 'tools.process_registry' not in sys.modules
    asyncio.run(check())
    assert not path.exists()


def test_reused_agent_reservations_and_exceptions_balance(tmp_path):
    a = module().maintenance_adapter(Base, sources=lambda: components(tmp_path))()
    def work():
        assert a._maintenance_workers == 1
        raise ValueError('turn failed')
    a.agent_factory = lambda: SimpleNamespace(run_conversation=work)
    agent = a._create_agent()
    for _ in range(2):
        with pytest.raises(ValueError, match='turn failed'):
            agent.run_conversation()
        assert a._maintenance_workers == 0


@pytest.mark.parametrize('mutation', ['payload', 'type', 'missing_row', 'live_row', 'bad_result', 'typed_payload'])
def test_unpreserved_notifications_are_not_certified(tmp_path, mutation):
    event = {'type': 'async_delegation', 'delegation_id': 'd', 'attempt': 1}
    path = durable(tmp_path, [event])
    q = queue.Queue()
    altered = dict(event)
    if mutation == 'payload':
        altered['routing'] = 'changed'
    elif mutation == 'type':
        altered['type'] = 'process_completion'
    elif mutation == 'missing_row':
        altered['delegation_id'] = 'other'
    elif mutation == 'typed_payload':
        altered['attempt'] = True
    else:
        with sqlite3.connect(path) as db:
            if mutation == 'live_row':
                db.execute("UPDATE async_delegations SET state='running'")
            else:
                db.execute("UPDATE async_delegations SET result_json='null'")
    q.put(altered)
    before = path.read_bytes()
    result = module().notification_evidence(q, path)
    assert result['durable_retained'] == 0 and result['unpreserved'] == 1
    assert q.qsize() == 1 and path.read_bytes() == before


def run_installed_probe(tmp_path, script):
    """Never import native registries in the pytest process or live profile."""
    import subprocess
    from pathlib import Path
    prelude = '''
import json, os, sqlite3, sys, threading
from pathlib import Path
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from backend.native_maintenance import maintenance_adapter, maintenance_snapshot
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
adapter = maintenance_adapter(APIServerAdapter)(PlatformConfig(enabled=True, extra={'key': 'test-secret'}))
from tools import async_delegation as delegations
from tools.process_registry import process_registry as registry
from deploy.native_readiness import require_native_readiness
assert str(delegations._db_path()).startswith(os.environ['HERMES_HOME'] + '/')
def snapshot():
    return maintenance_snapshot(adapter, registry, delegations, delegations._db_path())
def ready(evidence):
    return require_native_readiness({'pid': os.getpid(), 'native_maintenance': evidence},
        expected_pid=os.getpid(), expected_start_ticks=evidence['start_ticks'])
'''
    result = subprocess.run(
        ['/usr/local/lib/hermes-agent/venv/bin/python', '-c', prelude + script],
        capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parents[1],
        env={'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path),
             'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
             'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    return result.stdout


def test_installed_terminal_dispatch_persistence_and_publication_are_idle(tmp_path):
    output = run_installed_probe(tmp_path, r'''
expected = {}
for state in ('completed', 'failed', 'error', 'interrupted', 'timeout'):
    result = {'status': state, 'summary': 'deterministic fixture ' + state,
              'exit_reason': state}
    handle = delegations.dispatch_async_delegation(
        goal='terminal fixture ' + state, context=None, toolsets=None, role='leaf',
        model=None, session_key='isolated-owner', parent_session_id='isolated-parent',
        origin_ui_session_id='isolated-ui', origin_session_id='isolated-origin',
        runner=lambda result=result: result, max_async_children=8)
    assert handle['status'] == 'dispatched', handle
    expected[handle['delegation_id']] = result
# Join real submissions, including native finalization/persistence/publication.
delegations._executor.shutdown(wait=True)
assert adapter._maintenance_delegations.count() == 0
queued = list(registry.completion_queue.queue)
assert len(queued) == len(expected)
with sqlite3.connect(delegations._db_path()) as db:
    rows_before = db.execute('SELECT * FROM async_delegations ORDER BY delegation_id').fetchall()
    for event in queued:
        delegation_id = event['delegation_id']
        result = expected[delegation_id]
        assert event['status'] == result['status']
        assert event['parent_session_id'] == 'isolated-parent'
        assert event['origin_session_id'] == 'isolated-origin'
        assert event['origin_ui_session_id'] == 'isolated-ui'
        row = db.execute('SELECT state,event_json,result_json,delivery_state FROM async_delegations WHERE delegation_id=?',
                         (delegation_id,)).fetchone()
        assert row[0] == result['status'] == delegations._records[delegation_id]['status']
        assert json.loads(row[1]) == event
        assert json.loads(row[2]) == result
        assert row[3] == 'pending'
evidence = snapshot()
print('native terminal observation=' + json.dumps(evidence), flush=True)
assert evidence['status'] == 'ok', evidence
assert evidence['notifications']['durable_retained'] == len(expected), evidence
assert ready(evidence), evidence
assert list(registry.completion_queue.queue) == queued
assert registry.completion_queue.unfinished_tasks == len(expected)
with sqlite3.connect(delegations._db_path()) as db:
    assert db.execute('SELECT * FROM async_delegations ORDER BY delegation_id').fetchall() == rows_before
print('native terminals: completed, failed, error, interrupted, timeout -> idle; all 5 notices pending and untouched')
''')
    assert 'all 5 notices pending and untouched' in output


@pytest.mark.parametrize('mode', ['mixed_batch', 'single'])
def test_installed_mixed_batch_blocks_until_abandoned_nested_child_exits(tmp_path, mode):
    output = run_installed_probe(tmp_path, 'mode = ' + repr(mode) + '\n' + r'''
from tools import delegate_tool
stop, entered = threading.Event(), threading.Event()
workers = []
class BlockedChild:
    session_id = 'isolated-blocked-child'
    _delegate_saved_tool_names = []
    def get_activity_summary(self):
        return {'api_call_count': 1}
    def run_conversation(self, **kwargs):
        workers.append(threading.current_thread())
        entered.set()
        stop.wait()
        return {'final_response': 'released', 'messages': []}
# Bound only the timeout policy; installed child execution, abandonment,
# async batch aggregation, durable persistence and publication are real.
delegate_tool._get_child_timeout = lambda: 0.1
def batch():
    nested = delegate_tool._run_single_child(0, 'blocked fixture', child=BlockedChild())
    assert nested['status'] == 'timeout', nested
    if mode == 'single':
        return nested
    return {'results': [nested, {'task_index': 1, 'status': 'completed',
                                'summary': 'successful sibling fixture'}]}
try:
    dispatch = (delegations.dispatch_async_delegation_batch if mode == 'mixed_batch'
                else delegations.dispatch_async_delegation)
    goal_args = ({'goals': ['blocked fixture', 'successful sibling fixture']}
                 if mode == 'mixed_batch' else {'goal': 'blocked fixture'})
    expected_status = 'completed' if mode == 'mixed_batch' else 'timeout'
    handle = dispatch(**goal_args, context=None,
        toolsets=None, role='leaf', model=None, session_key='isolated-owner',
        runner=batch, max_async_children=1)
    assert handle['status'] == 'dispatched', handle
    assert entered.wait(10)
    # Joining only the outer executor proves its submission really exited.
    delegations._executor.shutdown(wait=True)
    assert delegations._records[handle['delegation_id']]['status'] == expected_status
    assert workers[0].is_alive() and not stop.is_set()
    queued = list(registry.completion_queue.queue)
    assert len(queued) == 1 and queued[0]['status'] == expected_status
    before = snapshot()
    print(mode + ' blocked observation=' + json.dumps(before), flush=True)
    assert before['status'] == 'ok', before
    assert before['work']['active_delegations'] == 0
    assert before['work']['delegation_executor_threads'] == 1, before
    assert before['notifications']['durable_retained'] == 1
    assert not ready(before), before
finally:
    stop.set()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    if delegations._executor is not None:
        delegations._executor.shutdown(wait=True)
after = snapshot()
assert ready(after), after
assert not any(after['work'].values()), after
assert list(registry.completion_queue.queue) == queued
assert after['notifications'] == before['notifications']
print(mode + ': ' + expected_status + ' + live nested timeout -> blocked; child exit -> idle; notice retained')
''')
    assert 'child exit -> idle' in output


@pytest.mark.parametrize('interpreter', ['project', 'installed'])
def test_isolated_installed_native_composition_health_and_78_durable_events(tmp_path, interpreter):
    import subprocess
    import sys
    script = r'''
import asyncio, json, os, sqlite3, sys
from pathlib import Path
sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages')
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from aiohttp.test_utils import make_mocked_request
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from tools.process_registry import process_registry
from tools import async_delegation
from backend.native_maintenance import maintenance_adapter
from backend.native_run_controls import run_controls_adapter
from deploy.native_readiness import require_native_readiness
assert str(async_delegation._db_path()).startswith(os.environ['HERMES_HOME'] + '/')
# The native module's initialization occurs only against the fresh temporary home.
with sqlite3.connect(async_delegation._db_path()) as db:
    for i in range(78):
        evt = {'type': 'async_delegation', 'delegation_id': 'd'+str(i), 'session_key': 'foreign:'+str(i),
               'status': 'completed', 'summary': 'retained result'}
        db.execute('INSERT INTO async_delegations (delegation_id,origin_session,state,dispatched_at,updated_at,event_json,result_json) VALUES (?,?,?,?,?,?,?)',
                   (evt['delegation_id'], evt['session_key'], 'completed', 1, 1, json.dumps(evt), '{}'))
        process_registry.completion_queue.put({**evt, 'restored': True})
Adapter = maintenance_adapter(run_controls_adapter(APIServerAdapter))
a = Adapter(PlatformConfig(enabled=True, extra={'key': 'test-secret'}))
async def check():
    denied = await a._handle_health_detailed(make_mocked_request('GET', '/health/detailed'))
    assert denied.status == 401
    request = make_mocked_request('GET', '/health/detailed', headers={'Authorization': 'Bearer test-secret'})
    response = await a._handle_health_detailed(request)
    assert response.status == 200, response.text
    health = json.loads(response.text)
    ev = health['native_maintenance']
    assert ev['status'] == 'ok', ev
    assert require_native_readiness(health, expected_pid=os.getpid(), expected_start_ticks=ev['start_ticks'])
    # Real native _get_executor lifecycle, including abandoned generation.
    import threading
    stop, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        stop.wait(5)
    pool = async_delegation._get_executor(1)
    assert pool.submit(lambda: 'first').result(timeout=2) == 'first'
    idle = json.loads((await a._handle_health_detailed(request)).text)
    assert require_native_readiness(idle, expected_pid=os.getpid(), expected_start_ticks=ev['start_ticks']), idle
    running = pool.submit(blocked)
    try:
        assert entered.wait(2)
        async_delegation._records['test-live'] = {'status': 'stalled'}
        new = async_delegation._get_executor(2)
        assert new is not pool
        assert new.submit(lambda: 'resized').result(timeout=2) == 'resized'
        async_delegation._records.clear()
        busy = json.loads((await a._handle_health_detailed(request)).text)
        assert busy['native_maintenance']['work']['delegation_executor_threads'] == 1
        assert not require_native_readiness(busy, expected_pid=os.getpid(), expected_start_ticks=ev['start_ticks'])
    finally:
        stop.set()
        running.result(timeout=2)
        pool.shutdown(wait=True)
    idle = json.loads((await a._handle_health_detailed(request)).text)
    assert require_native_readiness(idle, expected_pid=os.getpid(), expected_start_ticks=ev['start_ticks']), idle
    new.shutdown(wait=True)
    assert ev['notifications']['backlog'] == ev['notifications']['durable_retained'] == 78
    assert process_registry.completion_queue.qsize() == 78
    with sqlite3.connect(async_delegation._db_path()) as db:
        assert db.execute("SELECT count(*) FROM async_delegations WHERE delivery_state='pending'").fetchone()[0] == 78
    caps = json.loads((await a._handle_capabilities(request)).text)
    assert caps['mobile_native_maintenance']['version'] == 1
    assert caps['mobile_run_controls']['version'] == 1
    a._pending_agent_requests = 1
    ev = json.loads((await a._handle_health_detailed(request)).text)
    assert not require_native_readiness(ev, expected_pid=os.getpid(), expected_start_ticks=ev['native_maintenance']['start_ticks'])
asyncio.run(check())
print('native composition: 78 durable entries retained; pending admission blocks; auth enforced')
'''
    python = sys.executable if interpreter == 'project' else '/usr/local/lib/hermes-agent/venv/bin/python'
    result = subprocess.run([python, '-c', script], capture_output=True, text=True, timeout=60,
                            env={**os.environ, 'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path),
                                 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert '78 durable entries retained' in result.stdout
