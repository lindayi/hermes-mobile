"""Read-only preservation and receipt checks for guarded native releases."""
from contextlib import contextmanager
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
STATUS_KEYS = ('pending', 'delivered', 'quarantined', 'foreign', 'conflicts',
               'foreign_retained', 'active_workers', 'shutdown_publications')


class _EvidenceChanged(RuntimeError):
    def __init__(self, message='Native notification evidence changed during observation'):
        super().__init__(message)


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
        from backend.native_notifications import OwnerRoute

        identities = self._file_identities()
        owner = self._owner()
        if expected_owner is not None and owner != expected_owner:
            raise RuntimeError('Native notification owner changed')
        scope = json.dumps(['default', str(self.home)], separators=(',', ':'))
        try:
            with _readonly(self.outbox) as db:
                binding = [row['home'] for row in db.execute('SELECT home FROM notification_binding')]
                conflicts = db.execute('SELECT COUNT(*) FROM notification_conflicts').fetchone()[0]
                total = db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0]
                rows, after = [], -(2 ** 63)
                while True:
                    page = db.execute('''SELECT rowid,event_id,event_json,result_json,
                        payload_sha256,route,state,historical,provenance,source_state,receipt_id,
                        lease_token FROM notification_outbox WHERE rowid>?
                        ORDER BY rowid LIMIT ?''', (after, PAGE_SIZE)).fetchall()
                    if not page:
                        break
                    rows.extend(dict(row) for row in page)
                    after = page[-1]['rowid']
            if binding != [str(self.home)] or type(conflicts) is not int or conflicts != 0:
                raise ValueError()
            if type(total) is not int or len(rows) != total:
                raise ValueError()
            classify = OwnerRoute(self.home, self.state)
            records = {}
            for row in rows:
                if (not isinstance(row['event_id'], str) or not row['event_id']
                        or row['event_id'] in records):
                    raise ValueError()
                event = json.loads(row['event_json'])
                if not isinstance(event, dict):
                    raise ValueError()
                route, session_id = classify.resolve(event)
                records[row['event_id']] = self._record(row, route, session_id, event)
            receipt_rows = []
            if include_receipts:
                with _readonly(self.inbox) as db:
                    receipt_rows = [dict(row) for row in db.execute('''SELECT
                        r.event_id,r.scope,r.digest,r.user_id,r.origin,r.inbox_id,r.event_json,
                        r.lease_token,r.acknowledged,i.id AS joined_inbox_id,i.delivery_id,
                        i.user_id AS inbox_user_id,i.title,i.body,i.session_id
                        FROM background_receipts r LEFT JOIN inbox i ON i.id=r.inbox_id
                        WHERE r.scope=? AND r.user_id=?''', (scope, owner))]
            receipts = {}
            for row in receipt_rows:
                if row['event_id'] in receipts:
                    raise ValueError()
                receipts[row['event_id']] = row
            native_status = self.native.request('/v1/mobile/notifications/status')
            self._validate_status(native_status, records, conflicts)
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError('Native notification preservation evidence unavailable') from None
        if identities != self._file_identities():
            raise RuntimeError('Native notification database binding changed')
        return dict(owner=owner, scope=scope, records=records, receipts=receipts,
                    status=native_status, identities=identities)

    @staticmethod
    def _validate_status(status, records, conflicts):
        if not isinstance(status, dict) or set(status) != set(STATUS_KEYS):
            raise ValueError()
        if any(type(status[key]) is not int or status[key] < 0 for key in STATUS_KEYS):
            raise ValueError()
        counts = {key: 0 for key in ('pending', 'delivered', 'quarantined', 'foreign')}
        foreign_retained = 0
        for record in records.values():
            counts[record['state']] += 1
            foreign_retained += int(
                record['route'] == 'foreign' and record['source_state'] == 'foreign-retained')
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

    @staticmethod
    def _fingerprints(records):
        fields = ('event_hash', 'result_hash', 'payload_sha256', 'route',
                  'historical', 'provenance_hash')
        return {key: {field: record[field] for field in fields}
                for key, record in records.items()}

    @staticmethod
    def _preserved(before, after, *, exact=False):
        if exact and set(before) != set(after):
            raise RuntimeError('Native notification records changed after handoff')
        for key, old in before.items():
            new = after.get(key)
            if (new is None or any(old[field] != new[field] for field in
                    ('event_hash', 'payload_sha256', 'route', 'historical', 'provenance_hash'))
                    or old['result_hash'] is not None and old['result_hash'] != new['result_hash']):
                raise RuntimeError('Durable native notification record was not preserved')
        if exact and any(before[key]['result_hash'] != value['result_hash']
                         for key, value in after.items()):
            raise RuntimeError('Durable native notification record changed after handoff')

    @staticmethod
    def _deliveries(records):
        return {key: record['receipt_id'] for key, record in records.items()
                if record['state'] == 'delivered'}

    @staticmethod
    def _preserved_deliveries(before, records):
        # Delivery is monotonic: pending may become delivered, never the reverse,
        # and an established receipt binding cannot be erased or replaced.
        for key, receipt_id in before.items():
            record = records.get(key)
            if record is None or record['state'] != 'delivered' or record['receipt_id'] != receipt_id:
                raise RuntimeError('Delivered native notification record was not preserved')

    @staticmethod
    def _receipt_fingerprints(snapshot):
        fields = ('event_id', 'scope', 'digest', 'user_id', 'origin', 'inbox_id',
                  'event_json', 'joined_inbox_id', 'delivery_id',
                  'inbox_user_id', 'title', 'body', 'session_id')
        result = {}
        for key, receipt in snapshot['receipts'].items():
            acknowledged = receipt['acknowledged']
            if type(acknowledged) is not int or acknowledged not in (0, 1):
                raise RuntimeError('Owned notification acknowledgement is unknown')
            result[key] = dict(
                binding=_sha(_canonical({field: receipt[field] for field in fields})),
                acknowledged=acknowledged)
        return result

    @staticmethod
    def _preserved_receipts(before, snapshot):
        after = snapshot['receipts']
        for key, captured in before.items():
            receipt = after.get(key)
            if receipt is None:
                raise RuntimeError('Owned notification receipt was not preserved')
            current = NativeNotificationCallbacks._receipt_fingerprints(
                dict(receipts={key: receipt}))[key]
            if (current['binding'] != captured['binding']
                    or current['acknowledged'] < captured['acknowledged']):
                raise RuntimeError('Owned notification receipt was not preserved')

    def _require_delivered_receipts(self, snapshot):
        for event_id, record in snapshot['records'].items():
            if record['route'] != 'owned' or record['state'] != 'delivered':
                continue
            focused = dict(snapshot, records={event_id: record},
                           receipts={event_id: snapshot['receipts'][event_id]}
                           if event_id in snapshot['receipts'] else {})
            if not self._receipts_complete(focused):
                raise RuntimeError('Owned notification receipt is unavailable')

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
        # Capture precedes drain: legitimate appends may race the native status
        # reads. Retry only that churn, re-proving gate and identity each time.
        for attempt in range(CAPTURE_ATTEMPTS):
            if attempt:
                self.sleep(self.poll_interval)
                self._require_controller_gate(baseline['gate_owner'])
                if (self.native.attest(root) != baseline['pid']
                        or self.native._start_ticks(baseline['pid']) != baseline['start_ticks']):
                    raise RuntimeError('Native notification process identity changed')
            try:
                snapshot = self._snapshot(include_receipts=True)
                self._require_health(identity=(baseline['pid'], baseline['start_ticks']),
                                     baseline=baseline, require_idle=False, snapshot=snapshot)
                break
            except _EvidenceChanged:
                if attempt + 1 == CAPTURE_ATTEMPTS:
                    raise
        self.baseline = dict(baseline)
        self.owner_id, self.scope = snapshot['owner'], snapshot['scope']
        self._require_delivered_receipts(snapshot)
        self.identities = snapshot['identities']
        self.initial_records = self._fingerprints(snapshot['records'])
        self.initial_deliveries = self._deliveries(snapshot['records'])
        self.initial_receipts = self._receipt_fingerprints(snapshot)
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
        self.handoff_receipts = self._receipt_fingerprints(snapshot)
        self._preserved(self.initial_records, snapshot['records'])
        self._preserved_deliveries(self.initial_deliveries, snapshot['records'])
        self._preserved_receipts(self.initial_receipts, snapshot)
        self._require_delivered_receipts(snapshot)
        if snapshot['identities'] != self.identities:
            raise RuntimeError('Native notification database binding changed')
        if not self._require_health(identity=(self.baseline['pid'], self.baseline['start_ticks']),
                                    baseline=self.baseline, require_idle=True, snapshot=snapshot):
            raise RuntimeError('Native notification handoff is not idle')
        self.handoff_records = self._fingerprints(snapshot['records'])
        self.handoff_deliveries = self._deliveries(snapshot['records'])
        return True

    def _receipts_complete(self, snapshot):
        from backend.background_delivery import BackgroundDeliveryService

        for event_id, record in snapshot['records'].items():
            if record['route'] == 'foreign':
                continue
            if record['state'] != 'delivered':
                return False
            receipt = snapshot['receipts'].get(event_id)
            if receipt is None:
                return False
            event = record['event']
            try:
                valid = (
                    receipt['scope'] == self.scope
                    and receipt['user_id'] == self.owner_id
                    and receipt['digest'] == record['payload_sha256']
                    and _canonical(json.loads(receipt['event_json'])) == _canonical(event)
                    and receipt['origin'] == BackgroundDeliveryService._origin(event)
                    and receipt['lease_token'] == record['lease_token']
                    and receipt['inbox_id'] == record['receipt_id']
                    and receipt['joined_inbox_id'] == receipt['inbox_id']
                    and receipt['inbox_user_id'] == self.owner_id
                    and receipt['delivery_id'] == 'native-event:v1:' + json.dumps([self.scope, event_id])
                    and receipt['title'] == 'Background result'
                    and receipt['body'] == BackgroundDeliveryService._body(event)
                    and receipt['session_id'] == record['session_id'])
            except (KeyError, TypeError, ValueError):
                raise RuntimeError('Owned notification receipt is inconsistent') from None
            if not valid:
                raise RuntimeError('Owned notification receipt is inconsistent')
            if type(receipt['acknowledged']) is not int or receipt['acknowledged'] not in (0, 1):
                raise RuntimeError('Owned notification acknowledgement is unknown')
            if receipt['acknowledged'] != 1:
                return False
        return True

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
            try:
                snapshot = self._snapshot(expected_owner=self.owner_id, include_receipts=True)
                if snapshot['identities'] != self.identities:
                    raise RuntimeError('Native notification database binding changed')
                self._preserved(self.handoff_records, self._fingerprints(snapshot['records']), exact=True)
                self._preserved_deliveries(self.handoff_deliveries, snapshot['records'])
                self._preserved_receipts(self.initial_receipts, snapshot)
                self._preserved_receipts(self.handoff_receipts, snapshot)
                ready = self._require_health(identity=(pid, started), root=stage,
                                             require_idle=True, snapshot=snapshot)
            except _EvidenceChanged:
                ready = False
                snapshot = None
            if ready and self._receipts_complete(snapshot):
                return True
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise RuntimeError('Native notification receipt verification timed out')
            self.sleep(min(self.poll_interval, remaining))

    def verify_rollback(self, root, baseline):
        if not self.capture_attempted:
            return True
        if (self.baseline is None or self.initial_records is None or self.initial_receipts is None
                or baseline != self.baseline
                or Path(root).resolve(strict=True) != Path(self.baseline['root']).resolve(strict=True)):
            raise RuntimeError('Native notification rollback baseline is unavailable')
        self._require_controller_gate(self.baseline['gate_owner'])
        self._require_native_state_dir()
        pointer = self.controller / 'current'
        if not pointer.is_symlink() or pointer.resolve(strict=True) != self.bridge_root:
            raise RuntimeError('Native notification rollback bridge binding changed')
        self._source(root, self.baseline['source_hashes'])
        pid = self.native.attest(Path(root))
        started = self.native._start_ticks(pid)
        if type(pid) is not int or pid <= 0 or type(started) is not int or started <= 0:
            raise RuntimeError('Native notification rollback process identity is unknown')
        snapshot = self._snapshot(expected_owner=self.owner_id, include_receipts=True)
        if snapshot['identities'] != self.identities:
            raise RuntimeError('Native notification database binding changed')
        handed_off = self.handoff_records is not None
        expected = self.handoff_records if handed_off else self.initial_records
        # Before handoff the old listener was not yet drained and may append.
        self._preserved(expected, self._fingerprints(snapshot['records']), exact=handed_off)
        self._preserved_deliveries(self.initial_deliveries, snapshot['records'])
        if handed_off:
            self._preserved_deliveries(self.handoff_deliveries, snapshot['records'])
        self._preserved_receipts(self.initial_receipts, snapshot)
        if self.handoff_receipts is not None:
            self._preserved_receipts(self.handoff_receipts, snapshot)
        self._require_delivered_receipts(snapshot)
        self._require_health(identity=(pid, started), baseline=self.baseline,
                             require_idle=False, snapshot=snapshot)
        return True
