"""Only synthetic private fixtures: never connect to deployment state."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'backup.py'


def utility():
    assert SCRIPT.is_file(), 'backup utility not implemented'
    spec = importlib.util.spec_from_file_location('backup_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(data)
    path.chmod(0o600)


@pytest.fixture
def source(tmp_path):
    app, native = tmp_path / 'live-app', tmp_path / 'native'
    app.mkdir(mode=0o700)
    native.mkdir(mode=0o700)
    # Keep WAL connections open to prove committed WAL rows are included.
    connections = []
    for path in [app / name for name in ('auth.sqlite', 'runs.sqlite', 'notifications.sqlite')] + [native / 'state.db']:
        db = sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, body TEXT)')
        db.execute('INSERT INTO records VALUES (1, ?)', ('retained-' + path.name,))
        db.commit()
        connections.append(db)
    private_file(app / 'vapid.pem', 'fixture-private-key-not-real')
    config = app / 'config.json'
    private_file(config, json.dumps({'state_dir': str(app), 'profiles': {'default': str(native)}, 'vapid_private_key': str(app / 'vapid.pem')}))
    yield config, app, native, tmp_path
    for db in connections:
        db.close()


@pytest.mark.parametrize('hazard', ['config-link', 'state-link', 'profile-link', 'memory-link', 'dangling-memory', 'cron-link', 'vapid-link', 'wal-link', 'hardlink', 'fifo', 'traversal-name', 'traversal-destination', 'public', 'frontend', 'live-child', 'native-child', 'parent-link', 'open-parent', 'existing'])
def test_create_rejects_unsafe_paths_before_writing(source, hazard):
    config, app, native, root = source
    destination = root / 'snapshot'
    data = json.loads(config.read_text())
    if hazard == 'config-link':
        alias = root / 'config-link'
        alias.symlink_to(config)
        config = alias
    elif hazard in ('state-link', 'profile-link'):
        alias = root / 'alias'
        alias.symlink_to(app if hazard == 'state-link' else native, target_is_directory=True)
        if hazard == 'state-link':
            data['state_dir'] = str(alias)
        else:
            data['profiles']['default'] = str(alias)
    elif hazard in ('memory-link', 'dangling-memory', 'cron-link'):
        if hazard == 'cron-link':
            (native / 'cron').symlink_to(app, target_is_directory=True)
        else:
            (native / 'memories').mkdir()
            (native / 'memories/secret').symlink_to(root / 'missing' if hazard == 'dangling-memory' else config)
    elif hazard == 'vapid-link':
        (app / 'vapid.pem').unlink()
        (app / 'vapid.pem').symlink_to(config)
    elif hazard == 'wal-link':
        # An optional SQLite sidecar must not be followed either.
        (native / 'cron').mkdir()
        db = sqlite3.connect(native / 'cron/notepad.db')
        db.execute('CREATE TABLE test (id INTEGER)')
        db.close()
        (native / 'cron/notepad.db-wal').symlink_to(config)
    elif hazard in ('hardlink', 'fifo'):
        (native / 'memories').mkdir()
        path = native / 'memories/unsafe'
        os.link(config, path) if hazard == 'hardlink' else os.mkfifo(path)
    elif hazard == 'traversal-name':
        data['profiles'] = {'../escape': str(native)}
    elif hazard == 'traversal-destination':
        destination = root / 'unused/../snapshot'
    elif hazard in ('public', 'frontend'):
        (root / hazard).mkdir(mode=0o700)
        destination = root / hazard / 'snapshot'
    elif hazard == 'live-child':
        destination = app / 'snapshot'
    elif hazard == 'native-child':
        destination = native / 'snapshot'
    elif hazard == 'parent-link':
        (root / 'alias').symlink_to(root, target_is_directory=True)
        destination = root / 'alias/snapshot'
    elif hazard == 'open-parent':
        (root / 'shared').mkdir(mode=0o755)
        (root / 'shared').chmod(0o755)  # Explicitly unsafe even with umask 077.
        assert (root / 'shared').stat().st_mode & 0o077
        destination = root / 'shared/snapshot'
    elif hazard == 'existing':
        destination.mkdir(mode=0o700)
    if hazard != 'config-link':
        private_file(config, json.dumps(data))
    with pytest.raises((ValueError, OSError)):
        utility().create(config, destination)
    if hazard != 'existing':
        assert not destination.exists()


@pytest.mark.parametrize('damage', ['none', 'bytes', 'sqlite-rehashed', 'missing', 'extra', 'symlink', 'manifest-link', 'traversal', 'absolute', 'sqlite-flag', 'permissions', 'directory-permissions'])
def test_manifest_verification_and_sqlite_integrity(source, damage):
    config, app, native, root = source
    backup = utility()
    snapshot = root / 'snapshot'
    backup.create(config, snapshot)
    target = snapshot / 'app/state/auth.sqlite'
    manifest_path = snapshot / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if damage == 'bytes':
        target.write_bytes(b'tampered')
    elif damage == 'sqlite-rehashed':
        target.write_bytes(b'not a database')
        manifest['files']['app/state/auth.sqlite']['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
    elif damage == 'missing':
        target.unlink()
    elif damage == 'extra':
        private_file(snapshot / 'extra', 'unexpected')
    elif damage in ('symlink', 'manifest-link'):
        target = manifest_path if damage == 'manifest-link' else target
        target.unlink()
        target.symlink_to(config)
    elif damage in ('traversal', 'absolute'):
        entry = manifest['files'].pop('app/config.json')
        manifest['files']['../outside' if damage == 'traversal' else str(root / 'outside')] = entry
        manifest_path.write_text(json.dumps(manifest))
    elif damage == 'sqlite-flag':
        manifest['files']['app/state/auth.sqlite']['sqlite'] = False
        manifest_path.write_text(json.dumps(manifest))
    elif damage == 'permissions':
        target.chmod(0o644)
    elif damage == 'directory-permissions':
        (snapshot / 'app').chmod(0o755)
    if damage == 'none':
        assert backup.verify(snapshot)['version'] == 1
    else:
        with pytest.raises((ValueError, OSError, sqlite3.DatabaseError)):
            backup.verify(snapshot)


@pytest.mark.parametrize('destination_kind', ['new', 'existing', 'live', 'native', 'live-child', 'snapshot-child', 'public', 'symlink', 'traversal', 'tampered'])
def test_restore_is_new_private_inspection_only_and_preserves_rows(source, destination_kind):
    config, app, native, root = source
    backup = utility()
    snapshot = root / 'snapshot'
    backup.create(config, snapshot)
    destination = root / 'restored'
    if destination_kind == 'existing':
        destination.mkdir(mode=0o700)
    elif destination_kind == 'live':
        destination = app
    elif destination_kind == 'native':
        destination = native
    elif destination_kind == 'live-child':
        destination = app / 'new-child'
    elif destination_kind == 'snapshot-child':
        destination = snapshot / 'new-child'
    elif destination_kind == 'public':
        (root / 'public').mkdir(mode=0o700)
        destination = root / 'public/new-child'
    elif destination_kind == 'symlink':
        destination.symlink_to(app, target_is_directory=True)
    elif destination_kind == 'traversal':
        destination = root / 'unused/../restored'
    elif destination_kind == 'tampered':
        (snapshot / 'app/state/vapid.pem').write_text('tampered')
    if destination_kind != 'new':
        with pytest.raises((ValueError, OSError)):
            backup.restore(snapshot, destination, config)
        return
    backup.restore(snapshot, destination, config)
    assert backup.verify(destination)['version'] == 1
    for relative in ('app/state/auth.sqlite', 'app/state/runs.sqlite', 'app/state/notifications.sqlite', 'profiles/default/state.db'):
        with sqlite3.connect(destination / relative) as db:
            assert db.execute('SELECT body FROM records').fetchone() == ('retained-' + Path(relative).name,)
    warning = (destination / 'DO-NOT-RUN.txt').read_text()
    assert 'DO NOT RUN' in warning and 'remap' in warning
    assert (destination / 'app/config.json').read_bytes() == config.read_bytes()
    assert (destination / 'app/state/vapid.pem').read_bytes() == (app / 'vapid.pem').read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o700
    for path in destination.rglob('*'):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


def test_creation_checks_sqlite_integrity_before_manifest_completion(source):
    config, app, native, root = source
    db = sqlite3.connect(native / 'state.db')
    db.execute('CREATE TABLE invalid (value INTEGER CHECK(value > 0))')
    db.execute('PRAGMA ignore_check_constraints=ON')
    db.execute('INSERT INTO invalid VALUES (-1)')
    db.commit()
    db.close()
    destination = root / 'bad-snapshot'
    with pytest.raises(ValueError, match='integrity'):
        utility().create(config, destination)
    assert not (destination / 'manifest.json').exists()


def test_missing_optional_files_are_reported(source):
    config, app, native, root = source
    destination = root / 'snapshot'
    utility().create(config, destination)
    manifest = json.loads((destination / 'manifest.json').read_text())
    assert 'profiles/default/cron/jobs.json' in manifest['absent_optional']
    assert 'profiles/default/memories' in manifest['absent_optional']


def test_cli_round_trip_and_failure_never_prints_secrets(source):
    config, app, native, root = source
    snapshot, restored = root / 'snapshot', root / 'restored'
    base = [sys.executable, '-m', 'deploy.backup']
    commands = [
        ['create', '--config', str(config), '--destination', str(snapshot)],
        ['verify', '--snapshot', str(snapshot)],
        ['restore', '--config', str(config), '--snapshot', str(snapshot), '--destination', str(restored)],
    ]
    for args in commands:
        result = subprocess.run(base + args, cwd=SCRIPT.parents[1], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert 'verified' in result.stdout.lower()
        assert 'fixture-private-key' not in result.stdout + result.stderr
    (snapshot / 'app/state/vapid.pem').write_text('fixture-private-key-tampered')
    result = subprocess.run(base + commands[1], cwd=SCRIPT.parents[1], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'fixture-private-key' not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('mask', [0, 0o777])
def test_output_modes_are_exact_independent_of_umask(source, mask):
    config, app, native, root = source
    backup = utility()
    previous = os.umask(mask)
    try:
        backup.create(config, root / 'snapshot')
        backup.restore(root / 'snapshot', root / 'restore', config)
    finally:
        os.umask(previous)
    backup.verify(root / 'restore')


@pytest.mark.parametrize('remove', ['DO-NOT-RUN.txt', 'app/state/auth.sqlite', 'profiles/default/state.db'])
def test_manifest_cannot_omit_mandatory_files(source, remove):
    config, app, native, root = source
    backup = utility()
    snapshot = root / 'snapshot'
    backup.create(config, snapshot)
    (snapshot / remove).unlink()
    path = snapshot / 'manifest.json'
    data = json.loads(path.read_text())
    del data['files'][remove]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        backup.verify(snapshot)


def test_native_canonical_files_memories_and_cron_store(source):
    config, app, native, root = source
    for name in ('config.yaml', '.env', 'auth.json', 'SOUL.md', 'memories/MEMORY.md', 'memories/nested/USER.md', 'cron/jobs.json'):
        private_file(native / name, '{"jobs": []}' if name.endswith('.json') else 'fixture-' + name)
    for name in ('executions.db', 'notepad.db'):
        db = sqlite3.connect(native / 'cron' / name)
        db.execute('CREATE TABLE records (body TEXT)')
        db.execute('INSERT INTO records VALUES (?)', ('fixture-' + name,))
        db.commit()
        db.close()
    private_file(native / 'cron/output/ignored.txt', 'not in scope')
    private_file(native / 'profiles/unconfigured/.env', 'must not leak')
    snapshot = root / 'snapshot'
    utility().create(config, snapshot)
    for name in ('config.yaml', '.env', 'auth.json', 'SOUL.md', 'memories/MEMORY.md', 'memories/nested/USER.md', 'cron/jobs.json'):
        assert (snapshot / 'profiles/default' / name).read_bytes() == (native / name).read_bytes()
    for name in ('executions.db', 'notepad.db'):
        with sqlite3.connect(snapshot / 'profiles/default/cron' / name) as db:
            assert db.execute('SELECT body FROM records').fetchone() == ('fixture-' + name,)
    assert not (snapshot / 'profiles/default/cron/output').exists()
    assert not (snapshot / 'profiles/default/profiles').exists()


def test_snapshot_keeps_committed_wal_rows_and_private_modes(source):
    config, app, native, root = source
    backup = utility()
    snapshot = root / 'snapshot'
    backup.create(config, snapshot)
    for path in (snapshot / 'app/state').glob('*.sqlite'):
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT body FROM records').fetchone() == ('retained-' + path.name,)
    with sqlite3.connect(snapshot / 'profiles/default/state.db') as db:
        assert db.execute('SELECT body FROM records').fetchone() == ('retained-state.db',)
    assert (snapshot / 'app/config.json').read_bytes() == config.read_bytes()
    assert (snapshot / 'app/state/vapid.pem').read_bytes() == (app / 'vapid.pem').read_bytes()
    assert snapshot.stat().st_mode & 0o777 == 0o700
    for path in snapshot.rglob('*'):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    assert manifest['files']['profiles/default/state.db']['sha256'] == hashlib.sha256((snapshot / 'profiles/default/state.db').read_bytes()).hexdigest()
