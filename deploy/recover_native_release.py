"""Abort the investigated pre-mutation native release; never restart a service."""
import base64
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from urllib.error import HTTPError

from . import self_deploy as bridge
from .native_controls_release import NATIVE_DROPIN, NativeProbe
from .assets import checked_path, public_tree

RELEASE_ID = '4ea1cabbd02341698cc6b871dd4971d6'
WORKER_UNIT = 'hermes-mobile-native-fb0cc677245e412fb565be1112a0f5aa.service'
FAILURE = dict(status='rollback_failed', release=RELEASE_ID,
               error='Native idle wait timed out', rollback_error='Native verification failed')


def _exclusive(path, data):
    """Never overwrite evidence, including evidence from interrupted recovery."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _workers_stopped(worker_unit, run):
    """Only queries systemd; collected/not-found units must be positively inactive."""
    options = dict(check=True, capture_output=True, text=True, timeout=10)
    for unit in (worker_unit, worker_unit.removesuffix('.service') + '.timer'):
        properties = ['LoadState', 'ActiveState', 'SubState', 'Job']
        if unit.endswith('.service'):
            properties += ['MainPID', 'ControlPID']
        response = run(['systemctl', '--user', 'show', unit,
                        '--property=' + ','.join(properties)], **options)
        values = dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)
        if (any(key not in values for key in properties)
                or values.get('LoadState') not in ('loaded', 'not-found')
                or (values.get('ActiveState'), values.get('SubState')) not in
                   (('inactive', 'dead'), ('failed', 'failed'))
                or values.get('Job') not in ('', '0')
                or any(values.get(k) != '0' for k in ('MainPID', 'ControlPID')
                       if unit.endswith('.service'))):
            raise RuntimeError('Deployment worker/unit is active or unknown')
    inventory = json.loads(run(['systemctl', '--user', 'list-units', '--all', '--output=json',
                                '--no-pager', 'hermes-mobile-native-*', 'hermes-native-controls-*',
                                'hermes-mobile-deploy-*'], **options).stdout)
    if not isinstance(inventory, list):
        raise RuntimeError('Unknown deployment unit inventory')
    for unit in inventory:
        if (not isinstance(unit, dict) or not isinstance(unit.get('unit'), str)
                or unit.get('load') not in ('loaded', 'not-found')
                or (unit.get('active'), unit.get('sub')) not in
                   (('inactive', 'dead'), ('failed', 'failed'))):
            raise RuntimeError('Another deployment worker/unit is active or unknown')


def _native_check(native, baseline):
    pid = native.attest(Path(baseline['root']), legacy=baseline['legacy'])
    if type(pid) is not int or pid != baseline['pid']:
        raise RuntimeError('Native PID changed')
    try:
        native.request('/health/detailed', authenticated=False)
    except HTTPError as error:
        if error.code != 401:
            raise RuntimeError('Native anonymous boundary changed') from error
    else:
        raise RuntimeError('Native anonymous boundary changed')
    health = native.request('/health/detailed')
    try:
        counts = health['readiness']['checks']['background_queues']
        if (health['status'] != 'ok' or type(health['pid']) is not int or health['pid'] != pid
                or counts['status'] != 'ok'
                or any(type(counts[k]) is not int or counts[k] < 0 for k in
                       ('active_api_runs', 'active_delegations', 'process_completions'))
                or counts['active_api_runs'] or counts['active_delegations']):
            raise RuntimeError('Native health is not safe to continue')
    except (KeyError, TypeError) as error:
        raise RuntimeError('Unknown native health/counts') from error
    if json.dumps(native.request('/v1/capabilities'), sort_keys=True, allow_nan=False) != json.dumps(
            baseline['caps'], sort_keys=True, allow_nan=False):
        raise RuntimeError('Native capabilities changed')


def _path(path, *, private=False, missing=False):
    if not isinstance(path, Path) or not path.is_absolute() or checked_path(path) != path:
        raise RuntimeError('Noncanonical recovery path')
    if missing and not path.exists():
        ancestor = path.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        _path(ancestor)
        return None
    info = path.lstat()
    if (info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o002)
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
        raise RuntimeError('Unsafe recovery path ownership, type or permissions: ' + str(path))
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink if path.is_file() else 0)


def _snapshot(saved, paths, native_dropin):
    if (not isinstance(saved, dict) or set(saved) != {'current', 'dropins', 'dropin_encoding', 'native'}
            or saved['dropin_encoding'] != 'base64' or not isinstance(saved['dropins'], dict)
            or set(saved['dropins']) != {str(paths.dropin), str(native_dropin)}):
        raise RuntimeError('Invalid rollback snapshot')
    old = Path(saved['current'])
    if old.parent != paths.state / 'releases' or not re.fullmatch('[0-9a-f]{32}', old.name):
        raise RuntimeError('Invalid saved current release')
    baseline = saved['native']
    if (not isinstance(baseline, dict) or set(baseline) != {'root', 'pid', 'legacy', 'caps'}
            or type(baseline['legacy']) is not bool or type(baseline['pid']) is not int
            or baseline['pid'] <= 0 or not isinstance(baseline['caps'], dict)
            or baseline['root'] != str(paths.source if baseline['legacy'] else old)):
        raise RuntimeError('Invalid native baseline')
    for data in saved['dropins'].values():
        if data is not None:
            if not isinstance(data, str) or base64.b64encode(base64.b64decode(data, validate=True)).decode() != data:
                raise RuntimeError('Invalid drop-in preimage')
            base64.b64decode(data, validate=True).decode('utf-8')
    return old


def _trees(paths, old):
    result = {}
    names = (*bridge.SOURCE_TREES, *bridge.SOURCE_FILES, 'public')
    for root in dict.fromkeys((paths.source, old)):
        for name in names:
            tree = root / name
            _path(tree, missing=True)
            for p in sorted(tree.rglob('*')) if tree.is_dir() else []:
                if set(p.relative_to(root).parts) & bridge.SKIP:
                    continue
                _path(p)
        result[str(root)] = bridge.fingerprints(root, names)
    # Public-tree filtering returns files only; validate directories (including
    # empty ones) and every descendant's ownership/mode on every recheck too.
    for p in sorted(paths.webroot.rglob('*')):
        _path(p)
    result[str(paths.webroot)] = {str(p.relative_to(paths.webroot)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in public_tree(paths.webroot)}
    return result


def recover(paths, *, release_id, worker_unit, native, verify,
            native_dropin=NATIVE_DROPIN, run=subprocess.run):
    """Recover only this incident, using injected read-only boundaries in tests."""
    if (release_id, worker_unit) != (RELEASE_ID, WORKER_UNIT):
        raise RuntimeError('Recovery incident/worker is not allowlisted')
    status = paths.state / 'status.json'
    directory = paths.state / 'recovery' / release_id
    snapshot = directory / 'native-rollback.json'
    lock_path = paths.state / 'deploy.lock'
    current = paths.state / 'current'
    private_paths = (paths.state, paths.database.parent, paths.database, status, lock_path,
                     paths.state / 'recovery', directory, snapshot, paths.state / 'releases')
    ordinary_paths = (paths.source, paths.webroot, paths.dropin.parent, native_dropin.parent)
    for path in (*private_paths, *ordinary_paths, paths.dropin, native_dropin):
        if path.is_relative_to(paths.webroot) and path != paths.webroot:
            raise RuntimeError('Private recovery path inside public root')

    def identities():
        # SQLite may create/remove these itself; validate safety, not presence.
        for suffix in ('-wal', '-shm', '-journal'):
            _path(Path(str(paths.database) + suffix), private=True, missing=True)
        values = {str(p): _path(p, private=True) for p in private_paths}
        values.update({str(p): _path(p, missing=p == native_dropin.parent) for p in ordinary_paths})
        values.update({str(p): _path(p, missing=True) for p in (paths.dropin, native_dropin)})
        if not current.is_symlink():
            raise RuntimeError('Current must be a release symlink')
        info = current.lstat()
        values[str(current)] = (info.st_dev, info.st_ino, os.readlink(current))
        backup = paths.state / 'backups' / release_id
        checked_path(backup)
        if backup.exists():
            raise RuntimeError('Publication evidence blocks pre-mutation recovery')
        return values

    pinned = identities()
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW), 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A deployment is already running') from None
        original = status.read_bytes()
        previous = json.loads(original)
        if previous != FAILURE:
            raise RuntimeError('Unexpected failure record')
        saved_bytes = snapshot.read_bytes()
        saved = json.loads(saved_bytes)
        old = _snapshot(saved, paths, native_dropin)
        old_identity = _path(old)
        frozen = _trees(paths, old)

        def check(db):
            if identities() != pinned or _path(old) != old_identity or _trees(paths, old) != frozen:
                raise RuntimeError('Recovery identity or fingerprints changed')
            if status.read_bytes() != original or snapshot.read_bytes() != saved_bytes:
                raise RuntimeError('Recovery evidence changed')
            if (paths.state / 'current').resolve(strict=True) != old:
                raise RuntimeError('Current release changed')
            for path in (paths.dropin, native_dropin):
                data = saved['dropins'][str(path)]
                expected = None if data is None else base64.b64decode(data, validate=True)
                if (path.read_bytes() if path.exists() else None) != expected:
                    raise RuntimeError('Drop-in changed')
            if db.execute('SELECT singleton, owner FROM deployment_gate').fetchall() != [(1, release_id)]:
                raise RuntimeError('Recovery gate owner mismatch')
            if db.execute("SELECT 1 FROM runs WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone():
                raise RuntimeError('Active/unknown journal runs block recovery')

        def boundary(db):
            check(db)
            _workers_stopped(worker_unit, run)
            _native_check(native, saved['native'])
            verify(old, True)
            _workers_stopped(worker_unit, run)
            _native_check(native, saved['native'])
            check(db)

        with closing(sqlite3.connect(paths.database.as_uri() + '?mode=rw', uri=True, timeout=0)) as db:
            boundary(db)
            with db:
                db.execute('BEGIN IMMEDIATE')
                boundary(db)
                _exclusive(directory / 'original-failure.json', original)
                receipt = dict(release=release_id, worker_unit=worker_unit, original_failure=previous,
                               recovery_kind='pre_mutation_abort', phase='prepared',
                               snapshot_sha256=hashlib.sha256(saved_bytes).hexdigest(),
                               verified_fingerprints=frozen)
                _exclusive(directory / 'recovery-receipt.json', json.dumps(receipt).encode())
                result = dict(previous, status='rolled_back', recovered=True, recovery_kind='pre_mutation_abort',
                              recovery_receipt=str(directory / 'recovery-receipt.json'))
                pending = directory / 'recovered-status.pending.json'
                _exclusive(pending, json.dumps(result).encode())
                _workers_stopped(worker_unit, run)
                _native_check(native, saved['native'])
                check(db)
                changed = db.execute('DELETE FROM deployment_gate WHERE singleton=1 AND owner=?', (release_id,))
                if changed.rowcount != 1:
                    raise RuntimeError('Owned gate was not cleared')
            # Never mask a commit failure, overwrite the original failure copy, or
            # run blanket gate cleanup. Prepared evidence survives any exception.
            if status.read_bytes() != original or identities() != pinned:
                raise RuntimeError('Recovery state changed after gate commit; inspect evidence')
            os.replace(pending, status)
            fd = os.open(status.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return result


def main(argv=None, *, paths=None, native_dropin=NATIVE_DROPIN, run=subprocess.run):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, choices=[RELEASE_ID])
    parser.add_argument('--worker-unit', required=True, choices=[WORKER_UNIT])
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        raise RuntimeError('Run as the application owner, never root')
    paths = paths or bridge.Paths()
    result = recover(paths, release_id=args.release, worker_unit=args.worker_unit,
                     native=NativeProbe(paths.source, run=run), native_dropin=native_dropin, run=run,
                     verify=lambda old, backend: bridge.verify_release(paths, old, backend, run=run))
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
