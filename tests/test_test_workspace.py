"""Managed test tooling acceptance tests; never touch the default managed root."""
import importlib.util
import json
import time
import os
import signal
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1]
NODE = '/home/lindayi/.hermes/node/bin/node'


def workspace_module():
    assert (SOURCE / 'deploy/test_workspace.py').exists(), 'managed workspace implementation missing'
    from deploy import test_workspace
    return test_workspace


@pytest.fixture
def process_free_workspace(monkeypatch):
    """Opt in only for fabricated records or callbacks that spawn no children."""
    module = workspace_module()
    # Policy fixtures cannot infer inactivity from unrelated same-UID /proc
    # churn. Keep marker/lock/PGID guards and cleanup policy real; live-child,
    # uncertain-user and active-lock tests must use workspace_module() instead.
    monkeypatch.setattr(module, '_workspace_processes', lambda *a, **k: ({}, False))
    return module


def test_deploy_checks_use_managed_suite_with_exact_staged_assets(tmp_path, monkeypatch):
    from deploy import self_deploy
    from types import SimpleNamespace
    module = workspace_module()
    stage = fixture_source(tmp_path)
    (stage / 'public').mkdir()
    source = tmp_path / 'interpreter-source'
    calls = []
    runner = lambda *a, **k: None
    monkeypatch.setenv('HERMES_TEST_PYTHON', '/untrusted/python')
    monkeypatch.setattr(module, 'run_suite', lambda *a, **k: calls.append((a, k)))
    self_deploy.run_checks(SimpleNamespace(source=source), stage, run=runner)
    assert calls == [((stage,), dict(python=str(source / '.venv/bin/python'),
                                   node=NODE, suite='all', assets=stage / 'public', run=runner))]


def test_installed_playwright_cache_is_pinned_before_home_isolation(tmp_path, monkeypatch, process_free_workspace):
    module = process_free_workspace
    home = tmp_path / 'original-home'
    cache = home / '.cache/ms-playwright'
    (cache / 'ffmpeg-1011').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    calls = []
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                     root=tmp_path / 'managed', run=lambda *a, **k: calls.append(k['env']))
    assert all(env.get('PLAYWRIGHT_BROWSERS_PATH') == str(cache) for env in calls)
    assert cache.is_dir()


