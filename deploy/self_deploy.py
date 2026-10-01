"""Unprivileged, serialized mobile releases. Never controls the native API."""
from pathlib import Path
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import subprocess
import uuid
import shutil
import time
from contextlib import closing


@dataclass(frozen=True)
class Paths:
    source: Path = Path('/home/lindayi/projects/hermes-mobile-git')
    state: Path = Path('/home/lindayi/.local/share/hermes-mobile-deploy')
    webroot: Path = Path('/var/www/html/hermes')
    database: Path = Path('/home/lindayi/.local/share/hermes-mobile-live/runs.sqlite')
    dropin: Path = Path('/home/lindayi/.config/systemd/user/hermes-mobile.service.d/50-self-deploy.conf')


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    temporary.write_text(content)
    os.replace(temporary, path)


def point_current(current, destination):
    temporary = current.with_name('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(destination, target_is_directory=True)
    os.replace(temporary, current)


def wait_idle(journal, *, bootstrap=False, timeout=1800, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while True:
        with closing(journal.connect()) as connection:
            states = [row[0] for row in connection.execute("SELECT status FROM runs WHERE status NOT IN ('completed','failed','cancelled')")]
        if not states:
            return
        if 'unknown' in states or any(s not in ('queued', 'running', 'stopping', 'waiting_for_approval') for s in states):
            raise RuntimeError('unknown/unresolved run blocks deployment')
        if bootstrap or time.monotonic() >= deadline:
            raise RuntimeError('Bridge must be idle before deployment')
        sleep(1)


PROTECTED = ('requirements.lock', 'patches', 'hermes-plugin', 'backend/native_api_service.py',
             'backend/native_controls_service.py', 'backend/native_run_controls.py', 'backend/native_maintenance.py',
             'backend/native_session_deletion.py', 'backend/native_notifications.py',
             'backend/member_runtime.py', 'backend/member_scheduler.py', 'backend/member_jobs.py',
             'deploy/hermes-mobile-api.service', 'deploy/hermes-family-scheduler@.service')


def fingerprints(root, names):
    result = {}
    for name in names:
        path = root / name
        files = path.rglob('*') if path.is_dir() else [path]
        for file in files:
            if file.is_file() and not set(file.relative_to(root).parts) & SKIP:
                result[str(file.relative_to(root))] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


# One previously authorized, already-installed repair; not a general approval API.
# See docs/cron-delivery-registration-repair.md and plugin-release-reconciliation.md.
INSTALLED_MOBILE_PLUGIN = Path('/home/lindayi/.hermes/plugins/mobile_delivery')
CRON_REPAIR_OLD_SHA = '3c5ae90cd19fa6b62fa98d45f1ac53ceea67d984c8724d9dc299852f0e42c56f'
CRON_REPAIR_NEW_SHA = '66b6ab320e8375459d40eb0b63510d07df3b03468b9efb03b0e6b5fa2af38778'
CRON_REPAIR_MANIFEST_SHA = '7db60cfd51284c9b9fc37cc38ab4fbe2196c5c7399b73ed64e63e35f7ed407a7'
CRON_REPAIR_PREIMAGE = 'mobile-delivery-before-cron-metadata-20260928.py'


def _repair_bytes(path):
    from deploy.assets import checked_path
    import stat
    path = checked_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Cron repair evidence must be regular, unaliased files')
    return path.read_bytes()


def _repair_fingerprints(root):
    """Full repair evidence inventory; only Python bytecode caches are excluded."""
    from deploy.assets import checked_path
    import stat
    root = checked_path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('Cron repair evidence directory missing')
    result = {}

    def visit(directory):
        # Unlike Path.rglob, scandir propagates incomplete-enumeration errors.
        with os.scandir(directory) as entries:
            for entry in entries:
                file = checked_path(Path(entry.path))
                if stat.S_ISDIR(file.lstat().st_mode):
                    if file.name != '__pycache__':
                        visit(file)
                else:
                    result[str(file.relative_to(root))] = hashlib.sha256(_repair_bytes(file)).hexdigest()

    visit(root)
    return result


def check_protected_release(paths, stage, previous):
    """Read-only preflight: equality or the single evidenced cron metadata repair."""
    candidate = fingerprints(stage, PROTECTED)
    baseline = fingerprints(previous, PROTECTED)
    if candidate == baseline:
        return False
    error = 'Unsupported dependency/native changes require operator maintenance'
    plugin_file = 'hermes-plugin/mobile_delivery/__init__.py'
    old = {'mobile_delivery/__init__.py': CRON_REPAIR_OLD_SHA,
           'mobile_delivery/plugin.yaml': CRON_REPAIR_MANIFEST_SHA}
    new = {**old, 'mobile_delivery/__init__.py': CRON_REPAIR_NEW_SHA}
    # Compare the entire protected mapping, not just the allowed filename.
    if (baseline.get(plugin_file) != CRON_REPAIR_OLD_SHA
            or candidate != {**baseline, plugin_file: CRON_REPAIR_NEW_SHA}):
        raise RuntimeError(error)
    try:
        installed = {'mobile_delivery/' + name: digest
                     for name, digest in _repair_fingerprints(INSTALLED_MOBILE_PLUGIN).items()}
        preimage = _repair_bytes(paths.state / CRON_REPAIR_PREIMAGE)
        if (_repair_fingerprints(previous / 'hermes-plugin') != old
                or _repair_fingerprints(stage / 'hermes-plugin') != new
                or _repair_fingerprints(paths.source / 'hermes-plugin') != new
                or installed != new or hashlib.sha256(preimage).hexdigest() != CRON_REPAIR_OLD_SHA):
            raise RuntimeError(error + ': cron repair evidence mismatch')
    except (OSError, ValueError) as exc:
        raise RuntimeError(error + ': cron repair evidence unavailable or unsafe') from exc
    return True


def deploy(paths, **kwargs):
    from deploy.git_source import preflight
    git_sha = preflight(paths, service_run=(None if kwargs.get('frontend_only')
                                           else kwargs.get('run', subprocess.run)))
    from deploy.assets import checked_path
    for path in (paths.state, paths.source, paths.webroot, paths.database, paths.dropin,
                 *(paths.state / name for name in ('releases', 'backups', 'deploy.lock', 'status.json'))):
        checked_path(path)
    current = paths.state / 'current'
    if current.is_symlink():
        try:
            target = current.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError('Invalid current release pointer') from error
        releases = checked_path(paths.state / 'releases')
        if not target.is_dir() or target.parent != releases:
            raise ValueError('current must point to an existing private release')
        checked_path(target)
    elif current.exists():
        raise ValueError('current must be a private release symlink')
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.state.chmod(0o700)
    with (paths.state / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A deployment is already running') from None
        status_path = paths.state / 'status.json'
        previous_status = json.loads(status_path.read_text()) if status_path.exists() else {}
        if previous_status.get('status') == 'rollback_failed':
            if not kwargs.get('bootstrap') or kwargs.get('frontend_only'):
                raise RuntimeError('Failed rollback requires verified bootstrap recovery')
            recover_bootstrap(paths, previous_status, verify=kwargs['verify'])
        atomic_write(status_path, json.dumps({'status': 'running', 'git_sha': git_sha}))
        try:
            return _deploy(paths, git_sha=git_sha, **kwargs)
        except Exception as error:
            if json.loads(status_path.read_text())['status'] == 'running':
                atomic_write(status_path, json.dumps({'status': 'failed', 'git_sha': git_sha, 'error': str(error)}))
            raise


def recover_bootstrap(paths, previous_status, *, verify):
    """Recover only an initial rollback, under deploy()'s file lock; never restore data."""
    import re
    import sqlite3
    from deploy.assets import checked_path, public_tree, _asset_name
    release = previous_status.get('release')
    if not isinstance(release, str) or not re.fullmatch(r'[0-9a-f]{32}', release):
        raise RuntimeError('Invalid recovery release ID')
    status_path = paths.state / 'status.json'
    backup = checked_path(paths.state / 'backups' / release)

    def check_state(connection):
        if json.loads(status_path.read_text()) != previous_status:
            raise RuntimeError('Recovery status changed')
        for path in (paths.state / 'current', paths.dropin):
            if path.exists() or path.is_symlink():
                raise RuntimeError('Recovery requires absent current pointer and dropin')
        if connection.execute('SELECT singleton, owner FROM deployment_gate').fetchall() != [(1, release)]:
            raise RuntimeError('Recovery gate owner mismatch')
        if connection.execute("SELECT 1 FROM runs WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone():
            raise RuntimeError('Recovery requires idle bridge; active/unresolved runs remain')

    def check_assets():
        manifest = json.loads(checked_path(backup / 'manifest.json').read_text())
        if (not isinstance(manifest, dict) or set(manifest) != {'version', 'webroot', 'old', 'new'}
                or manifest['version'] != 1 or manifest['webroot'] != str(paths.webroot.absolute())):
            raise ValueError('Invalid recovery backup manifest')
        for key in ('old', 'new'):
            records = manifest[key]
            if not isinstance(records, dict) or 'index.html' not in records:
                raise ValueError('Invalid recovery backup records')
            for name, digest in records.items():
                _asset_name(name)
                if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise ValueError('Invalid recovery backup checksum')
        for root in (backup / 'files', paths.webroot):
            actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in public_tree(root)}
            if actual != manifest['old']:
                raise RuntimeError('Recovery assets differ from backup manifest checksums')

    if not paths.database.is_file():
        raise RuntimeError('Existing run database is required for recovery')
    # mode=rw cannot silently create a database; avoid RunJournal schema migrations.
    with closing(sqlite3.connect(paths.database.absolute().as_uri() + '?mode=rw', uri=True, timeout=10)) as connection:
        check_state(connection)
        check_assets()
        verify(paths.source, True, assets=backup / 'files')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            check_state(connection)
            check_assets()
            connection.execute('DELETE FROM deployment_gate WHERE singleton=1 AND owner=?', (release,))
        atomic_write(status_path, json.dumps({**previous_status, 'status': 'rolled_back', 'recovered': True}))


def _abort_baseline(paths, previous):
    """Read-only evidence for an abort that has not published or restarted anything."""
    from deploy.assets import checked_path, public_tree
    import stat

    current = paths.state / 'current'
    if previous is None:
        if current.exists() or current.is_symlink():
            raise RuntimeError('Abort baseline current pointer changed')
        pointer = None
    else:
        if not current.is_symlink() or current.resolve(strict=True) != previous:
            raise RuntimeError('Abort baseline current pointer changed')
        pointer = os.readlink(current)
    root = checked_path(previous or paths.source)
    source = {}

    def visit(path):
        path = checked_path(path)
        info = path.stat()
        if stat.S_ISDIR(info.st_mode):
            # scandir propagates incomplete enumeration; do not accept partial evidence.
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name not in SKIP:
                        visit(Path(entry.path))
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            source[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise RuntimeError('Unsafe abort baseline source')

    for name in (*SOURCE_TREES, *SOURCE_FILES, 'public'):
        path = checked_path(root / name)
        if path.exists():
            visit(path)
    public = {str(p.relative_to(paths.webroot)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in public_tree(paths.webroot)}
    dropin = checked_path(paths.dropin)
    return (pointer, source, public, dropin.read_bytes() if dropin.exists() else None)


def _clear_owned_deployment_gate(journal, owner, *, check=None):
    """The controller requires ownership, unlike RunJournal's idempotent clear API."""
    with closing(journal.connect()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        rows = connection.execute('SELECT singleton, owner FROM deployment_gate').fetchall()
        if [tuple(row) for row in rows] != [(1, owner)]:
            raise RuntimeError('Deployment gate ownership mismatch; admission state is unverified')
        if check is not None:
            check()
        deleted = connection.execute('DELETE FROM deployment_gate WHERE singleton=1 AND owner=?', (owner,))
        if deleted.rowcount != 1:
            raise RuntimeError('Deployment gate deletion did not remove exactly one owned row')


def _deploy(paths, *, frontend_only=False, bootstrap=False, checks, verify, run=subprocess.run,
           sleep=time.sleep, idle_timeout=1800, git_sha=None):
    from deploy.assets import publish_assets
    release_id = uuid.uuid4().hex
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.state.chmod(0o700)
    current = paths.state / 'current'
    if not frontend_only:
        if not current.exists() and not bootstrap:
            raise RuntimeError('Initial bootstrap is required in an operator maintenance window')
        if not paths.database.is_file():
            raise RuntimeError('Existing run database is required; missing does not mean idle')
    stage = stage_release(paths.source, paths.state / 'releases' / release_id, git_sha=git_sha)
    previous = current.resolve() if current.exists() else None
    reconciled = check_protected_release(paths, stage, previous) if previous else False
    from deploy.frontend_release import build_frontend
    build_frontend(stage / 'frontend', stage / 'public')
    frozen = fingerprints(stage, (*SOURCE_TREES, *SOURCE_FILES, 'public'))
    checks(stage)
    if fingerprints(stage, (*SOURCE_TREES, *SOURCE_FILES, 'public')) != frozen:
        raise RuntimeError('Staged source changed during checks; refusing untested release')
    if reconciled and not check_protected_release(paths, stage, previous):
        raise RuntimeError('Cron repair baseline changed during checks')
    backup = paths.state / 'backups' / release_id
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = None
    current = paths.state / 'current'
    previous_dropin = paths.dropin.read_text() if paths.dropin.exists() else None
    baseline = _abort_baseline(paths, previous) if not frontend_only else None
    gate_acquired = False
    activated = False
    try:
        if not frontend_only:
            from backend.runs import RunJournal
            journal = RunJournal(paths.database)
            journal.set_deployment_gate(release_id)
            gate_acquired = True
            wait_idle(journal, bootstrap=bootstrap, timeout=idle_timeout, sleep=sleep)
        if reconciled and not check_protected_release(paths, stage, previous):
            raise RuntimeError('Cron repair baseline changed before publication')
        publish_assets(stage / 'public', paths.webroot, backup)
        if journal:
            activated = True
            if bootstrap:
                run(['systemctl', '--user', 'stop', 'hermes-mobile.service'], check=True)
            point_current(current, stage)
            atomic_write(paths.dropin, '[Service]\nWorkingDirectory=' + str(current) + '\nNoNewPrivileges=yes\n')
            run(['systemctl', '--user', 'daemon-reload'], check=True)
        if journal:
            run(['systemctl', '--user', 'restart', 'hermes-mobile.service'], check=True)
        verify(stage, not frontend_only)
    except Exception as error:
        if not frontend_only and not gate_acquired:
            # Acquisition may have failed because somebody else owns admission.
            # Never run recovery or clear a gate we did not acquire.
            raise
        try:
            from deploy.assets import restore_assets
            if (backup / 'manifest.json').exists():
                restore_assets(paths.webroot, backup)
            if activated:
                if previous:
                    point_current(current, previous)
                else:
                    current.unlink(missing_ok=True)
                if previous_dropin is None:
                    paths.dropin.unlink(missing_ok=True)
                else:
                    atomic_write(paths.dropin, previous_dropin)
                run(['systemctl', '--user', 'daemon-reload'], check=True)
                run(['systemctl', '--user', 'restart', 'hermes-mobile.service'], check=True)
            abort_check = None
            if (backup / 'manifest.json').exists():
                verify(previous or paths.source, activated, assets=backup / 'files')
            elif activated:
                verify(previous or paths.source, True)
            elif journal:
                def abort_check():
                    if _abort_baseline(paths, previous) != baseline:
                        raise RuntimeError('Abort baseline changed; admission must not reopen')
                abort_check()
                verify(previous or paths.source, True)
            if journal:
                _clear_owned_deployment_gate(journal, release_id, check=abort_check)
        except Exception as rollback_error:
            atomic_write(paths.state / 'status.json', json.dumps({'status': 'rollback_failed', 'release': release_id, 'git_sha': git_sha,
                                                                 'error': str(error), 'rollback_error': str(rollback_error)}))
            raise RuntimeError('Deployment rollback failed; admission state is unverified') from rollback_error
        atomic_write(paths.state / 'status.json', json.dumps({'status': 'rolled_back', 'release': release_id, 'git_sha': git_sha, 'error': str(error)}))
        raise
    if journal:
        _clear_owned_deployment_gate(journal, release_id)
    atomic_write(paths.state / 'status.json', json.dumps({'status': 'succeeded', 'release': release_id, 'git_sha': git_sha}))
    return stage

# Explicit source trees: never copy the project's config, state or interpreter.
SOURCE_TREES = ('backend', 'frontend', 'tests', 'deploy', 'patches', 'hermes-plugin', 'scripts', 'spikes', 'docs', '.github')
SOURCE_FILES = ('requirements.lock', 'README.md')
SKIP = {'__pycache__', '.pytest_cache', 'artifacts', '.venv', 'node_modules', '.git',
        '.env', 'config.json', 'state', 'auth.sqlite', 'runs.sqlite'}


def stage_release(source, destination, *, git_sha=None):
    from deploy.assets import checked_path
    from deploy.git_source import preflight, verify_stage
    source, destination = checked_path(source), checked_path(destination)
    if git_sha is None:
        git_sha = preflight(Paths(source=source, state=destination, webroot=destination,
                                  database=destination, dropin=destination))
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    for name in (*SOURCE_TREES, *SOURCE_FILES):
        original = source / name
        if original.is_symlink() or (original.is_dir() and any(p.is_symlink() for p in original.rglob('*'))):
            raise ValueError(f'symlink in source: {name}')
        if not original.exists():
            continue
        if original.is_dir():
            shutil.copytree(original, destination / name,
                            ignore=lambda directory, names: set(names) & SKIP)
        else:
            shutil.copy2(original, destination / name)
    if git_sha is not None:
        verify_stage(source, destination, git_sha)
        # One small record follows this release's lifecycle, including native
        # callers that only receive a Path. Attempt status may then advance
        # without losing the still-active/rolled-back release's source binding.
        atomic_write(destination / 'git-provenance.json', json.dumps({'git_sha': git_sha}))
    return destination



def run_checks(paths, stage, *, run=subprocess.run):
    from deploy.test_workspace import run_suite
    # Developer and release checks share isolated, bounded test storage. Keep
    # the exact staged public assets and the established owner interpreters.
    return run_suite(
        stage, python=str(paths.source / '.venv/bin/python'),
        node='/home/lindayi/.hermes/node/bin/node', suite='all',
        assets=stage / 'public' if (stage / 'public').is_dir() else None,
        run=run,
    )


def verify_release(paths, stage, backend, *, assets=None, run=subprocess.run, sleep=time.sleep,
                   public_url='https://lindayi.me/hermes/',
                   health_url='http://127.0.0.1:9120/hermes/app-api/health'):
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.error import HTTPError
    urlopen = build_opener(ProxyHandler({})).open
    from urllib.parse import quote
    from deploy.assets import public_tree
    expected_root = assets if assets is not None else stage / ('public' if (stage / 'public').is_dir() else 'frontend')
    expected = {str(p.relative_to(expected_root)): p.read_bytes() for p in public_tree(expected_root)}
    actual = {str(p.relative_to(paths.webroot)): p.read_bytes() for p in public_tree(paths.webroot)}
    if actual != expected:
        raise RuntimeError('Published assets differ from expected release')
    if backend:
        last_error = None
        for attempt in range(30):
            try:
                with urlopen(health_url, timeout=2) as response:
                    if json.load(response).get('status') != 'ok':
                        raise RuntimeError('Bridge health is not ok')
                last_error = None
                break
            except (OSError, ValueError, RuntimeError) as error:
                last_error = error
                sleep(1)
        if last_error:
            raise RuntimeError('Bridge health verification failed') from last_error
        pid = run(['systemctl', '--user', 'show', 'hermes-mobile.service', '--property=MainPID', '--value'],
                  capture_output=True, text=True, check=True).stdout.strip()
        if not pid.isdigit() or pid == '0' or Path('/proc', pid, 'cwd').resolve() != stage.resolve():
            raise RuntimeError('Bridge process is not running the expected release')
        try:
            with urlopen(health_url.rsplit('/', 1)[0] + '/sessions', timeout=5):
                raise RuntimeError('Anonymous sessions endpoint must return 401')
        except HTTPError as error:
            if error.code != 401:
                raise RuntimeError('Anonymous sessions endpoint must return 401') from error
    # A readable index.html is insufficient: directory authorization can deny
    # the actual launch URL before Apache performs DirectoryIndex resolution.
    deadline = time.monotonic() + 90
    for name, data in [('', expected['index.html']), *expected.items()]:
        for attempt in range(6):
            matches = True
            # Check what browsers actually request as well as a cache-busted URL.
            # CDNs may return stale bytes while revalidating either cache key.
            for suffix in ('', '?deploy=' + uuid.uuid4().hex):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Public asset verification timed out: ' + (name or '/'))
                from deploy.public_http import public_asset_matches
                try:
                    matches = public_asset_matches(public_url + quote(name) + suffix,
                                                   data, min(10, remaining)) and matches
                except (TimeoutError, ConnectionError):
                    matches = False
                if time.monotonic() >= deadline:
                    raise RuntimeError('Public asset verification timed out: ' + (name or '/'))
            if matches:
                break
            if attempt == 5:
                raise RuntimeError('Public asset verification failed: ' + (name or '/'))
            sleep(min(2 ** attempt, 8, max(0, deadline - time.monotonic())))


def main(argv=None, *, paths=None, run=subprocess.run):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--frontend-only', action='store_true', help='Publish static assets now; never restart services')
    modes.add_argument('--bootstrap', action='store_true', help='Operator maintenance only: initial idle bridge migration')
    modes.add_argument('--status', action='store_true', help='Read last deployment status')
    modes.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    paths = paths or Paths()
    if os.geteuid() == 0:
        raise RuntimeError('Run as the unprivileged application owner, never root')
    if args.status:
        status = paths.state / 'status.json'
        print(status.read_text() if status.exists() else '{"status": "not_deployed"}')
        return 0
    if not (args.frontend_only or args.bootstrap or args.worker):
        from deploy.git_source import preflight
        preflight(paths, service_run=run)
        unit = 'hermes-mobile-deploy-' + uuid.uuid4().hex
        run(['systemd-run', '--user', '--collect', '--unit=' + unit, '--on-active=5s',
             '--property=NoNewPrivileges=yes', '--property=UMask=0077',
             '--property=WorkingDirectory=' + str(paths.source),
             str(paths.source / '.venv/bin/python'), '-m', 'deploy.self_deploy', '--worker'], check=True)
        print('Scheduled ' + unit + '; inspect journalctl --user -u ' + unit + '.service')
        return 0
    if args.worker and not os.environ.get('INVOCATION_ID'):
        raise RuntimeError('Worker must run in its separate transient systemd unit')
    deploy(paths, frontend_only=args.frontend_only, bootstrap=args.bootstrap,
           checks=lambda stage: run_checks(paths, stage, run=run),
           verify=lambda stage, backend, **kw: verify_release(paths, stage, backend, run=run, **kw), run=run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
