"""Disposable test workspaces shared by developer and deployment checks.

Dependencies are never copied. With no supplied assets, tests use source/frontend;
release callers should supply their generated public directory explicitly.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import json
import math
import stat
import time
import fcntl
import re
import sys
import signal
import selectors
import threading
import ctypes
import secrets
from contextlib import contextmanager, ExitStack


@contextmanager
def _lock(path, *, create=False, blocking=True):
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    fd = _open_private(path, flags)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield fd
    finally:
        os.close(fd)


def _run_marker(path):
    if not re.fullmatch(r'r-[a-zA-Z0-9_-]{8}', path.name):
        raise ValueError('Unknown entry')
    _private(path, directory=True)
    _private(path / '.run.json')
    data = json.loads((path / '.run.json').read_text())
    if (not isinstance(data, dict)
            or data.get('kind') != ROOT_MARKER['kind'] or data.get('uid') != os.getuid()
            or data.get('state') not in ('running', 'failed')):
        raise ValueError('Invalid run marker')
    for value in (data.get('created'), data.get('finished', data.get('created'))):
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False  # Too large for timestamp arithmetic.
        if not valid:
            raise ValueError('Invalid run timestamp')
    if 'token' in data and (not isinstance(data['token'], str)
                            or not re.fullmatch(r'[0-9a-f]{64}', data['token'])):
        raise ValueError('Invalid process ownership marker')
    pgid = data.get('pgid')
    # Linux killpg takes a signed 32-bit pid_t; reject overflow before probing.
    if pgid is not None and (type(pgid) is not int or not 1 < pgid < 2 ** 31):
        raise ValueError('Invalid process group marker')
    return data


def _size(path):
    total = 0
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            entry = Path(parent) / name
            info = entry.lstat()
            if stat.S_ISREG(info.st_mode):
                if info.st_uid != os.getuid():
                    raise ValueError(f'Foreign-owned workspace entry: {entry}')
                total += info.st_size
    return total


def cleanup(root=None, *, apply=False, now=None, max_records=3,
            max_age=7 * 86400, max_bytes=64 * 1024 * 1024):
    """Report/remove only inactive marked runs; dry-run is the default.

    Oldest failure records are discarded whole until count/age/byte limits hold.
    Unknown entries are reported and never removed. Active locks are never waited on.
    """
    candidate = Path(root) if root is not None else Path(f'/tmp/hmt-{os.getuid()}')
    if not candidate.exists() and not candidate.is_symlink():
        return []
    root = _root(root)
    now = time.time() if now is None else now
    report, failures = [], []
    with _lock(root / '.lock', create=True), ExitStack() as locks:
        for path in sorted(root.iterdir()):
            if path.name in {'.managed.json', '.lock'}:
                continue
            try:
                _run_marker(path)
                locks.enter_context(_lock(path / '.lock', blocking=False))
                data = _run_marker(path)  # Revalidate after acquiring the lock.
                if _workspace_active(path, data):
                    report.append(dict(path=str(path), action='skip', bytes=0, reason='live or uninspectable workspace user'))
                    continue
                size = _size(path)
                if data['state'] == 'failed':
                    failures.append((data.get('finished', data['created']), path, size))
                elif now - data['created'] > 86400:
                    report.append(dict(path=str(path), action='remove', bytes=size, reason='stale scratch'))
            except BlockingIOError:
                report.append(dict(path=str(path), action='skip', bytes=0, reason='active'))
            except (ValueError, OSError):
                report.append(dict(path=str(path), action='skip', bytes=0, reason='unrecognized or unsafe'))
        used = count = 0
        for finished, path, size in sorted(failures, reverse=True):
            if now - finished > max_age or count >= max_records or used + size > max_bytes:
                report.append(dict(path=str(path), action='remove', bytes=size, reason='failure retention limit'))
            else:
                used += size
                count += 1
        if apply:
            for item in report:
                if item['action'] == 'remove':
                    path = Path(item['path'])
                    data = _run_marker(path)
                    if _workspace_active(path, data):
                        item.update(action='skip', bytes=0, reason='live or uninspectable workspace user')
                        continue
                    shutil.rmtree(path)
    return report


def _auto_cleanup(root):
    for item in cleanup(root, apply=True):
        if item['action'] == 'remove':
            print(f"Test cleanup: {item['reason']}: {item['path']} ({item['bytes']} bytes)", file=sys.stderr)


def _cleanup_error(primary, error):
    message = f'Test cleanup failed: {error}'
    print(message, file=sys.stderr)
    if primary is None:
        raise error
    primary.add_note(message)


def _open_private(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private(path)
        opened, current = os.fstat(fd), path.lstat()
        if ((opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or opened.st_uid != os.getuid() or opened.st_mode & 0o077
                or opened.st_nlink != 1 or not stat.S_ISREG(opened.st_mode)):
            raise ValueError(f'Unsafe opened management file: {path}')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_json(path, data):
    temporary = path.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream)
    os.replace(temporary, path)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _retain(workspace, error):
    # Only logs and screenshots survive, never native homes, DBs or caches.
    for path in workspace.iterdir():
        if path.name not in {'.run.json', '.lock', 'run.log', 'artifacts'}:
            _remove(path)
    artifacts = workspace / 'artifacts'
    if artifacts.is_symlink():
        artifacts.unlink()
    elif artifacts.exists():
        for path in artifacts.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.log'}:
                _remove(path)
    log = workspace / 'run.log'
    fd = _open_private(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    with os.fdopen(fd, 'w') as stream:
        stream.write(f'\n{type(error).__name__}: {error}\n')
    data = json.loads((workspace / '.run.json').read_text())
    data.update(state='failed', finished=time.time())
    _write_json(workspace / '.run.json', data)


ROOT_MARKER = {'kind': 'hermes-mobile-tests-v1', 'uid': os.getuid()}


def _private(path, *, directory=False):
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f'Not a private same-user managed path: {path}')
    if not directory and info.st_nlink != 1:
        raise ValueError(f'Hard-linked management file: {path}')


def _root(root):
    root = Path(root) if root is not None else Path(f'/tmp/hmt-{os.getuid()}')
    root = root.absolute()
    # Reject symlinks in every component, not merely the final path.
    if root.resolve() != root:
        raise ValueError(f'Symlink or non-canonical root: {root}')
    if not root.exists() and not root.is_symlink():
        # Publish the directory and its marker together; concurrent first runs
        # must never observe an incomplete root. Only this temporary is disposable.
        staging = Path(tempfile.mkdtemp(prefix=f'.{root.name}-', dir=root.parent))
        try:
            fd = os.open(staging / '.managed.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(ROOT_MARKER, stream)
            try:
                os.rename(staging, root)
            except OSError:
                if not root.exists():
                    raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    _private(root, directory=True)
    marker = root / '.managed.json'
    try:
        _private(marker)
        if json.loads(marker.read_text()) != ROOT_MARKER:
            raise ValueError(f'Invalid managed root marker: {root}')
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ValueError(f'Unmarked managed root: {root}') from error
    return root


PYTEST_BOOTSTRAP = (
    "import sys; "
    "sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages'); "
    "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
)


def _validate_args(suite, args, source):
    if suite not in {'all', 'python', 'js', 'browser'}:
        raise ValueError(f'Unknown suite: {suite}')
    if suite == 'all' and args:
        raise ValueError('Extra arguments require a selected suite')
    flags = {'-x', '-v', '-vv', '-q', '-s', '--disable-warnings'} if suite == 'python' else set()
    values = {'-k', '-m', '--maxfail', '--tb'} if suite == 'python' else {'--test-name-pattern', '--test-skip-pattern'}
    options, selected = [], []
    iterator = iter(args)
    for arg in iterator:
        if not arg.startswith('-'):
            # Reject alternate spellings, node IDs, traversal, option-like path
            # components, and symlinks even when their target remains in tests/.
            suffix = {'python': '.py', 'js': '.test.mjs', 'browser': '.spec.mjs'}[suite]
            if (not re.fullmatch(r'tests/(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*[A-Za-z0-9_][A-Za-z0-9_.-]*', arg)
                    or not arg.endswith(suffix)):
                raise ValueError(f'Invalid {suite} test path: {arg}')
            path = source
            for part in Path(arg).parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError(f'Symlink test path: {arg}')
            if not path.is_file() or not path.resolve().is_relative_to((source / 'tests').resolve()):
                raise ValueError(f'Missing or unsafe test path: {arg}')
            if arg not in selected:
                selected.append(arg)
            continue
        option, separator, value = arg.partition('=')
        if arg in flags:
            options.append(arg)
            continue
        if option not in values:
            raise ValueError(f'Unsupported test option (may escape isolation): {arg}')
        options.append(arg)
        if not separator:
            value = next(iterator, '')
            options.append(value)
        if not value or value.startswith('-'):
            raise ValueError(f'Missing value for {option}')
    return tuple(options), tuple(selected)


def run_suite(source, *, python, node, suite='all', assets=None,
              run=subprocess.run, root=None, extra_args=()):
    """Run tests with isolated paths and bounded diagnostics.

    Real execution is main-thread/Linux only and owns the initial process group
    plus identity-verified descendants (including detached Playwright browsers).
    The injected run callback is a synchronous test seam: it must honor check=True
    and finish its children before returning; lifecycle/capture are then its duty.
    """
    source = Path(source).absolute()
    extra_args, selected = _validate_args(suite, tuple(extra_args), source)
    root = _root(root)
    _auto_cleanup(root)
    try:
        with ExitStack() as locks:
            with _lock(root / '.lock', create=True):
                workspace = Path(tempfile.mkdtemp(prefix='r-', dir=root))
                lock = locks.enter_context(_lock(workspace / '.lock', create=True))
                _write_json(workspace / '.run.json', dict(ROOT_MARKER, state='running', created=time.time()))
            return _suite_in_workspace(source, python, node, suite, assets, run, extra_args, workspace, lock, selected)
    finally:
        primary = sys.exc_info()[1]
        try:
            _auto_cleanup(root)
        except Exception as error:
            _cleanup_error(primary, error)


def _workspace_processes(workspace, token=None, known=None):
    """Find same-UID workspace users; unreadable live processes fail closed.

    A path association guards deletion but alone never authorizes signalling.
    """
    users, uncertain, identities = {}, False, {}
    # Marker timestamps can be aged by recovery tooling; the untouched lock's
    # creation mtime bounds when this run could first have spawned a child.
    born = (workspace / '.lock').stat().st_mtime
    boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
                if line.startswith('btime '))
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        predates_run = False
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            identities[int(entry.name)] = (fields[19], int(fields[1]), fields[0])
            predates_run = boot + int(fields[19]) / os.sysconf('SC_CLK_TCK') + 1 < born
            if fields[0] == 'Z':
                continue
            env = dict(item.split(b'=', 1) for item in (entry / 'environ').read_bytes().split(b'\0')
                       if b'=' in item)
            owned = bool(token) and env.get(b'HERMES_TEST_RUN_ID') == token.encode()
            paths = [os.fsdecode(env.get(key, b'')) for key in (b'HOME', b'TMPDIR')]
            paths.append(os.readlink(entry / 'cwd'))
            # Chromium clears /proc/environ in its main process. Its exact
            # workspace-bound profile still protects scratch after SIGKILL.
            paths.extend(os.fsdecode(arg.split(b'=', 1)[1])
                         for arg in (entry / 'cmdline').read_bytes().split(b'\0')
                         if arg.startswith(b'--user-data-dir='))
            if owned or any(value and Path(value).is_relative_to(workspace) for value in paths):
                users[int(entry.name)] = (fields[19], owned)
        except (FileNotFoundError, ProcessLookupError):
            # A disappearing process is harmless; a live process with an
            # unreadable/deleted cwd is not proof of inactivity.
            if entry.exists() and not predates_run:
                uncertain = True
        except (OSError, ValueError, IndexError):
            # Pre-existing opaque user services cannot be this run's children.
            uncertain = uncertain or not predates_run
    # Preserve verified lineage when a browser clears its environment or is
    # orphaned. PID + kernel start time, not a numeric PID alone, is authority.
    owned = {pid for pid, (_, ours) in users.items() if ours}
    owned.update(pid for pid, started in (known or {}).items()
                 if pid in identities and identities[pid][0] == started)
    while True:
        descendants = {pid for pid, (_, parent, _) in identities.items() if parent in owned}
        if descendants <= owned:
            break
        owned.update(descendants)
    for pid in owned:
        started, _, state = identities[pid]
        if state != 'Z':
            users[pid] = (started, True)
    return users, uncertain


def _workspace_active(workspace, data):
    if _group_alive(data.get('pgid')):
        return True
    users, uncertain = _workspace_processes(workspace, data.get('token'))
    return bool(users) or uncertain


def _group_alive(pgid):
    if pgid is None:
        return False
    if not isinstance(pgid, int) or pgid <= 1:
        raise ValueError('Invalid process group marker')
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Reused/foreign PGID: unknown activity must only skip this run.
    # Linux /proc distinguishes unreaped zombies from processes that can use files.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != 'Z':
                return True
        except FileNotFoundError:
            continue
        except PermissionError:
            return True  # Fail closed if inactivity cannot be established.
    return False


@contextmanager
def _signals():
    pending = []
    previous = {}
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Managed subprocess execution requires the main thread')
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, lambda signum, frame: pending.append(signum))
        yield pending
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _stop_group(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            process.poll()  # Reap our direct child before checking the group.
            if not _group_alive(process.pid):
                process.wait()
                return
            time.sleep(0.02)
    raise RuntimeError(f'Process group {process.pid} still alive; preserving workspace')


@contextmanager
def _subreaper():
    """Adopt orphaned browsers for exact-PID reaping, never waitpid(-1)."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'Cannot enable test child subreaper')
    try:
        yield
    finally:
        if libc.prctl(36, previous.value, 0, 0, 0):
            raise OSError(ctypes.get_errno(), 'Cannot restore test child subreaper')


