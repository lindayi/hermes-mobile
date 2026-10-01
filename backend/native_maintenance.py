"""Read-only maintenance evidence for the dedicated native owner listener.

Not a drain API. Callers must separately enforce exclusive bridge ingress and
an owned admission gate. Never import native registries from an external probe.
"""
import asyncio
import gc
import json
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from aiohttp import web


POLICY = 'retain-durable-no-drain-no-replay'
# Installed dispatch/_run_single_child terminal outcomes; these classify records,
# never worker liveness (which is independently tracked below).
TERMINAL = {'completed', 'failed', 'error', 'interrupted', 'timeout',
            'cancelled', 'stalled', 'unknown'}


_tracker_install_lock = threading.Lock()


class _DelegationWorkTracker:
    """One process-local counter shared by every native daemon pool generation."""

    def __init__(self, executor_class):
        self.lock = threading.Lock()
        self.pending = 0
        self.executor_class = executor_class
        # Startup-only inspection. Even an unused preexisting pool may have a
        # previously bound submit; no retrospective claim of coverage is safe.
        from concurrent.futures.thread import _worker
        self.known = not (
            any(issubclass(type(obj), executor_class) for obj in gc.get_objects())
            or any(t.is_alive() and (
                t.name.startswith('async-delegate')
                # Abandoned pools can be collected while their unnamed daemon
                # workers still run. The installed pool uses stdlib _worker.
                or (t.daemon and getattr(t, '_target', None) is _worker))
                for t in threading.enumerate()))
        original_submit = executor_class.submit
        self.known = self.known and original_submit is ThreadPoolExecutor.submit

        def submit(executor, fn, /, *args, **kwargs):
            # Nested child timeout pools are unnamed; all daemon-pool work in
            # this dedicated process must retain reservations until exit.
            with self.lock:
                self.pending += 1
            released = False
            started = False

            def release(*, pre_start_only=False):
                nonlocal released
                with self.lock:
                    if not released and not (pre_start_only and started):
                        released = True
                        self.pending -= 1

            def run():
                nonlocal started
                with self.lock:
                    # Stdlib submit enqueues before starting a thread. If
                    # thread creation rejected this submission, its leftover
                    # queue entry must not later execute uncounted user work.
                    if released:
                        return None
                    started = True
                try:
                    return fn(*args, **kwargs)
                finally:
                    release()
            try:
                future = original_submit(executor, run)
            except BaseException:
                release(pre_start_only=True)
                raise
            # A cancelled queued Future never enters run's finally block.
            # Do not equate done/exception/terminal records with worker exit.
            future.add_done_callback(
                lambda done: release(pre_start_only=True) if done.cancelled() else None)
            return future

        self.submit_wrapper = submit
        if original_submit is ThreadPoolExecutor.submit:
            executor_class.submit = submit

    def count(self):
        with self.lock:
            if not self.known or self.executor_class.submit is not self.submit_wrapper:
                self.known = False
                raise ValueError('Delegation tracking coverage is unknown')
            return self.pending


def install_delegation_tracking():
    """Install once, before constructing any agent in the dedicated process.

    Only the dependency-free daemon pool module is imported, never registries.
    All daemon-pool prefixes are covered; stdlib executors remain unchanged.
    """
    from tools.daemon_pool import DaemonThreadPoolExecutor

    with _tracker_install_lock:
        tracker = DaemonThreadPoolExecutor.__dict__.get('_native_maintenance_tracker')
        if tracker is None:
            tracker = _DelegationWorkTracker(DaemonThreadPoolExecutor)
            DaemonThreadPoolExecutor._native_maintenance_tracker = tracker
        return tracker


