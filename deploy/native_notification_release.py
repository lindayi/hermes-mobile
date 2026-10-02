"""Read-only preservation and receipt checks for guarded native releases."""
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
import contextvars
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


# Retained outbox history is append-only; read it in bounded pages, never truncated.
PAGE_SIZE = 500
CAPTURE_ATTEMPTS = 5
# Per-snapshot owner-route results reused across pages; evicted, never grown.
ROUTE_CACHE_KEYS = 512
SCRATCH_CACHE_KIB = 1024
STATUS_KEYS = ('pending', 'delivered', 'quarantined', 'foreign', 'conflicts',
               'foreign_retained', 'active_workers', 'shutdown_publications')


class _EvidenceChanged(RuntimeError):
    def __init__(self, message='Native notification evidence changed during observation'):
        super().__init__(message)


class _Pending(_EvidenceChanged):
    def __init__(self):
        super().__init__('Owned notification receipt acknowledgement is still pending')


_ROUTES = contextvars.ContextVar('native_notification_routes', default=None)
_RECORD_FIELDS = ('event_hash', 'result_hash', 'payload_sha256', 'route', 'state', 'historical',
                  'provenance_hash', 'source_state', 'receipt_id', 'lease_token', 'session_id')
_FINGERPRINT_FIELDS = ('event_hash', 'result_hash', 'payload_sha256', 'route',
                       'historical', 'provenance_hash')
_RAW_FIELDS = ('event_id', 'event_json', 'result_json', 'payload_sha256', 'route', 'state',
               'historical', 'provenance', 'source_state', 'receipt_id', 'lease_token')
_RECEIPT_FIELDS = ('event_id', 'scope', 'digest', 'user_id', 'origin', 'inbox_id',
                   'event_json', 'joined_inbox_id', 'delivery_id',
                   'inbox_user_id', 'title', 'body', 'session_id')


class _ScratchProof:
    """Private disk-backed proof for one release worker; deleted when closed.

    SQLite's empty filename creates an unnamed 0600 temporary database that is
    unlinked immediately and removed on close or process exit. Only bounded pages
    and a bounded page cache are resident; untyped columns preserve storage types.
    """

    def __init__(self):
        try:
            db = sqlite3.connect('', isolation_level=None)
            db.row_factory = sqlite3.Row
            if [tuple(row)[1:] for row in db.execute('PRAGMA database_list')] != [('main', '')]:
                raise ValueError()
            db.execute(f'PRAGMA cache_size=-{int(SCRATCH_CACHE_KIB)}')
            db.execute('PRAGMA temp_store=FILE')
            db.executescript('''
                CREATE TABLE raw(snap, event_id, event_json, result_json, payload_sha256, route,
                    state, historical, provenance, source_state, receipt_id, lease_token,
                    PRIMARY KEY(snap, event_id));
                CREATE TABLE records(snap, event_id, event_hash, result_hash, payload_sha256,
                    route, state, historical, provenance_hash, source_state, receipt_id,
                    lease_token, session_id, receipt_check, PRIMARY KEY(snap, event_id));
                CREATE TABLE receipts(snap, event_id, binding, acknowledged,
                    PRIMARY KEY(snap, event_id));''')
        except (sqlite3.Error, ValueError):
            raise RuntimeError('Native notification proof scratch is unavailable') from None
        self.db, self.serial = db, 0

    def execute(self, sql, values=()):
        if self.db is None:
            raise RuntimeError('Native notification proof scratch is closed')
        return self.db.execute(sql, values)

    def exists(self, sql, values):
        return self.execute(sql + ' LIMIT 1', values).fetchone() is not None

    def discard(self, snap):
        if self.db is not None and snap is not None:
            for table in ('raw', 'records', 'receipts'):
                self.db.execute(f'DELETE FROM {table} WHERE snap=?', (snap,))

    def close(self):
        db, self.db = self.db, None
        if db is not None:
            db.close()


class _ProofView(Mapping):
    """Read-only per-key view of one scratch snapshot; iteration is paged."""

    def __init__(self, proof, table, snap, fields, *, where='', scalar=False):
        self.proof, self.table, self.snap = proof, table, snap
        self.fields, self.where, self.scalar = fields, where, scalar

    def __getitem__(self, key):
        row = self.proof.execute(
            f'SELECT {",".join(self.fields)} FROM {self.table} '
            f'WHERE snap=? AND event_id=? {self.where}', (self.snap, key)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0] if self.scalar else dict(zip(self.fields, row))

    def __iter__(self):
        after = -1
        while True:
            page = self.proof.execute(
                f'SELECT rowid,event_id FROM {self.table} WHERE snap=? AND rowid>? {self.where} '
                'ORDER BY rowid LIMIT ?', (self.snap, after, PAGE_SIZE)).fetchall()
            if not page:
                return
            after = page[-1][0]
            yield from (row[1] for row in page)

    def __len__(self):
        return self.proof.execute(f'SELECT COUNT(*) FROM {self.table} WHERE snap=? {self.where}',
                                  (self.snap,)).fetchone()[0]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


