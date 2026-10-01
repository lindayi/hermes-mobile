"""Local private snapshots. Restores are inspection-only, NOT runnable deployments."""
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import sqlite3

from backend.configuration import load_settings


def safe_path(value):
    path = Path(value).expanduser()
    if '..' in path.parts or '\\' in str(path):
        raise ValueError('Path traversal refused')
    path = path.absolute()
    if any(part.lower() in {'frontend', 'public', 'public_html', 'www', 'htdocs'} for part in path.parts) or path.is_relative_to('/srv/http'):
        raise ValueError('Public paths refused')
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise ValueError('Symlink refused')
    return path


def regular(path):
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Only single-link regular files allowed')
    return path


def read_regular(path):
    path = regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe input file')
        return stream.read()


def configured(config):
    config = regular(config)
    raw = json.loads(read_regular(config))
    # Validate BEFORE load_settings normalizes paths and erases symlink evidence.
    if 'state_dir' in raw:
        safe_path(raw['state_dir'])
    settings = load_settings(config)
    homes = {}
    for name, home in settings.profiles.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            raise ValueError('Unsafe profile name')
        homes[name] = safe_path(home)
    state = safe_path(settings.state_dir)
    key = safe_path(settings.vapid_private_key or state / 'vapid.pem')
    return config, state, homes, key


def new_destination(destination, protected):
    destination = safe_path(destination)
    if destination.exists():
        raise FileExistsError('Destination must be NEW')
    if any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in protected):
        raise ValueError('Live/source target refused')
    parent = destination.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.getuid():
        raise ValueError('Destination parent must be owned by you with mode 0700')
    return destination


def private_dirs(path):
    if not path.exists():
        private_dirs(path.parent)
        path.mkdir(mode=0o700)
        path.chmod(0o700)


def write_private(path, data):
    private_dirs(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)


def sqlite_copy(source, target):
    write_private(target, b'')
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
    finally:
        dst.close()
        src.close()


def integrity(path):
    # SQLite 3.45 skips CHECK-constraint validation on mode=ro connections.
    # These are private COPIES, never live databases; query_only prevents SQL writes.
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
    finally:
        db.close()