def notification_evidence(completion_queue, state_db):
    """Match queued payloads to durable records without consuming or updating."""
    with completion_queue.mutex:
        events = [dict(event) if isinstance(event, dict) else event
                  for event in completion_queue.queue]
    retained = 0
    if events:
        uri = Path(state_db).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            for event in events:
                if not isinstance(event, dict) or event.get('type') != 'async_delegation':
                    continue
                payload = {k: v for k, v in event.items() if k != 'restored'}
                row = db.execute(
                    'SELECT state,event_json,result_json,delivery_state FROM async_delegations WHERE delegation_id=?',
                    (event.get('delegation_id'),)).fetchone()
                if (row and row[0] in TERMINAL and row[3] in {'pending', 'delivered', 'dropped'}
                        and json.dumps(json.loads(row[1]), sort_keys=True, allow_nan=False)
                        == json.dumps(payload, sort_keys=True, allow_nan=False)
                        and isinstance(json.loads(row[2]), dict)):
                    retained += 1
    return {'status': 'ok', 'backlog': len(events), 'durable_retained': retained,
            'unpreserved': len(events) - retained, 'policy': POLICY}


def maintenance_snapshot(adapter, registry, delegations, state_db):
    """Observe process-local sources; no gateway-global busy state is consulted."""
    identity = {'version': 1, 'scope': 'dedicated-listener', 'pid': os.getpid(),
                'start_ticks': int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])}
    try:
        with adapter._maintenance_lock:
            if (any(not isinstance(getattr(adapter, key), dict) for key in
                    ('_active_run_tasks', '_run_statuses', '_active_run_agents', '_shutdown_interruptible_agents'))
                    or not isinstance(adapter._stopping_run_ids, set)):
                raise ValueError('Invalid native containers')
            done = [t.done() for t in list(adapter._active_run_tasks.values())]
            statuses = [r['status'] for r in list(adapter._run_statuses.values())]
            if (any(type(v) is not bool for v in done)
                    or any(s not in {'completed', 'failed', 'cancelled', 'queued', 'running',
                                     'stopping', 'waiting_for_approval'} for s in statuses)
                    or type(adapter._maintenance_uncertain) is not bool):
                raise ValueError('Unrecognized native work state')
            work = {
                'pending_admissions': adapter._pending_agent_requests,
                'inflight_agent_calls': adapter._inflight_agent_runs,
                'active_run_tasks': sum(not v for v in done),
                'nonterminal_runs': sum(s not in {'completed', 'failed', 'cancelled'} for s in statuses),
                'active_run_agents': len(adapter._active_run_agents),
                'shutdown_agents': len(adapter._shutdown_interruptible_agents),
                'stopping_runs': len(adapter._stopping_run_ids),
                'agent_workers': adapter._maintenance_workers,
                # Optional owner deletion adapter: reservation lasts until the
                # off-loop worker actually exits, even after HTTP cancellation.
                'session_deletion_workers': getattr(adapter, '_session_deletion_workers', 0),
                'cancellation_uncertain': int(adapter._maintenance_uncertain),
            }
        with registry._lock:
            if not isinstance(registry._running, dict):
                raise ValueError('Invalid process registry')
            work['live_subprocesses'] = len(registry._running)
        with delegations._records_lock:
            statuses = [r['status'] for r in delegations._records.values()]
            if any(s not in TERMINAL | {'running', 'stalling', 'finalizing'} for s in statuses):
                raise ValueError('Unrecognized delegation state')
            work['active_delegations'] = sum(s in {'running', 'stalling', 'finalizing'} for s in statuses)
        # Wire-compatible name; units are outstanding submitted tasks, NOT
        # live threads. Includes queued work and workers with pruned records.
        work['delegation_executor_threads'] = adapter._maintenance_delegations.count()
        notifications = notification_evidence(registry.completion_queue, state_db)
        outbox_probe = getattr(adapter, 'notification_evidence', None)
        if outbox_probe is not None:
            outbox = outbox_probe()
            keys = ('pending', 'quarantined', 'delivered', 'foreign', 'foreign_retained', 'conflicts', 'active_workers', 'shutdown_publications')
            if (not isinstance(outbox, dict) or any(type(outbox[k]) is not int or outbox[k] < 0 for k in keys)
                    or outbox['conflicts'] or outbox['foreign'] != outbox['foreign_retained']):
                raise ValueError('Unknown durable notification state')
            work['notification_workers'] = outbox['active_workers']
            # Sticky uncertainty after a producer used a retired hook. A fresh
            # process, not an in-process capture replacement, clears this fence.
            work['notification_lifecycle_uncertain'] = int(outbox['shutdown_publications'] > 0)
            # A durably captured result is restart-safe, but not yet delivered.
            # Include pending/quarantined web work in deletion's zero-backlog gate.
            # Foreign copies remain durably retained for their original consumer;
            # they are not claimed/acknowledged as web-owned results.
            pending = outbox['pending'] + outbox['quarantined']
            notifications.update(backlog=notifications['backlog'] + pending,
                                 durable_retained=notifications['durable_retained'] + pending,
                                 web_pending=outbox['pending'], quarantined=outbox['quarantined'],
                                 foreign_retained=outbox['foreign_retained'], web_delivered=outbox['delivered'],
                                 shutdown_publications=outbox['shutdown_publications'])
        if any(type(v) is not int or v < 0 for v in work.values()):
            raise ValueError('Invalid work count')
        return {**identity, 'status': 'ok', 'work': work, 'notifications': notifications}
    except Exception:
        # Do not expose payloads, paths, credentials or exception text in health.
        return {**identity, 'status': 'unknown', 'reason': 'native_evidence_unavailable'}