@pytest.mark.parametrize(('suite', 'selected'), [
    ('python', 'tests/test_selected.py'),
    ('js', 'tests/browser/unit.test.mjs'),
    ('browser', 'tests/browser/ui.spec.mjs'),
])
def test_explicit_test_paths_replace_default_collection(tmp_path, suite, selected, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    (source / 'tests/test_selected.py').write_text('')
    calls = []
    flags = ('-k', 'tests/value.py') if suite == 'python' else ('--test-name-pattern=fixture',)
    module.run_suite(source, python=sys.executable, node=NODE, suite=suite,
                     root=tmp_path / 'managed', extra_args=(selected, *flags),
                     run=lambda command, **kw: calls.append(command))
    assert len(calls) == 1
    command = calls[0]
    assert command.count(selected) == 1
    assert 'tests' not in command
    assert all(flag in command for flag in flags)
    assert [arg for arg in command if arg.endswith('.mjs')] == ([] if suite == 'python' else [selected])


@pytest.mark.parametrize(('suite', 'selected'), [
    ('python', '/tmp/outside.py'), ('python', '../tests/test_selected.py'),
    ('python', 'tests/../tests/test_selected.py'), ('python', './tests/test_selected.py'),
    ('python', 'tests//test_selected.py'), ('python', 'tests/test_selected.py::test_one'),
    ('python', 'tests/missing.py'), ('python', 'tests/test_link.py'),
    ('python', 'tests/linked/test_selected.py'), ('python', 'tests/browser/unit.test.mjs'),
    ('python', 'tests'), ('python', 'tests/--evil.py'), ('python', 'tests/test_selected.py\n'),
    ('js', 'tests/browser/ui.spec.mjs'), ('browser', 'tests/browser/unit.test.mjs'),
    ('all', 'tests/test_selected.py'), ('js', '--import=tests/browser/unit.test.mjs'),
])
def test_explicit_test_paths_reject_unsafe_or_wrong_suite(tmp_path, suite, selected):
    module = workspace_module()
    source = fixture_source(tmp_path)
    (source / 'tests/test_selected.py').write_text('')
    (source / 'tests/test_link.py').symlink_to(source / 'tests/test_selected.py')
    (source / 'tests/linked').symlink_to(source / 'tests', target_is_directory=True)
    root = tmp_path / 'managed'
    with pytest.raises(ValueError):
        module.run_suite(source, python=sys.executable, node=NODE, suite=suite,
                         root=root, extra_args=(selected,), run=lambda *a, **k: pytest.fail('executed'))
    assert not root.exists(), 'invalid selection must fail before allocating scratch'


def fixture_source(tmp_path):
    source = tmp_path / 'source'
    (source / 'tests/browser').mkdir(parents=True)
    (source / 'frontend').mkdir()
    (source / 'tests/browser/unit.test.mjs').write_text('')
    (source / 'tests/browser/ui.spec.mjs').write_text('')
    return source


def test_suite_isolates_child_files_and_removes_success(tmp_path, monkeypatch):
    module = workspace_module()
    source = fixture_source(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_text('keep')
    monkeypatch.setenv('HERMES_MOBILE_CONFIG', str(outside))
    monkeypatch.setenv('PYTHONPATH', str(outside))
    monkeypatch.setenv('HERMES_HOME', str(outside))
    calls = []

    def run(command, *, cwd, env, check, **kwargs):
        assert cwd == source
        assert check is True
        assert 'PYTHONPATH' not in env and 'HERMES_MOBILE_CONFIG' not in env
        assert env['PYTHONDONTWRITEBYTECODE'] == '1'
        assert env['HERMES_TEST_PYTHON'] == sys.executable
        assert env['PATH'].split(os.pathsep)[0] == str(Path(NODE).parent)
        assert env['HERMES_FRONTEND_DIR'] == str(source / 'frontend')
        for key in ('HOME', 'HERMES_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
                    'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR', 'HERMES_TEST_ARTIFACT_DIR'):
            path = Path(env[key])
            assert path.is_relative_to(tmp_path / 'managed')
            assert path.is_dir()
        subprocess.run([sys.executable, '-c',
                        "import os,pathlib,tempfile; "
                        "pathlib.Path(os.environ['HOME'],'native.sqlite').write_text('data'); "
                        "pathlib.Path(tempfile.mkdtemp(),'cache').write_text('cache'); "
                        "pathlib.Path(os.environ['HERMES_TEST_ARTIFACT_DIR'],'shot.png').write_bytes(b'png')"],
                       cwd=cwd, env=env, check=True)
        calls.append((command, dict(env)))

    module.run_suite(source, python=sys.executable, node=NODE, run=run, root=tmp_path / 'managed')
    assert len(calls) == 2
    python, env = calls[0]
    assert python[0] == sys.executable
    assert '-p' in python and 'no:cacheprovider' in python
    assert '--basetemp' in python
    assert "sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages')" in python[2]
    assert calls[1][0] == [NODE, '--test', '--test-concurrency=2', 'tests/browser/ui.spec.mjs', 'tests/browser/unit.test.mjs']
    assert not Path(env['HOME']).exists()
    assert list((tmp_path / 'managed').glob('r-*')) == []
    assert outside.read_text() == 'keep'


@pytest.mark.parametrize('unsafe', ['unmarked', 'symlink', 'public', 'marker-symlink', 'invalid'])
def test_refuses_unsafe_roots_without_deleting_anything(tmp_path, unsafe):
    module = workspace_module()
    source = fixture_source(tmp_path)
    root = tmp_path / 'managed'
    if unsafe == 'symlink':
        root.symlink_to(source, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        if unsafe == 'public':
            root.chmod(0o755)
        if unsafe == 'marker-symlink':
            (root / '.managed.json').symlink_to(source / 'tests/browser/unit.test.mjs')
        if unsafe == 'invalid':
            (root / '.managed.json').write_text('{}')
    with pytest.raises((ValueError, PermissionError)):
        module.run_suite(source, python=sys.executable, node=NODE,
                         run=lambda *a, **k: None, root=root)
    assert (source / 'tests/browser/unit.test.mjs').exists()
    assert list(root.glob('r-*')) == []


def test_new_root_is_private_and_explicitly_marked(tmp_path, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    root = tmp_path / 'managed'
    module.run_suite(source, python=sys.executable, node=NODE, run=lambda *a, **k: None, root=root)
    assert root.stat().st_mode & 0o777 == 0o700
    marker = json.loads((root / '.managed.json').read_text())
    assert marker == {'kind': 'hermes-mobile-tests-v1', 'uid': os.getuid()}


def test_failure_keeps_only_diagnostics_and_original_error(tmp_path, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    failure = subprocess.CalledProcessError(23, ['synthetic'])
    root = tmp_path / 'managed'

    def fail(command, *, cwd, env, check, **kwargs):
        assert kwargs['umask'] == 0o077
        Path(env['HOME'], 'private.sqlite').write_text('secret database')
        Path(env['TMPDIR'], 'junk').write_bytes(b'x' * 8192)
        Path(env['HERMES_TEST_ARTIFACT_DIR'], 'failure.png').write_bytes(b'screenshot')
        Path(env['HERMES_TEST_ARTIFACT_DIR'], 'private.sqlite').write_text('not evidence')
        raise failure

    with pytest.raises(subprocess.CalledProcessError) as caught:
        module.run_suite(source, python=sys.executable, node=NODE, run=fail, root=root)
    assert caught.value is failure
    records = list(root.glob('r-*'))
    assert len(records) == 1
    record = records[0]
    assert (record / 'artifacts/failure.png').read_bytes() == b'screenshot'
    assert (record / 'run.log').is_file()
    assert not list(record.rglob('*.sqlite'))
    assert not (record / 'home').exists()
    assert not (record / 't').exists()
    data = json.loads((record / '.run.json').read_text())
    assert data['state'] == 'failed'


def marked_run(root, name, *, state='running', created=None, size=32):
    directory = root / name
    directory.mkdir(mode=0o700)
    for filename, content in {
        '.run.json': json.dumps({'kind': 'hermes-mobile-tests-v1', 'uid': os.getuid(),
                               'state': state, 'created': created or time.time(),
                               'finished': created or time.time()}),
        '.lock': '', 'run.log': 'x' * size,
    }.items():
        path = directory / filename
        path.write_text(content)
        path.chmod(0o600)
    return directory


def test_cleanup_requires_inactive_marked_stale_runs_and_defaults_to_dry_run(tmp_path, process_free_workspace):
    module = process_free_workspace
    assert hasattr(module, 'cleanup'), 'safe cleanup API missing'
    root = tmp_path / 'managed'
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                     root=root, run=lambda *a, **k: None)
    stale = marked_run(root, 'r-12345678', created=time.time() - 90000)
    recent = marked_run(root, 'r-abcdefgh')
    unknown = root / 'unrelated'; unknown.mkdir()
    (unknown / 'keep').write_text('outside')
    link = root / 'r-linklink'; link.symlink_to(unknown, target_is_directory=True)
    invalid = marked_run(root, 'r-invalid1', created=time.time() - 90000)
    (invalid / '.run.json').write_text('{}')
    linked_marker = marked_run(root, 'r-marker12', created=time.time() - 90000)
    (linked_marker / '.run.json').unlink()
    (linked_marker / '.run.json').symlink_to(stale / '.run.json')
    candidates = module.cleanup(root)
    assert [item['path'] for item in candidates if item['action'] == 'remove'] == [str(stale)]
    assert next(item['bytes'] for item in candidates if item['action'] == 'remove') >= 32
    assert stale.exists()
    module.cleanup(root, apply=True)
    assert not stale.exists()
    assert all(p.exists() for p in (recent, unknown, link, invalid, linked_marker))


@pytest.mark.parametrize('state,field,value', [
    ('running', 'finished', 'invalid'),
    *[('failed', field, value) for field, value in [
    (None, []), (None, None), (None, 'marker'), (None, 1), (None, True),
    *[(field, value) for field in ('created', 'finished')
      for value in ('invalid', None, [], {}, True, False,
                    float('nan'), float('inf'), -float('inf'), 10 ** 400)],
    *[('token', value) for value in (None, [], {}, True, 1, '', 'x' * 64)],
    *[('pgid', value) for value in ('invalid', [], {}, True, False, 0, 1, -1,
                                   2.5, float('nan'), float('inf'), 2 ** 31)],
    ]],
])
def test_cleanup_skips_malformed_markers_without_blocking_stale_runs(tmp_path, state, field, value, process_free_workspace):
    module = process_free_workspace
    root = module._root(tmp_path / 'managed')
    malformed = marked_run(root, 'r-invalid1', state=state, created=1)
    marker = malformed / '.run.json'
    data = json.loads(marker.read_text())
    if field is None:
        data = value
    else:
        data[field] = value
    marker.write_text(json.dumps(data))
    original = {path.name: path.read_bytes() for path in malformed.iterdir()}
    stale = [marked_run(root, 'r-stale001', created=1),
             marked_run(root, 'r-stale002', state='failed', created=2.5),
             marked_run(root, 'r-stale003', state='failed', created=3)]
    fallback = stale[-1] / '.run.json'
    data = json.loads(fallback.read_text())
    del data['finished']  # Older failed records fall back to created.
    data['pgid'] = None  # Completed runs also publish a null process group.
    fallback.write_text(json.dumps(data))

    for apply in (False, True):
        report = module.cleanup(root, apply=apply, now=1000000)
        assert next(item for item in report if item['path'] == str(malformed)) == {
            'path': str(malformed), 'action': 'skip', 'bytes': 0,
            'reason': 'unrecognized or unsafe',
        }
        assert {item['path'] for item in report if item['action'] == 'remove'} == {
            str(path) for path in stale
        }
        assert {path.name: path.read_bytes() for path in malformed.iterdir()} == original
        assert all(path.exists() is not apply for path in stale)
    with pytest.raises(ValueError):
        module._run_marker(malformed)


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('fault', ['symlink', 'open-error'])
def test_cleanup_skips_unsafe_locks_without_blocking_stale_runs(tmp_path, monkeypatch, apply, fault, process_free_workspace):
    module = process_free_workspace
    root = module._root(tmp_path / 'managed')
    invalid = marked_run(root, 'r-invalid1', created=1)
    stale = marked_run(root, 'r-stale001', created=1)
    lock = invalid / '.lock'
    original = {path.name: path.read_bytes() for path in invalid.iterdir()}
    if fault == 'symlink':
        lock.unlink()
        lock.symlink_to(stale / '.lock')
    else:
        real_open = module.os.open

        def denied(path, flags, *args, **kwargs):
            if path == lock:
                raise PermissionError('inaccessible run lock')
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(module.os, 'open', denied)

    report = module.cleanup(root, apply=apply, now=1000000)
    assert next(item for item in report if item['path'] == str(invalid)) == {
        'path': str(invalid), 'action': 'skip', 'bytes': 0,
        'reason': 'unrecognized or unsafe',
    }
    assert [item['path'] for item in report if item['action'] == 'remove'] == [str(stale)]
    assert stale.exists() is not apply
    assert {path.name for path in invalid.iterdir()} == set(original)
    for name, content in original.items():
        if name == '.lock' and fault == 'symlink':
            assert lock.is_symlink() and lock.readlink() == stale / '.lock'
        else:
            assert (invalid / name).read_bytes() == content


@pytest.mark.parametrize('fault', ['root-lock', 'deletion'])
def test_cleanup_propagates_root_and_deletion_errors(tmp_path, monkeypatch, fault, process_free_workspace):
    module = process_free_workspace
    root = module._root(tmp_path / 'managed')
    stale = marked_run(root, 'r-stale001', created=1)
    failure = PermissionError('root lock or deletion denied')
    if fault == 'root-lock':
        real_open = module.os.open

        def denied(path, flags, *args, **kwargs):
            if path == root / '.lock':
                raise failure
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(module.os, 'open', denied)
    else:
        def denied(path):
            assert path == stale
            raise failure

        monkeypatch.setattr(module.shutil, 'rmtree', denied)
    with pytest.raises(PermissionError) as caught:
        module.cleanup(root, apply=True, now=1000000)
    assert caught.value is failure
    assert stale.exists()


def test_cleanup_cannot_remove_active_run_even_when_marker_is_stale(tmp_path):
    module = workspace_module()
    assert hasattr(module, 'cleanup'), 'safe cleanup API missing'
    root = tmp_path / 'managed'

    def active(command, *, env, **kwargs):
        workspace = Path(env['HOME']).parent
        marker = workspace / '.run.json'
        data = json.loads(marker.read_text()); data['created'] = time.time() - 90000
        marker.write_text(json.dumps(data))
        result = module.cleanup(root, apply=True)
        assert not [entry for entry in result if entry['action'] == 'remove']
        assert workspace.exists()
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE, root=root, run=active)


@pytest.mark.parametrize('limit', ['age', 'count', 'bytes'])
def test_failure_retention_is_bounded_and_reports_trimming(tmp_path, limit, process_free_workspace):
    module = process_free_workspace
    root = tmp_path / 'managed'
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE, root=root,
                     run=lambda *a, **k: None)
    now = time.time()
    records = [marked_run(root, f'r-0000000{i}', state='failed', created=now - i, size=2048)
               for i in range(4)]
    kwargs = {'now': now + 8 * 86400} if limit == 'age' else {'max_bytes': 1024} if limit == 'bytes' else {}
    report = module.cleanup(root, **kwargs)
    trimmed = [r for r in report if r['action'] == 'remove']
    assert len(trimmed) == (1 if limit == 'count' else 4)
    assert all(p.exists() for p in records), 'dry-run must not change records'
    assert all(r['bytes'] >= 2048 and 'failure' in r['reason'] for r in trimmed)
    module.cleanup(root, apply=True, **kwargs)
    assert len(list(root.glob('r-*'))) == (3 if limit == 'count' else 0)


def test_start_and_end_cleanup_bound_repeated_failures(tmp_path, capsys, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    root = tmp_path / 'managed'
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(17, ['synthetic'])
    for _ in range(5):
        with pytest.raises(subprocess.CalledProcessError):
            module.run_suite(source, python=sys.executable, node=NODE, root=root, run=fail)
    assert len(list(root.glob('r-*'))) == 3
    assert 'failure retention' in capsys.readouterr().err


@pytest.mark.parametrize('suite,expected', [('python', 1), ('js', 1), ('browser', 1), ('all', 2)])
def test_selection_assets_and_safe_filters(tmp_path, monkeypatch, suite, expected, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    assets = tmp_path / 'public'; assets.mkdir()
    monkeypatch.setenv('PYTEST_ADDOPTS', '--basetemp=/outside')
    monkeypatch.setenv('PYTHONPYCACHEPREFIX', '/outside')
    monkeypatch.setenv('PYTEST_DEBUG_TEMPROOT', '/outside')
    calls = []
    extra = ('-k', 'one or two') if suite == 'python' else ('--test-name-pattern=one|two',) if suite != 'all' else ()
    module.run_suite(source, python=sys.executable, node=NODE, suite=suite, assets=assets,
                     root=tmp_path / 'managed', extra_args=extra,
                     run=lambda command, **kwargs: calls.append((command, kwargs)))
    assert len(calls) == expected
    for command, kwargs in calls:
        assert kwargs['env']['HERMES_FRONTEND_DIR'] == str(assets)
        assert 'PYTEST_ADDOPTS' not in kwargs['env']
        assert 'PYTHONPYCACHEPREFIX' not in kwargs['env']
        assert 'PYTEST_DEBUG_TEMPROOT' not in kwargs['env']
        assert all(arg in command for arg in extra)
    if suite == 'js':
        assert 'tests/browser/unit.test.mjs' in calls[0][0]
        assert 'tests/browser/ui.spec.mjs' not in calls[0][0]
    if suite == 'browser':
        assert 'tests/browser/ui.spec.mjs' in calls[0][0]
        assert 'tests/browser/unit.test.mjs' not in calls[0][0]


@pytest.mark.parametrize('args', [('--basetemp=/outside',), ('-p', 'cacheprovider'),
                                  ('-o', 'cache_dir=/outside'), ('/outside/test.py',)])
def test_rejects_arguments_that_escape_managed_paths(tmp_path, args):
    module = workspace_module()
    with pytest.raises(ValueError):
        module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE, suite='python',
                         root=tmp_path / 'managed', extra_args=args, run=lambda *a, **k: None)


@pytest.mark.parametrize('fails', [False, True])
def test_real_child_output_and_scratch_lifecycle(tmp_path, fails, capfd):
    module = workspace_module()
    source = fixture_source(tmp_path)
    (source / 'tests/test_synthetic.py').write_text(
        "import os, pathlib, tempfile\n"
        "def test_synthetic():\n"
        "    pathlib.Path(os.environ['HOME'], 'secret.sqlite').write_text('secret')\n"
        "    pathlib.Path(tempfile.mkdtemp(), 'large').write_bytes(b'x' * 10000)\n"
        "    pathlib.Path(os.environ['HERMES_TEST_ARTIFACT_DIR'], 'test.png').write_bytes(b'PNG')\n"
        "    print('diagnostic evidence from real child')\n"
        f"    assert {not fails!r}\n")
    root = tmp_path / 'managed'
    if fails:
        with pytest.raises(subprocess.CalledProcessError) as error:
            module.run_suite(source, python=sys.executable, node=NODE, suite='python', root=root)
        assert error.value.returncode == 1
        record, = root.glob('r-*')
        assert 'diagnostic evidence from real child' in (record / 'run.log').read_text()
        assert (record / 'artifacts/test.png').exists()
        assert not list(record.rglob('*.sqlite'))
    else:
        module.run_suite(source, python=sys.executable, node=NODE, suite='python', root=root)
        assert not list(root.glob('r-*'))
    assert not list(source.rglob('__pycache__'))
    assert not list(source.rglob('.pytest_cache'))


@pytest.mark.parametrize('close_output', [False, True])
def test_sigterm_reaps_process_group_before_removing_scratch(tmp_path, close_output):
    module = workspace_module()
    source = fixture_source(tmp_path)
    ready = tmp_path / 'ready.json'
    observed = tmp_path / 'terminated.json'
    grandchild = (
        'import os,signal,time,json; from pathlib import Path; '
        + ('os.close(1); os.close(2); ' if close_output else '') +
        f"signal.signal(signal.SIGTERM, lambda *a: (Path({str(observed)!r}).write_text(json.dumps(Path(os.environ['HOME']).exists())), exit(0))); "
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(),os.getppid(),os.environ['HOME']])); "
        'time.sleep(60)')
    (source / 'tests/test_signal.py').write_text(
        'import subprocess,sys,time,os\ndef test_wait():\n'
        f'    subprocess.Popen([sys.executable, "-c", {grandchild!r}])\n'
        + ('    os.close(1); os.close(2)\n' if close_output else '') +
        '    time.sleep(60)\n')
    root = tmp_path / 'managed'
    python = sys.executable
    if close_output:
        executable = source / 'closed-output-python'
        executable.write_text(f'#!{sys.executable}\n' + grandchild.replace('os.getppid()', 'os.getpid()'))
        executable.chmod(0o700)
        python = str(executable)
    runner = subprocess.Popen([sys.executable, '-c',
        f'import sys; sys.path.insert(0,{str(SOURCE)!r}); from deploy.test_workspace import run_suite; '
        f'run_suite({str(source)!r},python={python!r},node={NODE!r},suite="python",root={str(root)!r},extra_args=("-s",))'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    child_pids = []
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and runner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), 'synthetic child did not become ready'
        grandpid, childpid, home = json.loads(ready.read_text())
        child_pids = [grandpid, childpid]
        runner.send_signal(signal.SIGTERM)
        _, stderr = runner.communicate(timeout=10)
        assert runner.returncode == 128 + signal.SIGTERM, stderr.decode()
        assert observed.exists() and json.loads(observed.read_text()) is True
        assert not Path(home).exists()
        record, = root.glob('r-*')
        assert json.loads((record / '.run.json').read_text())['state'] == 'failed'
    finally:
        for pid in child_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if runner.poll() is None:
            runner.kill()
        runner.communicate(timeout=5)


@pytest.mark.parametrize('ending', ['success', 'failed', 'sigterm'])
def test_runner_stops_and_reaps_detached_child_before_cleanup(tmp_path, ending):
    source = fixture_source(tmp_path)
    ready, observed = tmp_path / 'detached.json', tmp_path / 'terminated.json'
    child_code = (
        'import os,signal,time,json; from pathlib import Path; '
        f"signal.signal(signal.SIGTERM, lambda *a: (Path({str(observed)!r}).write_text(json.dumps(Path(os.environ['HOME']).exists())), exit(0))); "
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(), os.environ['HOME']])); "
        'time.sleep(60)')
    executable = source / 'detaching-python'
    executable.write_text(
        f'#!{sys.executable}\nimport subprocess,sys,time\nfrom pathlib import Path\n'
        f'subprocess.Popen([sys.executable, "-c", {child_code!r}], start_new_session=True, '
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        f'while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n'
        + ('time.sleep(60)\n' if ending == 'sigterm' else f'sys.exit({23 if ending == "failed" else 0})\n'))
    executable.chmod(0o700)
    root = tmp_path / 'managed'
    runner = subprocess.Popen([sys.executable, '-c',
        f'import sys; sys.path.insert(0,{str(SOURCE)!r}); from scripts.test import main; '
        f'raise SystemExit(main(["--source",{str(source)!r},"--root",{str(root)!r},"python"]))'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HERMES_TEST_PYTHON=str(executable)))
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 start_new_session=True)
    identity = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and runner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), runner.communicate(timeout=5)[1].decode() if runner.poll() is not None else 'not ready'
        pid, home = json.loads(ready.read_text())
        identity = (Path(f'/proc/{pid}/stat'), None)
        identity = (identity[0], identity[0].read_text().rsplit(')', 1)[1].split()[19])
        if ending == 'sigterm':
            runner.send_signal(signal.SIGTERM)
        _, stderr = runner.communicate(timeout=12)
        assert runner.returncode == {'success': 0, 'failed': 23, 'sigterm': 143}[ending], stderr.decode()
        assert observed.exists() and json.loads(observed.read_text()) is True
        assert not identity[0].exists(), 'owned detached child must be reaped, not left as a zombie'
        assert not Path(home).exists()
        assert unrelated.poll() is None
    finally:
        if identity and identity[0].exists():
            if identity[0].read_text().rsplit(')', 1)[1].split()[19] == identity[1]:
                os.kill(int(identity[0].parent.name), signal.SIGKILL)
        if runner.poll() is None:
            runner.kill()
        runner.communicate(timeout=5)
        unrelated.kill()
        unrelated.wait()


