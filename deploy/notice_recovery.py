"""Narrow INSERT-only recovery of archived notification rows; never DB rewind.

Caller supplies independently verified archive provenance and an exact ID list.
Recovered rows are held as dropped (not delivered) to forbid automatic model
replay. A separate reconciler must commit and verify real web delivery receipts.
"""
from contextlib import closing, contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from deploy.assets import checked_path

COLUMNS = ('delegation_id','origin_session','origin_ui_session_id','parent_session_id',
           'state','dispatched_at','completed_at','updated_at','event_json','result_json',
           'delivery_state','delivery_attempts','delivered_at','owner_pid','owner_started_at',
           'task_json','delivery_claim','delivery_claimed_at','origin_session_id')
TERMINAL = {'completed','failed','error','interrupted','timeout','cancelled','stalled','unknown'}
IDENTITY = ('delegation_id','origin_session','origin_ui_session_id','parent_session_id',
            'state','owner_pid','owner_started_at','origin_session_id','event_json','result_json')


def _identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


@contextmanager
def _pin(path, *, directory=False):
    """Hold every ancestor and the regular file open, without following links."""
    with ExitStack() as stack:
        records = []
        parent_fd = None
        for part in (*reversed(path.parents), path):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if part != path or directory:
                flags |= os.O_DIRECTORY
            fd = os.open(str(part) if parent_fd is None else part.name,
                         flags, dir_fd=parent_fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if part == path and not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Recovery requires regular unaliased databases')
            records.append((part, fd, _identity(info)))
            parent_fd = fd
        yield records


def _check_pins(*pins):
    for records in pins:
        for path, fd, identity in records:
            info = path.lstat()
            if (_identity(info) != identity or _identity(os.fstat(fd)) != identity
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise ValueError('Recovery path identity changed')


def _regular_fds():
    # Linux maintenance helper: Python's sqlite3 has no file-handle API.
    # Fail closed unless connect opens exactly one identifiable regular-file fd.
    # Concurrent fd activity or SQLite reusing an older handle may cause refusal;
    # callers should run this operation in a dedicated, quiescent process.
    result = {}
    for name in os.listdir('/proc/self/fd'):
        try:
            info = os.fstat(int(name))
        except OSError:
            continue  # The descriptor used by listdir itself has already closed.
        if stat.S_ISREG(info.st_mode):
            result[int(name)] = _identity(info)
    return result


def write_private_plan(path, plan):
    """Publish complete JSON exclusively; never replace any existing evidence."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Path traversal is not allowed')
    raw = json.dumps(plan, indent=2, allow_nan=False).encode()
    with _pin(checked_path(path.parent), directory=True) as pins:
        directory_fd = pins[-1][1]
        temporary = '.recovery-plan-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _check_pins(pins)
            os.link(temporary, path.name, src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=directory_fd)
            os.fsync(directory_fd)


def _schema(db):
    # table_info omits constraints, triggers, indexes and generated expressions.
    return [tuple(row) for row in db.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')]


def _insert_only(action, name, column, database, origin):
    # Defense in depth even when the pinned archive itself contains a trigger.
    if origin is not None:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT:
        allowed = database == 'main' and name == 'async_delegations'
    elif action == sqlite3.SQLITE_READ:
        allowed = database == 'main' and name == 'async_delegations'
    else:
        allowed = action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION)
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def restore_missing(live, archive, ids, *, archive_sha256, owner_pid):
    live, archive = checked_path(live), checked_path(archive)
    if live == archive:
        raise ValueError('Archive and live database must be separate')
    with _pin(live) as live_pin, _pin(archive) as archive_pin:
        return _restore_missing(live, archive, ids, archive_sha256=archive_sha256,
                                owner_pid=owner_pid, pins=(live_pin, archive_pin))


def _restore_missing(live, archive, ids, *, archive_sha256, owner_pid, pins):
    if (not isinstance(ids,(list,tuple)) or not 0<len(ids)<=1000
            or any(not isinstance(key,str) or not re.fullmatch(r'deleg_[A-Za-z0-9_-]{1,100}',key) for key in ids)
            or len(set(ids))!=len(ids) or type(owner_pid) is not int or owner_pid<=0):
        raise ValueError('Invalid recovery identity allowlist')
    _check_pins(*pins)
    with os.fdopen(os.dup(pins[1][-1][1]), 'rb') as stream:
        raw = stream.read()
    if hashlib.sha256(raw).hexdigest()!=archive_sha256:
        raise ValueError('Archive hash mismatch')
    _check_pins(*pins)
    # Query the exact hashed snapshot bytes, not a separately reopened path.
    with closing(sqlite3.connect(':memory:')) as source:
        source.deserialize(raw)
        source.execute('PRAGMA query_only=ON')
        source.row_factory=sqlite3.Row
        schema=[tuple(r) for r in source.execute('PRAGMA table_info(async_delegations)')]
        if tuple(r[1] for r in schema)!=COLUMNS:
            raise ValueError('Unexpected archive schema')
        full_schema = _schema(source)
        rows=[]
        for key in ids:
            found=source.execute('SELECT * FROM async_delegations WHERE delegation_id=?',(key,)).fetchone()
            if found is None:
                raise ValueError('Requested archive identity missing')
            row=dict(found)
            try:
                event=json.loads(row['event_json']);result=json.loads(row['result_json'])
                json.dumps([event,result],allow_nan=False)
            except (TypeError,ValueError):
                raise ValueError('Malformed archived payload') from None
            if (row['owner_pid']!=owner_pid or row['state'] not in TERMINAL
                    or not isinstance(event,dict) or not isinstance(result,dict)
                    or event.get('type')!='async_delegation' or event.get('delegation_id')!=key
                    or not row['origin_session'] or event.get('session_key')!=row['origin_session']
                    or not row['origin_session_id'] or event.get('origin_session_id')!=row['origin_session_id']):
                raise ValueError('Archived identity/payload mismatch')
            rows.append(row)
    inserted=[]
    _check_pins(*pins)
    before_fds = _regular_fds()
    with closing(sqlite3.connect(live.as_uri()+'?mode=rw',uri=True,timeout=10)) as target, target:
        opened = {fd: identity for fd, identity in _regular_fds().items()
                  if before_fds.get(fd) != identity}
        if len(opened) != 1 or next(iter(opened.values())) != pins[0][-1][2]:
            raise ValueError('Cannot bind SQLite handle to validated database')
        _check_pins(*pins)
        target.row_factory=sqlite3.Row
        target.execute('BEGIN IMMEDIATE')
        if ([tuple(r) for r in target.execute('PRAGMA table_info(async_delegations)')]!=schema
                or _schema(target) != full_schema):
            raise ValueError('Live schema differs from archive')
        target.set_authorizer(_insert_only)
        missing=[]
        for row in rows:
            current=target.execute('SELECT * FROM async_delegations WHERE delegation_id=?',(row['delegation_id'],)).fetchone()
            if current is not None:
                if any(current[k]!=row[k] for k in IDENTITY):
                    raise ValueError('Existing identity conflicts with archived payload')
                continue  # Never roll back an existing delivery acknowledgement.
            missing.append(row)
        for row in missing:
            _check_pins(*pins)
            row.update(delivery_state='dropped',delivery_claim=None,delivery_claimed_at=None)
            target.execute('INSERT INTO async_delegations('+','.join(COLUMNS)+') VALUES('+','.join('?' for _ in COLUMNS)+')',tuple(row[k] for k in COLUMNS))
            inserted.append(row['delegation_id'])
        _check_pins(*pins)
    return {'inserted':inserted,'policy':'recovered-held-no-model-replay'}
