"""Process-local compatibility for the dedicated mobile owner listener only."""
import json
import threading
import time
from contextlib import contextmanager

from aiohttp import web


def run_controls_adapter(base):
    class RunControlsAdapter(base):
        def __init__(self, *args, **kwargs):
            self._controls_lock = threading.RLock()
            self._controls = {}
            super().__init__(*args, **kwargs)

        def _state(self, run_id):
            return self._controls.setdefault(run_id, {'receipts': {}, 'closed': False, 'pending': []})

        def _sweep_orphaned_runs_once(self, now=None):
            with self._controls_lock:
                super()._sweep_orphaned_runs_once(now)
                for run_id in list(self._controls):
                    if run_id not in self._run_statuses:
                        del self._controls[run_id]

        def _retain(self, state, text):
            pending = state.setdefault('pending', [])
            if isinstance(text, str) and text and text not in pending and text != '\n'.join(pending):
                pending.append(text)

        def _snapshot(self, run_id):
            state = self._state(run_id)
            fields = {'steer_receipts': [dict(v['receipt']) for v in state['receipts'].values()]}
            if state.get('pending'):
                fields['pending_steer'] = '\n'.join(state['pending'])
            return fields

        def _set_run_status(self, run_id, status, **fields):
            with self._controls_lock:
                state = self._controls.get(run_id)
                current = self._run_statuses.get(run_id, {})
                previous = current.get('status')
                terminal = {'completed', 'failed', 'cancelled'}
                if (previous in terminal and status != previous
                        or previous == 'stopping' and status not in terminal | {'stopping'}):
                    return current
                if state and status in {'completed', 'failed', 'cancelled', 'stopping'}:
                    state['closed'] = True
                    agent = self._active_run_agents.get(run_id)
                    if agent is not None:
                        self._retain(state, agent._drain_pending_steer() if hasattr(agent, '_drain_pending_steer') else None)
                    self._retain(state, fields.get('pending_steer'))
                    for item in state['receipts'].values():
                        if item['receipt']['status'] == 'accepted_unconfirmed':
                            item['receipt']['status'] = 'unknown'
                    fields.update(self._snapshot(run_id))
                    emit = state.get('emit')
                    if emit:
                        emit({'event': 'run.steer_receipts', 'run_id': run_id, **fields})
                return super()._set_run_status(run_id, status, **fields)

        def _make_run_event_callback(self, run_id, loop):
            native_callback = super()._make_run_event_callback(run_id, loop)
            with self._controls_lock:
                state = self._state(run_id)
                q = self._run_streams.get(run_id)
                state['queue'] = q
                state['approval_session'] = getattr(self, '_run_approval_sessions', {}).get(run_id)
            def owns_run():
                return (self._controls.get(run_id) is state
                        and self._run_streams.get(run_id) is q)
            def callback(*args, **kwargs):
                # Lock the native status READ as well as its eventual update.
                # Identity fences old workers without permanent tombstones.
                with self._controls_lock:
                    if not owns_run() or state['closed']:
                        return
                    return native_callback(*args, **kwargs)
            callback._mobile_run_id = run_id
            if q is not None:
                native_put = q.put_nowait
                def put_with_controls(event):
                    with self._controls_lock:
                        if not owns_run():
                            return
                        # Native terminal frames can precede status publication.
                        if isinstance(event, dict) and event.get('event') in {
                                'run.completed', 'run.failed', 'run.cancelled'}:
                            state['closed'] = True
                            agent = self._active_run_agents.get(run_id)
                            if agent is not None and hasattr(agent, '_drain_pending_steer'):
                                self._retain(state, agent._drain_pending_steer())
                            self._retain(state, event.get('pending_steer'))
                            for item in state['receipts'].values():
                                if item['receipt']['status'] == 'accepted_unconfirmed':
                                    item['receipt']['status'] = 'unknown'
                            event = {**event, **self._snapshot(run_id)}
                        native_put(event)
                q.put_nowait = put_with_controls
            def emit(event):
                def put():
                    if q is not None:
                        q.put_nowait(event)
                try:
                    loop.call_soon_threadsafe(put)
                except RuntimeError:
                    pass  # Closed loop; pollable receipt remains authoritative.
            state['emit'] = emit
            return callback

        def _create_agent(self, *args, **kwargs):
            agent = super()._create_agent(*args, **kwargs)
            run_id = getattr(kwargs.get('tool_progress_callback'), '_mobile_run_id', None)
            if run_id is None:
                return agent  # Other native entry points retain their ordinary lifecycle.
            state = self._state(run_id)
            state['agent'] = agent
            original_steer = agent.steer
            original_run = agent.run_conversation
            original_clear = agent.clear_interrupt
            def steer(text):
                with self._controls_lock:
                    return False if state['closed'] else original_steer(text)
            def clear_interrupt(*a, **kw):
                with self._controls_lock:
                    with agent._pending_steer_lock:
                        pending = agent._pending_steer
                    cleared = original_clear(*a, **kw)
                    if cleared:
                        self._retain(state, pending)
                    return cleared
            def run(*a, **kw):
                result = None
                try:
                    result = original_run(*a, **kw)
                    return result
                finally:
                    with self._controls_lock:
                        state['closed'] = True
                        if isinstance(result, dict):
                            self._retain(state, result.get('pending_steer'))
                        self._retain(state, agent._drain_pending_steer())
                        if isinstance(result, dict) and state.get('pending'):
                            result['pending_steer'] = '\n'.join(state['pending'])
            agent.steer = steer
            agent.clear_interrupt = clear_interrupt
            agent.run_conversation = run
            def commentary(text, already_streamed=False):
                # This is the native safe PUBLIC interim seam, never reasoning.
                if (already_streamed or not getattr(agent, 'show_commentary', True)
                        or not isinstance(text, str) or not text.strip()):
                    return
                with self._controls_lock:
                    if not state['closed']:
                        state['emit']({'event': 'message.commentary', 'run_id': run_id,
                                       'timestamp': time.time(), 'text': text,
                                       'phase': 'commentary', 'channel': 'commentary'})
            agent.interim_assistant_callback = commentary
            return agent

        async def _handle_steer_run(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            body, error = await self._read_json_body(request)
            if error is not None:
                return error
            if (not isinstance(body, dict) or set(body) != {'input', 'idempotency_key'}
                    or not isinstance(body.get('input'), str)
                    or not 0 < len(body['input']) <= 32768 or not body['input'].strip()
                    or not isinstance(body.get('idempotency_key'), str)
                    or not 0 < len(body['idempotency_key']) <= 128
                    or not body['idempotency_key'].strip()):
                return web.json_response({'error': {'code': 'invalid_steer_input'}}, status=400)
            run_id, key, text = request.match_info['run_id'], body['idempotency_key'], body['input']
            with self._controls_lock, self._approval_snapshot(run_id) as pending:
                if run_id not in self._run_statuses:
                    return web.json_response({'error': {'code': 'run_not_found'}}, status=404)
                state = self._controls.setdefault(run_id, {'receipts': {}, 'closed': False})
                previous = state['receipts'].get(key)
                if previous:
                    if previous['input'] != text:
                        return web.json_response({'error': {'code': 'idempotency_conflict'}}, status=409)
                    return web.json_response(previous['receipt'])
                if len(state['receipts']) >= 256:
                    return web.json_response({'error': {'code': 'steer_capacity'}}, status=429)
                agent = self._active_run_agents.get(run_id)
                eligible = (self._run_statuses[run_id].get('status') == 'running'
                            and (pending == [] or state.get('approval_session') is None)
                            and run_id not in self._stopping_run_ids and not state['closed']
                            and callable(getattr(agent, 'steer', None)))
                receipt = {'object': 'hermes.run.steer', 'run_id': run_id,
                           'steer_id': key, 'status': 'not_delivered', 'accepted': False}
                state['receipts'][key] = {'input': text, 'receipt': receipt}
                if eligible:
                    # Record before calling: an exception may happen after acceptance.
                    receipt['status'] = 'unknown'
                    try:
                        receipt['accepted'] = bool(agent.steer(text))
                        receipt['status'] = 'accepted_unconfirmed' if receipt['accepted'] else 'not_delivered'
                    except Exception:
                        pass
                self._run_statuses[run_id]['steer_receipts'] = [
                    dict(item['receipt']) for item in state['receipts'].values()]
                return web.json_response(receipt, status=200 if eligible else 409)

        @contextmanager
        def _approval_snapshot(self, run_id):
            """Caller holds controls; retain registry lock through publication/admission.

            Native inserts/drops entries under approval._lock, but calls notify
            AFTER releasing it. Order is controls -> approval; never call the
            public listing API under its non-reentrant lock.
            """
            pending, locked, lock = None, False, None
            state = self._controls.get(run_id)
            current = self._run_statuses.get(run_id)
            task = getattr(self, '_active_run_tasks', {}).get(run_id)
            agent = self._active_run_agents.get(run_id)
            session = getattr(self, '_run_approval_sessions', {}).get(run_id)
            try:
                if not isinstance(session, str) or not session.strip():
                    raise ValueError('Missing approval identity')
                from tools import approval
                listed = approval.list_gateway_approvals(session)
                lock = approval._lock
                lock.acquire()
                locked = True
                queues = approval._gateway_queues
                if not isinstance(queues, dict):
                    raise ValueError('Unavailable approval registry')
                entries = queues.get(session, [])
                if not isinstance(entries, list):
                    raise ValueError('Malformed approval queue')
                fresh = [dict(entry.data) for entry in entries]
                if (not isinstance(listed, list) or listed != fresh or len(fresh) > 1000
                        or any(not isinstance(item.get('request_id'), str)
                               or not 0 < len(item['request_id']) <= 200 for item in fresh)
                        or self._run_approval_sessions.get(run_id) != session):
                    raise ValueError('Changed or malformed approval snapshot')
                pending = [{'run_id': run_id, 'request_id': item['request_id']} for item in fresh]
                if (not pending and state is not None and current is not None
                        and self._controls.get(run_id) is state
                        and self._run_statuses.get(run_id) is current
                        and current.get('status') == 'waiting_for_approval'
                        and current.get('run_id') == run_id
                        and not state['closed'] and run_id not in self._stopping_run_ids
                        and state.get('queue') is not None
                        and self._run_streams.get(run_id) is state['queue']
                        and session == run_id == state.get('approval_session')
                        and agent is not None and state.get('agent') is agent
                        and self._active_run_agents.get(run_id) is agent
                        and task is not None and self._active_run_tasks.get(run_id) is task
                        and task.done() is False):
                    self._set_run_status(run_id, 'running')
            except Exception:
                pending = None  # Missing/changed evidence never authorizes recovery.
            try:
                yield pending
            finally:
                if locked:
                    lock.release()

        async def _handle_get_run(self, request):
            response = await super()._handle_get_run(request)
            if response.status != 200:
                return response
            run_id = request.match_info['run_id']
            with self._controls_lock, self._approval_snapshot(run_id) as pending:
                data = dict(self._run_statuses.get(run_id, json.loads(response.text)))
                if pending is not None:
                    data['pending_approvals'] = pending
                return web.json_response(data)

        async def _handle_capabilities(self, request):
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            data['mobile_run_controls'] = {
                'version': 1, 'steering': True, 'live_commentary': True}
            data['mobile_run_controls_v1'] = True
            return web.json_response(data)
    return RunControlsAdapter