def test_playwright_chromium_detached_interruption_smoke(tmp_path):
    import tempfile
    chrome = '/usr/bin/google-chrome'
    playwright = Path('/usr/local/lib/hermes-agent/node_modules/playwright')
    if not (Path(NODE).is_file() and Path(chrome).is_file() and playwright.is_dir()):
        pytest.skip('installed Node, Playwright and Chromium required')
    source = fixture_source(tmp_path)
    ready = tmp_path / 'browser.json'
    (source / 'tests/browser/ui.spec.mjs').write_text(
        "import {createRequire} from 'node:module'; import fs from 'node:fs';\n"
        "const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');\n"
        f"const server=await chromium.launchServer({{executablePath:{chrome!r},headless:true,"
        "handleSIGTERM:false,handleSIGINT:false,handleSIGHUP:false,"
        "args:['--no-sandbox','--disable-dev-shm-usage']});\n"
        f"fs.writeFileSync({str(ready)!r},JSON.stringify([server.process().pid,process.env.HOME]));\n"
        "await new Promise(resolve=>setTimeout(resolve,60000));\n")
    # Do not inherit the outer managed TMPDIR: another workspace level makes
    # Chromium 145's SingletonSocket exceed Linux's 107-byte pathname limit.
    # TemporaryDirectory still creates a private 0700 directory and cleans it up.
    with tempfile.TemporaryDirectory(prefix='hmt-smoke-', dir='/tmp') as short:
        assert Path(short).parent == Path('/tmp'), 'browser scratch must not inherit the outer managed TMPDIR'
        assert Path(short).stat().st_mode & 0o777 == 0o700
        root = Path(short) / 'managed'
        runner = subprocess.Popen([sys.executable, '-c',
            f'import sys; sys.path.insert(0,{str(SOURCE)!r}); from deploy.test_workspace import run_suite; '
            f'run_suite({str(source)!r},python={sys.executable!r},node={NODE!r},suite="browser",root={str(root)!r})'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        owned = {}
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and runner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), runner.communicate(timeout=5)[0].decode() if runner.poll() is not None else 'Chromium not ready'
            browserpid, home = json.loads(ready.read_text())
            workspace = Path(home).parent
            marker = json.loads((workspace / '.run.json').read_text())
            assert os.getpgid(browserpid) == browserpid != marker['pgid'], 'Playwright must really detach'
            users, _ = workspace_module()._workspace_processes(workspace, marker.get('token'))
            owned = {pid: started for pid, (started, ours) in users.items() if ours}
            assert browserpid in owned
            runner.send_signal(signal.SIGTERM)
            output, _ = runner.communicate(timeout=15)
            assert runner.returncode == 143, output.decode()
            assert not Path(home).exists(), output.decode()
            remaining = {}
            for pid, started in owned.items():
                path = Path(f'/proc/{pid}/stat')
                try:
                    fields = path.read_text().rsplit(')', 1)[1].split()
                except FileNotFoundError:
                    continue
                if fields[19] == started and fields[0] != 'Z':
                    remaining[pid] = {'state': fields[0], 'ppid': fields[1]}
            # A nested subreaper may temporarily hold exited browser helpers as
            # zombies. They have no address space or open files; their /proc
            # entry is not evidence of a live scratch user. The runner itself
            # was reaped by communicate(), and the explicit owned-child test
            # separately requires exact-PID reaping. Never accept a live user.
            assert not remaining, f'live browser children after cleanup: {remaining}'
        finally:
            for pid, started in owned.items():
                path = Path(f'/proc/{pid}/stat')
                if path.exists() and path.read_text().rsplit(')', 1)[1].split()[19] == started:
                    os.kill(pid, signal.SIGKILL)
            if runner.poll() is None:
                runner.kill()
            runner.communicate(timeout=5)


def test_cleanup_skips_permission_denied_group_without_blocking_stale_runs(tmp_path, monkeypatch, process_free_workspace):
    module = process_free_workspace
    root = module._root(tmp_path / 'managed')
    blocked = marked_run(root, 'r-denied12', created=1)
    data = module._run_marker(blocked)
    data['pgid'] = 424242
    module._write_json(blocked / '.run.json', data)
    stale = marked_run(root, 'r-stale123', created=1)

    def denied(pgid, sig):
        assert (pgid, sig) == (424242, 0), 'cleanup must only probe, never signal'
        raise PermissionError('stale PGID now belongs to another UID')

    monkeypatch.setattr(module.os, 'killpg', denied)
    for apply in (False, True):
        report = module.cleanup(root, apply=apply, now=1000000)
        assert next(item['action'] for item in report if item['path'] == str(blocked)) == 'skip'
        assert next(item['action'] for item in report if item['path'] == str(stale)) == 'remove'
        assert blocked.exists()
        assert stale.exists() is not apply


def test_cleanup_refuses_live_orphan_process_group(tmp_path):
    module = workspace_module()
    root = tmp_path / 'managed'
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                     root=root, run=lambda *a, **k: None)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    try:
        stale = marked_run(root, 'r-orphan12', created=time.time() - 90000)
        marker = stale / '.run.json'
        data = json.loads(marker.read_text()); data['pgid'] = child.pid
        marker.write_text(json.dumps(data))
        report = module.cleanup(root, apply=True)
        assert not [item for item in report if item['action'] == 'remove']
        assert stale.exists()
    finally:
        child.kill(); child.wait()
    module.cleanup(root, apply=True)
    assert not stale.exists()