def _signal_owned(pid, started, token, sig, *, verified=False):
    """Pin the PID and recheck identity; verified lineage survives env clearing."""
    entry = Path('/proc') / str(pid)
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
        if entry.stat().st_uid != os.getuid() or fields[19] != started or fields[0] == 'Z':
            return
        if not verified and f'HERMES_TEST_RUN_ID={token}'.encode() not in (entry / 'environ').read_bytes().split(b'\0'):
            return
        signal.pidfd_send_signal(fd, sig)
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        os.close(fd)


def _stop_owned(workspace, token, known):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + 3
        while True:
            users, uncertain = _workspace_processes(workspace, token, known)
            owned = {pid: started for pid, (started, ours) in users.items() if ours}
            known.update(owned)
            for pid, started in owned.items():
                _signal_owned(pid, started, token, sig, verified=True)
            for pid, started in known.items():
                try:
                    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    if fields[19] == started:
                        os.waitpid(pid, os.WNOHANG)
                except (FileNotFoundError, ProcessLookupError, ChildProcessError):
                    pass
            if not users and not uncertain:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    raise RuntimeError('Live or uninspectable workspace user; preserving workspace')


def _execute(command, *, workspace, lock, **kwargs):
    check = kwargs.pop('check')
    log = workspace / 'run.log'
    output = bytearray(log.read_bytes() if log.exists() else b'')
    truncated = False
    token, known = secrets.token_hex(32), {}
    kwargs['env'] = dict(kwargs['env'], HERMES_TEST_RUN_ID=token)
    with _signals() as pending, _subreaper():
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True, pass_fds=(lock,), **kwargs)
        try:
            data = _run_marker(workspace)
            data.update(pgid=process.pid, token=token)
            _write_json(workspace / '.run.json', data)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    users, _ = _workspace_processes(workspace, token, known)
                    known.update({pid: started for pid, (started, ours) in users.items()
                                  if ours and pid != process.pid})
                    if pending:
                        raise SystemExit(128 + pending[0])
                    events = selector.select(timeout=0.1)
                    if events:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            selector.unregister(process.stdout)
                            continue
                        sys.stdout.write(chunk.decode('utf-8', errors='replace'))
                        sys.stdout.flush()
                        output.extend(chunk)
                        truncated = truncated or len(output) > 1024 * 1024
                        del output[:-1024 * 1024]
                    elif process.poll() is not None:
                        break
            returncode = process.wait()
            if check and returncode:
                raise subprocess.CalledProcessError(returncode, command)
        finally:
            primary = sys.exc_info()[1]
            try:
                try:
                    _stop_group(process)
                finally:
                    _stop_owned(workspace, token, known)
                    process.stdout.close()
                data = _run_marker(workspace)
                data['pgid'] = None
                _write_json(workspace / '.run.json', data)
                fd = _open_private(log, os.O_WRONLY | os.O_CREAT)
                with os.fdopen(fd, 'wb') as stream:
                    stream.truncate(0)
                    stream.write(output)
                if truncated:
                    print(f'Test log truncated to its last 1 MiB: {log}', file=sys.stderr)
            except Exception as error:
                _cleanup_error(primary, error)
        if pending:
            raise SystemExit(128 + pending[0])


