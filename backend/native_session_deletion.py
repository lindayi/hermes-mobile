"""Owner-listener compatibility boundary for standard stored-history deletion.

Never imported by the bridge to open native storage. No global/native patches.
Receipts are app-private evidence, NOT native-ID resurrection tombstones.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
import time
import re
import stat
from contextlib import closing


class DeleteRefused(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


SUPPORTED_STORAGE_PATH = Path('/usr/local/lib/hermes-agent/hermes_state.py')
SUPPORTED_STORAGE_SHA256 = '70c69963f39902bad1b3ed1b943aaa1dc195b986ebd267fe1b0ce49a0c6d6723'


def _supported_storage(db=None):
    """Identify our direct destructive dependency, not arbitrary plugin code.

    The OS and in-process plugins remain trusted. This is a fail-closed ABI
    compatibility pin, not protection against an attacker monkeypatching Python.
    Never opens storage or tests destructive semantics to establish support.
    """
    try:
        import hermes_state as storage
        path = SUPPORTED_STORAGE_PATH
        cls = storage.SessionDB
        if (Path(storage.__file__) != path or Path(storage.__spec__.origin) != path
                or path.resolve() != path or cls.__module__ != 'hermes_state'
                or cls.__qualname__ != 'SessionDB'
                or Path(cls.__init__.__code__.co_filename) != path
                or Path(cls.delete_session.__code__.co_filename) != path
                or (db is not None and type(db) is not cls)
                or hashlib.sha256(path.read_bytes()).hexdigest() != SUPPORTED_STORAGE_SHA256):
            raise ValueError('Unsupported storage identity')
    except Exception:
        raise DeleteRefused('unsupported_native_storage', 503) from None


class _NativeWriteFacade:
    """Per-call composition, never patches a live SessionDB or native class.

    Native delete_session supplies the actual deletion callback. Its original
    _execute_write still owns BEGIN/commit/rollback; validation runs on that
    exact connection, under its write lock, immediately before the callback.
    """
    def __init__(self, db, validate=None):
        self.db, self.validate = db, validate
        self.mutation_started = False

    def __getattr__(self, name):
        return getattr(self.db, name)

    def _execute_write(self, fn, patience_s=None):
        def guarded(conn):
            if self.validate:
                self.validate(conn)
                self.mutation_started = True
            return fn(conn)
        return self.db._execute_write(guarded, patience_s=0)


def _references(value, ids):
    if isinstance(value, str):
        return value in ids
    if isinstance(value, dict):
        return any(_references(v, ids) or k in ids for k, v in value.items())
    if isinstance(value, list):
        return any(_references(v, ids) for v in value)
    return False


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', value) is not None


def _valid_operation(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def _private(path, *, directory=False):
    info = path.lstat()
    if (path.resolve() != path or info.st_uid != os.getuid() or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise DeleteRefused('unsafe_receipt_storage', 503)


class DeletionGuard:
    def __init__(self, db, home, *, quiescent):
        _supported_storage(db)
        self.db, self.home, self.quiescent = db, Path(home), quiescent
        if (not self.home.is_absolute() or self.home.resolve() != self.home
                or Path(db.db_path) != self.home / 'state.db'
                or Path(db.db_path).resolve() != self.home / 'state.db'):
            raise DeleteRefused('native_profile_mismatch', 503)
        directory = self.home / 'mobile-delete-records'
        directory.mkdir(mode=0o700, exist_ok=True)
        _private(directory, directory=True)
        self.records = directory / 'receipts.sqlite'
        fd = os.open(self.records, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        _private(self.records)
        with closing(sqlite3.connect(self.records)) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS receipts (operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, receipt TEXT NOT NULL)')
            conn.commit()

    def receipt(self, operation_id):
        if not _valid_operation(operation_id):
            raise DeleteRefused('invalid_operation_id', 400)
        with closing(sqlite3.connect(self.records)) as conn:
            row = conn.execute('SELECT receipt FROM receipts WHERE operation_id=?', (operation_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def _claim(self, receipt):
        with closing(sqlite3.connect(self.records)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT receipt FROM receipts WHERE operation_id=?', (receipt['operation_id'],)).fetchone()
            if previous:
                return json.loads(previous[0])
            if conn.execute("SELECT 1 FROM receipts WHERE session_id=? AND json_extract(receipt, '$.status') != 'refused'", (receipt['id'],)).fetchone():
                raise DeleteRefused('session_operation_conflict')
            conn.execute('INSERT INTO receipts VALUES (?,?,?)',
                         (receipt['operation_id'], receipt['id'], json.dumps(receipt)))
            conn.commit()
            return None

    def _save(self, receipt):
        with closing(sqlite3.connect(self.records)) as conn:
            conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)',
                         (receipt['operation_id'], receipt['id'], json.dumps(receipt)))
            conn.commit()

    def _validate(self, conn, root, targets, holder):
        if not conn.in_transaction or self.quiescent() is not True:
            raise DeleteRefused('native_not_quiescent')
        ids = set(targets)
        # SessionDB's Python lock is non-reentrant: use its connection-level
        # cascade walker here, never the locking public enumeration method.
        from hermes_state import _collect_delegate_child_ids
        actual = {root, *_collect_delegate_child_ids(conn, [root])}
        if conn is not self.db._conn or actual != ids:
            raise DeleteRefused('session_changed')
        ph = ','.join('?' for _ in targets)
        rows = {r['id']: dict(r) for r in conn.execute(
            f'SELECT * FROM sessions WHERE id IN ({ph})', targets)}
        if set(rows) != ids:
            raise DeleteRefused('session_changed')
        markers = {}
        for sid, row in rows.items():
            config = json.loads(row['model_config']) if row['model_config'] else {}
            if not isinstance(config, dict) or '_branched_from' in config:
                raise DeleteRefused('invalid_provenance')
            marker = config.get('_delegate_from')
            markers[sid] = marker
            sources = {'cli', 'api_server'} if sid == root else {'cli', 'api_server', 'subagent'}
            if (row['source'] not in sources
                    or row['profile_name'] not in {None, '', 'default'}
                    or row['session_key'] or row['origin_json'] or row['chat_id']
                    or row['chat_type'] or row['thread_id']
                    or row['handoff_state'] or row['handoff_platform']
                    or row['end_reason'] in {'compression', 'compaction'}):
                raise DeleteRefused('unsupported_session_identity')
            if sid == root:
                if row['parent_session_id'] or '_delegate_from' in config:
                    raise DeleteRefused('ambiguous_session_lineage')
            elif (not isinstance(marker, str) or marker not in ids
                  or not row['ended_at'] or row['end_reason'] not in {'completed', 'agent_close'}
                  or row['parent_session_id'] not in ids | {None}):
                raise DeleteRefused('unsafe_delegate')
            for table, key in (('session_turn_leases', 'conversation_id'),
                               ('compression_locks', 'session_id')):
                lease = conn.execute(f'SELECT holder,expires_at FROM {table} WHERE {key}=?', (sid,)).fetchone()
                if not lease or lease['holder'] != holder or lease['expires_at'] <= time.time():
                    raise DeleteRefused('lease_lost')
        for sid in ids - {root}:
            seen = set()
            while sid != root:
                if sid in seen or sid not in markers:
                    raise DeleteRefused('ambiguous_delegate_lineage')
                seen.add(sid)
                sid = markers[sid]
        children = conn.execute(f'SELECT id FROM sessions WHERE parent_session_id IN ({ph})', targets)
        if any(r['id'] not in ids for r in children):
            raise DeleteRefused('independent_child')
        if conn.execute(f"SELECT 1 FROM sessions WHERE json_extract(COALESCE(model_config, '{{}}'), '$._branched_from') IN ({ph}) LIMIT 1", targets).fetchone():
            raise DeleteRefused('independent_branch_marker')
        for row in conn.execute('SELECT session_key,entry_json FROM gateway_routing'):
            if row['session_key'] in ids or _references(json.loads(row['entry_json']), ids):
                raise DeleteRefused('routed_session')
        for row in conn.execute('SELECT origin_session,origin_ui_session_id,parent_session_id,event_json,result_json,task_json FROM async_delegations'):
            if any(row[k] in ids for k in ('origin_session', 'origin_ui_session_id', 'parent_session_id')):
                raise DeleteRefused('associated_async_delegation')
            if any(row[k] and _references(json.loads(row[k]), ids) for k in ('event_json', 'result_json', 'task_json')):
                raise DeleteRefused('associated_async_delegation')

    def delete(self, session_id, operation_id):
        if not _valid_id(session_id) or not _valid_operation(operation_id):
            raise DeleteRefused('invalid_deletion_input', 400)
        _supported_storage(self.db)
        previous = self.receipt(operation_id)
        if previous:
            if previous['id'] != session_id:
                raise DeleteRefused('operation_conflict')
            return previous
        receipt = {'id': session_id, 'operation_id': operation_id, 'deleted': False,
                   'status': 'unknown', 'scope': 'native_session_db', 'files_deleted': False}
        previous = self._claim(receipt)
        if previous:
            if previous['id'] != session_id:
                raise DeleteRefused('operation_conflict')
            return previous
        holder = 'mobile-delete:' + uuid.uuid4().hex
        turns, compressions = [], []
        native = _NativeWriteFacade(self.db)
        cls = type(self.db)
        try:
            if self.quiescent() is not True:
                raise DeleteRefused('native_not_quiescent')
            targets = self.db.get_session_delete_targets(session_id)
            if not targets:
                raise DeleteRefused('session_not_found', 404)
            if len(targets) > 100:
                raise DeleteRefused('delegate_scope_too_large')
            if any(not _valid_id(sid) for sid in targets):
                raise DeleteRefused('invalid_delegate_identity')
            targets = sorted(targets)
            for sid in targets:
                if not cls.try_acquire_session_turn_lease(native, sid, holder, patience_s=0):
                    raise DeleteRefused('session_busy')
                turns.append(sid)
                if not cls.try_acquire_compression_lock(native, sid, holder):
                    raise DeleteRefused('session_busy')
                compressions.append(sid)
            native.validate = lambda conn: self._validate(conn, session_id, targets, holder)
            if not cls.delete_session(native, session_id, expected_delete_ids=targets):
                raise DeleteRefused('session_changed')
            receipt.update(deleted=True, status='deleted', deleted_ids=targets)
            self._save(receipt)
            return receipt
        except Exception as error:
            failure = error if isinstance(error, DeleteRefused) else DeleteRefused('native_evidence_unavailable', 503)
            if not native.mutation_started:
                receipt.update(status='refused', reason=failure.code)
                try:
                    self._save(receipt)
                except Exception:
                    raise DeleteRefused('receipt_unavailable', 503) from None
                failure.receipt = dict(receipt)
            raise failure from None
        finally:
            native.validate = None
            for sid in reversed(compressions):
                cls.release_compression_lock(native, sid, holder)
            for sid in reversed(turns):
                cls.release_session_turn_lease(native, sid, holder)




def _native_evidence(adapter):
    if __package__:
        from .native_maintenance import maintenance_snapshot, _local_sources
    else:
        from native_maintenance import maintenance_snapshot, _local_sources
    return maintenance_snapshot(adapter, *_local_sources())


def session_deletion_adapter(base, home, *, evidence=_native_evidence):
    """Compose OUTSIDE owner maintenance/run controls; never on member paths."""
    import asyncio
    import threading
    from aiohttp import web

    class SessionDeletionAdapter(base):
        def __init__(self, *args, **kwargs):
            self._session_deletion_workers = 0
            self._mobile_deletion_busy = False
            self._mobile_deletion_closing = False
            self._mobile_deletion_shutdown = None
            self._mobile_deletion_ingress = 0
            self._mobile_deletion_jobs = set()
            super().__init__(*args, **kwargs)
            if not hasattr(self, '_maintenance_lock'):
                self._maintenance_lock = threading.RLock()

        def _deletion_quiescent(self):
            try:
                snapshot = evidence(self)
                work = snapshot['work']
                notifications = snapshot['notifications']
                return (snapshot['status'] == 'ok' and isinstance(work, dict) and bool(work)
                        and all(type(v) is int and v == (1 if k == 'session_deletion_workers' else 0)
                                for k, v in work.items())
                        and notifications['status'] == 'ok'
                        and type(notifications['backlog']) is int and notifications['backlog'] == 0)
            except Exception:
                return False

        def _deletion_access(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            if request.match_info.get('profile') or request.path.startswith('/p/'):
                return web.json_response({'error': {'code': 'owner_default_only'}}, status=403)
            return None

        async def _deletion_job(self, function, *, exclusive=False):
            # Admission and registration have no intervening event-loop await.
            # Closing is monotonic and independent of an ordinary job's fence.
            if self._mobile_deletion_closing:
                raise DeleteRefused('native_closing', 503)
            import contextvars
            loop = asyncio.get_running_loop()
            completion = loop.create_future()
            # Track actual worker lifetime independently of executor submission
            # and the HTTP waiter: either can fail while user work is running.
            self._mobile_deletion_jobs.add(completion)
            completion.add_done_callback(
                lambda done: None if done.cancelled() else done.exception())
            with self._maintenance_lock:
                self._session_deletion_workers += 1
            started = released = False

            def finished(result, error):
                self._mobile_deletion_jobs.discard(completion)
                if exclusive:
                    self._mobile_deletion_busy = False
                if not completion.done():
                    if error is None:
                        completion.set_result(result)
                    else:
                        completion.set_exception(error)

            def release(result=None, error=None, *, pre_start_only=False):
                nonlocal released
                with self._maintenance_lock:
                    if released or (pre_start_only and started):
                        return
                    released = True
                    self._session_deletion_workers -= 1
                loop.call_soon_threadsafe(finished, result, error)

            def run():
                nonlocal started
                with self._maintenance_lock:
                    # submit() can enqueue and then fail to start a thread.
                    # A released leftover queue entry must never run user work.
                    if released:
                        return
                    started = True
                result = error = None
                try:
                    result = function()
                except BaseException as exc:
                    error = exc
                finally:
                    release(result, error)

            try:
                context = contextvars.copy_context()
                submitted = loop.run_in_executor(None, context.run, run)
            except BaseException as exc:
                release(error=exc, pre_start_only=True)
                raise

            def submitted_done(done):
                # Pool shutdown can cancel queued work without entering run.
                error = asyncio.CancelledError() if done.cancelled() else done.exception()
                if error is not None:
                    release(error=error, pre_start_only=True)
            submitted.add_done_callback(submitted_done)
            return await asyncio.shield(completion)

        async def _handle_mobile_delete(self, request):
            error = self._deletion_access(request)
            if error is not None:
                return error
            try:
                body, error = await self._read_json_body(request)
            except Exception:
                body, error = None, None
            if error is not None:
                return error
            sid = request.match_info['session_id']
            if (not isinstance(body, dict) or set(body) != {'confirm', 'operation_id'}
                    or body['confirm'] is not True or not _valid_operation(body['operation_id'])
                    or not _valid_id(sid)):
                return web.json_response({'error': {'code': 'invalid_deletion_input'}}, status=400)
            if self._mobile_deletion_closing:
                return web.json_response({'error': {'code': 'native_closing'}}, status=503)
            if self._mobile_deletion_busy:
                return web.json_response({'error': {'code': 'deletion_in_progress'}}, status=409)
            # Capture admitted mutations before closing this listener's ingress.
            admitted = self._mobile_deletion_ingress
            self._mobile_deletion_busy = True
            scheduled = False
            try:
                def run():
                    # Owner-only fixed home; native open/schema work belongs
                    # to this tracked worker too, not an untracked lazy await.
                    _supported_storage()
                    db = self._ensure_session_db()
                    guard = DeletionGuard(db, home, quiescent=lambda: admitted == 0 and self._deletion_quiescent())
                    return guard.delete(sid, body['operation_id'])
                scheduled = True
                receipt = await self._deletion_job(run, exclusive=True)
                return web.json_response(receipt, status=200 if receipt['deleted'] else 409)
            except DeleteRefused as exc:
                return web.json_response(getattr(exc, 'receipt', {'error': {'code': exc.code}}), status=exc.status)
            except Exception:
                return web.json_response({'error': {'code': 'native_delete_unavailable'}}, status=503)
            finally:
                if not scheduled:
                    self._mobile_deletion_busy = False

        async def _handle_mobile_delete_receipt(self, request):
            error = self._deletion_access(request)
            if error is not None:
                return error
            operation_id = request.match_info['operation_id']
            if not _valid_operation(operation_id):
                return web.json_response({'error': {'code': 'invalid_operation_id'}}, status=400)
            try:
                def lookup():
                    db = self._ensure_session_db()
                    return DeletionGuard(db, home, quiescent=lambda: False).receipt(operation_id)
                receipt = await self._deletion_job(lookup)
                if receipt is None:
                    return web.json_response({'error': {'code': 'receipt_not_found'}}, status=404)
                return web.json_response(receipt)
            except Exception:
                return web.json_response({'error': {'code': 'receipt_unavailable'}}, status=503)

        async def _handle_capabilities(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            # Prefix mirrors must not advertise a capability they cannot use.
            if not request.match_info.get('profile') and not request.path.startswith('/p/'):
                try:
                    _supported_storage(getattr(self, '_session_db', None))
                    _supported_storage(getattr(self, '_session_dbs', {}).get(str(home)))
                except DeleteRefused:
                    data.setdefault('features', {}).pop('mobile_session_delete_version', None)
                else:
                    data.setdefault('features', {})['mobile_session_delete_version'] = 1
            return web.json_response(data)

        def _http_route_table(self):
            routes = []
            for method, path, handler in super()._http_route_table():
                if method not in {'GET', 'HEAD', 'OPTIONS'}:
                    async def fenced(request, handler=handler):
                        if self._mobile_deletion_closing or self._mobile_deletion_busy:
                            return web.json_response({'error': {'code': 'deletion_in_progress'}}, status=409)
                        self._mobile_deletion_ingress += 1
                        try:
                            return await handler(request)
                        finally:
                            self._mobile_deletion_ingress -= 1
                    handler = fenced
                routes.append((method, path, handler))
            routes.extend([
                ('DELETE', '/api/mobile/sessions/{session_id}', self._handle_mobile_delete),
                ('GET', '/api/mobile/session-deletions/{operation_id}', self._handle_mobile_delete_receipt),
            ])
            return routes

        async def disconnect(self):
            self._mobile_deletion_closing = True
            if self._mobile_deletion_shutdown is None:
                async def drain_and_close():
                    # Admission is already closed, so this set cannot grow.
                    if self._mobile_deletion_jobs:
                        await asyncio.gather(*self._mobile_deletion_jobs, return_exceptions=True)
                    return await super(SessionDeletionAdapter, self).disconnect()
                # Retain the entire teardown, not just gather. Cancellation of
                # any waiter cannot orphan storage close or cause double close.
                self._mobile_deletion_shutdown = asyncio.create_task(drain_and_close())
                self._mobile_deletion_shutdown.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception())
            return await asyncio.shield(self._mobile_deletion_shutdown)

    return SessionDeletionAdapter