@pytest.mark.parametrize('evidence', ['HOME', 'TMPDIR', 'cwd', 'token', 'profile'])
def test_cleanup_skips_detached_workspace_user_with_stale_marker(tmp_path, evidence):
    module = workspace_module()
    root = module._root(tmp_path / 'managed')
    stale = marked_run(root, 'r-detached', state='failed', created=1)
    bound = stale / 'scratch'
    bound.mkdir()
    env = dict(os.environ)
    if evidence in ('HOME', 'TMPDIR'):
        env[evidence] = str(bound)
    elif evidence == 'token':
        data = module._run_marker(stale)
        data['token'] = env['HERMES_TEST_RUN_ID'] = 'a' * 64
        module._write_json(stale / '.run.json', data)
    command = [sys.executable, '-c', 'import time; time.sleep(60)']
    if evidence == 'profile':
        command.append(f'--user-data-dir={bound}')
    child = subprocess.Popen(command,
                             env=env, cwd=bound if evidence == 'cwd' else tmp_path,
                             start_new_session=True)
    try:
        for apply in (False, True):
            report = module.cleanup(root, apply=apply, now=1000000)
            assert next(item['action'] for item in report if item['path'] == str(stale)) == 'skip'
            assert bound.exists()
            assert child.poll() is None, 'manual cleanup must never kill a workspace user'
    finally:
        child.kill()
        child.wait()
    module.cleanup(root, apply=True, now=1000000)
    assert not stale.exists()


