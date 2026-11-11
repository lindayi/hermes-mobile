"""Managed test runner: isolation, selection, retention and process cleanup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from deploy import test_workspace as module

SOURCE = Path(__file__).resolve().parents[1]
NODE = '/home/lindayi/.hermes/node/bin/node'


def fixture_source(tmp_path):
    source = tmp_path / 'source'
    (source / 'tests/browser').mkdir(parents=True)
    (source / 'frontend').mkdir()
    (source / 'tests/browser/unit.test.mjs').write_text('')
    (source / 'tests/browser/ui.spec.mjs').write_text('')
    return source


def test_deploy_checks_use_managed_suite_with_exact_staged_assets(tmp_path, monkeypatch):
    from deploy import self_deploy
    from types import SimpleNamespace
    stage = fixture_source(tmp_path)
    (stage / 'public').mkdir()
    source = tmp_path / 'interpreter-source'
    calls = []
    runner = lambda *a, **k: None
    monkeypatch.setattr(module, 'run_suite', lambda *a, **k: calls.append((a, k)))
    self_deploy.run_checks(SimpleNamespace(source=source), stage, run=runner)
    assert calls == [((stage,), dict(python=str(source / '.venv/bin/python'),
                                   node=NODE, suite='all', assets=stage / 'public', run=runner))]


def test_installed_playwright_cache_is_pinned_before_home_isolation(tmp_path, monkeypatch):
    cache = tmp_path / 'home/.cache/ms-playwright'
    cache.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    calls = []
    module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE,
                     root=tmp_path / 'managed', run=lambda *a, **k: calls.append(k['env']))
    assert calls and all(env['PLAYWRIGHT_BROWSERS_PATH'] == str(cache) for env in calls)


@pytest.mark.parametrize(('suite', 'selected'), [
    ('python', 'tests/test_selected.py'),
    ('js', 'tests/browser/unit.test.mjs'),
    ('browser', 'tests/browser/ui.spec.mjs'),
])
def test_explicit_test_paths_replace_default_collection(tmp_path, suite, selected):
    source = fixture_source(tmp_path)
    (source / 'tests/test_selected.py').write_text('')
    calls = []
    flags = ('-k', 'ready') if suite == 'python' else ('--test-name-pattern=fixture',)
    module.run_suite(source, python=sys.executable, node=NODE, suite=suite, root=tmp_path / 'managed',
                     extra_args=(selected, *flags), run=lambda command, **kw: calls.append(command))
    assert len(calls) == 1 and calls[0].count(selected) == 1 and 'tests' not in calls[0]
    assert all(flag in calls[0] for flag in flags)


@pytest.mark.parametrize(('suite', 'selected'), [
    ('python', '/tmp/outside.py'), ('python', '../tests/test_selected.py'),
    ('python', 'tests/missing.py'), ('python', 'tests/browser/unit.test.mjs'),
    ('js', 'tests/browser/ui.spec.mjs'), ('all', 'tests/test_selected.py'),
    ('js', '--import=tests/browser/unit.test.mjs'), ('python', '--rootdir=/'),
])
def test_rejects_wrong_suite_paths_and_unsupported_options(tmp_path, suite, selected):
    source = fixture_source(tmp_path)
    (source / 'tests/test_selected.py').write_text('')
    with pytest.raises(ValueError):
        module.run_suite(source, python=sys.executable, node=NODE, suite=suite,
                         root=tmp_path / 'managed', extra_args=(selected,),
                         run=lambda *a, **k: pytest.fail('executed'))
    assert not (tmp_path / 'managed').exists()


def test_suite_isolates_child_environment_and_removes_success(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    monkeypatch.setenv('HERMES_MOBILE_CONFIG', '/private/live.json')
    monkeypatch.setenv('PYTHONPATH', '/private/native')
    calls = []

    def run(command, *, cwd, env, check, umask):
        assert cwd == source and check is True and umask == 0o077
        assert 'PYTHONPATH' not in env and 'HERMES_MOBILE_CONFIG' not in env
        assert env['PATH'].split(os.pathsep)[0] == str(Path(NODE).parent)
        assert env['HERMES_FRONTEND_DIR'] == str(source / 'frontend')
        for key in ('HOME', 'HERMES_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'HERMES_TEST_ARTIFACT_DIR'):
            assert Path(env[key]).is_dir() and Path(env[key]).is_relative_to(tmp_path / 'managed')
        Path(env['HOME'], 'native.sqlite').write_text('data')
        calls.append(command)

    module.run_suite(source, python=sys.executable, node=NODE, run=run, root=tmp_path / 'managed')
    assert calls[0][:3] == [sys.executable, '-c', module.PYTEST_BOOTSTRAP]
    assert calls[1] == [NODE, '--test', '--test-concurrency=2',
                        'tests/browser/ui.spec.mjs', 'tests/browser/unit.test.mjs']
    assert not list((tmp_path / 'managed').glob('r-*'))
    marker = json.loads((tmp_path / 'managed/.managed.json').read_text())
    assert marker == {'kind': 'hermes-mobile-tests-v1', 'uid': os.getuid()}


@pytest.mark.parametrize('unsafe', ['unmarked', 'symlink', 'public', 'invalid'])
def test_refuses_unsafe_roots(tmp_path, unsafe):
    source = fixture_source(tmp_path)
    root = tmp_path / 'managed'
    if unsafe == 'symlink':
        root.symlink_to(source, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        if unsafe == 'public':
            (root / '.managed.json').write_text(json.dumps(module.ROOT_MARKER))
            root.chmod(0o755)
        if unsafe == 'invalid':
            (root / '.managed.json').write_text('{}')
    with pytest.raises(ValueError):
        module.run_suite(source, python=sys.executable, node=NODE, run=lambda *a, **k: None, root=root)


def test_failure_keeps_only_diagnostics_and_original_error(tmp_path):
    failure = subprocess.CalledProcessError(23, ['synthetic'])
    root = tmp_path / 'managed'

    def fail(command, *, env, **kwargs):
        Path(env['HOME'], 'private.sqlite').write_text('secret')
        Path(env['HERMES_TEST_ARTIFACT_DIR'], 'failure.png').write_bytes(b'screenshot')
        Path(env['HERMES_TEST_ARTIFACT_DIR'], 'private.sqlite').write_text('not evidence')
        raise failure

    with pytest.raises(subprocess.CalledProcessError) as caught:
        module.run_suite(fixture_source(tmp_path), python=sys.executable, node=NODE, run=fail, root=root)
    assert caught.value is failure
    [record] = root.glob('r-*')
    assert (record / 'artifacts/failure.png').read_bytes() == b'screenshot'
    assert not list(record.rglob('*.sqlite')) and not (record / 'home').exists()
    assert json.loads((record / '.run.json').read_text())['state'] == 'failed'


def marked_run(root, name, *, state='running', created=None, size=32):
    directory = root / name
    directory.mkdir(mode=0o700)
    stamp = created or time.time()
    (directory / '.run.json').write_text(json.dumps(dict(module.ROOT_MARKER, state=state,
                                                         created=stamp, finished=stamp)))
    (directory / '.lock').write_text('')
    (directory / 'run.log').write_text('x' * size)
    return directory


def test_cleanup_is_dry_run_by_default_and_skips_active_or_unknown(tmp_path):
    root = tmp_path / 'managed'
    module._root(root)
    stale = marked_run(root, 'r-stale001', created=time.time() - 90000)
    recent = marked_run(root, 'r-recent01')
    invalid = marked_run(root, 'r-invalid1', created=time.time() - 90000)
    (invalid / '.run.json').write_text('{}')
    active = marked_run(root, 'r-active01', created=time.time() - 90000)
    report = module.cleanup(root)
    assert {i['path'] for i in report if i['action'] == 'remove'} == {str(stale), str(active)}
    with open(active / '.lock') as held:
        import fcntl
        fcntl.flock(held, fcntl.LOCK_EX)
        module.cleanup(root, apply=True)
        assert active.exists()
    assert not stale.exists() and recent.exists() and invalid.exists()


@pytest.mark.parametrize('limit', ['count', 'age', 'bytes'])
def test_failure_retention_is_bounded(tmp_path, limit):
    root = tmp_path / 'managed'
    module._root(root)
    now = time.time()
    runs = [marked_run(root, f'r-fail000{i}', state='failed', created=now - i, size=10) for i in range(4)]
    size = module._size(runs[0])
    kwargs = {'count': {}, 'age': {'max_age': 2.5}, 'bytes': {'max_bytes': 2 * size + 1}}[limit]
    module.cleanup(root, apply=True, now=now, **kwargs)
    kept = [run for run in runs if run.exists()]
    assert kept == runs[:{'count': 3, 'age': 3, 'bytes': 2}[limit]]


def test_real_child_output_is_logged_and_failure_returns_code(tmp_path, capfd):
    source = fixture_source(tmp_path)
    (source / 'tests/test_synthetic.py').write_text('def test_synthetic():\n    print("marker")\n    assert False\n')
    root = tmp_path / 'managed'
    with pytest.raises(subprocess.CalledProcessError):
        module.run_suite(source, python=sys.executable, node=NODE, suite='python', root=root,
                         extra_args=('tests/test_synthetic.py', '-s'))
    [record] = root.glob('r-*')
    assert 'marker' in (record / 'run.log').read_text()
    assert 'marker' in capfd.readouterr().out


def test_detached_child_in_run_session_is_killed_before_cleanup(tmp_path):
    source = fixture_source(tmp_path)
    pidfile = tmp_path / 'child.pid'
    (source / 'tests/test_spawn.py').write_text(
        'import subprocess, sys\n'
        'def test_spawn():\n'
        f'    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        f'    open({str(pidfile)!r}, "w").write(str(p.pid))\n')
    module.run_suite(source, python=sys.executable, node=NODE, suite='python',
                     root=tmp_path / 'managed', extra_args=('tests/test_spawn.py',))
    pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            break
        if state == 'Z':
            break
        time.sleep(0.05)
    else:
        pytest.fail('orphaned child survived the run')
    assert not list((tmp_path / 'managed').glob('r-*'))


def test_streaming_logs_are_bounded(tmp_path, capfd, monkeypatch):
    monkeypatch.setattr(module, 'LOG_LIMIT', 1000)
    source = fixture_source(tmp_path)
    (source / 'tests/test_noisy.py').write_text(
        'def test_noisy():\n    print("y" * 5000 + "END")\n    assert False\n')
    root = tmp_path / 'managed'
    with pytest.raises(subprocess.CalledProcessError):
        module.run_suite(source, python=sys.executable, node=NODE, suite='python', root=root,
                         extra_args=('tests/test_noisy.py', '-s'))
    [record] = root.glob('r-*')
    log = (record / 'run.log').read_text()
    assert log.startswith('y') and len(log) < 2000 and 'y' * 5000 not in log
    assert 'truncated' in capfd.readouterr().err


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / 'scripts' / f'{name}.py')
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_runner_cli_uses_explicit_python_and_propagates_exit(tmp_path, monkeypatch):
    cli = load_script('test')
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    calls = []
    assert cli.main(['--source', str(tmp_path), 'python', '--', 'tests/x.py'],
                    run=lambda *a, **k: calls.append(k)) == 0
    assert calls[0]['python'] == str(Path(sys.executable).absolute())
    assert calls[0]['extra_args'] == ['tests/x.py']

    def failing(*a, **k):
        raise subprocess.CalledProcessError(7, ['x'])
    assert cli.main(['--source', str(tmp_path), 'python'], run=failing) == 7
    monkeypatch.setenv('HERMES_TEST_PYTHON', str(tmp_path / 'missing'))
    with pytest.raises(SystemExit):
        cli.main(['--source', str(tmp_path), 'python'], run=failing)


def test_cleanup_cli_is_dry_run_until_apply(tmp_path, capsys):
    cli = load_script('clean_tests')
    root = tmp_path / 'managed'
    module._root(root)
    stale = marked_run(root, 'r-stale001', created=time.time() - 90000)
    assert cli.main(['--root', str(root)]) == 0
    assert stale.exists() and 'would remove' in capsys.readouterr().out
    assert cli.main(['--root', str(root), '--apply']) == 0
    assert not stale.exists()


def test_cleanup_never_deletes_unrecognized_marker(tmp_path):
    root = tmp_path / 'managed'
    module._root(root)
    foreign = root / 'r-foreign1'
    foreign.mkdir()
    (foreign / '.run.json').write_text('{"created": 1}')
    (foreign / '.lock').write_text('')
    module.cleanup(root, apply=True)
    assert foreign.exists()


def test_concurrent_first_runs_see_marked_root(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = tmp_path / 'managed'
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: module._root(root), range(8)))
    assert all(r == root for r in results)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['managed']


def test_sigterm_to_runner_kills_tests_and_cleans_scratch(tmp_path):
    source = fixture_source(tmp_path)
    started = tmp_path / 'started'
    (source / 'tests/test_wait.py').write_text(
        'import time, pathlib\n'
        f'def test_wait():\n    pathlib.Path({str(started)!r}).write_text("x")\n    time.sleep(60)\n')
    root = tmp_path / 'managed'
    script = (f'import sys; sys.path.insert(0, {str(SOURCE)!r}); from deploy.test_workspace import run_suite; '
              f'run_suite({str(source)!r}, python={sys.executable!r}, node={NODE!r}, suite="python", '
              f'root={str(root)!r}, extra_args=("tests/test_wait.py",))')
    runner = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.DEVNULL)
    deadline = time.time() + 20
    while not started.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert started.exists()
    runner.terminate()
    assert runner.wait(timeout=15) != 0
    [record] = root.glob('r-*')
    assert json.loads((record / '.run.json').read_text())['state'] == 'failed'
    assert not (record / 'home').exists()
