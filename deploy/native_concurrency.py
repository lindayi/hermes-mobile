"""Same-source dedicated-listener cap activation. Importing/running does nothing.

Call only from a reviewed operator with exclusive bridge ingress; this is not
an atomic native drain. No SDK, gateway, bridge, drop-in or release-pointer writes.
"""
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from . import self_deploy as bridge

KEY = 'gateway.api_server.max_concurrent_runs'
SERVICE = 'hermes-mobile-api.service'


class HermesCap:
    """Explicit owner home; writes only through the supported Hermes CLI."""
    def __init__(self, home, *, run=subprocess.run):
        self.home, self.run = Path(home), run

    def command(self, *args):
        env = dict(os.environ, HERMES_HOME=str(self.home))
        return self.run(['hermes', 'config', *args], env=env, check=True,
                        capture_output=True, text=True, timeout=30).stdout.strip()

    def get(self):
        return int(self.command('get', KEY))

    def set(self, value):
        self.command('set', KEY, str(value))
        if self.get() != value:
            raise RuntimeError('Config write did not persist the expected cap')


class Journal:
    """Existing schema only: no constructor migrations or database creation."""
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        return sqlite3.connect(self.path.absolute().as_uri() + '?mode=rw', uri=True, timeout=10)


def notification_snapshot(path):
    """Read only existing durable records; do not CLAIM, ACK, replay or migrate."""
    with closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT event_id,event_json,payload_sha256,result_json FROM notification_outbox')
        return {row[0]: tuple(None if value is None else hashlib.sha256(value.encode()).hexdigest()
                             for value in row[1:]) for row in rows}


def require_preserved(before, after):
    # Delivery state/leases/route may advance and a formerly absent result may arrive.
    if any(key not in after or any(value is not None and after[key][index] != value
                                  for index, value in enumerate(values))
           for key, values in before.items()):
        raise RuntimeError('Durable notification records lost or changed')