@pytest.mark.parametrize('fails,opaque', [(False, False), (True, False), (True, True)])
def test_runner_preserves_workspace_for_unowned_or_uninspectable_user(tmp_path, fails, opaque):
    module = workspace_module()
    child = None
    home = None
    failure = subprocess.CalledProcessError(27, ['synthetic'])
    ready = tmp_path / 'ready'

    def run(command, *, env, **kwargs):
        nonlocal child, home
        home = Path(env['HOME'])
        child = subprocess.Popen([sys.executable, '-c',
            'import ctypes,time; from pathlib import Path; '
            + ('ctypes.CDLL(None).prctl(4,0,0,0,0); ' if opaque else '')
            + f'Path({str(ready)!r}).touch(); time.sleep(60)'],
            env=env, start_new_session=True)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        if fails:
            raise failure

    try:
        with pytest.raises(subprocess.CalledProcessError if fails else RuntimeError) as caught:
            module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                             suite='python', root=tmp_path / 'managed', run=run)
        if fails:
            assert caught.value is failure
        assert home.exists(), 'uncertain inactivity must preserve the entire scratch workspace'
        assert child.poll() is None, 'path evidence alone must not authorize a signal'
    finally:
        if child is not None:
            child.kill()
            child.wait()


def test_cleanup_rechecks_detached_users_immediately_before_deletion(tmp_path, monkeypatch):
    module = workspace_module()
    root = module._root(tmp_path / 'managed')
    stale = marked_run(root, 'r-race1234', created=1)
    original = module._size
    child = None

    def size(path):
        nonlocal child
        result = original(path)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 cwd=path, start_new_session=True)
        return result

    monkeypatch.setattr(module, '_size', size)
    try:
        report = module.cleanup(root, apply=True, now=1000000)
        assert stale.exists()
        assert report[0]['action'] == 'skip'
        assert child.poll() is None
    finally:
        if child is not None:
            child.kill()
            child.wait()