class _RouteSession:
    """One coherent set of owner-route read snapshots shared by every page."""

    def __init__(self, stack, home, state):
        from backend.native_notifications import readonly

        self.stack, self.readonly = stack, readonly
        self.binding = (Path(home), Path(state))
        self.runs = self.native = None
        self.cache = {}
        auth = stack.enter_context(readonly(Path(state) / 'auth.sqlite'))
        owners = auth.execute(
            "SELECT id FROM users WHERE role='owner' AND profile='default' AND status='ready'"
        ).fetchall()
        if (len(owners) != 1 or not isinstance(owners[0]['id'], str)
                or not owners[0]['id']):
            raise ValueError('default owner is unavailable')
        self.owner_id = owners[0]['id']

    def _resolve_keys(self, keys):
        """Resolve session keys to (root, chain, tip, deleted) or None, in batches."""
        result = {key: self.cache[key] for key in keys if key in self.cache}
        missing = sorted(keys - set(result))
        if not missing:
            return result
        if self.runs is None:
            home, state = self.binding
            self.runs = self.stack.enter_context(self.readonly(state / 'runs.sqlite'))
            self.native = self.stack.enter_context(self.readonly(home / 'state.db'))
        runs, native = self.runs, self.native
        run_rows = {}
        for offset in range(0, len(missing), 400):
            page = missing[offset:offset + 400]
            placeholders = ','.join('?' for _ in page)
            # Prove uniqueness across all default-profile owners in SQL before
            # returning rows: one ambiguous key must not fan out Python storage
            # or lineage walks. COUNT(*)=1 also makes the selected row exact.
            found = runs.execute(
                f'''SELECT upstream_id,session_id,user_id FROM runs
                    WHERE profile='default' AND upstream_id IN ({placeholders})
                    GROUP BY upstream_id HAVING COUNT(*)=1''', page)
            for row in found:
                if row['user_id'] == self.owner_id:
                    run_rows[row['upstream_id']] = row

        sessions, children = {}, {}

        def session(session_id):
            if session_id not in sessions:
                row = native.execute('SELECT * FROM sessions WHERE id=?',
                                     (session_id,)).fetchone()
                sessions[session_id] = None if row is None else dict(row)
            return sessions[session_id]

        def lineage(root):
            current = root
            ancestors = set()
            for _ in range(100):
                row = session(current)
                if row is None:
                    return None
                parent = row['parent_session_id']
                if not parent:
                    break
                ancestor = session(parent)
                if (ancestor is None or ancestor['end_reason'] != 'compression'
                        or parent in ancestors):
                    return None
                ancestors.add(current)
                current = parent
            else:
                return None

            chain = set()
            for _ in range(100):
                row = session(current)
                if row is None:
                    return None
                try:
                    config = json.loads(row['model_config'] or '{}')
                except (TypeError, ValueError):
                    return None
                if (row['source'] in ('tool', 'subagent')
                        or row['profile_name'] not in (None, '', 'default')
                        or not isinstance(config, dict)
                        or '_branched_from' in config or '_delegate_from' in config
                        or current in chain):
                    return None
                chain.add(current)
                if row['end_reason'] != 'compression':
                    return frozenset(chain), current
                if current not in children:
                    # Two eligible children positively prove ambiguity; do not
                    # materialize siblings beyond that proof. Filter before LIMIT.
                    children[current] = [child['id'] for child in native.execute(
                        '''SELECT id FROM sessions WHERE parent_session_id=?
                           AND COALESCE(source,'') NOT IN ('tool','subagent')
                           AND json_extract(COALESCE(model_config,'{}'),
                               '$._branched_from') IS NULL
                           AND json_extract(COALESCE(model_config,'{}'),
                               '$._delegate_from') IS NULL LIMIT 2''',
                        (current,))]
                if len(children[current]) != 1:
                    return None
                current = children[current][0]
            return None

        lineages = {}
        for row in run_rows.values():
            if isinstance(row['session_id'], str) and row['session_id']:
                lineages.setdefault(row['session_id'], None)
        for root in lineages:
            lineages[root] = lineage(root)

        lineage_sessions = {
            session_id
            for resolved in lineages.values() if resolved is not None
            for session_id in resolved[0]
        }
        deleted = set()
        sorted_sessions = sorted(lineage_sessions)
        for offset in range(0, len(sorted_sessions), 400):
            page = sorted_sessions[offset:offset + 400]
            placeholders = ','.join('?' for _ in page)
            deleted.update(row['session_id'] for row in runs.execute(
                f'''SELECT session_id FROM session_deletions
                    WHERE user_id=? AND profile='default'
                    AND session_id IN ({placeholders})''',
                [self.owner_id, *page]).fetchall())

        resolved_keys = {}
        for key in missing:
            row = run_rows.get(key)
            resolved = None
            if row is not None:
                root = row['session_id']
                found = lineages.get(root)
                if found is not None:
                    chain, tip = found
                    resolved = (root, chain, tip, any(name in deleted for name in chain))
            resolved_keys[key] = resolved
        if len(self.cache) + len(resolved_keys) > ROUTE_CACHE_KEYS:
            self.cache.clear()
        if len(resolved_keys) <= ROUTE_CACHE_KEYS:
            self.cache.update(resolved_keys)
        result.update(resolved_keys)
        return result

    def route(self, events):
        routes = [None] * len(events)
        keys = set()
        for index, event in enumerate(events):
            kind = event.get('type')
            if kind not in ('async_delegation', 'completion', 'watch_match'):
                routes[index] = ('quarantined', None)
            elif kind == 'async_delegation' and not event.get('delegation_id'):
                routes[index] = ('quarantined', None)
            elif (event.get('platform') not in (None, '', 'api', 'api_server')
                    or any(event.get(key) for key in
                           ('scope_id', 'chat_id', 'chat_type', 'thread_id', 'user_id'))):
                routes[index] = ('foreign', None)
            else:
                key = event.get('session_key')
                if isinstance(key, str) and ':' in key:
                    routes[index] = ('foreign', None)
                elif isinstance(key, str):
                    keys.add(key)
                else:
                    routes[index] = ('quarantined', None)
        if not keys:
            return routes

        resolved_keys = self._resolve_keys(keys)
        for index, event in enumerate(events):
            if routes[index] is not None:
                continue
            resolved = resolved_keys[event['session_key']]
            if resolved is None:
                routes[index] = ('quarantined', None)
                continue
            root, chain, tip, deleted = resolved
            origin = (event.get('task_id') if event['type'] in
                      ('completion', 'watch_match') else event.get('origin_session_id'))
            if (event['type'] in ('completion', 'watch_match')
                    and event.get('origin_session_id') not in (None, '', origin)):
                routes[index] = ('quarantined', None)
            elif (not origin or origin not in chain or root not in chain
                  or any(event.get(name) and event[name] not in chain
                         for name in ('origin_ui_session_id', 'parent_session_id'))
                  or deleted):
                routes[index] = ('quarantined', None)
            else:
                routes[index] = ('owned', tip)
        return routes