def activate(paths, *, native, config, notifications, native_dropin,
             approved=False, exclusive_ingress=False, run=subprocess.run,
             idle_timeout=1800, sleep=time.sleep):
    """Reviewed 1→4 change, with all paths and the read-only NativeProbe explicit."""
    if approved is not True or exclusive_ingress is not True:
        raise RuntimeError('Explicit review and exclusive bridge ingress required')
    from .assets import checked_path
    owner = uuid.uuid4().hex
    status = paths.state / 'status.json'
    for path in (paths.state, paths.database, paths.dropin, native_dropin,
                 notifications, status, paths.state / 'deploy.lock'):
        checked_path(path)
    with (paths.state / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if status.exists() and json.loads(status.read_text()).get('status') == 'rollback_failed':
            raise RuntimeError('Failed rollback requires operator recovery')
        pointer = paths.state / 'current'
        current = pointer.resolve(strict=True)
        if not pointer.is_symlink() or current.parent != paths.state / 'releases':
            raise RuntimeError('Existing immutable bridge baseline required')
        checked_path(current)
        baseline = native.capture(current, False)
        if baseline['legacy'] or 'backend/native_notifications.py' not in baseline['source_hashes']:
            raise RuntimeError('Attested durable-notification native source required')
        root = Path(baseline['root'])
        previous = config.get()
        if type(previous) is not int or previous != 1:
            raise RuntimeError('Reviewed preimage cap must be exactly 1')
        retained = notification_snapshot(notifications)
        journal = Journal(paths.database)
        with closing(journal.connect()) as db:
            if db.execute('SELECT 1 FROM deployment_gate').fetchone():
                raise RuntimeError('A deployment is already in progress')
        saved = {p: p.read_bytes() for p in (paths.dropin, native_dropin)}
        def bindings():
            if (not pointer.is_symlink() or pointer.resolve(strict=True) != current
                    or any(checked_path(p).read_bytes() != value for p, value in saved.items())):
                raise RuntimeError('Service/source binding changed')

        def owned():
            bindings()
            with closing(journal.connect()) as db:
                if db.execute('SELECT singleton,owner FROM deployment_gate').fetchall() != [(1, owner)]:
                    raise RuntimeError('Admission gate ownership changed')

        def clear_gate():
            deleted = False
            try:
                with closing(journal.connect()) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    bindings()
                    if db.execute('SELECT singleton,owner FROM deployment_gate').fetchall() != [(1, owner)]:
                        raise RuntimeError('Admission gate ownership changed')
                    if db.execute('DELETE FROM deployment_gate WHERE singleton=1 AND owner=?', (owner,)).rowcount != 1:
                        raise RuntimeError('Owned admission gate was not cleared')
                    deleted = True
            except Exception:
                # Commit/close can raise after COMMIT succeeded. Never restart or
                # claim closed admission after our owned deletion is proven committed.
                with closing(journal.connect()) as db:
                    gates = db.execute('SELECT singleton,owner FROM deployment_gate').fetchall()
                if not (deleted and gates == []):
                    raise

        def report(state, **extra):
            result = dict(status=state, release=owner, operation='native-concurrency',
                          previous=previous, target=4, root=str(root), **extra)
            bridge.atomic_write(status, json.dumps(result))
            return result

        recovery = checked_path(paths.state / 'native-concurrency-recovery.json')
        bridge.atomic_write(recovery, json.dumps(dict(version=1, owner=owner, previous=previous,
            target=4, native=baseline, bridge_root=str(current),
            dropins={str(p): hashlib.sha256(value).hexdigest() for p, value in saved.items()})))
        recovery.chmod(0o600)
        report('running')
        changed = False
        try:
            with closing(journal.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM deployment_gate').fetchone():
                    raise RuntimeError('A deployment is already in progress')
                db.execute('INSERT INTO deployment_gate VALUES(1,?)', (owner,))
                draining = tuple(row[0] for row in db.execute(
                    "SELECT id FROM runs WHERE profile='default' AND status NOT IN ('completed','failed','cancelled')"))
            bridge.wait_idle(journal, timeout=idle_timeout, sleep=sleep)
            with closing(journal.connect()) as db:
                required_ids = tuple(dict.fromkeys(row[0] for rid in draining for row in db.execute(
                    'SELECT upstream_id FROM runs WHERE id=? AND upstream_id IS NOT NULL', (rid,))))
            deadline = time.monotonic() + idle_timeout
            while not native.idle(journal, baseline, required_ids=required_ids):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Native idle wait timed out')
                sleep(1)
            owned()
            if config.get() != previous:
                raise RuntimeError('Concurrent cap edit; refusing overwrite')
            if not native.idle(journal, baseline, required_ids=required_ids):
                raise RuntimeError('Native activity changed after drain')
            owned()
            latest = notification_snapshot(notifications)
            require_preserved(retained, latest)
            retained = latest
            changed = True  # CLI can write and subsequently fail.
            config.set(4)
            owned()
            if not native.idle(journal, baseline, required_ids=required_ids):
                raise RuntimeError('Native activity changed during config write')
            owned()
            run(['systemctl', '--user', 'restart', SERVICE], check=True, timeout=120)
            native.verify(root, baseline=baseline)
            loaded = native.capture(current, False)
            if (any(loaded[k] != baseline[k] for k in ('root', 'legacy', 'source_hashes', 'caps'))
                    or (loaded['pid'], loaded['start_ticks']) == (baseline['pid'], baseline['start_ticks'])):
                raise RuntimeError('Expected a fresh native process on the exact same source')
            if config.get() != 4:
                raise RuntimeError('Activated cap no longer equals 4')
            require_preserved(retained, notification_snapshot(notifications))
            result = report('succeeded', loaded_pid=loaded['pid'], loaded_start_ticks=loaded['start_ticks'],
                            config_verified=4, capacity_evidence='config-and-fresh-attested-constructor')
            clear_gate()
        except Exception as error:
            if not changed:
                with closing(journal.connect()) as db:
                    gates = db.execute('SELECT singleton,owner FROM deployment_gate').fetchall()
                if not gates:
                    report('failed', error=str(error), admission='open')
                    raise
            try:
                owned()
                require_preserved(retained, notification_snapshot(notifications))
                if changed:
                    now = native.capture(current, False)
                    if any(now[k] != baseline[k] for k in ('root', 'legacy', 'source_hashes', 'caps')):
                        raise RuntimeError('Rollback native source binding changed')
                    if not native.idle(journal, now):
                        raise RuntimeError('Rollback native not idle; refusing restart')
                    if config.get() not in (previous, 4):
                        raise RuntimeError('Concurrent cap edit; refusing overwrite')
                    config.set(previous)
                    owned()
                    if (now['pid'], now['start_ticks']) != (baseline['pid'], baseline['start_ticks']):
                        if not native.idle(journal, now):
                            raise RuntimeError('Rollback native activity changed during config restore')
                        owned()
                        run(['systemctl', '--user', 'restart', SERVICE], check=True, timeout=120)
                        native.verify(root, baseline=baseline)
                        restored = native.capture(current, False)
                        if (any(restored[k] != baseline[k] for k in ('root', 'legacy', 'source_hashes', 'caps'))
                                or (restored['pid'], restored['start_ticks']) == (now['pid'], now['start_ticks'])):
                            raise RuntimeError('Rollback requires a fresh native process on the same source')
                    else:
                        native.verify_unchanged(root, baseline=baseline)
                    if config.get() != previous:
                        raise RuntimeError('Restored cap differs from preimage')
                else:
                    if config.get() != previous:
                        raise RuntimeError('Pre-mutation cap changed')
                    native.verify_unchanged(root, baseline=baseline)
                require_preserved(retained, notification_snapshot(notifications))
                report('rolled_back', error=str(error))
                clear_gate()
            except Exception as recovery_error:
                admission = 'unknown'
                try:
                    with closing(journal.connect()) as db:
                        admission = 'closed' if db.execute('SELECT 1 FROM deployment_gate').fetchone() else 'open'
                except Exception:
                    pass
                report('rollback_failed', error=str(error), rollback_error=str(recovery_error), admission=admission)
                detail = {'closed': 'admission gate remains closed', 'open': 'admission gate is open',
                          'unknown': 'admission state is unknown'}[admission]
                raise RuntimeError('Recovery failed; ' + detail) from recovery_error
            raise
        return result
