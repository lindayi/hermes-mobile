"""Owner-only durable background-result delivery; never starts a model turn.

Parent wiring: BackgroundDeliveryService(notifications, journal, catalog, gateway,
owner=<synchronous fresh auth-store owner lookup>, binding=RuntimeBinding.matches).
Only default/owner is enabled. Both callbacks are live, synchronous and fail closed.
Call/await tick() from the existing lifespan worker; session_items(user, sid) is a
synchronous read suitable for a FastAPI def route. No SDK imports or native writes.
"""
import asyncio
from contextlib import closing, contextmanager
import json
import sqlite3
import time

from .hermes_client import IntegrationUnavailable


# Optional attribution is deliberately smaller than full history. These limits
# are shared by all proofs in one response, not reset for every receipt.
_PROOF_FIELD_BYTES = 256 * 1024
_PROOF_BYTES = 4 * 1024 * 1024
_PROOF_ROWS = 10000
_PROOF_STEPS = 2_000_000
_PROOF_SECONDS = 0.5


class _ProofLimit(Exception):
    pass


class _ProofBudget:
    def __init__(self):
        self.bytes = self.rows = self.steps = 0
        self.deadline = time.monotonic() + _PROOF_SECONDS
        self.exhausted = self.interrupted = False

    def check(self):
        if self.exhausted or time.monotonic() >= self.deadline:
            self.exhausted = True
            raise _ProofLimit

    def reserve(self, sizes, *, copies=1):
        self.check()
        sizes = [size or 0 for size in sizes]
        self.rows += 1
        self.bytes += sum(sizes) * copies
        if (any(size > _PROOF_FIELD_BYTES for size in sizes)
                or self.bytes > _PROOF_BYTES or self.rows > _PROOF_ROWS):
            self.exhausted = True
            raise _ProofLimit

    def progress(self):
        self.steps += 1000
        if self.steps > _PROOF_STEPS or time.monotonic() >= self.deadline:
            self.exhausted = self.interrupted = True
            return 1
        return 0

    @contextmanager
    def guard(self, *connections):
        self.check()
        # These are the service's per-response readers (no outer progress hook).
        # Keep their transactions/snapshots; remove ONLY our temporary handlers.
        for connection in connections:
            connection.set_progress_handler(self.progress, 1000)
        try:
            yield
            self.check()
        except sqlite3.OperationalError as error:
            if self.interrupted and getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT:
                raise _ProofLimit from error
            raise
        finally:
            for connection in connections:
                connection.set_progress_handler(None, 0)


