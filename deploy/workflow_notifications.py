"""Consume the fixed private workflow export into the existing owner Inbox."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from deploy.workflow_events import (
    MAX_CLOCK_SKEW, MAX_EVENT_AGE, REPOSITORY_ID, SCHEMA_VERSION, event_digest, validate_export,
)


CONFIG = Path('/home/lindayi/.local/share/hermes-mobile-live/config.json')
DELIVERY_STATE = Path('/home/lindayi/.local/share/hermes-mobile-delivery/state.json')
CONTROLLER_STATE = Path('/home/lindayi/.local/share/hermes-mobile-deploy')
EVENT_NAME = 'workflow-events.json'
ADAPTER_STATE_NAME = 'workflow-notifications.sqlite'
LOCK_NAME = 'workflow-notifications.lock'
MAX_CONFIG_BYTES = 65536
MAX_EXPORT_BYTES = 1048576
MAX_STATE_BYTES = 4194304

_STATE_COLUMNS = {
    'binding': ('singleton', 'version', 'repository_id', 'owner_user_id'),
    'events': ('event_id', 'digest', 'recipient_id', 'status', 'inbox_id', 'created_at', 'updated_at'),
}
_STATE_PRIMARY_KEYS = {'binding': ('singleton',), 'events': ('event_id',)}
_NOTIFICATION_COLUMNS = {
    'inbox': ('id', 'user_id', 'delivery_id', 'title', 'body', 'session_id', 'created_at', 'read'),
    'notification_policy': ('inbox_id', 'category', 'profile', 'device_id'),
    'subscriptions': ('endpoint', 'user_id', 'device_id', 'subscription'),
    'outbox': ('id', 'inbox_id', 'endpoint', 'status', 'attempts', 'next_attempt_at', 'expires_at', 'last_error'),
    'push_preferences': ('user_id', 'device_id', 'payload'),
    'dismissed_inbox': ('inbox_id', 'user_id', 'dismissed_at'),
}
_NOTIFICATION_PRIMARY_KEYS = {
    'inbox': ('id',),
    'notification_policy': ('inbox_id',),
    'subscriptions': ('endpoint',),
    'outbox': ('id',),
    'push_preferences': ('user_id', 'device_id'),
    'dismissed_inbox': ('inbox_id',),
}
_DECISION_TEXT = {
    'approve_production': 'approve production',
    'resolve_review': 'resolve review findings',
    'authorize_sensitive_action': 'authorize the sensitive action',
}
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_RELEASE = re.compile(r'[0-9a-f]{32}\Z')


class Blocked(ValueError):
    """Missing, unsafe, stale, or conflicting evidence must not be guessed."""


@dataclass(frozen=True)
class Paths:
    config: Path = CONFIG
    delivery_state: Path = DELIVERY_STATE
    controller_state: Path = CONTROLLER_STATE


def _owned_private_path(path, *, directory=False, max_bytes=None):
    path = Path(path)
    if not path.is_absolute():
        raise Blocked('An absolute private path is required')
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise Blocked('Required private evidence is unavailable') from error
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (resolved != path or not expected(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or (not directory and info.st_nlink != 1)
            or (max_bytes is not None and info.st_size > max_bytes)):
        raise Blocked('Private evidence path is unsafe or oversized')
    if not directory:
        try:
            parent_info = path.parent.lstat()
            parent_resolved = path.parent.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise Blocked('Private evidence parent is unavailable') from error
        if (parent_resolved != path.parent or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077):
            raise Blocked('Private evidence parent directory is unsafe')
    return info


def _read_private(path, *, max_bytes):
    before = _owned_private_path(path, max_bytes=max_bytes)
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_dev != before.st_dev
                    or info.st_ino != before.st_ino or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077
                    or info.st_size > max_bytes):
                raise Blocked('Private evidence changed while being read')
            data = stream.read(max_bytes + 1)
    except (OSError, ValueError) as error:
        raise Blocked('Private evidence could not be read safely') from error
    if len(data) > max_bytes:
        raise Blocked('Private evidence is oversized')
    return data, info


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Blocked('Duplicate JSON fields are not allowed')
        result[key] = value
    return result


def _json_file(path, *, max_bytes):
    data, info = _read_private(path, max_bytes=max_bytes)
    try:
        value = json.loads(data, object_pairs_hook=_pairs_no_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(Blocked('Invalid JSON number')))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, RecursionError) as error:
        raise Blocked('Private JSON evidence is malformed') from error
    return value, info


def _fresh(info, now):
    age = now.timestamp() - info.st_mtime
    if age > MAX_EVENT_AGE or age < -300:
        raise Blocked('Private evidence is stale or future-dated')


def _assert_readonly_database(path):
    path = Path(path)
    before = _owned_private_path(path)
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(f'{path}{suffix}'):
            raise Blocked('SQLite sidecars are unsupported in read-only mode')
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            header = stream.read(100)
    except OSError as error:
        raise Blocked('Existing private database cannot be inspected safely') from error
    if (not stat.S_ISREG(info.st_mode) or info.st_dev != before.st_dev
            or info.st_ino != before.st_ino or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or len(header) != 100 or header[:16] != b'SQLite format 3\x00'
            or header[18:20] != b'\x01\x01'):
        raise Blocked('Only rollback-journal SQLite databases are supported read-only')


def _open_readonly(path):
    _owned_private_path(path)
    _assert_readonly_database(path)
    try:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        _assert_readonly_database(path)
        return db
    except (sqlite3.Error, ValueError) as error:
        raise Blocked('Existing private database is unavailable') from error


def _has_unique_index(db, table, columns):
    for index in db.execute(f'PRAGMA index_list("{table}")'):
        if index['unique'] and not index['partial']:
            index_name = index['name'].replace('"', '""')
            names = tuple(row['name'] for row in db.execute(f'PRAGMA index_info("{index_name}")'))
            if names == columns:
                return True
    return False


def _table_matches(db, table, columns, primary_key):
    rows = db.execute(f'PRAGMA table_info("{table}")').fetchall()
    return (tuple(row['name'] for row in rows) == columns
            and tuple(row['name'] for row in sorted(rows, key=lambda row: row['pk']) if row['pk'])
            == primary_key)


def _validate_notification_schema(path):
    with closing(_open_readonly(path)) as db:
        try:
            for table, expected in _NOTIFICATION_COLUMNS.items():
                if not _table_matches(db, table, expected, _NOTIFICATION_PRIMARY_KEYS[table]):
                    raise Blocked('Existing notification schema is incompatible')
            for table, columns in (
                    ('inbox', ('user_id', 'delivery_id')),
                    ('subscriptions', ('endpoint',)),
                    ('outbox', ('inbox_id', 'endpoint')),
                    ('push_preferences', ('user_id', 'device_id'))):
                if not _has_unique_index(db, table, columns):
                    raise Blocked('Existing notification uniqueness policy is incompatible')
            expected_foreign_keys = {
                'notification_policy': {('inbox', 'inbox_id', 'id')},
                'outbox': {('inbox', 'inbox_id', 'id'),
                           ('subscriptions', 'endpoint', 'endpoint')},
                'dismissed_inbox': {('inbox', 'inbox_id', 'id')},
            }
            for table, expected in expected_foreign_keys.items():
                actual = {(row['table'], row['from'], row['to'])
                          for row in db.execute(f'PRAGMA foreign_key_list("{table}")')}
                if not expected <= actual:
                    raise Blocked('Existing notification ownership references are incompatible')
            triggers = {row['name']: (row['tbl_name'], row['sql']) for row in db.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'")}
            for name, phase in (('policy_guard_insert', 'after insert on outbox'),
                                ('policy_guard_update', 'after update of status on outbox')):
                table, source = triggers.get(name, (None, ''))
                normalized = re.sub(r'\s+', ' ', (source or '').lower())
                if (table != 'outbox' or phase not in normalized
                        or "when new.status in ('pending','retry')" not in normalized
                        or "update outbox set status='suppressed',last_error='legacy_policy' where id=new.id"
                        not in normalized):
                    raise Blocked('Existing notification sender policy is unavailable')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise Blocked('Existing notification database has invalid references')
        except sqlite3.Error as error:
            raise Blocked('Existing notification schema is unavailable') from error


def _owner(auth_path):
    with closing(_open_readonly(auth_path)) as db:
        try:
            columns = {row['name'] for row in db.execute('PRAGMA table_info("users")')}
            if not {'id', 'role', 'status', 'profile'} <= columns:
                raise Blocked('Existing owner database schema is incompatible')
            candidates = db.execute(
                "SELECT id,role,status,profile FROM users WHERE role='owner' OR profile='default'"
            ).fetchall()
        except sqlite3.Error as error:
            raise Blocked('Existing owner database is unavailable') from error
    if (len(candidates) != 1 or candidates[0]['role'] != 'owner'
            or candidates[0]['status'] != 'ready' or candidates[0]['profile'] != 'default'
            or not isinstance(candidates[0]['id'], str) or not candidates[0]['id']):
        raise Blocked('Exactly one ready default-profile owner is required')
    return candidates[0]['id']


def _unique_state_index(db, table, columns):
    return _has_unique_index(db, table, columns)


def _validate_adapter_state(path):
    with closing(_open_readonly(path)) as db:
        try:
            for table, expected in _STATE_COLUMNS.items():
                if not _table_matches(db, table, expected, _STATE_PRIMARY_KEYS[table]):
                    raise Blocked('Existing adapter state schema is incompatible')
            if (not _unique_state_index(db, 'binding', ('singleton',))
                    or not _unique_state_index(db, 'events', ('event_id',))):
                raise Blocked('Existing adapter state uniqueness is incompatible')
        except sqlite3.Error as error:
            raise Blocked('Existing adapter state schema is unavailable') from error


def _deployed_evidence(event, paths, now):
    occurred = datetime.fromisoformat(event['occurred_at'].replace('Z', '+00:00')).timestamp()
    ledger, ledger_info = _json_file(paths.delivery_state, max_bytes=MAX_STATE_BYTES)
    _fresh(ledger_info, now)
    if (not isinstance(ledger, dict) or set(ledger) != {'version', 'latest_id', 'records', 'last'}
            or type(ledger['version']) is not int or ledger['version'] != 1
            or type(ledger['latest_id']) is not int or ledger['latest_id'] <= 0
            or not isinstance(ledger['records'], dict) or len(ledger['records']) > 10000
            or not isinstance(ledger['last'], dict)):
        raise Blocked('Trusted deployment ledger is incomplete')
    required = {'status', 'reason', 'sha', 'approval_run_id', 'source_run_id', 'deployment_id'}
    records = ledger['records']
    terminal = [record for record in records.values()
                if isinstance(record, dict) and record.get('status') == 'deployed'
                and record.get('sha') == event['merge_sha']
                and type(record.get('deployment_id')) is int
                and record['deployment_id'] == ledger['latest_id']]
    if (len(terminal) != 1 or set(terminal[0]) != required
            or not isinstance(terminal[0]['sha'], str)
            or any(type(terminal[0][name]) is not int or terminal[0][name] <= 0
                   for name in ('approval_run_id', 'source_run_id', 'deployment_id'))
            or not isinstance(terminal[0]['reason'], str) or len(terminal[0]['reason']) > 1000
            or records.get(f"{event['merge_sha']}:{terminal[0]['approval_run_id']}") != terminal[0]
            or terminal[0]['deployment_id'] != ledger['latest_id']):
        raise Blocked('Trusted deployment ledger does not bind this exact SHA')
    last = ledger['last']
    duplicate = {'status': 'duplicate', 'reason': 'Consumed intent: deployed'}
    if last != terminal[0] and last != duplicate:
        raise Blocked('Latest delivery intent is inconsistent with the deployed record')

    controller_root = paths.controller_state
    _owned_private_path(controller_root, directory=True)
    status_path = controller_root / 'status.json'
    status, status_info = _json_file(status_path, max_bytes=MAX_CONFIG_BYTES)
    _fresh(status_info, now)
    if status_info.st_mtime > occurred + MAX_CLOCK_SKEW:
        raise Blocked('Controller success postdates the exported lifecycle event')
    if (not isinstance(status, dict) or status.get('status') != 'succeeded'
            or status.get('git_sha') != event['merge_sha']
            or not isinstance(status.get('release'), str)
            or not _RELEASE.fullmatch(status['release'])):
        raise Blocked('Fresh controller success for the exact merge SHA is required')
    release = status['release']
    releases = controller_root / 'releases'
    _owned_private_path(releases, directory=True)
    stage = releases / release
    _owned_private_path(stage, directory=True)
    current = controller_root / 'current'
    try:
        current_info = current.lstat()
        current_target = current.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise Blocked('Current controller provenance is unavailable') from error
    if (not stat.S_ISLNK(current_info.st_mode) or current_info.st_uid != os.geteuid()
            or current_info.st_nlink != 1
            or current_target != stage):
        raise Blocked('Controller current release differs from its success record')
    provenance, _ = _json_file(stage / 'git-provenance.json', max_bytes=4096)
    if not isinstance(provenance, dict) or provenance != {'git_sha': event['merge_sha']}:
        raise Blocked('Current release provenance differs from the exact merge SHA')
    if ledger_info.st_mtime + 5 < status_info.st_mtime:
        raise Blocked('Deployment ledger predates controller success')


def _acked_event_ids(state_path, payload, owner):
    if not (state_path.exists() or state_path.is_symlink()):
        return set()
    acked = set()
    with closing(_open_readonly(state_path)) as db:
        for item in payload['events']:
            row = db.execute('SELECT * FROM events WHERE event_id=?',
                             (item['event_id'],)).fetchone()
            if row is None:
                continue
            if (row['digest'] != event_digest(item) or row['recipient_id'] != owner
                    or row['status'] not in ('pending', 'acked')
                    or (row['status'] == 'pending' and row['inbox_id'] is not None)
                    or (row['status'] == 'acked'
                        and (not isinstance(row['inbox_id'], str) or not row['inbox_id']))):
                raise Blocked('Existing lifecycle adapter state conflicts with this event')
            if row['status'] == 'acked':
                acked.add(item['event_id'])
    return acked


def _load(paths, now):
    if now.tzinfo is None:
        raise Blocked('Timezone-aware current time is required')
    now = now.astimezone(timezone.utc)
    config, _ = _json_file(paths.config, max_bytes=MAX_CONFIG_BYTES)
    if not isinstance(config, dict) or not isinstance(config.get('state_dir'), str):
        raise Blocked('Existing configuration must specify its state directory')
    try:
        state_dir = Path(config['state_dir'])
    except (TypeError, ValueError) as error:
        raise Blocked('Configured state directory is invalid') from error
    if not state_dir.is_absolute():
        raise Blocked('Configured state directory must be absolute')
    _owned_private_path(state_dir, directory=True)
    try:
        from backend.configuration import load_settings
        settings = load_settings(paths.config)
    except (OSError, TypeError, ValueError) as error:
        raise Blocked('Existing configuration schema is incompatible') from error
    if settings.state_dir != state_dir:
        raise Blocked('Configured state directory binding changed')
    auth_path = state_dir / 'auth.sqlite'
    inbox_path = state_dir / 'notifications.sqlite'
    owner = _owner(auth_path)
    _validate_notification_schema(inbox_path)
    export_path = state_dir / EVENT_NAME
    payload, export_info = _json_file(export_path, max_bytes=MAX_EXPORT_BYTES)
    _fresh(export_info, now)
    try:
        validate_export(payload, now=now)
    except (TypeError, ValueError) as error:
        raise Blocked('Lifecycle export is invalid, stale, or incomplete') from error
    if payload['owner_user_id'] != owner:
        raise Blocked('Lifecycle export is not bound to the current owner')
    state_path = state_dir / ADAPTER_STATE_NAME
    if state_path.exists() or state_path.is_symlink():
        _owned_private_path(state_path, max_bytes=MAX_STATE_BYTES)
        _validate_adapter_state(state_path)
        _check_binding(state_path, owner)
    acked = _acked_event_ids(state_path, payload, owner)
    deferred = {}
    for item in payload['events']:
        if item['outcome'] == 'deployed' and item['event_id'] not in acked:
            try:
                _deployed_evidence(item, paths, now)
            except Blocked:
                # Only deployment proof is event-local. Schema, owner and all
                # durable identity checks above remain whole-batch failures.
                deferred[item['event_id']] = 'deployment_evidence_unavailable'
    return state_dir, owner, auth_path, inbox_path, payload, state_path, deferred


def _check_binding(path, owner):
    with closing(_open_readonly(path)) as db:
        row = db.execute("SELECT version,repository_id,owner_user_id FROM binding WHERE singleton='repository'").fetchone()
    if (row is None or row['version'] != SCHEMA_VERSION
            or row['repository_id'] != REPOSITORY_ID or row['owner_user_id'] != owner):
        raise Blocked('Existing adapter state is bound to a different repository or owner')


def _state_connection(path):
    _owned_private_path(path, max_bytes=MAX_STATE_BYTES)
    try:
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        # max_page_count is connection-local: set it before *every* write,
        # including initialization and ACK updates, not just reservations.
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        max_pages = MAX_STATE_BYTES // page_size
        if (max_pages < 1 or db.execute('PRAGMA page_count').fetchone()[0] > max_pages
                or db.execute(f'PRAGMA max_page_count={max_pages}').fetchone()[0] != max_pages):
            raise Blocked('Adapter state capacity cannot be enforced')
        return db
    except (sqlite3.Error, Blocked) as error:
        if 'db' in locals():
            db.close()
        raise Blocked('Adapter state database is unavailable') from error


def _initialize_state(path, owner):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
    except FileExistsError:
        _owned_private_path(path, max_bytes=MAX_STATE_BYTES)
        _validate_adapter_state(path)
        _check_binding(path, owner)
        return
    except OSError as error:
        raise Blocked('Adapter state could not be created safely') from error
    try:
        with closing(_state_connection(path)) as db:
            db.executescript('''
                CREATE TABLE binding(
                    singleton TEXT PRIMARY KEY CHECK(singleton='repository'),
                    version INTEGER NOT NULL, repository_id INTEGER NOT NULL,
                    owner_user_id TEXT NOT NULL
                );
                CREATE TABLE events(
                    event_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','acked')),
                    inbox_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
            ''')
            db.execute("INSERT INTO binding VALUES('repository',?,?,?)",
                       (SCHEMA_VERSION, REPOSITORY_ID, owner))
            db.commit()
        _validate_adapter_state(path)
    except (OSError, sqlite3.Error) as error:
        raise Blocked('Adapter state schema could not be initialized') from error


def _notification_copy(path, *, clock=time.time):
    from backend.notifications import NotificationService

    class ExistingNotifications(NotificationService):
        def __init__(self):
            self.db_path = str(path)
            self.clock = clock

        @contextmanager
        def _db(self):
            _owned_private_path(path)
            try:
                db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                with db:
                    yield db
            except sqlite3.Error as error:
                raise Blocked('Existing notification database is unavailable') from error
            finally:
                if 'db' in locals():
                    db.close()

    return ExistingNotifications()


def _verify_inbox_delivery(path, owner, delivery_id, item, title, body):
    with closing(_open_readonly(path)) as db:
        row = db.execute('''SELECT i.user_id,i.delivery_id,i.title,i.body,i.session_id,
                p.category,p.profile,p.device_id
            FROM inbox i LEFT JOIN notification_policy p ON p.inbox_id=i.id
            WHERE i.id=?''', (item['id'],)).fetchone()
    if (row is None or row['user_id'] != owner or row['delivery_id'] != delivery_id
            or row['title'] != title or row['body'] != body or row['session_id'] is not None
            or row['category'] != 'operational' or row['profile'] != 'default'
            or row['device_id'] is not None):
        raise Blocked('Existing Inbox delivery conflicts with this lifecycle identity')


def _message(event):
    issue = event['issue_number']
    pr = event['pr_number']
    subject = f'issue #{issue}' if issue is not None else f'PR #{pr}'
    if event['outcome'] == 'approval_required':
        title = f'Owner decision required for PR #{pr}'
        body = (f'PR #{pr} at head SHA {event["head_sha"]} requires you to '
                f'{_DECISION_TEXT[event["decision"]]}.')
    elif event['outcome'] == 'merged':
        title = f'PR #{pr} merged'
        body = (f'PR #{pr} merged as {event["merge_sha"]}. Merge is separate from deployment.')
    elif event['outcome'] == 'deployed':
        title = f'PR #{pr} verified deployed'
        body = (f'PR #{pr} deployed at merge SHA {event["merge_sha"]}; current controller '
                'provenance matches. Phone delivery is not confirmed.')
    elif event['reason'] == 'closed_without_merge':
        title = f'PR #{pr} closed without merging'
        body = f'PR #{pr} closed without merging. Review its current status.'
    elif event['reason'] == 'conflict_incompatible':
        title = f'Incompatible conflict for PR #{pr}'
        body = f'PR #{pr} needs owner review because the conflict is incompatible.'
    elif event['reason'] == 'policy_broken':
        title = f'Workflow policy needs review for PR #{pr}'
        body = f'PR #{pr} needs owner review because workflow policy is unavailable.'
    elif event['reason'] == 'issue_failed':
        title = f'Issue #{issue} needs attention'
        body = f'Issue #{issue} ended unsuccessfully. Review its current workflow status.'
    elif event['reason'] == 'execution_exhausted':
        title = f'Workflow budget exhausted for {subject}'
        body = f'{subject.capitalize()} needs owner review after the bounded execution budget was exhausted.'
    elif event['reason'] == 'execution_uncertain':
        title = f'Workflow outcome uncertain for {subject}'
        body = f'{subject.capitalize()} has an unresolved execution outcome. Inspect its current status before retrying.'
    else:
        title = f'Workflow task failed for {subject}'
        body = f'{subject.capitalize()} workflow task failed. Review the current issue and task status.'
    return title, body


def _with_deferred(result, deferred):
    if deferred:
        result['deferred'] = [
            {'event_id': event_id, 'status': 'deferred', 'reason': reason}
            for event_id, reason in deferred.items()]
    return result


def _locked_state(state_dir, state_path, owner, auth_path, payload, inbox_path, now, deferred):
    lock_path = state_dir / LOCK_NAME
    existed = lock_path.exists() or lock_path.is_symlink()
    if existed:
        _owned_private_path(lock_path)
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    if not existed:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        created = False
        try:
            fd = os.open(lock_path, flags, 0o600)
            created = not existed
        except FileExistsError:
            _owned_private_path(lock_path)
            fd = os.open(lock_path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
                          | getattr(os, 'O_CLOEXEC', 0))
        lock_info = os.fstat(fd)
        if created:
            os.fchmod(fd, 0o600)
            lock_info = os.fstat(fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.geteuid()
                or lock_info.st_nlink != 1 or lock_info.st_mode & 0o077):
            raise Blocked('Adapter lock file is unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _owner(auth_path) != owner:
            raise Blocked('Owner binding changed before apply')
        if state_path.exists() or state_path.is_symlink():
            _owned_private_path(state_path, max_bytes=MAX_STATE_BYTES)
            _validate_adapter_state(state_path)
            _check_binding(state_path, owner)
            # A lock waiter must recheck the entire batch, not discover a later
            # conflict only after committing an earlier event.
            _acked_event_ids(state_path, payload, owner)
        else:
            _initialize_state(state_path, owner)
        notifications = _notification_copy(inbox_path, clock=lambda: now.timestamp())
        ingested = 0
        for item in payload['events']:
            digest = event_digest(item)
            with closing(_state_connection(state_path)) as db:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT * FROM events WHERE event_id=?',
                                   (item['event_id'],)).fetchone()
                if prior and (prior['digest'] != digest or prior['recipient_id'] != owner):
                    raise Blocked('Lifecycle identity was reused with different content or owner')
                if prior:
                    if (prior['status'] not in ('pending', 'acked')
                            or not re.fullmatch(r'[0-9a-f]{64}', prior['digest'])
                            or (prior['status'] == 'pending' and prior['inbox_id'] is not None)
                            or (prior['status'] == 'acked'
                                and (not isinstance(prior['inbox_id'], str) or not prior['inbox_id']))):
                        raise Blocked('Existing lifecycle adapter state is inconsistent')
                    if prior['status'] == 'acked':
                        db.commit()
                        deferred.pop(item['event_id'], None)
                        continue
                try:
                    if prior is None:
                        db.execute('''INSERT INTO events
                            (event_id,digest,recipient_id,status,inbox_id,created_at,updated_at)
                            VALUES(?,?,?,'pending',NULL,?,?)''',
                            (item['event_id'], digest, owner, now.timestamp(), now.timestamp()))
                    db.commit()
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode != sqlite3.SQLITE_FULL:
                        raise
                    db.rollback()
                    deferred.setdefault(item['event_id'], 'state_capacity')
                    continue
            if item['event_id'] in deferred:
                continue
            title, body = _message(item)
            delivery_id = 'workflow-event:v1:' + item['event_id']
            if _owner(auth_path) != owner:
                raise Blocked('Owner binding changed before Inbox ingestion')
            inbox_item = notifications.ingest(
                owner, delivery_id, title, body, session_id=None,
                category='operational', profile='default')
            _verify_inbox_delivery(inbox_path, owner, delivery_id, inbox_item, title, body)
            with closing(_state_connection(state_path)) as db:
                db.execute('BEGIN IMMEDIATE')
                if _owner(auth_path) != owner:
                    raise Blocked('Owner binding changed before event acknowledgement')
                try:
                    result = db.execute('''UPDATE events SET status='acked',inbox_id=?,updated_at=?
                        WHERE event_id=? AND digest=? AND recipient_id=? AND status='pending' ''',
                        (inbox_item['id'], now.timestamp(), item['event_id'], digest, owner))
                    if result.rowcount != 1:
                        raise Blocked('Lifecycle event acknowledgement binding changed')
                    db.commit()
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode != sqlite3.SQLITE_FULL:
                        raise
                    # Inbox may already be durable; retain pending identity and
                    # retry its stable delivery ID, never discard replay records.
                    db.rollback()
                    deferred[item['event_id']] = 'state_capacity'
                    continue
            ingested += 1
        return _with_deferred(
            {'status': 'applied', 'events': len(payload['events']), 'inbox_items': ingested},
            deferred)
    except OSError as error:
        raise Blocked('Adapter state lock is unavailable') from error
    finally:
        if 'fd' in locals():
            os.close(fd)


def process(paths=None, *, apply=False, now=None):
    """Validate the full batch; apply eligible events and report explicit deferrals."""
    paths = paths or Paths()
    now = now or datetime.now(timezone.utc)
    state_dir, owner, auth_path, inbox_path, payload, state_path, deferred = _load(paths, now)
    if not apply:
        return _with_deferred(
            {'status': 'plan', 'events': len(payload['events']), 'writes': False}, deferred)
    return _locked_state(state_dir, state_path, owner, auth_path, payload, inbox_path, now, deferred)


def main(argv=None, *, paths=None, now=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--plan', action='store_true', help='Validate evidence without writing (default)')
    modes.add_argument('--apply', action='store_true', help='Record eligible events in the existing owner Inbox')
    args = parser.parse_args(argv)
    try:
        result = process(paths, apply=args.apply, now=now)
    except Exception:
        result = {'status': 'blocked', 'reason': 'evidence_unavailable'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('plan', 'applied') else 1


if __name__ == '__main__':
    raise SystemExit(main())
