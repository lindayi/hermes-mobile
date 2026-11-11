"""Disposable test workspaces shared by developer and deployment checks.

Each run gets private HOME/TMP/XDG/HERMES_HOME directories under a marked
same-user root (default /tmp/hmt-UID). A run holds an flock on its workspace
lock for its whole lifetime and passes that descriptor to its children, so a
workspace is "active" exactly while any process of that run (including
orphaned browsers) is alive. Successful runs are removed; failed runs keep only
logs and screenshots, bounded by count, age and size.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import select
import signal
import subprocess
import sys
import tempfile
import time

ROOT_MARKER = {'kind': 'hermes-mobile-tests-v1', 'uid': os.getuid()}
LOG_LIMIT = 1024 * 1024
PYTEST_BOOTSTRAP = (
    "import sys; "
    "sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages'); "
    "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
)
SUFFIX = {'python': '.py', 'js': '.test.mjs', 'browser': '.spec.mjs'}
PYTHON_FLAGS = {'-x', '-v', '-vv', '-q', '-s', '--disable-warnings'}
PYTHON_VALUES = {'-k', '-m', '--maxfail', '--tb'}
NODE_VALUES = {'--test-name-pattern', '--test-skip-pattern'}
KEEP_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.log'}


def _root(root=None):
    root = Path(root if root is not None else f'/tmp/hmt-{os.getuid()}').absolute()
    if root.resolve() != root:
        raise ValueError(f'Symlink or non-canonical root: {root}')
    if not root.exists():
        root.mkdir(mode=0o700, exist_ok=True)
        marker = root / '.managed.json'
        if not marker.exists():
            marker.write_text(json.dumps(ROOT_MARKER))
    info = root.lstat()
    if not root.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f'Managed test root must be a private same-user directory: {root}')
    marker = root / '.managed.json'
    try:
        if marker.is_symlink() or json.loads(marker.read_text()) != ROOT_MARKER:
            raise ValueError
    except (OSError, ValueError):
        raise ValueError(f'Unmarked managed root: {root}') from None
    return root


@contextmanager
def _try_lock(path):
    """Yield True when the (unlocked) workspace lock was acquired."""
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield False
        return
    try:
        yield True
    finally:
        os.close(fd)


def _size(path):
    return sum(p.lstat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())


def cleanup(root=None, *, apply=False, now=None, max_records=3,
            max_age=7 * 86400, max_bytes=64 * 1024 * 1024):
    """Report (dry-run default) or remove inactive stale scratch and old failures."""
    candidate = Path(root if root is not None else f'/tmp/hmt-{os.getuid()}')
    if not candidate.exists():
        return []
    root = _root(candidate)
    now = time.time() if now is None else now
    report, failures = [], []
    for path in sorted(root.glob('r-*')):
        try:
            if path.is_symlink() or not path.is_dir():
                raise ValueError
            data = json.loads((path / '.run.json').read_text())
            with _try_lock(path / '.lock') as free:
                if not free:
                    report.append(dict(path=str(path), action='skip', bytes=0, reason='active'))
                    continue
            size = _size(path)
            if data.get('state') == 'failed':
                failures.append((data.get('finished', data['created']), path, size))
            elif now - data['created'] > 86400:
                report.append(dict(path=str(path), action='remove', bytes=size, reason='stale scratch'))
        except (OSError, ValueError, KeyError, TypeError):
            report.append(dict(path=str(path), action='skip', bytes=0, reason='unrecognized or unsafe'))
    used = count = 0
    for finished, path, size in sorted(failures, reverse=True):
        if now - finished > max_age or count >= max_records or used + size > max_bytes:
            report.append(dict(path=str(path), action='remove', bytes=size, reason='failure retention limit'))
        else:
            used, count = used + size, count + 1
    if apply:
        for item in report:
            if item['action'] == 'remove':
                path = Path(item['path'])
                with _try_lock(path / '.lock') as free:
                    if free:
                        shutil.rmtree(path)
                    else:
                        item.update(action='skip', bytes=0, reason='active')
    return report


def _auto_cleanup(root):
    try:
        for item in cleanup(root, apply=True):
            if item['action'] == 'remove':
                print(f"Test cleanup: {item['reason']}: {item['path']} ({item['bytes']} bytes)", file=sys.stderr)
    except OSError as error:
        print(f'Test cleanup failed: {error}', file=sys.stderr)


def _validate_args(suite, args, source):
    if suite not in {'all', 'python', 'js', 'browser'}:
        raise ValueError(f'Unknown suite: {suite}')
    if suite == 'all' and args:
        raise ValueError('Extra arguments require a selected suite')
    flags = PYTHON_FLAGS if suite == 'python' else set()
    values = PYTHON_VALUES if suite == 'python' else NODE_VALUES
    tests = (source / 'tests').resolve()
    options, selected = [], []
    iterator = iter(args)
    for arg in iterator:
        if not arg.startswith('-'):
            path = (source / arg).resolve()
            if (not arg.endswith(SUFFIX[suite]) or not path.is_relative_to(tests)
                    or not path.is_file() or Path(arg).is_absolute()):
                raise ValueError(f'Invalid {suite} test path: {arg}')
            if arg not in selected:
                selected.append(arg)
            continue
        option, separator, value = arg.partition('=')
        if arg in flags:
            options.append(arg)
            continue
        if option not in values:
            raise ValueError(f'Unsupported test option: {arg}')
        options.append(arg)
        if not separator:
            value = next(iterator, '')
            options.append(value)
        if not value or value.startswith('-'):
            raise ValueError(f'Missing value for {option}')
    return tuple(options), tuple(selected)


def run_suite(source, *, python, node, suite='all', assets=None,
              run=subprocess.run, root=None, extra_args=(), node_concurrency=2):
    """Run tests with isolated paths and bounded diagnostics.

    The injected ``run`` callback is a synchronous test seam that must honor
    ``check=True``; the real path streams output and kills the process group.
    """
    if type(node_concurrency) is not int or not 1 <= node_concurrency <= 4:
        raise ValueError('node_concurrency must be an integer from 1 to 4')
    source = Path(source).absolute()
    extra_args, selected = _validate_args(suite, tuple(extra_args), source)
    root = _root(root)
    _auto_cleanup(root)
    workspace = Path(tempfile.mkdtemp(prefix='r-', dir=root))
    lock = os.open(workspace / '.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (workspace / '.run.json').write_text(json.dumps(dict(ROOT_MARKER, state='running', created=time.time())))
        if run is subprocess.run:
            def run(command, **kwargs):
                return _execute(command, workspace=workspace, lock=lock, **kwargs)
        try:
            _suite(source, python, node, suite, assets, run, extra_args, selected, workspace, node_concurrency)
        except BaseException as error:
            _retain(workspace, error)
            raise
        shutil.rmtree(workspace)
    finally:
        os.close(lock)
        _auto_cleanup(root)


def _execute(command, *, workspace, lock, check, **kwargs):
    log = workspace / 'run.log'
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True, pass_fds=(lock,), **kwargs)
    tail, truncated = bytearray(), False
    os.set_blocking(process.stdout.fileno(), False)
    try:
        # Stop at the leader's exit even if a leaked descendant holds stdout open.
        while True:
            exited = process.poll() is not None
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            chunk = os.read(process.stdout.fileno(), 65536) if ready else b''
            if chunk:
                sys.stdout.write(chunk.decode('utf-8', errors='replace'))
                sys.stdout.flush()
                tail.extend(chunk)
                if len(tail) > LOG_LIMIT:
                    truncated = True
                    del tail[:-LOG_LIMIT]
            elif exited or ready:
                break
        returncode = process.wait()
    finally:
        _kill_group(process)
        process.stdout.close()
        log.write_bytes(bytes(tail))
        if truncated:
            print(f'Test log truncated to its last 1 MiB: {log}', file=sys.stderr)
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, command)


def _kill_group(process):
    """Terminate the run's session, including detached descendants in it."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            continue
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    process.poll()