class BackgroundDeliveryService:
    def __init__(self, notifications, journal, catalog, gateway, *, owner, binding,
                 limit=20, timeout=3):
        self.notifications, self.journal, self.catalog = notifications, journal, catalog
        self.gateway, self.owner, self.binding = gateway, owner, binding
        self.limit = min(max(int(limit), 1), 50)
        self.timeout = timeout
        self.home = catalog.profiles['default']
        self.scope = json.dumps(['default', str(self.home)], separators=(',', ':'))
        notifications.background_link = self._inbox_link

    def _user(self):
        user = self.owner()
        if (not user or user['role'] != 'owner' or user['profile'] != 'default'
                or user['status'] != 'ready' or self.catalog.profiles.get('default') != self.home
                or not self.binding(user)):
            raise PermissionError('Background delivery binding unavailable')
        return user

    @staticmethod
    def _envelope(item):
        import hashlib
        if not isinstance(item, dict) or not isinstance(item.get('event'), dict):
            raise ValueError('Invalid background envelope')
        event = item['event']
        if (not all(isinstance(item.get(k), str) and item[k] for k in
                    ('event_id', 'payload_sha256', 'lease_token'))
                or type(item.get('historical')) is not bool):
            raise ValueError('Invalid background identity')
        canonical = json.dumps({k: v for k, v in event.items() if k != 'restored'},
                               sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        if hashlib.sha256(canonical.encode()).hexdigest() != item['payload_sha256']:
            raise ValueError('Background digest conflict')
        kind = event.get('type')
        key = None
        if kind == 'async_delegation' and isinstance(event.get('delegation_id'), str) and event['delegation_id']:
            key = 'async:' + event['delegation_id']
        elif (kind == 'completion' and isinstance(event.get('session_id'), str)
              and type(event.get('started_at')) in (int, float)):
            key = 'process:' + event['session_id'] + ':' + json.dumps(event['started_at'], allow_nan=False)
        elif kind == 'watch_match' and event.get('occurrence_id'):
            key = 'occurrence:' + str(event['occurrence_id'])
        elif kind in ('completion', 'watch_match') and item['event_id'].startswith('migration:'):
            # Native-owned retained migration envelope, never a consumer-created ID.
            key = item['event_id']
        if key is None or item['event_id'] != key:
            raise ValueError('Unproved producer identity')
        origin = BackgroundDeliveryService._origin(event)
        if (not isinstance(origin, str) or not origin
                or not isinstance(event.get('session_key'), str) or not event['session_key']
                or ':' in event['session_key']
                or any(event.get(k) is not None and not isinstance(event[k], str)
                       for k in ('parent_session_id', 'origin_ui_session_id'))):
            raise ValueError('Invalid immutable route identity')
        BackgroundDeliveryService._body(event)
        return event

    @staticmethod
    def _body(event):
        if not isinstance(event, dict):
            raise ValueError('Invalid public report')
        if event.get('type') in ('completion', 'watch_match'):
            if not isinstance(event.get('output'), str):
                raise ValueError('Invalid public process report')
            return event['output']
        if event.get('is_batch') is True:
            results = event.get('results')
            if not isinstance(results, list) or not results:
                raise ValueError('Invalid batch report')
            return '\n\n'.join('Task ' + str(i + 1) + '\n' + BackgroundDeliveryService._body(child)
                               for i, child in enumerate(results))
        if isinstance(event.get('summary'), str):
            return event['summary']
        if event.get('summary') is None and event.get('status') in (
                'completed', 'failed', 'error', 'cancelled', 'timeout', 'unknown', 'stalled'):
            return 'Background task status: ' + event['status'] + '. No public report was provided.'
        raise ValueError('Invalid public report')

    @staticmethod
    def _origin(event):
        # Native process session_id is proc_..., NOT a conversation. Its task_id
        # is the immutable API conversation recorded at process dispatch.
        return event.get('task_id') if event.get('type') in ('completion', 'watch_match') else event.get('origin_session_id')

    def _route(self, user, event, db):
        if (event.get('platform') not in (None, '', 'api', 'api_server')
                or any(event.get(k) for k in ('scope_id', 'chat_id', 'chat_type', 'thread_id', 'user_id'))):
            raise PermissionError('Foreign route')
        rows = db.execute('SELECT user_id,session_id FROM runs WHERE profile=? AND upstream_id=? LIMIT 2',
                          (user['profile'], event.get('session_key'))).fetchall()
        if len(rows) != 1 or rows[0]['user_id'] != user['id']:
            raise PermissionError('Unproved background origin')
        self._fence(db, user)
        chain, tip = self._lineage(user, rows[0]['session_id'])
        self._fence(db, user, chain)
        if (self._origin(event) not in chain
                or any(event.get(k) and event[k] not in chain
                       for k in ('parent_session_id', 'origin_ui_session_id'))):
            raise PermissionError('Unproved background session')
        return tip

    def _lineage(self, user, sid):
        with closing(self.catalog._connect(user['profile'])) as db:
            db.execute('BEGIN')
            def row_for(key):
                row = db.execute('SELECT * FROM sessions WHERE id=?', (key,)).fetchone()
                if row is None:
                    raise PermissionError('Native session unavailable')
                row = dict(row)
                config = json.loads(row.get('model_config') or '{}')
                # History may originate on WhatsApp/CLI and later be resumed by
                # an owned web run. Only EVENT routing + exact journal identity
                # establishes delivery ownership, never historical source labels.
                if (row.get('profile_name') not in (None, '', user['profile'])
                        or not isinstance(config, dict)
                        or '_branched_from' in config or '_delegate_from' in config
                        or (row.get('source') in ('tool', 'subagent') and row.get('parent_session_id'))):
                    raise PermissionError('Foreign native session')
                return row
            seen, current = set(), row_for(sid)
            while current.get('parent_session_id'):
                if current['id'] in seen or len(seen) >= 100:
                    raise PermissionError('Ambiguous compression')
                seen.add(current['id'])
                parent = row_for(current['parent_session_id'])
                if parent.get('end_reason') != 'compression':
                    raise PermissionError('Not a compression continuation')
                current = parent
            chain = set()
            while True:
                if current['id'] in chain or len(chain) >= 100:
                    raise PermissionError('Ambiguous compression')
                chain.add(current['id'])
                if current.get('end_reason') != 'compression':
                    break
                children = []
                for child in db.execute('SELECT id FROM sessions WHERE parent_session_id=?', (current['id'],)):
                    try:
                        children.append(row_for(child['id']))
                    except PermissionError:
                        continue
                if len(children) != 1:
                    raise PermissionError('Ambiguous compression')
                current = children[0]
            if sid not in chain:
                raise PermissionError('Not a compression continuation')
            return chain, current['id']

    @staticmethod
    def _fence(db, user, ids=()):
        if db.execute("SELECT 1 FROM session_deletion_operations WHERE user_id=? AND profile=? AND state IN ('prepared','native_unknown')",
                      (user['id'], user['profile'])).fetchone():
            raise PermissionError('Session deletion unresolved')
        for sid in ids:
            if db.execute('SELECT 1 FROM session_deletions WHERE user_id=? AND profile=? AND session_id=?',
                          (user['id'], user['profile'], sid)).fetchone():
                raise PermissionError('Session deleted')

    def _inbox_link(self, user_id, receipt):
        try:
            user = self._user()
            if user['id'] != user_id or receipt['scope'] != self.scope:
                return None
            with closing(self.journal.connect()) as db:
                return self._route(user, json.loads(receipt['event_json']), db)
        except (PermissionError, ValueError, KeyError):
            return None

    async def _request(self, path, body):
        async with asyncio.timeout(self.timeout):
            return await self.gateway.request('POST', '/v1/mobile/notifications/' + path,
                                              json=body, timeout=self.timeout)

    async def _ack(self, row):
        user = self._user()
        if user['id'] != row['user_id']:
            raise PermissionError('Receipt owner changed')
        with closing(self.journal.connect()) as db:
            self._fence(db, user)
        body = dict(event_id=row['event_id'], payload_sha256=row['digest'],
                    lease_token=row['lease_token'], receipt_id=row['inbox_id'])
        try:
            reply = await self._request('ack', body)
        except (IntegrationUnavailable, TimeoutError):
            return
        if (isinstance(reply, dict) and reply.get('status') == 'delivered' and reply.get('event_id') == row['event_id']
                and reply.get('receipt_id') == row['inbox_id']):
            self.notifications.background_acknowledged(self.scope, row['event_id'], row['lease_token'])

    async def tick(self):
        report = {'status': 'ok', 'rejected': 0}
        try:
            await self._tick(report)
        except (PermissionError, IntegrationUnavailable, TimeoutError):
            report['status'] = 'deferred'
        return report

    async def _tick(self, report):
        user = self._user()
        for row in [r for r in self.notifications.background_rows(self.scope, user['id'])
                    if not r['acknowledged']][:self.limit]:
            await self._ack(row)
        user = self._user()
        with closing(self.journal.connect()) as db:
            self._fence(db, user)
        claimed = await self._request('claim', {'limit': self.limit})
        if not isinstance(claimed, dict) or not isinstance(claimed.get('items'), list):
            raise IntegrationUnavailable('Invalid notification claim response')
        for item in claimed['items'][:self.limit]:
            user = self._user()
            try:
                event = self._envelope(item)
                with closing(self.journal.connect()) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    user = self._user()
                    sid = self._route(user, event, db)
                    if self._user()['id'] != user['id']:
                        raise PermissionError('Receipt owner changed')
                    self.notifications.store_background(scope=self.scope, event_id=item['event_id'],
                        digest=item['payload_sha256'], user_id=user['id'], origin=self._origin(event),
                        session_id=sid, event=event, lease_token=item['lease_token'],
                        body=self._body(event), historical=item['historical'])
            except (ValueError, PermissionError):
                report['rejected'] += 1
                continue
            row = next(row for row in self.notifications.background_rows(self.scope, user['id'])
                       if row['event_id'] == item['event_id'])
            await self._ack(row)

    @staticmethod
    def _event_time(event):
        import math
        for key in ('dispatched_at', 'started_at', 'completed_at', 'timestamp'):
            value = event.get(key)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                return dict(event_at=value, event_at_source=key)
        return dict(event_at=None, event_at_source=None)

    def _placement(self, user, event, db, chain, native, structures, budget=None):
        """Optional native proof; owned run/anchor survive unavailable proof."""
        run = db.execute('SELECT id,session_id,status FROM runs WHERE user_id=? AND profile=? AND upstream_id=?',
                         (user['id'], user['profile'], event['session_key'])).fetchone()
        anchor = db.execute('SELECT session_id,message_id FROM run_history_anchors WHERE run_id=?',
                            (run['id'],)).fetchone()
        result = dict(origin_run_id=run['id'], origin_session_id=run['session_id'],
                      origin_anchor=None, origin_message_id=None)
        if anchor is None or anchor['session_id'] not in chain:
            return result
        result['origin_session_id'] = anchor['session_id']
        result['origin_anchor'] = dict(anchor)
        if run['status'] != 'completed':
            return result
        budget = budget or _ProofBudget()
        try:
            with budget.guard(db, native):
                origin = self._native_origin(user, run, anchor, db, native, structures, budget)
            result['origin_message_id'] = origin
        except _ProofLimit:
            pass
        return result

    @staticmethod
    def _native_origin(user, run, anchor, db, native, structures, budget):
        from .native_catalog import (_completed_turn, _first_turn_row, _ordinary_user,
                                     _native_rewrites, compression_ids, runtime_notice_ids)
        sid = anchor['session_id']
        # Shared admission boundaries cannot identify which owned run persisted.
        if db.execute('''SELECT 1 FROM run_history_anchors a JOIN runs r ON r.id=a.run_id
            WHERE r.user_id=? AND r.profile=? AND a.session_id=? AND a.message_id=?
            AND r.id<>? LIMIT 1''',
            (user['id'], user['profile'], sid, anchor['message_id'], run['id'])).fetchone():
            return None
        end = db.execute('''SELECT MIN(a.message_id) FROM run_history_anchors a
            JOIN runs r ON r.id=a.run_id WHERE r.user_id=? AND r.profile=?
            AND a.session_id=? AND a.message_id>?''',
            (user['id'], user['profile'], sid, anchor['message_id'])).fetchone()[0]
        sizes = db.execute('SELECT length(CAST(input AS BLOB)),length(CAST(output AS BLOB)) '
                           'FROM runs WHERE id=?', (run['id'],)).fetchone()
        budget.reserve(sizes)
        turn = db.execute('SELECT input,output FROM runs WHERE id=?', (run['id'],)).fetchone()
        if not isinstance(turn['output'], str):
            return None
        # Steering needs no body. Tool events project only the three identity
        # spellings understood by _completed_turn, never arguments or summaries.
        if db.execute("SELECT 1 FROM events WHERE run_id=? AND name='steering' LIMIT 1",
                      (run['id'],)).fetchone():
            return None
        tool_events = []
        for meta in db.execute("SELECT id,length(CAST(data AS BLOB)) AS size FROM events "
                               "WHERE run_id=? AND name='tool' ORDER BY id LIMIT ?",
                               (run['id'], _PROOF_ROWS + 1)):
            budget.reserve([meta['size']], copies=6)  # JSON escaping/projection overhead
            row = db.execute('''SELECT CASE WHEN json_valid(data) THEN
                CASE WHEN json_type(data)='object' AND NOT EXISTS (
                    SELECT 1 FROM json_each(data) WHERE key IN ('tool_call_id','toolCallId','call_id')
                    GROUP BY key HAVING count(*)>1) THEN json_object(
                    'tool_call_id',json_extract(data,'$.tool_call_id'),
                    'toolCallId',json_extract(data,'$.toolCallId'),
                    'call_id',json_extract(data,'$.call_id')) END END AS identity
                FROM events WHERE id=? AND run_id=?''', (meta['id'], run['id'])).fetchone()
            if row['identity'] is None:
                return None
            tool_events.append({'data': json.loads(row['identity'])})
        columns = {row[1] for row in native.execute('PRAGMA table_info(messages)')}
        if not {'id', 'session_id', 'role', 'content', 'tool_calls'} <= columns:
            return None
        if sid not in structures:
            # Legacy classifiers/rewrites stay unchanged. Before invoking their
            # full-session scans/sorts, bound every public field they can inspect.
            # Simple schemas only return IDs, but still need a row/work bound.
            lengths = '0'
            if 'display_kind' in columns or {'active', 'compacted'} <= columns:
                fields = [field for field in ('role', 'content', 'tool_calls', 'timestamp',
                    'tool_call_id', 'tool_name', 'display_kind', 'display_metadata',
                    'platform_message_id', 'active', 'compacted') if field in columns]
                lengths = ','.join('length(CAST(' + field + ' AS BLOB))' for field in fields)
            for sizes in native.execute('SELECT ' + lengths + ' FROM messages '
                    'WHERE session_id=? LIMIT ?', (sid, _PROOF_ROWS + 1)):
                # Reserve for repeated classifier projections + JSON identities.
                budget.reserve(sizes, copies=8)
            compressions = compression_ids(native, sid, columns)
            structures[sid] = (compressions | runtime_notice_ids(native, sid, columns),
                               _native_rewrites(native, sid, columns), compressions)
        processes, rewrites, compressions = structures[sid]
        if compressions:
            from .task_reminder_presentation import reminder_projections
            journal_columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            times = (dict(db.execute('SELECT created_at,updated_at FROM runs WHERE id=?', (run['id'],)).fetchone())
                     if {'created_at', 'updated_at'} <= journal_columns else {})
            admission = dict(run=dict(turn, id=run['id'], status=run['status'], **times),
                             anchor=dict(anchor, canonical_session_id=sid), tool_events=tool_events)
            proof_state = admission if end is None else dict(
                run=None, anchor=dict(session_id=sid, canonical_session_id=sid, message_id=end),
                prior=[admission])
            reminders = reminder_projections(native, sid, columns, proof_state, compressions, processes, rewrites)
            owned = [mid for mid, projection in reminders.items() if projection['run_id'] == run['id']]
            if len(owned) == 1:
                return owned[0]
        # Stream metadata, then bounded public proof fields from the SAME native
        # read snapshot. Tool bodies and function arguments never enter Python.
        call_size = 'length(CAST(tool_call_id AS BLOB))' if 'tool_call_id' in columns else '0'
        call_field = ',tool_call_id' if 'tool_call_id' in columns else ''
        rows, first = [], None
        for meta in native.execute('''SELECT id,
                CASE WHEN role IN ('user','assistant','tool','system') THEN role ELSE '' END AS role,
                CASE WHEN role IN ('user','assistant') THEN length(CAST(content AS BLOB)) END AS body_size,
                CASE WHEN role='assistant' THEN length(CAST(tool_calls AS BLOB)) END AS calls_size,
                CASE WHEN role='tool' THEN ''' + call_size + ''' END AS id_size
            FROM messages WHERE session_id=? AND id>? AND (? IS NULL OR id<=?)
            ORDER BY id LIMIT ?''', (sid, anchor['message_id'], end, end, _PROOF_ROWS + 1)):
            budget.reserve([meta['body_size'], meta['calls_size'], meta['id_size']])
            if meta['id'] in processes:
                continue
            row = dict(id=meta['id'], role=meta['role'], content=None, tool_calls=None)
            if meta['role'] in ('user', 'assistant'):
                row['content'] = native.execute('SELECT id,content FROM messages WHERE session_id=? AND id=?',
                                               (sid, meta['id'])).fetchone()['content']
            if _ordinary_user(row, processes) and first is not None:
                break
            if meta['role'] == 'assistant':
                # Same nonempty-object-array predicate as _tool_calls; no args.
                # Reject nonstandard JSON rather than reinterpret Python's
                # accepted NaN/Infinity objects as an empty/final call list.
                row['tool_calls'] = native.execute('''SELECT CASE
                    WHEN tool_calls IS NOT NULL AND tool_calls!='' AND NOT json_valid(tool_calls) THEN '[{}]'
                    WHEN EXISTS (
                    SELECT 1 FROM json_each(CASE WHEN json_valid(tool_calls) THEN
                        CASE WHEN json_type(tool_calls)='array' THEN tool_calls ELSE '[]' END
                        ELSE '[]' END) WHERE type='object') THEN '[{}]' END
                    FROM messages WHERE session_id=? AND id=?''', (sid, meta['id'])).fetchone()[0]
            elif meta['role'] == 'tool' and call_field:
                row['tool_call_id'] = native.execute('SELECT tool_call_id FROM messages WHERE session_id=? AND id=?',
                                                   (sid, meta['id'])).fetchone()[0]
            rows.append(row)
            if first is None:
                first = _first_turn_row(iter([row]), processes)
                if first is not None and (first['role'] != 'user' or first['content'] != turn['input']):
                    return None
        if _completed_turn(iter(rows), turn, tool_events, processes):
            return rewrites.get(first['id'], first['id'])
        return None

    def session_items(self, user, sid):
        current = self._user()
        if user['id'] != current['id'] or user['profile'] != current['profile']:
            raise PermissionError('Background results unavailable')
        chain, tip = self._lineage(current, sid)
        items, placements, structures = [], {}, {}
        budget = _ProofBudget()
        with closing(self.journal.connect()) as db, closing(self.catalog._connect(current['profile'])) as native:
            db.execute('BEGIN IMMEDIATE')
            native.execute('BEGIN')
            self._fence(db, current, chain)
            for row in self.notifications.background_rows(self.scope, user['id']):
                event = json.loads(row['event_json'])
                if row['origin'] in chain and self._route(current, event, db) == tip:
                    key = event['session_key']
                    if key not in placements:
                        placements[key] = self._placement(current, event, db, chain, native, structures, budget)
                    items.append(dict(id=row['inbox_id'], session_id=tip, title=row['title'],
                        body=row['body'], created_at=row['created_at'], kind='background_result',
                        source_event_id=row['event_id'], agent_context_state='not_injected',
                        **placements[key], **self._event_time(event)))
        if self._user()['id'] != current['id']:
            raise PermissionError('Receipt owner changed')
        return {'items': items}