def test_detached_signalling_rechecks_identity_and_ownership(tmp_path):
    module = workspace_module()
    token = 'a' * 64
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             env=dict(os.environ, HERMES_TEST_RUN_ID=token), start_new_session=True)
    try:
        started = Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        module._signal_owned(child.pid, 'not-the-same-process', token, signal.SIGTERM)
        module._signal_owned(child.pid, started, 'b' * 64, signal.SIGTERM)
        assert child.poll() is None
        module._signal_owned(child.pid, started, token, signal.SIGTERM)
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_cleanup_dry_run_does_not_create_absent_root(tmp_path):
    module = workspace_module()
    root = tmp_path / 'not-created'
    assert module.cleanup(root) == []
    assert not root.exists()


def test_failure_cleanup_rejects_hardlinked_log_and_preserves_exit(tmp_path, capsys, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    outside = tmp_path / 'outside.log'; outside.write_text('untouched')
    outside.chmod(0o600)
    failure = subprocess.CalledProcessError(31, ['synthetic'])
    def fail(command, *, env, **kwargs):
        os.link(outside, Path(env['HOME']).parent / 'run.log')
        raise failure
    with pytest.raises(subprocess.CalledProcessError) as caught:
        module.run_suite(source, python=sys.executable, node=NODE, root=tmp_path / 'managed', run=fail)
    assert caught.value is failure
    assert outside.read_text() == 'untouched'
    assert 'cleanup failed' in capsys.readouterr().err.lower()


def test_cleanup_error_is_surfaced_without_masking_test_failure(tmp_path, monkeypatch, capsys, process_free_workspace):
    module = process_free_workspace
    source = fixture_source(tmp_path)
    original = module.cleanup
    calls = []
    def cleanup(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise OSError('synthetic cleanup failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'cleanup', cleanup)
    failure = subprocess.CalledProcessError(37, ['synthetic'])
    def fail(*args, **kwargs):
        raise failure
    with pytest.raises(subprocess.CalledProcessError) as caught:
        module.run_suite(source, python=sys.executable, node=NODE, root=tmp_path / 'managed', run=fail)
    assert caught.value is failure
    assert 'synthetic cleanup failure' in capsys.readouterr().err


def test_empty_path_does_not_add_current_directory(tmp_path, monkeypatch, process_free_workspace):
    module = process_free_workspace
    monkeypatch.setenv('PATH', '')
    seen = []
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE, root=tmp_path / 'managed',
                     run=lambda command, **kwargs: seen.append(kwargs['env']['PATH']))
    assert seen == [str(Path(NODE).parent)] * 2


def load_script(name):
    path = SOURCE / 'scripts' / name
    assert path.exists(), f'{name} CLI missing'
    spec = importlib.util.spec_from_file_location(name.removesuffix('.py'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_cli_explicit_python_without_source_venv(tmp_path, monkeypatch):
    cli = load_script('test.py')
    source = fixture_source(tmp_path)
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    # Isolate this Python-selection contract from the host's Node PATH layout.
    node = tmp_path / 'node'
    node.write_text('#!/bin/sh\nexit 99\n')
    node.chmod(0o700)
    monkeypatch.setenv('HERMES_TEST_NODE', str(node))
    calls = []
    assert cli.main(['--source', str(source), '--root', str(tmp_path / 'managed'),
                     'python', '--', '-k', 'synthetic'],
                    run=lambda *a, **k: calls.append((a, k))) == 0
    args, kwargs = calls[0]
    assert args == (source,)
    assert kwargs['python'] == sys.executable
    assert kwargs['node'] == str(node)
    assert kwargs['suite'] == 'python'
    assert kwargs['extra_args'] == ['-k', 'synthetic']


def test_runner_cli_preserves_venv_symlink_and_nonzero_exit(tmp_path, monkeypatch):
    cli = load_script('test.py')
    source = fixture_source(tmp_path)
    (source / '.venv').symlink_to(sys.prefix, target_is_directory=True)
    monkeypatch.delenv('HERMES_TEST_PYTHON', raising=False)
    def fail(*args, **kwargs):
        assert kwargs['python'] == str(source / '.venv/bin/python')
        raise subprocess.CalledProcessError(29, ['synthetic'])
    assert cli.main(['--source', str(source), 'python'], run=fail) == 29


def test_cleanup_cli_is_dry_run_until_apply(tmp_path, capsys, process_free_workspace):
    module = process_free_workspace
    cli = load_script('clean_tests.py')
    root = tmp_path / 'managed'
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                     root=root, run=lambda *a, **k: None)
    stale = marked_run(root, 'r-cli12345', created=time.time() - 90000)
    assert cli.main(['--root', str(root)]) == 0
    assert stale.exists()
    assert str(stale) in capsys.readouterr().out
    assert cli.main(['--root', str(root), '--apply']) == 0
    assert not stale.exists()


def test_concurrent_first_runs_never_observe_unmarked_root(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    module = workspace_module()
    root = tmp_path / 'managed'
    entered, release = threading.Event(), threading.Event()
    original = os.open
    def slow_marker(path, flags, *args, **kwargs):
        if str(path).endswith('/.managed.json') and flags & os.O_CREAT and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', slow_marker)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(module._root, root)
        try:
            assert entered.wait(5)
            assert module._root(root) == root
        finally:
            release.set()
            assert first.result(timeout=5) == root


def test_real_child_cannot_replace_log_with_external_hardlink(tmp_path, capfd):
    module = workspace_module()
    source = fixture_source(tmp_path)
    outside = tmp_path / 'external.log'
    outside.write_text('untouched'); outside.chmod(0o600)
    (source / 'tests/test_hardlink.py').write_text(
        'import os; from pathlib import Path\n'
        'def test_fail():\n'
        f"    os.link({str(outside)!r}, Path(os.environ['HOME']).parent / 'run.log')\n"
        '    assert False\n')
    with pytest.raises(subprocess.CalledProcessError) as error:
        module.run_suite(source, python=sys.executable, node=NODE, suite='python', root=tmp_path / 'managed')
    assert error.value.returncode == 1
    assert outside.read_text() == 'untouched'


def test_streaming_logs_are_bounded_with_visible_truncation_report(tmp_path, capfd):
    module = workspace_module()
    source = fixture_source(tmp_path)
    executable = source / 'verbose-python'
    executable.write_text(f'#!{sys.executable}\nimport sys\nsys.stdout.write("x" * (1024 * 1024 + 4096))\nsys.exit(19)\n')
    executable.chmod(0o700)
    root = tmp_path / 'managed'
    with pytest.raises(subprocess.CalledProcessError) as error:
        module.run_suite(source, python=str(executable), node=NODE, suite='python', root=root)
    captured = capfd.readouterr()
    assert error.value.returncode == 19
    record, = root.glob('r-*')
    assert (record / 'run.log').stat().st_size <= 1024 * 1024 + 1024
    assert 'truncated' in captured.err.lower()