def _local_sources():
    # Looking up already-loaded modules avoids registry initialization (which
    # restores and can mutate the shared durable delegation DB).
    registry_module = sys.modules.get('tools.process_registry')
    return (getattr(registry_module, 'process_registry', None),
            sys.modules.get('tools.async_delegation'),
            Path(os.environ['HERMES_HOME']) / 'state.db')


def maintenance_adapter(base, *, sources=_local_sources):
    """Compose only in the dedicated listener, outside run_controls_adapter."""
    class MaintenanceAdapter(base):
        def __init__(self, *args, **kwargs):
            self._maintenance_delegations = install_delegation_tracking()
            self._maintenance_lock = threading.RLock()
            self._maintenance_workers = 0
            self._maintenance_uncertain = False
            super().__init__(*args, **kwargs)

        def _create_agent(self, *args, **kwargs):
            with self._maintenance_lock:
                self._maintenance_workers += 1
            try:
                agent = super()._create_agent(*args, **kwargs)
            except BaseException:
                with self._maintenance_lock:
                    self._maintenance_workers -= 1
                raise
            original = agent.run_conversation
            reserved = True
            def run(*a, **kw):
                nonlocal reserved
                with self._maintenance_lock:
                    if reserved:
                        reserved = False
                    else:
                        self._maintenance_workers += 1
                try:
                    return original(*a, **kw)
                finally:
                    with self._maintenance_lock:
                        self._maintenance_workers -= 1
            agent.run_conversation = run
            return agent

        def _set_run_status(self, run_id, status, **fields):
            with self._maintenance_lock:
                if status == 'cancelled' and self._maintenance_workers:
                    self._maintenance_uncertain = True
            return super()._set_run_status(run_id, status, **fields)

        async def _run_agent(self, *args, **kwargs):
            try:
                return await super()._run_agent(*args, **kwargs)
            except asyncio.CancelledError:
                with self._maintenance_lock:
                    self._maintenance_uncertain = True
                raise

        async def _handle_health_detailed(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            response = await super()._handle_health_detailed(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            try:
                evidence = maintenance_snapshot(self, *sources())
            except Exception:
                evidence = {'version': 1, 'scope': 'dedicated-listener', 'pid': os.getpid(),
                            'status': 'unknown', 'reason': 'native_evidence_unavailable'}
            data['native_maintenance'] = evidence
            return web.json_response(data)

        async def _handle_capabilities(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            data['mobile_native_maintenance'] = {
                'version': 1, 'scope': 'dedicated-listener', 'atomic_drain': False}
            return web.json_response(data)
    return MaintenanceAdapter
