"""Parent-owned run bridge. Upstream compatibility remains fail-closed."""
import asyncio
import inspect
import json
import time
import uuid
import sqlite3
from contextlib import closing

from .runs import NATIVE_RUN_LOST_ERROR, RunConflict

from .hermes_client import IntegrationUnavailable, NativeRunNotFound, NativeClarificationRejected


class ClarificationNotSent(IntegrationUnavailable):
    """Clarification capability was unavailable before claiming or sending an answer."""


class Orchestrator:
    def __init__(self, journal, gateway, catalog, *, history_loader=None, attachments=None,
                 profile="default", run_timeout=3600):
        self.journal, self.gateway, self.catalog = journal, gateway, catalog
        self.attachments = attachments
        self.profile = profile
        self.history_loader = history_loader
        self.approval_notifier = None
        self._tasks = {}
        self._dispatching = set()
        from weakref import WeakValueDictionary
        self._control_locks = WeakValueDictionary()
        self._clarification_locks = WeakValueDictionary()
        self._closed = False
        self.recovery_interval = 5.0
        self.observation_timeout = 10.0
        self._observations = {}
        self._recovery_task = None
        self._observation_locks = WeakValueDictionary()
        self.run_timeout = max(0.001, min(float(run_timeout), 3600))
        from .steering import SteeringJournal
        self.steering = SteeringJournal(journal)
        from .clarifications import ClarificationJournal
        self.clarifications = ClarificationJournal(journal)
        self.clarifications.recover()
        with closing(journal.connect()) as c, c:
            c.execute('''CREATE TABLE IF NOT EXISTS orchestration_approvals(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, request_id TEXT NOT NULL,
                action TEXT NOT NULL, status TEXT NOT NULL, expires_at REAL NOT NULL,
                UNIQUE(run_id, request_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS approval_notification_intents(
                approval_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending')''')
            # Migrate only on runtime startup after deployment drain/restart.
            # Controllers also open RunJournal before closing admission, so its
            # constructor must not relax the still-running old runtime's policy.
            # BEGIN IMMEDIATE admission now enforces capacity/canonical identity.
            c.execute('BEGIN IMMEDIATE')
            c.execute('DROP INDEX IF EXISTS orchestration_one_active')

    def claim_deletion(self, user, session_id):
        # No await between inspecting local workers and the durable writer claim.
        busy = set(self._dispatching)
        busy.update(run['id'] for task, (_, run) in self._tasks.items() if not task.done())
        busy.update(rid for rid, lock in self._control_locks.items() if lock.locked())
        busy.update(key[2] for key, lock in self._clarification_locks.items() if lock.locked())
        busy.update(rid for rid, lock in self._observation_locks.items() if lock.locked())
        return self.journal.claim_deletion(user['id'], user['profile'], session_id, busy_run_ids=busy)

    async def submit(self, user, body):
        limits = {'session_id': 200, 'input': 100000, 'idempotency_key': 128}
        if (not isinstance(body, dict) or set(body) - {'selection', 'attachments'} != set(limits)
                or any(not isinstance(body[k], str) or not body[k].strip() or len(body[k]) > limit for k, limit in limits.items())):
            raise ValueError('Invalid run request')
        attachment_ids = body.get('attachments', [])
        if (not isinstance(attachment_ids, list) or len(attachment_ids) > 4
                or any(not isinstance(value, str) or len(value) != 32
                       or any(char not in '0123456789abcdef' for char in value)
                       for value in attachment_ids)
                or len(set(attachment_ids)) != len(attachment_ids)):
            raise ValueError('Invalid photo attachment references')
        selection=body.get('selection')
        if selection is not None:
            if (not isinstance(selection,dict) or set(selection) != {'model','provider'}
                    or any(not isinstance(v,str) or not v or len(v)>200 for v in selection.values())):
                raise ValueError('Invalid model selection; reasoning supports Default only')
            selection=dict(selection)
        if self._closed:
            raise IntegrationUnavailable('Orchestrator is closed')
        if user['profile'] != self.profile or self.profile not in self.catalog.profiles:
            raise IntegrationUnavailable('No gateway is bound to this profile')
        self.journal.require_session(user['id'], user['profile'], body['session_id'])
        self.gateway.require_execution()
        with closing(self.journal.connect()) as c:
            existing = c.execute('SELECT * FROM runs WHERE user_id=? AND idempotency_key=?', (user['id'], body['idempotency_key'])).fetchone()
        if existing:
            if (existing['profile'], existing['session_id'], existing['input']) != (user['profile'], body['session_id'], body['input']):
                raise RunConflict('Idempotency key already used for another request')
            stored=self.journal.get(user['id'],existing['id'])
            if stored.get('selection')!=selection:
                raise RunConflict('Idempotency key already used for another selection')
            if stored.get('attachment_ids', []) != attachment_ids:
                raise RunConflict('Idempotency key already used for other photo attachments')
            return stored
        if selection is not None:
            if not hasattr(self,'model_options'):
                raise IntegrationUnavailable('Model controls are unavailable')
            choices=await self.model_options(body['session_id'],user)
            if not choices.get('available') or not any(m['id']==selection['model'] and m['provider']==selection['provider'] for m in choices['models']):
                raise ValueError('Model selection is unavailable')
        if self.history_loader is None:
            raise IntegrationUnavailable('Complete native history loader is required')
        context = self.history_loader(user['profile'], body['session_id'])
        if inspect.isawaitable(context):
            context = await context
        if self._closed:
            raise IntegrationUnavailable('Orchestrator is closed')
        self.gateway.require_execution()
        if (not isinstance(context, dict) or context.get('complete') is not True
                or context.get('profile') != user['profile']
                or context.get('session_id') != body['session_id']
                or not isinstance(context.get('history'), list)):
            raise IntegrationUnavailable('Native history is incomplete or mismatched')
        canonical_id = context.get('canonical_session_id', body['session_id'])
        if not isinstance(canonical_id, str) or not canonical_id.strip() or len(canonical_id) > 200:
            raise IntegrationUnavailable('Native canonical session identity is invalid')
        for message in context['history']:
            if (not isinstance(message, dict) or 'role' not in message or 'content' not in message
                    or (message['role'] == 'tool' and not message.get('tool_call_id'))):
                raise IntegrationUnavailable('Native tool context is incomplete')
        if attachment_ids:
            if self.attachments is None or not hasattr(self.gateway, 'validate_run_size'):
                raise IntegrationUnavailable('Native photo request budgeting is unavailable.')
            image_sizes = await asyncio.to_thread(
                self.attachments.run_image_sizes, user['id'], user['profile'],
                body['session_id'], attachment_ids)
            self.gateway.validate_run_size(
                body['session_id'], body['input'], context['history'], attachment_ids,
                image_sizes, **(selection or {}))
        try:
            run, created = self.journal.submit(user['id'], user['profile'], body['session_id'], body['input'], body['idempotency_key'],
                history_anchor=lambda: self.catalog.history_anchor(user['profile'], body['session_id'], canonical_id), selection=selection,
                conversation_roots=lambda ids: self.catalog.conversation_roots(user['profile'], ids),
                attachment_ids=attachment_ids, attachment_store=self.attachments)
        except sqlite3.IntegrityError as exc:
            raise RunConflict('A local run is active or unresolved') from exc
        if created:
            self.journal.event(user['id'], run['id'], 'status', {'status': 'queued'})
            native_run = dict(run, session_id=context.get('canonical_session_id', run['session_id']),
                              attachment_session_id=run['session_id'])
            task = asyncio.create_task(self._execute(user, native_run, context['history']))
            self._tasks[task] = (dict(user), run)
            task.add_done_callback(lambda done: self._tasks.pop(done, None))
        return run

    def get(self, user, rid):
        run = self.journal.get(user['id'], rid)
        if run['profile'] != user['profile'] or user['profile'] != self.profile:
            raise KeyError(rid)
        return run

    async def clarifications_for_run(self, user, rid):
        async with self._clarification_lock(user, rid):
            return await self._clarifications_for_run_locked(user, rid)

    async def _clarifications_for_run_locked(self, user, rid):
        run = self.get(user, rid)
        if user.get('role') != 'owner' or user.get('profile') != 'default':
            raise KeyError(rid)
        result = await self.clarifications.rehydrate(
            user, rid, self.gateway, run,
            validate_snapshot=lambda snapshot: (
                self._can_observe(user) and self._matches(run, snapshot)
                and self.get(user, rid)['upstream_id'] == run['upstream_id']))
        native_status = result.get('native_status')
        if native_status == 'unknown':
            self._native_run_lost(user, run)
        elif native_status == 'waiting_for_clarification':
            self._clarification_status(user, run, resume=False)
        elif result.get('native_snapshot') is not None:
            await self._reconcile_locked(user, rid, run=run, result=result['native_snapshot'])
        current = self.get(user, rid)
        return {
            'run_id': current['id'], 'status': current['status'],
            'available': (result['available']
                          and current['status'] not in ('completed', 'failed', 'cancelled')),
            'items': result['items'],
        }

    async def answer_clarification(self, user, rid, question_id, body):
        async with self._clarification_lock(user, rid):
            return await self._answer_clarification(user, rid, question_id, body)

    async def _answer_clarification(self, user, rid, question_id, body):
        run = self.get(user, rid)
        if user.get('role') != 'owner' or user.get('profile') != 'default':
            raise KeyError(rid)
        records = self.clarifications.list(user, rid)
        record = next((item for item in records if item['question_id'] == question_id), None)
        if record is None:
            raise KeyError(question_id)
        if not self.clarifications.validate_answer(record, body):
            raise ValueError('Invalid clarification answer')
        if record['status'] in ('answered', 'sending'):
            receipt, _ = self.clarifications.claim(user, run, question_id, body)
            return receipt
        if run['status'] != 'waiting_for_clarification' or not run['upstream_id']:
            raise RunConflict('Clarification is stale')
        if not hasattr(self.gateway, 'require_clarifications'):
            raise ClarificationNotSent('Native clarification controls are unavailable')
        try:
            await self.gateway.require_clarifications()
        except IntegrationUnavailable as exc:
            raise ClarificationNotSent(
                'Native clarification controls are unavailable') from exc
        claimed, fresh = self.clarifications.claim(user, run, question_id, body)
        if not fresh:
            return claimed
        try:
            async with asyncio.timeout(30):
                await self.gateway.answer_clarification(
                    run['upstream_id'], question_id, body['answer'], body['other'])
        except NativeClarificationRejected as exc:
            self.clarifications.finish(user, rid, question_id, 'unknown', rejected=True)
            await self._clarifications_for_run_locked(user, rid)
            raise RunConflict('Native clarification answer was rejected') from exc
        except (Exception, asyncio.CancelledError) as exc:
            result = self.clarifications.finish(user, rid, question_id, 'unknown')
            if isinstance(exc, asyncio.CancelledError):
                raise
            return result
        result = self.clarifications.finish(user, rid, question_id, 'answered')
        self._clarification_status(user, run, resume=True)
        return result

    def _native_run_lost(self, user, run):
        current = self.get(user, run['id'])
        if (not self._can_observe(user)
                or any(current[k] != run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at'))
                or current['status'] in ('stopping', 'completed', 'failed', 'cancelled')
                or current['error'] == NATIVE_RUN_LOST_ERROR):
            return
        with closing(self.journal.connect()) as connection:
            if connection.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?',
                                  (run['id'],)).fetchone():
                return
        self.journal.finish(user['id'], run['id'], 'unknown',
                            error=NATIVE_RUN_LOST_ERROR,
                            expected={k: run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')})

    def _clarification_status(self, user, run, *, resume):
        if run.get('error') == NATIVE_RUN_LOST_ERROR:
            return
        items = self.clarifications.list(user, run['id'])
        latest = max((item['created_at'] for item in items), default=None)
        items = [item for item in items if item['created_at'] == latest]
        if any(item['status'] in ('pending', 'sending') for item in items):
            status = 'waiting_for_clarification'
        elif resume and not any(item['status'] == 'unknown' for item in items):
            status = 'running'
        else:
            return
        if self.get(user, run['id'])['status'] == status:
            return
        self.journal.set_active_status(user['id'], run['id'], status, upstream_id=run['upstream_id'])

    async def refresh(self, user, rid):
        self.get(user, rid)  # Authorize before consulting shared observation state.
        lock = self._observation_locks.get(rid)
        if lock is None:
            lock = self._observation_locks[rid] = asyncio.Lock()
        async with lock:
            run = self.get(user, rid)
            if (not self._closed and run['upstream_id']
                    and run['status'] not in ('completed', 'failed', 'cancelled')
                    and time.monotonic() >= self._observations.get(rid, 0)):
                try:
                    await self._reconcile(user, rid)
                finally:
                    self._observations[rid] = time.monotonic() + self.recovery_interval
            return self.get(user, rid)

    def start_recovery(self):
        if not self._closed and self._recovery_task is None:
            self._recovery_task = asyncio.create_task(self._recover_runs())

    async def _recover_runs(self):
        while not self._closed:
            try:
                with closing(self.journal.connect()) as c:
                    rows = c.execute("""SELECT id,user_id,profile FROM runs WHERE profile=?
                        AND upstream_id IS NOT NULL AND status IN
                        ('queued','running','stopping','waiting_for_approval',
                         'waiting_for_clarification','unknown') LIMIT 1000""",
                        (self.profile,)).fetchall()
                for row in rows:
                    # Live streams own their observer; disconnected ones poll themselves.
                    if any(owned['id'] == row['id'] for _, owned in self._tasks.values()):
                        continue
                    await self.refresh({'id': row['user_id'], 'profile': row['profile']}, row['id'])
            except Exception:
                pass  # Retry on a paced tick; never expose transport details.
            await asyncio.sleep(self.recovery_interval)

    async def controls(self, user, rid):
        run = self.get(user, rid)
        eligible = False
        if run['status'] == 'running' and run['upstream_id'] and hasattr(self.gateway, 'require_steering'):
            try:
                await self.gateway.require_steering()
                eligible = True
            except IntegrationUnavailable:
                pass
        if run['upstream_id'] and (eligible or self.steering.attempts(user, rid)):
            try:
                from urllib.parse import quote
                self.gateway.require_execution()
                async with asyncio.timeout(30):
                    result = await self.gateway.request('GET', '/v1/runs/' + quote(run['upstream_id'], safe=''))
                self.steering.observe(user, run, result)
                if not isinstance(result, dict) or result.get('run_id') != run['upstream_id'] or result.get('status') != 'running':
                    eligible = False
            except Exception:
                eligible = False
        # A capability/status await may race a local approval or terminal event.
        eligible = bool(eligible and not self._closed and self.get(user, rid)['status'] == 'running')
        return dict(steering=eligible, attempts=self.steering.attempts(user, rid),
                    **self.steering.evidence(user, rid))

    def _control_lock(self, user, rid):
        self.get(user, rid)
        lock = self._control_locks.get(rid)
        if lock is None:
            lock = self._control_locks[rid] = asyncio.Lock()
        return lock

    def _clarification_lock(self, user, rid):
        run = self.get(user, rid)
        key = (user['id'], user['profile'], rid, run['upstream_id'])
        lock = self._clarification_locks.get(key)
        if lock is None:
            lock = self._clarification_locks[key] = asyncio.Lock()
        return lock

    async def steer(self, user, rid, body):
        async with self._control_lock(user, rid):
            return await self._steer(user, rid, body)

    async def _steer(self, user, rid, body):
        limits = {'input': 32000, 'idempotency_key': 128}
        if (not isinstance(body, dict) or set(body) != set(limits)
                or any(not isinstance(body[k], str) or not body[k].strip() or len(body[k]) > limit
                       for k, limit in limits.items())):
            raise ValueError('Invalid steering request')
        run = self.get(user, rid)
        existing = self.steering.existing(user, run, body)
        if existing is not None:
            return existing
        if run['status'] != 'running' or not run['upstream_id']:
            raise RunConflict('Run is not accepting steering')
        if self._closed or not hasattr(self.gateway, 'require_steering'):
            raise IntegrationUnavailable('Native durable steering controls are unavailable')
        await self.gateway.require_steering()
        attempt, claimed = self.steering.claim(user, run, body)
        if not claimed:
            return attempt
        try:
            async with asyncio.timeout(30):
                reply = await self.gateway.steer(run['upstream_id'], body['input'], body['idempotency_key'])
        except (Exception, asyncio.CancelledError) as exc:
            result = self.steering.finish(attempt['id'], 'unknown')
            if isinstance(exc, asyncio.CancelledError):
                raise
            return result
        return self.steering.finish(attempt['id'], reply['status'], reply['steer_id'])

    async def stop(self, user, rid):
        async with self._control_lock(user, rid):
            return await self._stop(user, rid)

    async def _stop(self, user, rid):
        run = self.get(user, rid)
        if run['status'] in ('completed', 'failed', 'cancelled', 'stopping'):
            return run
        self.gateway.require_execution()
        if run['status'] == 'queued' and rid not in self._dispatching:
            for task, (_, owned) in list(self._tasks.items()):
                if owned['id'] == rid:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self.journal.finish(user['id'], rid, 'cancelled')
                    return self.get(user, rid)
        if not run['upstream_id']:
            if rid in self._dispatching:
                self.journal.finish(user['id'], rid, 'stopping')
                return self.get(user, rid)
            raise RunConflict('Upstream run ID is unresolved')
        self.journal.finish(user['id'], rid, 'stopping')
        self.clarifications.mark_pending_unknown(user, rid)
        try:
            async with asyncio.timeout(30):
                await self.gateway.stop(run['upstream_id'])
        except Exception:
            if self.get(user, rid)['status'] not in ('completed', 'failed', 'cancelled'):
                self.journal.finish(user['id'], rid, 'unknown', error='Stop outcome is unresolved')
        return self.get(user, rid)

    async def _execute(self, user, run, history):
        try:
            await self._stream(user, run, history)
            return
        except Exception as exc:
            from .attachments import AttachmentError
            if isinstance(exc, AttachmentError) and not self.get(user, run['id']).get('upstream_id'):
                self.journal.finish(user['id'], run['id'], 'failed', error=str(exc))
                return
            self.journal.finish(user['id'], run['id'], 'unknown',
                                error='Stream interrupted; observing original native run without replay')
        if not self.get(user, run['id'])['upstream_id']:
            await self._reconcile(user, run['id'])
            return
        # No proven native SSE cursor: observe snapshots, never replay the stream.
        while not self._closed:
            observed = await self.refresh(user, run['id'])
            if observed['status'] in ('completed', 'failed', 'cancelled'):
                return
            await asyncio.sleep(self.recovery_interval)

    async def _events(self, upstream_id):
        from contextlib import aclosing
        async with aclosing(self.gateway.events(upstream_id)) as events:
            while True:
                try:
                    async with asyncio.timeout(self.run_timeout):
                        event = await anext(events)
                except StopAsyncIteration:
                    return
                yield event

    async def _stream(self, user, run, history):
        self._dispatching.add(run['id'])
        try:
            kwargs={'history':history}
            if run.get('selection') is not None:
                kwargs.update(run['selection'])
            attachment_ids = run.get('attachment_ids', [])
            if attachment_ids:
                if self.attachments is None:
                    raise IntegrationUnavailable('Private photo storage is unavailable; no image was sent.')
                kwargs['attachments'] = await asyncio.to_thread(self.attachments.run_images,
                    user['id'], user['profile'], run.get('attachment_session_id', run['session_id']),
                    run['id'], attachment_ids)
                kwargs['attachment_ids'] = attachment_ids
            async with asyncio.timeout(self.run_timeout):
                upstream = await self.gateway.start(run['session_id'], run['input'], **kwargs)
        finally:
            self._dispatching.discard(run['id'])
        stopping = self.get(user, run['id'])['status'] == 'stopping'
        self.journal.set_upstream(user['id'], run['id'], upstream['run_id'])
        self.journal.event(user['id'], run['id'], 'status', {'status': 'running'})
        if stopping:
            await self.stop(user, run['id'])
        async for event in self._events(upstream['run_id']):
            if event['event'] == 'approval.request':
                if hasattr(self.gateway, 'require_action_approvals'):
                    try:
                        await self.gateway.require_action_approvals()
                    except IntegrationUnavailable:
                        pass  # Keep the action visible, but not executable.
                self._approval(user, run, event)
            elif event['event'] == 'approval.responded':
                if (event.get('run_id') != upstream['run_id']
                        or not isinstance(event.get('request_id'), str)
                        or event.get('choice') not in ('once', 'deny', 'session', 'always')
                        or type(event.get('resolved')) is not int or event['resolved'] != 1):
                    continue  # An unbound or mismatched event is not an acknowledgement.
                with closing(self.journal.connect()) as c, c:
                    changed = c.execute("""UPDATE orchestration_approvals SET status='resolved_external'
                        WHERE run_id=? AND request_id=? AND status IN ('pending','sending')""",
                        (run['id'], event['request_id'])).rowcount
                if changed:
                    self._resume_after_approval(user, run['id'])
            elif event['event'].startswith('tool.'):
                from backend.tool_presentation import normalize_tool_event
                self.journal.event(user['id'], run['id'], 'tool', normalize_tool_event(event))
            elif event['event'] in ('message.delta', 'message.commentary'):
                # Only public assistant text crosses this boundary. In particular,
                # reasoning.available is not a public commentary channel.
                if any(event.get(key) not in (None, '', 'final', 'commentary', 'assistant', 'output')
                       for key in ('channel', 'phase')):
                    continue
                commentary = (event['event'] == 'message.commentary'
                              or any(event.get(key) == 'commentary' for key in ('channel', 'phase')))
                text = event.get('delta', event.get('text'))
                if isinstance(text, str) and text:
                    self.journal.event(user['id'], run['id'], 'commentary' if commentary else 'delta', {'text': text})
            elif event['event'] == 'run.steer_receipts':
                self.steering.observe(user, self.get(user, run['id']), event)
            elif event['event'] == 'run.clarification':
                await self._observe_clarification_event(user, run['id'], event)
            elif event['event'] in ('run.completed', 'run.failed', 'run.cancelled'):
                await self._observe_terminal_event(user, run['id'], event)
                return
        raise IntegrationUnavailable('Upstream stream ended without a terminal event')

    async def _observe_clarification_event(self, user, rid, event):
        async with self._clarification_lock(user, rid):
            run = self.get(user, rid)
            if not self._can_observe(user) or not self._matches(run, event):
                return
            result = self.clarifications.event(user, run, event)
            if result:
                self._clarification_status(
                    user, run, resume=result['status'] in ('answered', 'expired'))

    async def _observe_terminal_event(self, user, rid, event):
        async with self._clarification_lock(user, rid):
            current = self.get(user, rid)
            self.gateway.require_execution()
            if not self._can_observe(user) or not self._matches(current, event):
                raise IntegrationUnavailable('Unbound native terminal event')
            self.steering.observe(user, current, event)
            self.clarifications.observe(user, current, event)
            self.journal.finish(user['id'], rid, event['event'].split('.')[1], output=event.get('output'),
                                expected={k: current[k] for k in ('profile', 'upstream_id')})

    def _matches(self, run, result):
        if (not isinstance(result, dict) or result.get('run_id') != run['upstream_id']
                or ('profile' in result and result['profile'] != self.profile)):
            return False
        with closing(self.journal.connect()) as c:
            anchor = c.execute('SELECT canonical_session_id FROM run_history_anchors WHERE run_id=?',
                               (run['id'],)).fetchone()
        session = anchor[0] if anchor else run['session_id']
        return 'session_id' not in result or result['session_id'] == session

    def _can_observe(self, user):
        validator = getattr(self, 'recovery_validator', None)
        return (not self._closed and user['profile'] == self.profile
                and self.profile in self.catalog.profiles
                and (validator is None or validator(user) is True))

    async def _reconcile(self, user, rid):
        async with self._clarification_lock(user, rid):
            await self._reconcile_locked(user, rid)

    async def _reconcile_locked(self, user, rid, *, run=None, result=None):
        run = run or self.get(user, rid)
        if (not self._can_observe(user) or run['status'] in ('completed', 'failed', 'cancelled')
                or run.get('error') == NATIVE_RUN_LOST_ERROR):
            return
        if run['upstream_id']:
            try:
                from urllib.parse import quote
                self.gateway.require_execution()
                if result is None:
                    async with asyncio.timeout(self.observation_timeout):
                        result = await self.gateway.request('GET', '/v1/runs/' + quote(run['upstream_id'], safe=''))
                self.gateway.require_execution()
                if (not self._can_observe(user)
                        or self.get(user, rid)['upstream_id'] != run['upstream_id']):
                    return
                if not self._matches(run, result):
                    raise IntegrationUnavailable('Mismatched native run snapshot')
                if isinstance(result, dict) and result.get('run_id') == run['upstream_id']:
                    self.steering.observe(user, run, result)
                    self.clarifications.observe(user, run, result)
                if result.get('run_id') == run['upstream_id'] and result.get('status') in ('completed', 'failed', 'cancelled'):
                    self.journal.finish(user['id'], rid, result['status'], output=result.get('output'),
                                        expected={k: run[k] for k in ('profile', 'upstream_id')})
                    return
                if result.get('status') == 'stopping':
                    if run['status'] != 'stopping':
                        self.journal.finish(user['id'], rid, 'stopping',
                            expected={k: run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')})
                    return
                if result.get('status') == 'waiting_for_approval':
                    pending = result.get('pending_approvals')
                    if (not isinstance(pending, list) or not 0 < len(pending) <= 1000
                            or any(not isinstance(a, dict) or a.get('run_id') != run['upstream_id']
                                   or not isinstance(a.get('request_id'), str)
                                   or not 0 < len(a['request_id']) <= 200
                                   or ('expires_at' in a and type(a['expires_at']) not in (int, float))
                                   for a in pending)):
                        raise IntegrationUnavailable('Pending native action identities are unavailable')
                    pending = [a for a in pending if 'expires_at' not in a or a['expires_at'] > time.time()]
                    if hasattr(self.gateway, 'require_action_approvals'):
                        try:
                            async with asyncio.timeout(self.observation_timeout):
                                await self.gateway.require_action_approvals()
                        except IntegrationUnavailable:
                            pass
                    if not self._can_observe(user):
                        return
                    self.gateway.require_execution()
                    current = self.get(user, rid)
                    if any(current[k] != run[k] for k in ('upstream_id', 'status', 'updated_at')):
                        return
                    with closing(self.journal.connect()) as c, c:
                        c.execute('BEGIN IMMEDIATE')
                        fresh = self.journal._require_run(c, user['id'], rid)
                        if any(fresh[k] != run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')):
                            return
                        pending_ids = {a['request_id'] for a in pending}
                        rows = c.execute('SELECT request_id FROM orchestration_approvals WHERE run_id=?', (rid,)).fetchall()
                        for row in rows:
                            if row['request_id'] not in pending_ids:
                                c.execute("UPDATE orchestration_approvals SET status='resolved_external' WHERE run_id=? AND request_id=? AND status IN ('pending','details_unavailable')",
                                          (rid, row['request_id']))
                        fenced = c.execute("""SELECT 1 FROM run_stop_intents WHERE run_id=? UNION ALL
                            SELECT 1 FROM orchestration_approvals WHERE run_id=? AND status IN ('sending','unknown')""",
                            (rid, rid)).fetchone()
                        known = {r[0] for r in c.execute('SELECT request_id FROM orchestration_approvals WHERE run_id=?', (rid,))}
                    if fenced or current['status'] == 'stopping':
                        return
                    if run['status'] != 'waiting_for_approval':
                        self.journal.finish(user['id'], rid, 'waiting_for_approval',
                            expected={k: run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')})
                    for action in pending:
                        if action['request_id'] not in known:
                            self._missing_approval(user, run, action['request_id'])
                    return
                if result.get('status') == 'waiting_for_clarification':
                    pending = result.get('clarifications')
                    if (not isinstance(pending, list) or not pending or len(pending) > 256
                            or any(self.clarifications._normalize(item, run['upstream_id']) is None
                                   for item in pending)):
                        raise IntegrationUnavailable('Pending clarification identity is unavailable')
                    with closing(self.journal.connect()) as c:
                        if c.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?',
                                     (rid,)).fetchone():
                            return
                    saved_pending = [item for item in self.clarifications.list(user, rid)
                                     if item['status'] in ('pending', 'sending')]
                    if not saved_pending:
                        raise IntegrationUnavailable('Pending clarification identity is unavailable')
                    self._clarification_status(user, run, resume=False)
                    return
                if result.get('status') == 'running':
                    if 'pending_approvals' in result and result['pending_approvals'] != []:
                        raise IntegrationUnavailable('Contradictory native approval snapshot')
                    clarification_clear = (
                        isinstance(result.get('clarifications'), list)
                        and len(result['clarifications']) <= 256
                        and all(self.clarifications._normalize(item, run['upstream_id']) is not None
                                and item.get('status') != 'pending'
                                for item in result['clarifications']))
                    if (run['status'] == 'waiting_for_clarification'
                            and not clarification_clear):
                        raise IntegrationUnavailable('Native clarification outcome is unavailable')
                    with closing(self.journal.connect()) as c, c:
                        c.execute('BEGIN IMMEDIATE')
                        current = self.journal._require_run(c, user['id'], rid)
                        if any(current[k] != run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')):
                            return
                        if result.get('pending_approvals') == []:
                            c.execute("UPDATE orchestration_approvals SET status='resolved_external' WHERE run_id=? AND status IN ('pending','details_unavailable')", (rid,))
                        c.execute("""UPDATE runs SET status='running',error=NULL,updated_at=?
                            WHERE id=? AND user_id=? AND profile=? AND upstream_id=?
                            AND (status IN ('running','unknown')
                                 OR (status='waiting_for_approval' AND ?)
                                 OR (status='waiting_for_clarification' AND ?))
                            AND NOT EXISTS(SELECT 1 FROM orchestration_approvals WHERE run_id=?
                             AND status IN ('pending','sending','unknown','details_unavailable'))
                            AND NOT EXISTS(SELECT 1 FROM clarifications WHERE run_id=?
                             AND status IN ('pending','sending','unknown')
                             AND created_at=(SELECT MAX(created_at) FROM clarifications WHERE run_id=?))
                            AND NOT EXISTS(SELECT 1 FROM run_stop_intents WHERE run_id=?)""",
                            (time.time(), rid, user['id'], self.profile, run['upstream_id'],
                             result.get('pending_approvals') == [], clarification_clear, rid, rid, rid, rid))
                    return
            except NativeRunNotFound:
                self.clarifications.mark_run_not_found_unknown(user, run)
                self._native_run_lost(user, run)
                return
            except Exception:
                pass
        self.journal.finish(user['id'], rid, 'unknown', error='Upstream outcome is unresolved; automatic retry is disabled',
                            expected={k: run[k] for k in ('profile', 'upstream_id', 'status', 'updated_at')})

    def _missing_approval(self, user, run, request_id):
        # Status snapshots expose identity only, not the action or its lifetime.
        # Persist a visible, non-executable placeholder, never a notification grant.
        with closing(self.journal.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            current = self.journal._require_run(c, user['id'], run['id'])
            if (not current or current['profile'] != self.profile
                    or current['upstream_id'] != run['upstream_id']
                    or current['status'] != 'waiting_for_approval'):
                return
            aid = uuid.uuid4().hex
            changed = c.execute('INSERT OR IGNORE INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                (aid, run['id'], request_id, '{"details_unavailable":true}', 'details_unavailable', 0)).rowcount
            if changed:
                row = c.execute('SELECT * FROM orchestration_approvals WHERE id=?', (aid,)).fetchone()
                c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                          (run['id'], 'approval', json.dumps(self._approval_view(row)), time.time()))

    def _approval(self, user, run, event):
        run = self.get(user, run['id'])
        if not run['upstream_id'] or event.get('run_id') != run['upstream_id']:
            return  # Never bind another native run's action to this owned run.
        request_id = event.get('request_id')
        if not isinstance(request_id, str) or not request_id:
            raise IntegrationUnavailable('Native approval identity is missing')
        action = {k: v for k, v in event.items() if k not in ('event', 'run_id', 'timestamp', 'request_id', 'choices', 'expires_at')}
        encoded = json.dumps(action, sort_keys=True, separators=(',', ':'))
        with closing(self.journal.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            self.journal._require_run(c, user['id'], run['id'])
            existing = c.execute('SELECT * FROM orchestration_approvals WHERE run_id=? AND request_id=?', (run['id'], request_id)).fetchone()
            if existing and existing['status'] == 'details_unavailable' and existing['action'] == '{"details_unavailable":true}':
                # Native approval_data carries a (possibly redacted) display
                # command, not a required tool/arguments envelope. Metadata alone
                # must not turn an identity-only snapshot into an executable grant.
                command = action.get('command')
                if not isinstance(command, str) or not command.strip():
                    return
                # Only a genuine bound stream action may fill an identity-only
                # snapshot placeholder. Recheck every fence under the write lock.
                current = c.execute('SELECT * FROM runs WHERE id=? AND user_id=? AND profile=?',
                                    (run['id'], user['id'], self.profile)).fetchone()
                if (not current or current['status'] != 'waiting_for_approval'
                        or current['upstream_id'] != run['upstream_id']
                        or not self._can_observe(user) or not self._matches(current, event)
                        or action.get('details_unavailable') is True
                        or c.execute("""SELECT 1 FROM run_stop_intents WHERE run_id=? UNION ALL
                            SELECT 1 FROM orchestration_approvals WHERE run_id=? AND status IN ('sending','unknown')""",
                            (run['id'], run['id'])).fetchone()):
                    return
                try:
                    self.gateway.require_execution()
                except IntegrationUnavailable:
                    return
                aid = existing['id']
                expires = min(float(event.get('expires_at', time.time() + 300)), time.time() + 300)
                c.execute("UPDATE orchestration_approvals SET action=?,status='pending',expires_at=? WHERE id=? AND status='details_unavailable'",
                          (encoded, expires, aid))
            elif existing:
                if existing['action'] != encoded:
                    raise RunConflict('Native approval action changed')
                return
            else:
                aid = uuid.uuid4().hex
                expires = min(float(event.get('expires_at', time.time() + 300)), time.time() + 300)
                c.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)', (aid, run['id'], request_id, encoded, 'pending', expires))
            c.execute("UPDATE runs SET status='waiting_for_approval',updated_at=? WHERE id=? AND status IN ('running','waiting_for_approval')", (time.time(), run['id']))
            c.execute('INSERT INTO approval_notification_intents(approval_id) VALUES(?)', (aid,))
        view = self._approval_view(dict(id=aid, run_id=run['id'], request_id=request_id, action=encoded, expires_at=expires))
        if self.get(user, run['id'])['status'] != 'waiting_for_approval':
            view.update(executable=False, error='Run is stopping or unresolved')
        self.journal.event(user['id'], run['id'], 'approval', view)
        self.drain_approval_notifications()

    async def reconcile_approval_notifications(self):
        """Read-only native reconciliation, limited to explicit notification intents.

        A status-only legacy response cannot prove which action is still pending;
        absence of an authoritative pending_approvals snapshot therefore defers.
        """
        from urllib.parse import quote
        with closing(self.journal.connect()) as c:
            rows = c.execute('''SELECT a.*,r.user_id,r.upstream_id FROM approval_notification_intents i
                JOIN orchestration_approvals a ON a.id=i.approval_id JOIN runs r ON r.id=a.run_id
                WHERE i.status IN ('pending','sent') AND a.status='pending'
                AND r.status='unknown' AND r.profile=?''', (self.profile,)).fetchall()
        results = {}
        eligible = []
        for row in rows:
            validator = getattr(self, 'approval_validator', None)
            if not validator or validator(row['user_id'], row['run_id'], row['request_id'], row['id']) is not None:
                continue
            upstream = row['upstream_id']
            if upstream not in results:
                try:
                    self.gateway.require_execution()
                    async with asyncio.timeout(10):
                        results[upstream] = await self.gateway.request('GET', '/v1/runs/' + quote(upstream, safe=''))
                    self.gateway.require_execution()
                except Exception:
                    results[upstream] = None  # Includes 404: never infer execution outcome.
            result = results[upstream]
            if not isinstance(result, dict) or not isinstance(result.get('run_id'), str):
                continue
            user = {'id': row['user_id'], 'profile': self.profile}
            run = self.get(user, row['run_id'])
            if not self._can_observe(user):
                continue
            if result['run_id'] == upstream and not self._matches(run, result):
                continue
            invalid = result['run_id'] != upstream
            resolved = result.get('status') in ('completed', 'failed', 'cancelled')
            pending = result.get('pending_approvals')
            if not invalid and not resolved:
                if (result.get('status') != 'waiting_for_approval' or not isinstance(pending, list)
                        or any(not isinstance(a, dict) or a.get('run_id') != upstream
                               or not isinstance(a.get('request_id'), str) for a in pending)):
                    continue
                matching = [a for a in pending if a['request_id'] == row['request_id']]
                if any('expires_at' in a and type(a['expires_at']) not in (int, float) for a in matching):
                    continue
                resolved = not matching or any(a.get('expires_at', row['expires_at']) <= time.time() for a in matching)
            with closing(self.journal.connect()) as c, c:
                c.execute('BEGIN IMMEDIATE')
                current = c.execute('SELECT status,upstream_id FROM runs WHERE id=? AND user_id=? AND profile=?',
                                    (row['run_id'], row['user_id'], self.profile)).fetchone()
                if not current or current['status'] != 'unknown' or current['upstream_id'] != upstream:
                    continue  # Preserve local stop/approval/terminal fences after await.
                if invalid or resolved:
                    c.execute("UPDATE orchestration_approvals SET status=? WHERE id=? AND status='pending'",
                              ('invalid' if invalid else 'resolved_external', row['id']))
                else:
                    eligible.append(row)
        # Validate every intent on a run before making any of them eligible.
        with closing(self.journal.connect()) as c, c:
            for row in eligible:
                c.execute("UPDATE runs SET status='waiting_for_approval' WHERE id=? AND status='unknown' "
                          "AND user_id=? AND profile=? AND upstream_id=? AND EXISTS "
                          "(SELECT 1 FROM orchestration_approvals WHERE id=? AND status='pending' AND expires_at>?) "
                          "AND NOT EXISTS(SELECT 1 FROM run_stop_intents WHERE run_id=runs.id) "
                          "AND NOT EXISTS(SELECT 1 FROM orchestration_approvals WHERE run_id=runs.id AND status IN ('sending','unknown'))",
                          (row['run_id'], row['user_id'], self.profile, row['upstream_id'], row['id'], time.time()))

    def drain_approval_notifications(self):
        if self.approval_notifier is None:
            return
        # Explicit intents only. Never sweep historical approvals after upgrade.
        with closing(self.journal.connect()) as c:
            rows = c.execute('''SELECT a.*,r.user_id,r.session_id FROM approval_notification_intents i
                JOIN orchestration_approvals a ON a.id=i.approval_id JOIN runs r ON r.id=a.run_id
                WHERE i.status='pending' AND r.profile=? ORDER BY i.rowid LIMIT 1000''',
                (self.profile,)).fetchall()
        for row in rows:
            try:
                validator = getattr(self, 'approval_validator', None)
                if validator and validator(row['user_id'], row['run_id'], row['request_id'], row['id']) is None:
                    continue  # Unknown is retryable, not a definitive rejection.
                result = self.approval_notifier(row['user_id'], row['run_id'], row['request_id'],
                    row['id'], row['session_id'], row['expires_at'])
                with closing(self.journal.connect()) as c, c:
                    c.execute("UPDATE approval_notification_intents SET status=? WHERE approval_id=? AND status='pending'",
                              ('sent' if result is not None else 'discarded', row['id']))
            except Exception:
                # Ingestion is idempotent across its separate DB commit. Retry on the worker tick.
                continue

    def _approval_view(self, row):
        ready = (getattr(self.gateway, 'action_approvals_ready', False) is True
                 and getattr(self.gateway, 'execution_ready', False) is True)
        action = json.loads(row['action'])
        if action.get('details_unavailable') is True:
            return dict(id=row['id'], run_id=row['run_id'], request_id=row['request_id'],
                        expires_at=None, choices=[], executable=False,
                        error='Native approval details are unavailable after interruption; review in the native client')
        return dict(action, id=row['id'], run_id=row['run_id'], request_id=row['request_id'],
                    expires_at=row['expires_at'], choices=['once', 'deny'], executable=ready,
                    error=None if ready else 'Native API cannot atomically bind decisions to this action')

    def approvals(self, user):
        if user['profile'] != self.profile:
            return {'items': []}
        with closing(self.journal.connect()) as c:
            rows = c.execute("""SELECT a.* FROM orchestration_approvals a JOIN runs r ON a.run_id=r.id
                WHERE r.user_id=? AND r.profile=? AND r.status='waiting_for_approval'
                AND ((a.status='pending' AND a.expires_at>?) OR a.status='details_unavailable') ORDER BY a.rowid LIMIT 1000""",
                (user['id'], user['profile'], time.time())).fetchall()
        return {'items': [self._approval_view(row) for row in rows]}

    async def decide(self, user, approval_id, decision):
        with closing(self.journal.connect()) as c:
            row = c.execute('SELECT * FROM orchestration_approvals WHERE id=?', (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        run = self.get(user, row['run_id'])
        if decision not in ('once', 'deny'):
            raise ValueError('Only once or deny is permitted')
        if row['status'] != 'pending' or row['expires_at'] <= time.time() or run['status'] != 'waiting_for_approval':
            raise RunConflict('Approval is stale or no longer pending')
        self.gateway.require_execution()
        if not hasattr(self.gateway, 'require_action_approvals'):
            raise IntegrationUnavailable('Native API cannot atomically bind decisions to this action')
        await self.gateway.require_action_approvals()
        # Claim durably before I/O. A concurrent/replayed click can never send twice.
        with closing(self.journal.connect()) as c, c:
            changed = c.execute("""UPDATE orchestration_approvals SET status='sending'
                WHERE id=? AND status='pending' AND expires_at>?
                AND EXISTS(SELECT 1 FROM runs WHERE id=run_id AND status='waiting_for_approval'
                           AND upstream_id IS NOT NULL)""", (approval_id, time.time())).rowcount
        if not changed:
            raise RunConflict('Approval is stale or no longer pending')
        try:
            async with asyncio.timeout(30):
                await self.gateway.approve(run['upstream_id'], row['request_id'], decision)
        except (Exception, asyncio.CancelledError) as exc:
            # Even a timeout/cancellation may occur after the native resolver acted.
            # Never reset the claim to pending or silently replay the decision.
            with closing(self.journal.connect()) as c, c:
                c.execute("UPDATE orchestration_approvals SET status='unknown' WHERE id=?", (approval_id,))
            if self.get(user, run['id'])['status'] not in ('completed', 'failed', 'cancelled'):
                self.journal.finish(user['id'], run['id'], 'unknown', error='Approval outcome is unresolved; automatic retry is disabled')
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise IntegrationUnavailable('Approval outcome is unresolved; automatic retry is disabled') from exc
        with closing(self.journal.connect()) as c, c:
            c.execute("UPDATE orchestration_approvals SET status='resolved' WHERE id=?", (approval_id,))
        self._resume_after_approval(user, run['id'])
        return {'id': approval_id, 'run_id': run['id'], 'request_id': row['request_id'], 'status': 'resolved', 'decision': decision}

    def _resume_after_approval(self, user, rid):
        with closing(self.journal.connect()) as c, c:
            c.execute("""UPDATE runs SET status='running' WHERE id=? AND status='waiting_for_approval'
                AND NOT EXISTS(SELECT 1 FROM orchestration_approvals
                    WHERE run_id=? AND status IN ('pending','sending','unknown','details_unavailable'))""", (rid, rid))
        self.journal.event(user['id'], rid, 'status', {'status': self.get(user, rid)['status']})

    async def close(self):
        self._closed = True
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)
            self._recovery_task = None
        active = dict(self._tasks)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        for user, run in active.values():
            if self.get(user, run['id'])['status'] not in ('completed', 'failed', 'cancelled', 'unknown'):
                self.journal.finish(user['id'], run['id'], 'unknown', error='Bridge closed; upstream outcome is unresolved')
        self._tasks.clear()
