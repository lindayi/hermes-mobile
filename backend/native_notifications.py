"""Private durable notification handoff for the dedicated owner listener.

No SDK import occurs at module import. Never opens a profile selected by a request.
The SDK acceptance flag and durable web receipt are deliberately separate.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class NotificationConflict(ValueError):
    pass


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


class OwnerRoute:
    """Positive default-owner run + native conversation proof, fail closed.

    Ambiguous compression siblings are quarantined rather than guessed. Branch,
    delegation, /new and reset edges are never traversed as continuation edges.
    """
    def __init__(self, home, state_dir):
        self.home, self.state_dir = Path(home), Path(state_dir)

    def __call__(self, event, *, with_session=False):
        def result(route, session_id=None):
            return (route, session_id) if with_session else route

        if event.get('type') not in ('async_delegation', 'completion', 'watch_match'):
            return result('quarantined')
        if event.get('type') == 'async_delegation' and not event.get('delegation_id'):
            return result('quarantined')
        key = event.get('session_key')
        if isinstance(key, str) and ':' in key:
            return result('foreign')
        # Keep immutable event routing aligned with the bridge's _route guard.
        if (event.get('platform') not in (None, '', 'api', 'api_server')
                or any(event.get(k) for k in ('scope_id', 'chat_id', 'chat_type', 'thread_id', 'user_id'))):
            return result('foreign')
        try:
            with readonly(self.state_dir / 'auth.sqlite') as auth, readonly(self.state_dir / 'runs.sqlite') as runs, readonly(self.home / 'state.db') as native:
                owners = auth.execute("SELECT id FROM users WHERE role='owner' AND profile='default' AND status='ready'").fetchall()
                if len(owners) != 1:
                    return result('quarantined')
                rows = runs.execute("SELECT session_id,user_id FROM runs WHERE upstream_id=? AND profile='default'", (key,)).fetchall()
                if len(rows) != 1 or rows[0]['user_id'] != owners[0]['id']:
                    return result('quarantined')
                root = rows[0]['session_id']
                chain, current = set(), root
                # Walk backward only over explicitly compression-ended parents.
                for _ in range(100):
                    row = native.execute('SELECT * FROM sessions WHERE id=?', (current,)).fetchone()
                    if row is None:
                        return result('quarantined')
                    parent = row['parent_session_id']
                    if not parent:
                        break
                    ancestor = native.execute('SELECT * FROM sessions WHERE id=?', (parent,)).fetchone()
                    if ancestor is None or ancestor['end_reason'] != 'compression' or parent in chain:
                        return result('quarantined')
                    chain.add(current)
                    current = parent
                else:
                    return result('quarantined')
                chain = set()
                for _ in range(100):
                    row = native.execute('SELECT * FROM sessions WHERE id=?', (current,)).fetchone()
                    config = json.loads(row['model_config'] or '{}')
                    # Web runs may resume the owner's existing gateway chat.
                    # Routing authority is the immutable EVENT + bridge run, not
                    # the native conversation's historical source/chat metadata.
                    if (row['source'] in ('tool', 'subagent') or row['profile_name'] not in (None, '', 'default')
                            or not isinstance(config, dict) or '_branched_from' in config or '_delegate_from' in config
                            or current in chain):
                        return result('quarantined')
                    chain.add(current)
                    if row['end_reason'] != 'compression':
                        break
                    children = native.execute('''SELECT id FROM sessions WHERE parent_session_id=?
                        AND COALESCE(source,'') NOT IN ('tool','subagent')
                        AND json_extract(COALESCE(model_config,'{}'),'$._branched_from') IS NULL
                        AND json_extract(COALESCE(model_config,'{}'),'$._delegate_from') IS NULL''', (current,)).fetchall()
                    if len(children) != 1:
                        return result('quarantined')
                    current = children[0]['id']
                else:
                    return result('quarantined')
                origin = (event.get('task_id') if event.get('type') in ('completion', 'watch_match')
                          else event.get('origin_session_id'))
                if event.get('type') in ('completion', 'watch_match') and event.get('origin_session_id') not in (None, '', origin):
                    return result('quarantined')
                if not origin or origin not in chain or root not in chain:
                    return result('quarantined')
                if any(event.get(k) and event[k] not in chain for k in ('origin_ui_session_id', 'parent_session_id')):
                    return result('quarantined')
                for sid in chain:
                    if runs.execute("SELECT 1 FROM session_deletions WHERE user_id=? AND profile='default' AND session_id=?", (owners[0]['id'], sid)).fetchone():
                        return result('quarantined')
                if not isinstance(current, str) or not current:
                    return result('quarantined')
                return result('owned', current)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return result('quarantined')

    def resolve(self, event):
        return self(event, with_session=True)


class NotificationOutbox:
    def __init__(self, path, *, clock=time.time, lease_seconds=60):
        self.path, self.clock, self.lease_seconds = Path(path), clock, lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError('notification storage must be private')
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS notification_outbox(
                event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL, result_json TEXT,
                payload_sha256 TEXT NOT NULL, route TEXT NOT NULL, state TEXT NOT NULL,
                historical INTEGER NOT NULL, provenance TEXT NOT NULL,
                source_state TEXT NOT NULL DEFAULT 'unexamined',
                lease_token TEXT, lease_until REAL, receipt_id TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS notification_conflicts(
                evidence_sha256 TEXT PRIMARY KEY, event_id TEXT NOT NULL,
                event_json TEXT NOT NULL,result_json TEXT,route TEXT NOT NULL)''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def capture(self, event, result=None, *, route='quarantined', historical=False,
                provenance='publisher', migration_id=None):
        payload = {k: v for k, v in event.items() if k != 'restored'}
        if payload.get('type') == 'async_delegation' and payload.get('delegation_id'):
            key = 'async:' + payload['delegation_id']
        elif payload.get('type') == 'completion' and payload.get('session_id') and payload.get('started_at') is not None:
            key = 'process:' + payload['session_id'] + ':' + canonical(payload['started_at'])
        elif payload.get('occurrence_id'):
            key = 'occurrence:' + str(payload['occurrence_id'])
        elif migration_id:
            key = 'migration:' + migration_id
        else:
            key, route = 'migration:' + uuid.uuid4().hex, 'quarantined'
        if route not in ('owned', 'foreign', 'quarantined'):
            raise ValueError('invalid route')
        encoded = canonical(payload)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM notification_outbox WHERE event_id=?', (key,)).fetchone()
            if prior and (prior['payload_sha256'] != digest or prior['route'] != route
                    or (result is not None and prior['result_json'] not in (None, canonical(result)))):
                evidence = hashlib.sha256(canonical([key, payload, result, route]).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO notification_conflicts VALUES(?,?,?,?,?)',
                           (evidence, key, encoded, canonical(result), route))
                db.commit()  # retain both conflicting attempts; never overwrite the original
                raise NotificationConflict('immutable notification conflict')
            if prior and prior['result_json'] is None and result is not None:
                db.execute('UPDATE notification_outbox SET result_json=? WHERE event_id=?', (canonical(result), key))
            db.execute('''INSERT OR IGNORE INTO notification_outbox
                (event_id,event_json,result_json,payload_sha256,route,state,historical,provenance)
                VALUES(?,?,?,?,?,?,?,?)''', (key, encoded, canonical(result) if result is not None else None,
                    digest, route, 'pending' if route == 'owned' else route, historical, provenance))
        return key

    def import_records(self, records, *, classify):
        """Explicit exact recovery only; never resets SDK delivery state.

        records: [{event: dict, result: dict, provenance: nonempty str,
                   migration_id?: str}]. Call before admission/startup.
        """
        keys = []
        for record in records:
            if (not isinstance(record.get('event'), dict) or not isinstance(record.get('result'), dict)
                    or not isinstance(record.get('provenance'), str) or not record['provenance']):
                raise ValueError('exact recovery event/result/provenance required')
            keys.append(self.capture(record['event'], record['result'],
                historical=True, provenance=record['provenance'],
                migration_id=record.get('migration_id'), route=classify(record['event'])))
        return keys

    def claim(self, limit=20, *, eligible=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid claim limit')
        items = []
        with self.transaction() as db:
            rows = db.execute('''SELECT * FROM notification_outbox WHERE state='pending'
                AND event_id NOT IN (SELECT event_id FROM notification_conflicts)
                AND (lease_until IS NULL OR lease_until<=?) ORDER BY rowid''',
                (self.clock(),)).fetchall()
            for row in rows:
                if eligible is not None and not eligible(dict(row)):
                    continue
                if len(items) >= limit:
                    break
                token = uuid.uuid4().hex
                db.execute('UPDATE notification_outbox SET lease_token=?,lease_until=? WHERE event_id=?',
                           (token, self.clock() + self.lease_seconds, row['event_id']))
                items.append(dict(event_id=row['event_id'], payload_sha256=row['payload_sha256'],
                    lease_token=token, event=json.loads(row['event_json']), historical=bool(row['historical'])))
        return items

    def ack(self, *, event_id, payload_sha256, lease_token, receipt_id):
        if not all(isinstance(v, str) and 0 < len(v) <= 512 for v in
                   (event_id, payload_sha256, lease_token, receipt_id)):
            raise ValueError('invalid acknowledgement')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM notification_outbox WHERE event_id=?', (event_id,)).fetchone()
            if (row is None or row['payload_sha256'] != payload_sha256 or row['lease_token'] != lease_token
                    or db.execute('SELECT 1 FROM notification_conflicts WHERE event_id=?', (event_id,)).fetchone()
                    or row['state'] not in ('pending', 'delivered')
                    or (row['state'] == 'pending' and row['lease_until'] <= self.clock())
                    or (row['state'] == 'delivered' and row['receipt_id'] != receipt_id)):
                raise NotificationConflict('acknowledgement conflict')
            db.execute("UPDATE notification_outbox SET state='delivered',receipt_id=? WHERE event_id=?", (receipt_id, event_id))
        return dict(status='delivered', event_id=event_id, receipt_id=receipt_id)

    def record(self, event_id):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM notification_outbox WHERE event_id=?', (event_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data['event'] = json.loads(data.pop('event_json'))
        data['result'] = json.loads(data.pop('result_json') or 'null')
        return data

    def status(self):
        with self.transaction() as db:
            counts = dict(db.execute('SELECT state,COUNT(*) FROM notification_outbox GROUP BY state'))
            conflicts = db.execute('SELECT COUNT(*) FROM notification_conflicts').fetchone()[0]
            foreign_retained = db.execute("SELECT COUNT(*) FROM notification_outbox WHERE route='foreign' AND source_state='foreign-retained'").fetchone()[0]
        return {**{key: counts.get(key, 0) for key in ('pending', 'delivered', 'quarantined', 'foreign')},
                'conflicts': conflicts, 'foreign_retained': foreign_retained}


class _CaptureLifetime:
    """Process-lifetime counters for a resolved home/outbox; no capture references."""
    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.active = 0
        self.shutdown_publications = 0


# Keep the restart-only uncertainty fence even after all captures/hooks are GC'd.
# Retain only paths, condition and counters, never SDK objects or payloads.
_capture_lifetimes = {}
_capture_lifetimes_lock = threading.Lock()


class NotificationCapture:
    """Install before agent admission; owns only one process's publisher/queue.

    ``install(sdk, registry)`` accepts the actual loaded native module and singleton.
    No new executor, scheduler, agent, gateway or messaging transport is created.
    ``close`` drains registered workers and restores only hooks still ours.
    Already fetched hooks remain lossless after close: they durably capture with
    active-worker accounting and a cumulative shutdown-publication counter,
    shared across fresh instances for the same resolved home/outbox.
    A closed capture is single-use; reconnect with a fresh instance.
    """
    def __init__(self, outbox, home, classify):
        self.outbox, self.home, self.classify = outbox, Path(home).resolve(), classify
        with outbox.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS notification_binding(home TEXT PRIMARY KEY)')
            row = db.execute('SELECT home FROM notification_binding').fetchone()
            if row and row['home'] != str(self.home):
                raise ValueError('notification home binding conflict')
            if not row:
                db.execute('INSERT INTO notification_binding VALUES(?)', (str(self.home),))
        key = (self.home, outbox.path.resolve())
        with _capture_lifetimes_lock:
            self._lifetime = _capture_lifetimes.get(key)
            if self._lifetime is None:
                self._lifetime = _CaptureLifetime()
                _capture_lifetimes[key] = self._lifetime
        self.condition = self._lifetime.condition
        self.closing = False
        self.installed = False

    @property
    def active(self):
        return self._lifetime.active

    @property
    def shutdown_publications(self):
        return self._lifetime.shutdown_publications

    @contextmanager
    def worker(self, *, publication=False):
        with self.condition:
            if self.closing:
                if not publication:
                    raise RuntimeError('notification capture closing')
                # A producer can already hold a hook when it is uninstalled.
                # Count attempts, not delivery; retain the normal durable path.
                self._lifetime.shutdown_publications += 1
            self._lifetime.active += 1
        try:
            yield
        finally:
            with self.condition:
                self._lifetime.active -= 1
                self.condition.notify_all()

    def _matches_home(self):
        return Path(self.sdk._db_path()).resolve() == self.home / 'state.db'

    def source_row(self, event):
        if event.get('type') != 'async_delegation':
            return None
        with readonly(self.home / 'state.db') as db:
            row = db.execute('SELECT * FROM async_delegations WHERE delegation_id=?', (event['delegation_id'],)).fetchone()
        return dict(row) if row else None

    def _source_state(self, key, state):
        with self.outbox.transaction() as db:
            db.execute('UPDATE notification_outbox SET source_state=? WHERE event_id=?', (state, key))

    def transfer(self, key):
        record = self.outbox.record(key)
        event = record['event']
        if not self._matches_home():
            return False
        classified = self.classify(event)
        if record['route'] == 'foreign' and classified == 'foreign':
            # Retire only this dedicated listener's duplicate RAM projection.
            # Source/gateway delivery remains entirely untouched; exact private
            # event + async full result are already committed in our outbox.
            if event.get('type') == 'async_delegation' and record['result'] is None:
                return False
            self._source_state(key, 'foreign-retained')
            return True
        if record['route'] != 'owned' or classified != 'owned':
            return False
        if event.get('type') != 'async_delegation':
            self._source_state(key, 'queue-only')
            return True
        row = self.source_row(event)
        if row is None:
            self._source_state(key, 'missing')
            return record['result'] is not None
        native_event = json.loads(row['event_json'] or 'null')
        if native_event is None or canonical({k:v for k,v in native_event.items() if k != 'restored'}) != canonical(event):
            self._source_state(key, 'conflict')
            return False
        if record['result'] is None or canonical(record['result']) != canonical(json.loads(row['result_json'] or 'null')):
            self._source_state(key, 'incomplete')
            return False
        if row['delivery_state'] != 'pending':
            if record['source_state'] != 'accepted':
                self._source_state(key, row['delivery_state'])
            return True
        token = 'mobile-outbox:' + uuid.uuid4().hex
        if not self.sdk.claim_completion_delivery(event['delegation_id'], token):
            self._source_state(key, 'busy')
            return False
        if self.sdk.complete_completion_delivery(event['delegation_id'], token):
            self._source_state(key, 'accepted')
            return True
        self._source_state(key, 'missing' if self.source_row(event) is None else 'uncertain')
        return False

    def capture_queue(self, event):
        route = self.classify(event)
        migration_id = None
        stable = (event.get('type') == 'async_delegation' and event.get('delegation_id')
                  or event.get('type') == 'completion' and event.get('session_id') and event.get('started_at') is not None
                  or event.get('occurrence_id'))
        if not stable:
            with self.condition:
                previous = self.envelopes.get(id(event))
                migration_id = previous[1] if previous and previous[0] is event else uuid.uuid4().hex
                self.envelopes[id(event)] = (event, migration_id)
        result = None
        if event.get('type') == 'async_delegation' and event.get('delegation_id'):
            prior = self.outbox.record('async:' + event['delegation_id'])
            row = self.source_row(event)
            if prior:
                result = prior['result']
                route = prior['route']
            elif row and row['event_json']:
                native = json.loads(row['event_json'])
                if canonical(native) == canonical({k:v for k,v in event.items() if k != 'restored'}):
                    result = json.loads(row['result_json'] or 'null')
            if result is None:
                route = 'quarantined'
        key = self.outbox.capture(event, result, route=route, historical=bool(event.get('restored')),
                                  provenance='queue', migration_id=migration_id)
        accepted = self.transfer(key)
        if accepted and migration_id:
            with self.condition:
                self.envelopes.pop(id(event), None)
        return accepted

    def install(self, sdk, registry):
        with self.condition:
            if self.closing:
                raise RuntimeError('notification capture closed; create a fresh capture')
            self._install(sdk, registry)

    def _install(self, sdk, registry):
        if self.installed:
            return
        self.sdk, self.registry = sdk, registry
        if not self._matches_home():
            raise ValueError('notification owner home mismatch')
        if getattr(sdk._persist_completion, '_notification_owner', None) is not None:
            raise RuntimeError('notification publisher already owned')
        if getattr(registry.completion_queue, '_notification_owner', None) is not None:
            raise RuntimeError('notification queue already owned')
        self.original_publish = sdk._persist_completion
        self.original_queue = registry.completion_queue
        # Retained queue migration envelopes survive fresh capture installation; no
        # mutation of the event itself and no global queue class monkeypatch.
        self.envelopes = getattr(self.original_queue, '_notification_envelopes', {})
        self.original_queue._notification_envelopes = self.envelopes
        owner = self
        def publish(event, result):
            if not owner._matches_home():
                return owner.original_publish(event, result)
            with owner.worker(publication=True):
                owner.outbox.capture(event, result, route=owner.classify(event))
                return owner.original_publish(event, result)
        publish._notification_owner = self
        self.publish = publish
        class CapturingQueue:
            _notification_owner = owner
            def __getattr__(self, name):
                return getattr(owner.original_queue, name)

            def put(self, event, block=True, timeout=None):
                if not owner._matches_home():
                    return owner.original_queue.put(event, block, timeout)
                with owner.worker(publication=True):
                    if not owner.capture_queue(event):
                        return owner.original_queue.put(event, block, timeout)

            def put_nowait(self, event):
                return self.put(event, False)
        self.queue = CapturingQueue()
        sdk._persist_completion = publish
        registry.completion_queue = self.queue
        self.installed = True
        try:
            self.reconcile_queue()
        except BaseException:
            self.close()
            raise

    def reconcile_queue(self):
        # Admission MUST still be closed at installation. No queue class/global SQLite mutation.
        # Snapshot under queue mutex; remove only the exact captured object.
        with self.original_queue.mutex:
            existing = list(self.original_queue.queue)
        for item in existing:
            if self.capture_queue(item):
                with self.original_queue.mutex:
                    for i, queued in enumerate(self.original_queue.queue):
                        if queued is item:
                            del self.original_queue.queue[i]
                            self.original_queue.unfinished_tasks -= 1
                            self.original_queue.not_full.notify()
                            break

    def claim(self, limit=20):
        with self.worker():
            if not self._matches_home():
                raise ValueError('notification owner home mismatch')
            with self.outbox.transaction() as db:
                rows = db.execute("SELECT event_id,event_json,result_json,route FROM notification_outbox WHERE state IN ('pending','quarantined')").fetchall()
            for row in rows:
                event = json.loads(row['event_json'])
                if self.classify(event) != 'owned':
                    continue
                if row['route'] == 'quarantined':
                    if event.get('type') == 'async_delegation' and row['result_json'] is None:
                        continue
                    with self.outbox.transaction() as db:
                        db.execute("UPDATE notification_outbox SET route='owned',state='pending' WHERE event_id=? AND route='quarantined'", (row['event_id'],))
                self.transfer(row['event_id'])
            self.reconcile_queue()
            def eligible(row):
                return (row['source_state'] in ('accepted','missing','queue-only','dropped','delivered')
                        and self.classify(json.loads(row['event_json'])) == 'owned')
            return self.outbox.claim(limit, eligible=eligible)

    def evidence(self):
        with self.condition:
            return {**self.outbox.status(), 'active_workers': self.active,
                    'shutdown_publications': self.shutdown_publications}

    def close(self):
        with self.condition:
            self.closing = True
            while self.active:
                self.condition.wait()
            if self.installed:
                if self.sdk._persist_completion is self.publish:
                    self.sdk._persist_completion = self.original_publish
                if self.registry.completion_queue is self.queue:
                    self.registry.completion_queue = self.original_queue
                self.installed = False


CAPABILITY = {'version': 1, 'delivery': 'durable-inbox', 'automatic_model_wake': False}


def notification_adapter(base, home, *, state_dir):
    """Compose the dedicated default-owner adapter; call after configuring HOME.

    Extra public methods: ``import_notification_records(records)`` (before
    connect only), ``notification_evidence()`` (aggregate readiness counters).
    Connect installs before base opens admission. Disconnect is shielded and
    drains HTTP workers and native teardown before uninstalling owned hooks.
    After disconnect or failed connect, reconnect using a fresh adapter instance.
    No recovery file is automatically trusted/imported: parent validates it.
    """
    import asyncio
    from aiohttp import web
    home, state_dir = Path(home).resolve(), Path(state_dir).resolve()

    class NotificationAdapter(base):
        def __init__(self, *args, **kwargs):
            self._notification_outbox = NotificationOutbox(state_dir / 'native-notifications.sqlite')
            self._notification_capture = NotificationCapture(self._notification_outbox, home, OwnerRoute(home, state_dir))
            self._notification_jobs = set()
            self._notification_closing = False
            self._notification_shutdown = None
            super().__init__(*args, **kwargs)

        def _notification_sdk(self):
            from tools import async_delegation
            return async_delegation

        def _notification_registry(self):
            from tools.process_registry import process_registry
            return process_registry

        def import_notification_records(self, records):
            if self._notification_capture.installed or self._notification_closing:
                raise RuntimeError('recovery import requires closed admission')
            return self._notification_outbox.import_records(records, classify=self._notification_capture.classify)

        def notification_evidence(self):
            return self._notification_capture.evidence()

        def _archive_pending_notifications(self):
            # Preserve before the SDK registry's startup replay applies age caps.
            if not (home / 'state.db').exists():
                return
            with readonly(home / 'state.db') as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='async_delegations'").fetchone():
                    return
                rows = db.execute("SELECT event_json,result_json FROM async_delegations WHERE delivery_state='pending' AND event_json IS NOT NULL").fetchall()
            for row in rows:
                event, result = json.loads(row['event_json']), json.loads(row['result_json'] or 'null')
                self._notification_outbox.capture(event, result,
                    route=self._notification_capture.classify(event) if isinstance(result, dict) else 'quarantined',
                    historical=True, provenance='native-startup-pending')

        async def connect(self, *args, **kwargs):
            if self._notification_closing or self._notification_capture.closing:
                raise RuntimeError('notification adapter closed; create a fresh adapter')
            if Path(os.environ.get('HERMES_HOME', '')).resolve() != home:
                raise ValueError('configure owner HERMES_HOME before notification startup')
            sdk = self._notification_sdk()
            self._archive_pending_notifications()
            self._notification_capture.install(sdk, self._notification_registry())
            try:
                connected = await super().connect(*args, **kwargs)
                if not connected:
                    self._notification_capture.close()
                return connected
            except BaseException:
                self._notification_capture.close()
                raise

        def _notification_access(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            if request.match_info.get('profile') or request.path.startswith('/p/'):
                return web.json_response({'error': {'code': 'owner_default_only'}}, status=403)
            if self._notification_closing or not self._notification_capture.installed:
                return web.json_response({'error': {'code': 'native_closing'}}, status=503)
            if not self._notification_capture._matches_home():
                return web.json_response({'error': {'code': 'owner_home_mismatch'}}, status=403)
            return None

        async def _notification_job(self, function):
            import contextvars
            from concurrent.futures import Future
            loop = asyncio.get_running_loop()
            lifetime = self._notification_capture._lifetime
            decision = threading.Event()
            completion = Future()
            # This is a worker-exit promise, not an executor/asyncio waiter.
            # Cancellation of either waiter cannot mark real work finished.
            completion.set_running_or_notify_cancel()
            context = contextvars.copy_context()
            accepted = started = released = False
            with lifetime.condition:
                if self._notification_closing or self._notification_capture.closing:
                    raise RuntimeError('native closing')
                lifetime.active += 1
                self._notification_jobs.add(completion)

            def finish(result=None, error=None):
                nonlocal released
                with lifetime.condition:
                    if released:
                        return
                    released = True
                    if error is None:
                        completion.set_result(result)
                    else:
                        completion.set_exception(error)
                    self._notification_jobs.discard(completion)
                    lifetime.active -= 1
                    lifetime.condition.notify_all()

            def run():
                nonlocal started
                # submit can enqueue AND raise (including with an existing
                # worker). No user function runs until submit has returned.
                decision.wait()
                with lifetime.condition:
                    if not accepted or released:
                        return
                    started = True
                try:
                    result = context.run(function)
                except BaseException as exc:
                    finish(error=exc)
                else:
                    finish(result=result)

            try:
                submitted = loop.run_in_executor(None, run)
            except BaseException as exc:
                # Permanently revoke the leftover queue cell before waking it.
                finish(error=exc)
                decision.set()
                raise
            with lifetime.condition:
                accepted = True
            decision.set()

            def cancelled(future):
                nonlocal accepted
                if future.cancelled():
                    with lifetime.condition:
                        if not started:
                            accepted = False
                            finish(error=asyncio.CancelledError())
            submitted.add_done_callback(cancelled)
            waiter = asyncio.wrap_future(completion)
            waiter.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            return await asyncio.shield(waiter)

        async def _handle_notification_status(self, request):
            error = self._notification_access(request)
            if error is not None:
                return error
            return web.json_response(self.notification_evidence())

        async def _handle_notification_mutation(self, request, *, ack=False):
            error = self._notification_access(request)
            if error is not None:
                return error
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('invalid body')
                if ack:
                    if set(body) != {'event_id', 'payload_sha256', 'lease_token', 'receipt_id'}:
                        raise ValueError('invalid ack fields')
                    result = await self._notification_job(lambda: self._notification_outbox.ack(**body))
                else:
                    if set(body) != {'limit'} or type(body['limit']) is not int or not 1 <= body['limit'] <= 50:
                        raise ValueError('invalid claim fields')
                    result = {'items': await self._notification_job(lambda: self._notification_capture.claim(body['limit']))}
                return web.json_response(result)
            except NotificationConflict:
                return web.json_response({'error': {'code': 'notification_conflict'}}, status=409)
            except (ValueError, TypeError):
                return web.json_response({'error': {'code': 'invalid_notification_input'}}, status=400)
            except Exception:
                return web.json_response({'error': {'code': 'notification_unavailable'}}, status=503)

        async def _handle_notification_claim(self, request):
            return await self._handle_notification_mutation(request)

        async def _handle_notification_ack(self, request):
            return await self._handle_notification_mutation(request, ack=True)

        async def _handle_capabilities(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            if not request.match_info.get('profile') and not request.path.startswith('/p/'):
                data['mobile_notifications'] = dict(CAPABILITY)
            return web.json_response(data)

        def _http_route_table(self):
            return [*super()._http_route_table(),
                ('GET', '/v1/mobile/notifications/status', self._handle_notification_status),
                ('POST', '/v1/mobile/notifications/claim', self._handle_notification_claim),
                ('POST', '/v1/mobile/notifications/ack', self._handle_notification_ack)]

        async def disconnect(self):
            self._notification_closing = True
            if self._notification_shutdown is None:
                async def drain():
                    with self._notification_capture.condition:
                        jobs = list(self._notification_jobs)
                    if jobs:
                        await asyncio.gather(*(asyncio.shield(asyncio.wrap_future(job))
                                               for job in jobs), return_exceptions=True)
                    try:
                        return await super(NotificationAdapter, self).disconnect()
                    finally:
                        await asyncio.to_thread(self._notification_capture.close)
                self._notification_shutdown = asyncio.create_task(drain())
                self._notification_shutdown.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            return await asyncio.shield(self._notification_shutdown)

    return NotificationAdapter