def verify(snapshot):
    snapshot = safe_path(snapshot)
    actual = set()
    for path in [snapshot, *snapshot.rglob('*')]:
        safe_path(path)
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if path.is_dir() else 0o600):
            raise ValueError('Snapshot ownership or permissions unsafe')
        if not path.is_dir():
            regular(path)
            actual.add(path.relative_to(snapshot).as_posix())
    manifest = json.loads(read_regular(snapshot / 'manifest.json'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Invalid manifest')
    entries = manifest['files']
    required = {'DO-NOT-RUN.txt', 'app/config.json', 'app/state/vapid.pem',
                'app/state/auth.sqlite', 'app/state/runs.sqlite', 'app/state/notifications.sqlite'}
    if not required <= set(entries):
        raise ValueError('Mandatory snapshot files missing')
    for name, entry in entries.items():
        parts = name.split('/')
        if not name or Path(name).is_absolute() or any(p in ('', '.', '..') for p in parts) or '\\' in name:
            raise ValueError('Unsafe manifest path')
        path = safe_path(snapshot / name)
        is_sqlite = bool(re.fullmatch(r'app/state/(auth|runs|notifications)\.sqlite|profiles/[A-Za-z0-9_-]+/(state\.db|cron/(executions|notepad)\.db)', name))
        if not isinstance(entry, dict) or entry.get('sqlite') is not is_sqlite:
            raise ValueError('Invalid SQLite classification')
        if hashlib.sha256(read_regular(path)).hexdigest() != entry.get('sha256'):
            raise ValueError('Snapshot checksum mismatch')
        if is_sqlite:
            integrity(path)
    if actual != set(entries) | {'manifest.json'}:
        raise ValueError('Snapshot inventory mismatch')
    saved_config = json.loads(read_regular(snapshot / 'app/config.json'))
    profiles = saved_config.get('profiles', {'default': None})
    if not all(f'profiles/{name}/state.db' in entries for name in profiles):
        raise ValueError('Mandatory native state missing')
    return manifest


def create(config, destination):
    config, state, homes, key = configured(config)
    destination = new_destination(destination, [config.parent, state, *homes.values(), key])
    files = {'app/config.json': (config, False), 'app/state/vapid.pem': (key, False)}
    absent = []
    for name in ('auth.sqlite', 'runs.sqlite', 'notifications.sqlite'):
        files['app/state/' + name] = (state / name, True)
    for name, home in homes.items():
        files[f'profiles/{name}/state.db'] = (home / 'state.db', True)
        for relative in ('config.yaml', '.env', 'auth.json', 'SOUL.md', 'cron/jobs.json', 'cron/executions.db', 'cron/notepad.db'):
            path = safe_path(home / relative)
            if path.exists():
                files[f'profiles/{name}/{relative}'] = (path, relative.endswith('.db'))
            else:
                absent.append(f'profiles/{name}/{relative}')
        memory = safe_path(home / 'memories')
        if not memory.exists():
            absent.append(f'profiles/{name}/memories')
        for path in memory.rglob('*'):
            safe_path(path)
            if not path.is_dir():
                files[f'profiles/{name}/{path.relative_to(home)}'] = (path, False)
    for source, is_sqlite in files.values():
        regular(source)
        if is_sqlite:
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = safe_path(str(source) + suffix)
                if sidecar.exists():
                    regular(sidecar)
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    entries = {}
    for name, (source, is_sqlite) in files.items():
        target = destination / name
        if is_sqlite:
            sqlite_copy(source, target)
            integrity(target)
        else:
            write_private(target, read_regular(source))
        entries[name] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'sqlite': is_sqlite}
    warning = b'DO NOT RUN the app, Hermes CLI, gateway, or cron from this tree.\nInspection only: original config, credentials, endpoints and job targets remain live.\nBefore any runtime use, an operator must remap ALL paths, profiles, endpoints,\ncredentials and delivery/job destinations in a separate isolated environment.\nSee docs/backup.md. This marker is a warning, not a runtime sandbox.\n'
    write_private(destination / 'DO-NOT-RUN.txt', warning)
    entries['DO-NOT-RUN.txt'] = {'sha256': hashlib.sha256(warning).hexdigest(), 'sqlite': False}
    write_private(destination / 'manifest.json', json.dumps({'version': 1, 'files': entries, 'absent_optional': absent}, indent=2).encode())
    return verify(destination)


def restore(snapshot, destination, config):
    snapshot = safe_path(snapshot)
    manifest = verify(snapshot)
    config, state, homes, key = configured(config)
    destination = new_destination(destination, [snapshot, config.parent, state, *homes.values(), key])
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    for name in [*manifest['files'], 'manifest.json']:
        write_private(destination / name, read_regular(snapshot / name))
    return verify(destination)


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('create', 'verify', 'restore'):
        child = commands.add_parser(command)
        if command != 'verify':
            child.add_argument('--config', required=True, help='Private live config (read only; protects live targets)')
            child.add_argument('--destination', required=True, help='NEW directory under an existing owned 0700 parent')
        if command != 'create':
            child.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            result = create(args.config, args.destination)
        elif args.command == 'verify':
            result = verify(args.snapshot)
        else:
            result = restore(args.snapshot, args.destination, args.config)
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
        # Exception details can include configuration contents; do not log them.
        print(f'Backup operation refused/failed ({type(error).__name__}). Check private inputs, destination and manifest.', file=sys.stderr)
        return 1
    print(f"Verified {len(result['files'])} files; {len(result.get('absent_optional', []))} optional paths absent.")
    print('Inspection only: DO NOT RUN restored app/Hermes/cron before operator remapping.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