def _retain(workspace, error):
    """Keep only logs and screenshots of a failure; never homes, DBs or caches."""
    for path in workspace.iterdir():
        if path.name in {'.run.json', '.lock', 'run.log', 'artifacts'}:
            continue
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() and not path.is_symlink() else path.unlink()
    artifacts = workspace / 'artifacts'
    if artifacts.is_dir() and not artifacts.is_symlink():
        for path in artifacts.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in KEEP_SUFFIXES:
                shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    elif artifacts.is_symlink():
        artifacts.unlink()
    with (workspace / 'run.log').open('a') as stream:
        stream.write(f'\n{type(error).__name__}: {error}\n')
    data = json.loads((workspace / '.run.json').read_text())
    data.update(state='failed', finished=time.time())
    (workspace / '.run.json').write_text(json.dumps(data))


def _suite(source, python, node, suite, assets, run, extra_args, selected, workspace, node_concurrency):
    env = dict(os.environ)
    # Browser executables are installed dependencies: keep their real cache.
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
    if suite in {'all', 'python'}:
        run([str(python), '-c', PYTEST_BOOTSTRAP, *(selected or ('tests',)), '-q', '-p', 'no:cacheprovider',
             '--basetemp', str(workspace / 'pytest'), *extra_args], cwd=source, env=env, check=True, umask=0o077)
    if suite in {'all', 'js', 'browser'}:
        patterns = {'all': ('*.test.mjs', '*.spec.mjs'), 'js': ('*.test.mjs',), 'browser': ('*.spec.mjs',)}[suite]
        tests = list(selected) or sorted(str(p.relative_to(source)) for pattern in patterns
                                         for p in (source / 'tests/browser').glob(pattern))
        if not tests:
            raise RuntimeError(f'Frontend {suite} suite missing from source')
        run([str(node), '--test', f'--test-concurrency={node_concurrency}', *extra_args, *tests],
            cwd=source, env=env, check=True, umask=0o077)