def _resolve_routes(events, home, state):
    """Route one page against the snapshot's shared per-database read snapshots."""
    session = _ROUTES.get()
    if session is not None and session.binding == (Path(home), Path(state)):
        return session.owner_id, session.route(events)
    with ExitStack() as stack:
        session = _RouteSession(stack, home, state)
        return session.owner_id, session.route(events)


class NativeNotificationCallbacks:
    """Prove retained native outbox state and existing owner ACKs, without replay."""

    def __init__(self, paths, native, *, home, clock=time.monotonic, sleep=time.sleep,
                 receipt_timeout=1800, poll_interval=1):
        from .assets import checked_path

        self.native = native
        self.home = Path(home).resolve()
        # The controller owns the release pointer/lock, not the application's
        # databases. Validate supplied paths before resolving away any aliases.
        try:
            self.controller = checked_path(paths.state)
            self.runs = checked_path(paths.database)
        except (OSError, ValueError):
            raise RuntimeError('Native notification journal binding mismatch') from None
        self.state = self.runs.parent
        self.outbox = self.state / 'native-notifications.sqlite'
        self.inbox = self.state / 'notifications.sqlite'
        self.auth = self.state / 'auth.sqlite'
        if self.runs != self.state / 'runs.sqlite':
            raise RuntimeError('Native notification journal binding mismatch')
        # No default live root and no arbitrary caller-selected journal: the
        # exact parent must also be bound by the native probe's private config.
        # Do this before any gate/database reads or deployment side effects.
        self._require_native_state_dir()
        self.clock, self.sleep = clock, sleep
        self.receipt_timeout, self.poll_interval = receipt_timeout, poll_interval
        self.baseline = None
        self.capture_attempted = False
        self.initial_records = None
        self.initial_deliveries = None
        self.initial_receipts = None
        self.handoff_records = None
        self.handoff_deliveries = None
        self.handoff_receipts = None
        self.owner_id = None
        self.scope = None
        self.identities = None
        self.bridge_root = None
        self._proof = _ScratchProof()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        """End the worker-owned scratch proof; later phases fail closed."""
        self._proof.close()

    def __call__(self, stage):
        return self.handoff(stage)

    def _file_identities(self):
        paths = (self.home / 'state.db', self.auth, self.runs, self.inbox, self.outbox)
        result, seen = {}, set()
        try:
            for path in paths:
                info = path.lstat()
                identity = (info.st_dev, info.st_ino)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_mode & 0o077 or identity in seen or path.resolve() != path):
                    raise ValueError()
                seen.add(identity)
                result[str(path)] = identity
        except (OSError, ValueError):
            raise RuntimeError('Native notification database binding unavailable') from None
        return result

    def _require_controller_gate(self, owner):
        try:
            with (self.controller / 'deploy.lock').open('r+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    raise ValueError()
            with _readonly(self.runs) as db:
                gates = [tuple(row) for row in db.execute(
                    'SELECT singleton,owner FROM deployment_gate')]
            if gates != [(1, owner)]:
                raise ValueError()
        except (OSError, sqlite3.Error, ValueError):
            raise RuntimeError('Native notification release gate is not owned') from None

    def _source(self, root, expected=None, *, candidate=False):
        from .native_controls_release import approved_controls, attested_controls
        hashes = (approved_controls(Path(root)) if candidate
                  else attested_controls(Path(root)))
        if ('backend/native_notifications.py' not in hashes
                or expected is not None and hashes != expected):
            raise RuntimeError('Native notification source binding is unsupported')
        return hashes

    def _require_native_state_dir(self):
        try:
            config = json.loads(self.native.config_bytes)
            if not isinstance(config, dict) or config.get('state_dir') != str(self.state):
                raise ValueError()
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError('Native notification outbox path is not attested') from None

    def _owner(self):
        try:
            with _readonly(self.auth) as db:
                owners = [row['id'] for row in db.execute(
                    "SELECT id FROM users WHERE role='owner' AND profile='default' AND status='ready'")]
            if len(owners) != 1 or not isinstance(owners[0], str) or not owners[0]:
                raise ValueError()
        except (OSError, sqlite3.Error, ValueError):
            raise RuntimeError('Native notification owner binding unavailable') from None
        return owners[0]

    @staticmethod
    def _producer_id(event):
        kind = event.get('type')
        if kind == 'async_delegation' and isinstance(event.get('delegation_id'), str):
            return 'async:' + event['delegation_id']
        if (kind == 'completion' and isinstance(event.get('session_id'), str)
                and type(event.get('started_at')) in (int, float)):
            return 'process:' + event['session_id'] + ':' + _canonical(event['started_at'])
        if kind == 'watch_match' and event.get('occurrence_id'):
            return 'occurrence:' + str(event['occurrence_id'])
        if kind in ('completion', 'watch_match'):
            return None
        return None

    def _record(self, row, route, session_id, event):
        from backend.background_delivery import BackgroundDeliveryService

        try:
            if not isinstance(event, dict) or _canonical(event) != row['event_json']:
                raise ValueError()
            if (_sha(row['event_json']) != row['payload_sha256']
                    or self._producer_id(event) not in (row['event_id'], None)
                    or (self._producer_id(event) is None
                        and not (row['event_id'].startswith('migration:')
                                 and event.get('type') in ('completion', 'watch_match')))):
                raise ValueError()
            if row['result_json'] is not None:
                result = json.loads(row['result_json'])
                if _canonical(result) != row['result_json']:
                    raise ValueError()
            else:
                result = None
            if (row['route'] != route or route not in ('owned', 'foreign')
                    or type(row['historical']) is not int or row['historical'] not in (0, 1)
                    or not isinstance(row['provenance'], str) or not row['provenance']
                    or not isinstance(row['source_state'], str)
                    or (route == 'owned' and (not isinstance(session_id, str) or not session_id))
                    or (route != 'owned' and session_id is not None)):
                raise ValueError()
            if route == 'owned':
                # Closed vocabulary from the attested NotificationOutbox default,
                # NotificationCapture.transfer and claim eligibility. Unexamined
                # is a durable outbox default, not proof of SDK acceptance. ACK
                # evidence remains a separate requirement for delivered records.
                retained = ('unexamined', 'accepted', 'missing', 'queue-only', 'dropped', 'delivered')
                unresolved = ('busy', 'conflict', 'incomplete', 'uncertain')
                allowed = {'pending': retained + unresolved, 'delivered': retained}
                if row['source_state'] not in allowed.get(row['state'], ()):
                    raise ValueError()
                envelope = dict(event_id=row['event_id'], payload_sha256=row['payload_sha256'],
                                lease_token=row['lease_token'] or 'synthetic-read-only-proof',
                                event=event, historical=bool(row['historical']))
                BackgroundDeliveryService._envelope(envelope)
            elif (row['state'] != 'foreign' or row['source_state'] != 'foreign-retained'):
                raise ValueError()
            if row['state'] == 'delivered' and (
                    not isinstance(row['receipt_id'], str) or not row['receipt_id']
                    or not isinstance(row['lease_token'], str) or not row['lease_token']):
                raise ValueError()
            return {
                'event_hash': _sha(row['event_json']),
                'result_hash': None if row['result_json'] is None else _sha(row['result_json']),
                'payload_sha256': row['payload_sha256'],
                'route': route,
                'state': row['state'],
                'historical': row['historical'],
                'provenance_hash': _sha(row['provenance']),
                'source_state': row['source_state'],
                'receipt_id': row['receipt_id'],
                'lease_token': row['lease_token'],
                'session_id': session_id,
                'event': event,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError('Native notification record is unknown or inconsistent') from None

    def _snapshot(self, *, expected_owner=None, include_receipts=False):
        """Stream one complete snapshot into the scratch proof in bounded pages.

        The outbox is copied in one count-checked read transaction and closed before
        routing. Pages are routed against one coherent set of route read snapshots,
        then owner receipts are paged in one Inbox read transaction.
        """
        proof = self._proof
        identities = self._file_identities()
        scope = json.dumps(['default', str(self.home)], separators=(',', ':'))
        proof.serial += 1
        snap = proof.serial
        proof.execute('BEGIN')
        try:
            try:
                with _readonly(self.outbox) as db:
                    binding = [row['home'] for row in
                               db.execute('SELECT home FROM notification_binding').fetchall()]
                    conflicts = db.execute(
                        'SELECT COUNT(*) FROM notification_conflicts').fetchone()[0]
                    total = db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0]
                    if (binding != [str(self.home)] or type(conflicts) is not int
                            or conflicts != 0 or type(total) is not int):
                        raise ValueError()
                    copied, after = 0, -(2 ** 63)
                    while True:
                        page = db.execute('''SELECT rowid,event_id,event_json,result_json,
                            payload_sha256,route,state,historical,provenance,source_state,
                            receipt_id,lease_token FROM notification_outbox WHERE rowid>?
                            ORDER BY rowid LIMIT ?''', (after, PAGE_SIZE)).fetchall()
                        if not page:
                            break
                        for row in page:
                            if not isinstance(row['event_id'], str) or not row['event_id']:
                                raise ValueError()
                        proof.db.executemany(
                            f'INSERT INTO raw VALUES(?,{",".join("?" for _ in _RAW_FIELDS)})',
                            [(snap, *(row[name] for name in _RAW_FIELDS)) for row in page])
                        copied += len(page)
                        after = page[-1]['rowid']
                if copied != total:
                    raise ValueError()
                with ExitStack() as stack:
                    routes = _RouteSession(stack, self.home, self.state)
                    owner = routes.owner_id
                    if expected_owner is not None and owner != expected_owner:
                        raise RuntimeError('Native notification owner changed')
                    token = _ROUTES.set(routes)
                    try:
                        self._route_pages(snap, owner)
                    finally:
                        _ROUTES.reset(token)
                if include_receipts:
                    self._receipt_pages(snap, owner, scope)
                proof.execute('DELETE FROM raw WHERE snap=?', (snap,))
                counts = {key: 0 for key in ('pending', 'delivered', 'quarantined', 'foreign')}
                for state, count in proof.execute(
                        'SELECT state,COUNT(*) FROM records WHERE snap=? GROUP BY state',
                        (snap,)).fetchall():
                    counts[state] += count
                foreign_retained = proof.execute(
                    """SELECT COUNT(*) FROM records WHERE snap=? AND route='foreign'
                       AND source_state='foreign-retained'""", (snap,)).fetchone()[0]
                proof.execute('COMMIT')
                native_status = self.native.request('/v1/mobile/notifications/status')
                self._validate_status(native_status, counts, foreign_retained, conflicts)
            except (OSError, sqlite3.Error, KeyError, TypeError, ValueError,
                    json.JSONDecodeError):
                raise RuntimeError('Native notification preservation evidence unavailable') from None
            if identities != self._file_identities():
                raise RuntimeError('Native notification database binding changed')
        except BaseException:
            if proof.db is not None:
                if proof.db.in_transaction:
                    proof.db.execute('ROLLBACK')
                proof.discard(snap)
            raise
        return dict(id=snap, owner=owner, scope=scope, status=native_status,
                    identities=identities, records=self._records_view(snap),
                    receipts=_ProofView(proof, 'receipts', snap, ('binding', 'acknowledged')))

    def _records_view(self, snap, fields=_RECORD_FIELDS, **kwargs):
        return _ProofView(self._proof, 'records', snap, fields, **kwargs)

    def _route_pages(self, snap, owner):
        proof, after = self._proof, -1
        while True:
            page = proof.execute(
                f'SELECT rowid,{",".join(_RAW_FIELDS)} FROM raw WHERE snap=? AND rowid>? '
                'ORDER BY rowid LIMIT ?', (snap, after, PAGE_SIZE)).fetchall()
            if not page:
                return
            after = page[-1]['rowid']
            events = []
            for row in page:
                event = json.loads(row['event_json'])
                if not isinstance(event, dict):
                    raise ValueError()
                events.append(event)
            routed_owner, routes = _resolve_routes(events, self.home, self.state)
            if routed_owner != owner or len(routes) != len(page):
                raise ValueError()
            values = []
            for row, event, (route, session_id) in zip(page, events, routes):
                record = self._record(row, route, session_id, event)
                bound = route == 'owned' and record['state'] == 'delivered'
                values.append((snap, row['event_id'], *(record[name] for name in _RECORD_FIELDS),
                               'missing' if bound else None))
            proof.db.executemany(
                f'INSERT INTO records VALUES(?,?,{",".join("?" for _ in _RECORD_FIELDS)},?)',
                values)

    def _receipt_pages(self, snap, owner, scope):
        from backend.background_delivery import BackgroundDeliveryService

        proof = self._proof
        with _readonly(self.inbox) as db:
            total = db.execute(
                'SELECT COUNT(*) FROM background_receipts WHERE scope=? AND user_id=?',
                (scope, owner)).fetchone()[0]
            copied, after = 0, -(2 ** 63)
            while True:
                page = db.execute('''SELECT r.rowid AS receipt_rowid,
                    r.event_id,r.scope,r.digest,r.user_id,r.origin,r.inbox_id,r.event_json,
                    r.lease_token,r.acknowledged,i.id AS joined_inbox_id,i.delivery_id,
                    i.user_id AS inbox_user_id,i.title,i.body,i.session_id
                    FROM background_receipts r LEFT JOIN inbox i ON i.id=r.inbox_id
                    WHERE r.scope=? AND r.user_id=? AND r.rowid>?
                    ORDER BY r.rowid LIMIT ?''', (scope, owner, after, PAGE_SIZE)).fetchall()
                if not page:
                    break
                copied += len(page)
                after = page[-1]['receipt_rowid']
                for receipt in page:
                    acknowledged = receipt['acknowledged']
                    if type(acknowledged) is not int or acknowledged not in (0, 1):
                        raise RuntimeError('Owned notification acknowledgement is unknown')
                proof.db.executemany('INSERT INTO receipts VALUES(?,?,?,?)', [
                    (snap, receipt['event_id'],
                     _sha(_canonical({name: receipt[name] for name in _RECEIPT_FIELDS})),
                     receipt['acknowledged']) for receipt in page])
                keys = [receipt['event_id'] for receipt in page]
                placeholders = ','.join('?' for _ in keys)
                delivered = {row['event_id']: row for row in proof.execute(
                    f"""SELECT r.event_id,r.payload_sha256,r.lease_token,r.receipt_id,
                        r.session_id,w.event_json FROM records r JOIN raw w
                        ON w.snap=r.snap AND w.event_id=r.event_id
                        WHERE r.snap=? AND r.route='owned' AND r.state='delivered'
                        AND r.event_id IN ({placeholders})""", (snap, *keys)).fetchall()}
                checks = []
                for receipt in page:
                    event_id = receipt['event_id']
                    record = delivered.get(event_id)
                    if record is None:
                        continue
                    try:
                        event = json.loads(record['event_json'])
                        valid = (
                            receipt['scope'] == scope
                            and receipt['user_id'] == owner
                            and receipt['digest'] == record['payload_sha256']
                            and _canonical(json.loads(receipt['event_json'])) == _canonical(event)
                            and receipt['origin'] == BackgroundDeliveryService._origin(event)
                            and receipt['lease_token'] == record['lease_token']
                            and receipt['inbox_id'] == record['receipt_id']
                            and receipt['joined_inbox_id'] == receipt['inbox_id']
                            and receipt['inbox_user_id'] == owner
                            and receipt['delivery_id'] == 'native-event:v1:' + json.dumps(
                                [scope, event_id])
                            and receipt['title'] == 'Background result'
                            and receipt['body'] == BackgroundDeliveryService._body(event)
                            and receipt['session_id'] == record['session_id'])
                    except (KeyError, TypeError, ValueError):
                        valid = False
                    # Only a fully bound receipt can be merely awaiting its bridge ACK.
                    checks.append(('inconsistent' if not valid else
                                   'acknowledged' if receipt['acknowledged'] == 1
                                   else 'unacknowledged', snap, event_id))
                proof.db.executemany(
                    'UPDATE records SET receipt_check=? WHERE snap=? AND event_id=?', checks)
            if type(total) is not int or copied != total:
                raise ValueError()

    @staticmethod
    def _validate_status(status, counts, foreign_retained, conflicts):
        if not isinstance(status, dict) or set(status) != set(STATUS_KEYS):
            raise ValueError()
        if any(type(status[key]) is not int or status[key] < 0 for key in STATUS_KEYS):
            raise ValueError()
        if any(status[key] != counts[key] for key in counts):
            raise _EvidenceChanged()
        if (status['conflicts'] != conflicts or status['conflicts'] != 0
                or status['quarantined'] != 0):
            raise ValueError()
        if status['foreign_retained'] != foreign_retained:
            raise _EvidenceChanged()

    def _require_health(self, *, identity, baseline=None, root=None, require_idle, snapshot=None):
        try:
            pid, started = identity
            health = self.native.request('/health/detailed')
            observed = (health['pid'], health['native_maintenance']['pid'],
                        health['native_maintenance']['start_ticks'])
            if (any(type(value) is not int or value <= 0 for value in (*identity, *observed))
                    or observed != (pid, pid, started)):
                raise ValueError()
            idle = self.native._ready(health, baseline=baseline, root=root)
            evidence = health['native_maintenance']
            notices = evidence['notifications']
            status = self.native.request('/v1/mobile/notifications/status')
            if snapshot is not None and status != snapshot['status']:
                raise _EvidenceChanged()
            if health['pid'] != evidence['pid'] or notices['unpreserved'] != 0:
                raise ValueError()
            if any(type(notices[key]) is not int or notices[key] < 0 for key in
                   ('backlog', 'durable_retained', 'unpreserved', 'web_pending',
                    'quarantined', 'foreign_retained', 'web_delivered', 'shutdown_publications')):
                raise ValueError()
            work = evidence['work']
            if (not isinstance(status, dict) or set(status) != set(STATUS_KEYS)
                     or any(type(status[key]) is not int or status[key] < 0 for key in STATUS_KEYS)):
                raise ValueError()
            if (status['pending'] != notices['web_pending']
                    or status['quarantined'] != notices['quarantined']
                    or status['foreign_retained'] != notices['foreign_retained']
                    or status['delivered'] != notices['web_delivered']
                    or status['active_workers'] != work['notification_workers']
                    or status['shutdown_publications'] != notices['shutdown_publications']):
                raise _EvidenceChanged()
            # Health cannot choose its own expected process. Bracket all HTTP
            # reads with the phase's independently attested PID/start identity.
            observed_pid = self.native.attest(Path(baseline['root'] if baseline is not None else root))
            if type(observed_pid) is not int or observed_pid != pid:
                raise ValueError()
            observed_start = self.native._start_ticks(pid)
            if type(observed_start) is not int or observed_start != started:
                raise ValueError()
            if require_idle and not idle:
                return False
            return True
        except _EvidenceChanged:
            raise
        except (OSError, KeyError, TypeError, ValueError, RuntimeError):
            raise RuntimeError('Native notification readiness evidence unavailable') from None

    def _preserved(self, before, after, *, exact=False):
        exists = self._proof.exists
        if exact and (
                exists('''SELECT 1 FROM records a WHERE a.snap=? AND NOT EXISTS(SELECT 1
                    FROM records b WHERE b.snap=? AND b.event_id=a.event_id)''', (before, after))
                or exists('''SELECT 1 FROM records a WHERE a.snap=? AND NOT EXISTS(SELECT 1
                    FROM records b WHERE b.snap=? AND b.event_id=a.event_id)''', (after, before))):
            raise RuntimeError('Native notification records changed after handoff')
        if exists('''SELECT 1 FROM records a LEFT JOIN records b
                ON b.snap=? AND b.event_id=a.event_id WHERE a.snap=? AND (b.event_id IS NULL
                OR a.event_hash IS NOT b.event_hash OR a.payload_sha256 IS NOT b.payload_sha256
                OR a.route IS NOT b.route OR a.historical IS NOT b.historical
                OR a.provenance_hash IS NOT b.provenance_hash
                OR (a.result_hash IS NOT NULL AND a.result_hash IS NOT b.result_hash))''',
                  (after, before)):
            raise RuntimeError('Durable native notification record was not preserved')
        if exact and exists('''SELECT 1 FROM records a JOIN records b
                ON b.snap=? AND b.event_id=a.event_id WHERE a.snap=?
                AND a.result_hash IS NOT b.result_hash''', (after, before)):
            raise RuntimeError('Durable native notification record changed after handoff')

    def _preserved_deliveries(self, before, after):
        # Delivery is monotonic: pending may become delivered, never the reverse,
        # and an established receipt binding cannot be erased or replaced.
        if self._proof.exists('''SELECT 1 FROM records a LEFT JOIN records b
                ON b.snap=? AND b.event_id=a.event_id WHERE a.snap=? AND a.state='delivered'
                AND (b.event_id IS NULL OR b.state IS NOT 'delivered'
                     OR b.receipt_id IS NOT a.receipt_id)''', (after, before)):
            raise RuntimeError('Delivered native notification record was not preserved')

    def _preserved_receipts(self, before, after):
        if self._proof.exists('''SELECT 1 FROM receipts a LEFT JOIN receipts b
                ON b.snap=? AND b.event_id=a.event_id WHERE a.snap=? AND (b.event_id IS NULL
                OR b.binding IS NOT a.binding OR b.acknowledged < a.acknowledged)''',
                              (after, before)):
            raise RuntimeError('Owned notification receipt was not preserved')

    def _receipt_problem(self, snapshot):
        exists, snap = self._proof.exists, snapshot['id']
        for check in ('inconsistent', 'missing', 'unacknowledged'):
            if exists('''SELECT 1 FROM records WHERE snap=? AND route='owned'
                    AND state='delivered' AND receipt_check IS ?''', (snap, check)):
                return check
        return None

    def _require_delivered_receipts(self, snapshot, *, retry_pending=False):
        problem = self._receipt_problem(snapshot)
        if problem == 'inconsistent':
            raise RuntimeError('Owned notification receipt is inconsistent')
        if problem == 'unacknowledged' and retry_pending:
            # Native /ack marks delivered before the bridge records acknowledged=1.
            raise _Pending()
        if problem is not None:
            raise RuntimeError('Owned notification receipt is unavailable')

    def _discard(self, snapshot):
        if snapshot is not None:
            self._proof.discard(snapshot['id'])

    def capture(self, baseline):
        if self.baseline is not None:
            raise RuntimeError('Native notification baseline was already captured')
        self.capture_attempted = True
        root = Path(baseline.get('root', ''))
        source_hashes = self._source(root, baseline.get('source_hashes'))
        if ('backend/native_notifications.py' not in source_hashes
                or type(baseline.get('pid')) is not int
                or type(baseline.get('start_ticks')) is not int
                or not isinstance(baseline.get('gate_owner'), str)
                or not baseline['gate_owner']):
            raise RuntimeError('Native notification baseline is not positively attested')
        self._require_controller_gate(baseline['gate_owner'])
        if (self.native.attest(root) != baseline['pid']
                or self.native._start_ticks(baseline['pid']) != baseline['start_ticks']):
            raise RuntimeError('Native notification process identity changed')
        self._require_native_state_dir()
        pointer = self.controller / 'current'
        if not pointer.is_symlink():
            raise RuntimeError('Native notification bridge baseline is unavailable')
        # Capture precedes drain: legitimate appends and a positively bound native
        # ACK awaiting its bridge receipt ACK may race these reads. Retry only that
        # churn, re-proving gate and identity each time; exhaustion fails closed.
        for attempt in range(CAPTURE_ATTEMPTS):
            if attempt:
                self.sleep(self.poll_interval)
                self._require_controller_gate(baseline['gate_owner'])
                if (self.native.attest(root) != baseline['pid']
                        or self.native._start_ticks(baseline['pid']) != baseline['start_ticks']):
                    raise RuntimeError('Native notification process identity changed')
            snapshot = None
            try:
                snapshot = self._snapshot(include_receipts=True)
                self._require_health(identity=(baseline['pid'], baseline['start_ticks']),
                                     baseline=baseline, require_idle=False, snapshot=snapshot)
                self._require_delivered_receipts(snapshot, retry_pending=True)
                break
            except _EvidenceChanged:
                self._discard(snapshot)
                if attempt + 1 == CAPTURE_ATTEMPTS:
                    raise
            except BaseException:
                self._discard(snapshot)
                raise
        self.baseline = dict(baseline)
        self.owner_id, self.scope = snapshot['owner'], snapshot['scope']
        self.identities = snapshot['identities']
        snap = snapshot['id']
        self.initial_records = self._records_view(snap, _FINGERPRINT_FIELDS)
        self.initial_deliveries = self._records_view(
            snap, ('receipt_id',), where="AND state='delivered'", scalar=True)
        self.initial_receipts = snapshot['receipts']
        self.bridge_root = pointer.resolve(strict=True)
        return True

    def handoff(self, stage):
        if self.baseline is None or self.handoff_records is not None:
            raise RuntimeError('Native notification handoff is not prepared')
        self._require_controller_gate(self.baseline['gate_owner'])
        self._source(stage, candidate=True)
        pointer = self.controller / 'current'
        if not pointer.is_symlink() or pointer.resolve(strict=True) != self.bridge_root:
            raise RuntimeError('Native notification bridge baseline changed')
        if (self.native.attest(Path(self.baseline['root'])) != self.baseline['pid']
                or self.native._start_ticks(self.baseline['pid']) != self.baseline['start_ticks']):
            raise RuntimeError('Native notification process identity changed before handoff')
        snapshot = self._snapshot(expected_owner=self.owner_id, include_receipts=True)
        snap, initial = snapshot['id'], self.initial_records.snap
        # Retained for rollback even if handoff fails: these receipts were observed.
        self.handoff_receipts = snapshot['receipts']
        self._preserved(initial, snap)
        self._preserved_deliveries(initial, snap)
        self._preserved_receipts(initial, snap)
        self._require_delivered_receipts(snapshot)
        if snapshot['identities'] != self.identities:
            raise RuntimeError('Native notification database binding changed')
        if not self._require_health(identity=(self.baseline['pid'], self.baseline['start_ticks']),
                                    baseline=self.baseline, require_idle=True, snapshot=snapshot):
            raise RuntimeError('Native notification handoff is not idle')
        self.handoff_records = self._records_view(snap, _FINGERPRINT_FIELDS)
        self.handoff_deliveries = self._records_view(
            snap, ('receipt_id',), where="AND state='delivered'", scalar=True)
        return True

    def _receipts_complete(self, snapshot):
        if self._receipt_problem(snapshot) == 'inconsistent':
            raise RuntimeError('Owned notification receipt is inconsistent')
        return not self._proof.exists('''SELECT 1 FROM records WHERE snap=? AND route!='foreign'
            AND (state IS NOT 'delivered' OR receipt_check IS NOT 'acknowledged')''',
                                      (snapshot['id'],))

    def probe(self, stage):
        if self.baseline is None or self.handoff_records is None:
            raise RuntimeError('Native notification handoff evidence is unavailable')
        self._require_controller_gate(self.baseline['gate_owner'])
        pointer = self.controller / 'current'
        if not pointer.is_symlink() or pointer.resolve(strict=True) != Path(stage).resolve():
            raise RuntimeError('Native notification candidate is not active')
        self._source(stage, candidate=True)
        pid = self.native.attest(Path(stage))
        started = self.native._start_ticks(pid)
        if (type(pid) is not int or pid <= 0 or type(started) is not int or started <= 0
                or (pid, started) == (self.baseline['pid'], self.baseline['start_ticks'])):
            raise RuntimeError('Native notification activation identity is not fresh')
        deadline = self.clock() + self.receipt_timeout
        while True:
            self._require_controller_gate(self.baseline['gate_owner'])
            if (self.native.attest(Path(stage)) != pid
                    or self.native._start_ticks(pid) != started):
                raise RuntimeError('Native notification activation identity changed')
            snapshot = None
            try:
                try:
                    snapshot = self._snapshot(expected_owner=self.owner_id, include_receipts=True)
                    if snapshot['identities'] != self.identities:
                        raise RuntimeError('Native notification database binding changed')
                    snap = snapshot['id']
                    self._preserved(self.handoff_records.snap, snap, exact=True)
                    self._preserved_deliveries(self.handoff_deliveries.snap, snap)
                    self._preserved_receipts(self.initial_receipts.snap, snap)
                    self._preserved_receipts(self.handoff_receipts.snap, snap)
                    ready = self._require_health(identity=(pid, started), root=stage,
                                                 require_idle=True, snapshot=snapshot)
                except _EvidenceChanged:
                    ready = False
                if ready and self._receipts_complete(snapshot):
                    return True
            finally:
                self._discard(snapshot)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise RuntimeError('Native notification receipt verification timed out')
            self.sleep(min(self.poll_interval, remaining))

    def verify_rollback(self, root, baseline):
        if not self.capture_attempted:
            return True
        if (self.baseline is None or self.initial_records is None or self.initial_receipts is None
                or baseline != self.baseline
                or Path(root).resolve(strict=True) != self.bridge_root):
            raise RuntimeError('Native notification rollback baseline is unavailable')
        self._require_controller_gate(self.baseline['gate_owner'])
        self._require_native_state_dir()
        pointer = self.controller / 'current'
        if not pointer.is_symlink() or pointer.resolve(strict=True) != self.bridge_root:
            raise RuntimeError('Native notification rollback bridge binding changed')
        native_root = Path(self.baseline['root']).resolve(strict=True)
        self._source(native_root, self.baseline['source_hashes'])
        pid = self.native.attest(native_root)
        started = self.native._start_ticks(pid)
        if type(pid) is not int or pid <= 0 or type(started) is not int or started <= 0:
            raise RuntimeError('Native notification rollback process identity is unknown')
        snapshot = self._snapshot(expected_owner=self.owner_id, include_receipts=True)
        try:
            snap = snapshot['id']
            if snapshot['identities'] != self.identities:
                raise RuntimeError('Native notification database binding changed')
            handed_off = self.handoff_records is not None
            expected = self.handoff_records if handed_off else self.initial_records
            # Before handoff the old listener was not yet drained and may append.
            self._preserved(expected.snap, snap, exact=handed_off)
            self._preserved_deliveries(self.initial_deliveries.snap, snap)
            if handed_off:
                self._preserved_deliveries(self.handoff_deliveries.snap, snap)
            self._preserved_receipts(self.initial_receipts.snap, snap)
            if self.handoff_receipts is not None:
                self._preserved_receipts(self.handoff_receipts.snap, snap)
            self._require_delivered_receipts(snapshot)
            self._require_health(identity=(pid, started), baseline=self.baseline,
                                 require_idle=False, snapshot=snapshot)
        finally:
            self._discard(snapshot)
        return True
