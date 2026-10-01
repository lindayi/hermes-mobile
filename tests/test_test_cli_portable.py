"""Portable managed CLI discovery; synthetic executables are never launched."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1]


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 99\n')
    path.chmod(0o700)
    return path


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('portable_test_cli', SOURCE / 'scripts/test.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source = tmp_path / 'source'
    source.mkdir()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    legacy = tmp_path / 'legacy/node'
    monkeypatch.setattr(cli, 'NODE', str(legacy))
    monkeypatch.setenv('PATH', str(bin_dir))
    monkeypatch.setenv('HERMES_TEST_PYTHON', sys.executable)
    monkeypatch.delenv('HERMES_TEST_NODE', raising=False)
    return SimpleNamespace(cli=cli, source=source, bin=bin_dir, legacy=legacy,
                           argv=['--source', str(source), 'python'], run=Mock())


def test_path_only_cloud_node_without_legacy(cli_env):
    env = cli_env
    node = executable(env.bin / 'node')
    assert not env.legacy.exists()
    assert env.cli.main(env.argv, run=env.run) == 0
    env.run.assert_called_once_with(env.source, python=sys.executable, node=str(node),
                                    suite='python', assets=None, root=None, extra_args=[])


def test_explicit_node_overrides_path_and_legacy(cli_env, tmp_path, monkeypatch):
    env = cli_env
    executable(env.bin / 'node')
    executable(env.legacy)
    node = executable(tmp_path / 'override/node')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HERMES_TEST_NODE', 'override/node')
    assert env.cli.main(env.argv, run=env.run) == 0
    assert env.run.call_args.kwargs['node'] == str(node)


@pytest.mark.parametrize('invalid', ['missing', 'not-executable', 'directory', 'broken-symlink', 'empty'])
def test_invalid_explicit_node_never_falls_back(cli_env, tmp_path, monkeypatch, capsys, invalid):
    env = cli_env
    executable(env.bin / 'node')
    executable(env.legacy)
    node = tmp_path / 'invalid-node'
    if invalid == 'not-executable':
        node.write_text('not executable')
        node.chmod(0o600)
    elif invalid == 'directory':
        node.mkdir(mode=0o700)
    elif invalid == 'broken-symlink':
        node.symlink_to(tmp_path / 'absent')
    monkeypatch.setenv('HERMES_TEST_NODE', '' if invalid == 'empty' else str(node))
    with pytest.raises(SystemExit) as caught:
        env.cli.main(env.argv, run=env.run)
    assert caught.value.code == 2
    assert 'HERMES_TEST_NODE' in capsys.readouterr().err
    env.run.assert_not_called()


def test_path_node_precedes_legacy(cli_env):
    env = cli_env
    node = executable(env.bin / 'node')
    executable(env.legacy)
    assert env.cli.main(env.argv, run=env.run) == 0
    assert env.run.call_args.kwargs['node'] == str(node)


@pytest.mark.parametrize('path_entry', ['missing', 'not-executable', 'directory'])
def test_server_fallback_without_executable_on_path(cli_env, path_entry):
    env = cli_env
    if path_entry == 'not-executable':
        executable(env.bin / 'node').chmod(0o600)
    elif path_entry == 'directory':
        (env.bin / 'node').mkdir()
    executable(env.legacy)
    assert env.cli.main(env.argv, run=env.run) == 0
    assert env.run.call_args.kwargs['node'] == str(env.legacy)


@pytest.mark.parametrize('legacy_entry', ['missing', 'not-executable', 'directory'])
def test_no_executable_node_fails_closed(cli_env, capsys, legacy_entry):
    env = cli_env
    if legacy_entry == 'not-executable':
        executable(env.legacy).chmod(0o600)
    elif legacy_entry == 'directory':
        env.legacy.mkdir(parents=True)
    with pytest.raises(SystemExit) as caught:
        env.cli.main(env.argv, run=env.run)
    assert caught.value.code == 2
    assert 'HERMES_TEST_NODE' in capsys.readouterr().err
    env.run.assert_not_called()


@pytest.mark.parametrize('override', [False, True])
def test_python_symlink_is_not_resolved(cli_env, tmp_path, monkeypatch, override):
    env = cli_env
    executable(env.bin / 'node')
    target = executable(tmp_path / 'interpreter/python')
    link = env.source / '.venv/bin/python'
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    if override:
        monkeypatch.chdir(env.source)
        monkeypatch.setenv('HERMES_TEST_PYTHON', '.venv/bin/python')
    else:
        monkeypatch.delenv('HERMES_TEST_PYTHON')
    assert env.cli.main(env.argv, run=env.run) == 0
    assert env.run.call_args.kwargs['python'] == str(link)
    assert str(link) != str(target)


def test_invalid_python_still_fails_before_run(cli_env, monkeypatch, capsys):
    env = cli_env
    executable(env.bin / 'node')
    monkeypatch.setenv('HERMES_TEST_PYTHON', str(env.source / 'missing-python'))
    with pytest.raises(SystemExit) as caught:
        env.cli.main(env.argv, run=env.run)
    assert caught.value.code == 2
    assert 'HERMES_TEST_PYTHON' in capsys.readouterr().err
    env.run.assert_not_called()


@pytest.mark.parametrize(('suite', 'selected', 'filters'), [
    ('python', 'tests/test_selected.py', ['-k', 'one or two', '-v']),
    ('js', 'tests/browser/unit.test.mjs', ['--test-name-pattern=one|two']),
    ('browser', 'tests/browser/ui.spec.mjs', ['--test-name-pattern', 'one|two']),
])
@pytest.mark.parametrize('separator', [[], ['--']])
def test_selected_files_filters_and_managed_options_unchanged(cli_env, tmp_path, suite, selected, filters, separator):
    env = cli_env
    node = executable(env.bin / 'node')
    root, assets = tmp_path / 'managed', tmp_path / 'assets'
    extra = [selected, *filters]
    argv = ['--source', str(env.source), '--root', str(root), '--assets', str(assets),
            suite, *separator, *extra]
    assert env.cli.main(argv, run=env.run) == 0
    env.run.assert_called_once_with(env.source, python=sys.executable, node=str(node),
                                    suite=suite, assets=assets, root=root, extra_args=extra)
    assert not root.exists(), 'mocked CLI must not allocate a workspace'


@pytest.mark.parametrize(('returncode', 'expected'), [(23, 23), (-15, 143)])
def test_subprocess_failure_exit_code_unchanged(cli_env, returncode, expected):
    env = cli_env
    executable(env.bin / 'node')
    env.run.side_effect = subprocess.CalledProcessError(returncode, ['synthetic'])
    assert env.cli.main(env.argv, run=env.run) == expected
    env.run.assert_called_once()


@pytest.mark.parametrize('error_type', [ValueError, OSError, RuntimeError])
def test_runner_errors_still_report_failure(cli_env, capsys, error_type):
    env = cli_env
    executable(env.bin / 'node')
    env.run.side_effect = error_type('synthetic failure')
    assert env.cli.main(env.argv, run=env.run) == 1
    assert 'Test runner: synthetic failure' in capsys.readouterr().err
    env.run.assert_called_once()