def _suite_in_workspace(source, python, node, suite, assets, run, extra_args, workspace, lock, selected=()):
    if run is subprocess.run:
        def run(command, **kwargs):
            return _execute(command, workspace=workspace, lock=lock, **kwargs)
    env = dict(os.environ)
    # Browser executables/ffmpeg are installed dependencies, not disposable
    # test output. Preserve their real cache before replacing HOME/XDG paths.
    if not env.get('PLAYWRIGHT_BROWSERS_PATH'):
        browser_cache = Path(env.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'ms-playwright'
        if browser_cache.is_dir():
            env['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_cache.absolute())
    for key in ('HERMES_MOBILE_CONFIG', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONPYCACHEPREFIX',
                'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_DEBUG_TEMPROOT', 'NODE_OPTIONS'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['HERMES_TEST_PYTHON'] = str(python)
    env['HERMES_FRONTEND_DIR'] = str(Path(assets).absolute() if assets else source / 'frontend')
    env['PATH'] = os.pathsep.join(filter(None, (str(Path(node).parent), env.get('PATH', os.defpath))))
    for key, folder in {'HOME': 'home', 'HERMES_HOME': 'native', 'XDG_CACHE_HOME': 'cache',
                        'XDG_CONFIG_HOME': 'config', 'XDG_STATE_HOME': 'state',
                        'XDG_DATA_HOME': 'data', 'XDG_RUNTIME_DIR': 'runtime', 'TMPDIR': 't',
                        'HERMES_TEST_ARTIFACT_DIR': 'artifacts'}.items():
        directory = workspace / folder
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    try:
        if suite in {'all', 'python'}:
            run([str(python), '-c', PYTEST_BOOTSTRAP, *(selected or ('tests',)), '-q', '-p', 'no:cacheprovider',
                 '--basetemp', str(workspace / 'pytest'), *extra_args], cwd=source, env=env, check=True, umask=0o077)
        if suite in {'all', 'js', 'browser'}:
            patterns = ('*.test.mjs', '*.spec.mjs') if suite == 'all' else (('*.test.mjs',) if suite == 'js' else ('*.spec.mjs',))
            tests = list(selected) or sorted(str(p.relative_to(source)) for pattern in patterns
                           for p in (source / 'tests/browser').glob(pattern))
            if not tests:
                raise RuntimeError(f'Frontend {suite} suite missing from source')
            run([str(node), '--test', '--test-concurrency=2', *extra_args, *tests], cwd=source, env=env, check=True, umask=0o077)
    except BaseException as error:
        try:
            if not _workspace_active(workspace, _run_marker(workspace)):
                _retain(workspace, error)
        except Exception as cleanup_error:
            _cleanup_error(error, cleanup_error)
        raise
    else:
        if _workspace_active(workspace, _run_marker(workspace)):
            raise RuntimeError('Live or uninspectable workspace user; preserving workspace')
        shutil.rmtree(workspace)
