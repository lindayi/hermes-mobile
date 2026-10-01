import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.prepare_native_test_runtime import (
    _child_environment,
    _write_uv_requirements,
    load_runtime_spec,
    _remove_created_runtime,
    publish_sqlite_library,
    validate_action_preflight,
    validate_hosted_runner,
    validate_patch_inputs,
    validate_sqlite_version,
)


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REVISION = '8911e2e0edf750b104edbdc106d63d6cdac88524'
EXPECTED_TARGETS = {
    'cron/scheduler.py',
    'gateway/platforms/api_server.py',
    'run_agent.py',
    'tools/send_message_tool.py',
}


def hosted_environment():
    return {
        'CI': 'true',
        'GITHUB_ACTIONS': 'true',
        'GITHUB_REPOSITORY': 'lindayi/hermes-mobile',
        'GITHUB_WORKSPACE': '/home/runner/work/hermes-mobile/hermes-mobile',
        'RUNNER_ENVIRONMENT': 'github-hosted',
        'RUNNER_OS': 'Linux',
        'RUNNER_ARCH': 'X64',
        'RUNNER_TEMP': '/home/runner/work/_temp',
        'ImageOS': 'ubuntu24',
    }


def test_runner_guard_rejects_local_self_hosted_and_other_repositories():
    environment = hosted_environment()
    for key, value in (
        ('GITHUB_ACTIONS', 'false'),
        ('RUNNER_ENVIRONMENT', 'self-hosted'),
        ('GITHUB_REPOSITORY', 'other/project'),
        ('GITHUB_WORKSPACE', '/tmp/hermes-mobile'),
        ('RUNNER_ARCH', 'ARM64'),
    ):
        changed = dict(environment, **{key: value})
        with pytest.raises(RuntimeError):
            validate_hosted_runner(changed)


def test_runner_guard_accepts_only_canonical_hosted_layout():
    workspace, temporary = validate_hosted_runner(hosted_environment())
    assert workspace == Path('/home/runner/work/hermes-mobile/hermes-mobile')
    assert temporary == Path('/home/runner/work/_temp')


def test_action_validates_runner_before_privileged_runtime_creation():
    action = yaml.safe_load((ROOT / '.github/actions/native-test-environment/action.yml').read_text())
    steps = action['runs']['steps']
    assert steps[0]['shell'] == 'bash'
    assert '--preflight' in steps[0]['run']
    setup = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/setup-python@'))
    create = next(i for i, step in enumerate(steps) if 'sudo mkdir' in step.get('run', ''))
    assert 0 < setup < create
    assert '--preflight' in steps[create]['run'].split('sudo mkdir')[0]
    assert steps[create]['run'].index('sudo mkdir') < steps[create]['run'].index('sudo chown')


def test_action_preflight_rejects_existing_runtime_without_mutating_it(tmp_path):
    target = tmp_path / 'native-runtime'
    target.mkdir(mode=0o700)
    content = target / 'keep'
    content.write_text('do not touch')
    before = (target.stat().st_mode, content.read_bytes())
    with pytest.raises(RuntimeError, match='Refusing existing native runtime'):
        validate_action_preflight(hosted_environment(), ROOT, runtime_target=target)
    assert (target.stat().st_mode, content.read_bytes()) == before


def test_action_preflight_rejects_symlinked_runtime_without_following_it(tmp_path):
    target = tmp_path / 'native-runtime'
    real = tmp_path / 'existing'
    real.mkdir()
    marker = real / 'marker'
    marker.write_text('preserve')
    target.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match='Refusing existing native runtime'):
        validate_action_preflight(hosted_environment(), ROOT, runtime_target=target)
    assert target.is_symlink()
    assert marker.read_text() == 'preserve'


def test_runtime_spec_pins_public_sources_and_exact_four_preimages():
    spec = load_runtime_spec(ROOT)
    assert spec['repository'] == 'https://github.com/NousResearch/hermes-agent'
    assert spec['revision'] == UPSTREAM_REVISION
    assert spec['python_version'] == '3.11'
    assert spec['uv_version'] == '0.9.28'
    assert spec['uv_sha256'] == '7b8460a2b624d8ab27cb293a2c9f2393f9efc4e36e0fb886a6c2360e23fb48be'
    assert spec['extras'] == ['messaging']
    assert spec['sqlite'] == {
        'repository': 'https://github.com/sqlite/sqlite',
        'revision': 'a5333afb9ad1aa473f8963b92caeaa955f47dc74',
        'version': '3.51.3',
    }
    assert set(spec['targets']) == EXPECTED_TARGETS
    assert {entry['path'] for entry in spec['patches']} == {
        'patches/native-compat.patch',
        'patches/cron-delivery.patch',
    }
    validate_patch_inputs(ROOT, spec)


def test_patch_digest_mismatch_is_rejected(tmp_path):
    for path in (
        '.github/native-runtime.json',
        'patches/native-compat.patch',
        'patches/cron-delivery.patch',
        'patches/native-compat-baseline.json',
        'patches/cron-delivery-baseline.json',
    ):
        source = ROOT / path
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    spec = load_runtime_spec(tmp_path)
    validate_patch_inputs(tmp_path, spec)
    patch = tmp_path / 'patches/native-compat.patch'
    patch.write_bytes(patch.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='digest'):
        validate_patch_inputs(tmp_path, spec)


def test_entrypoint_rejects_arbitrary_command_line_inputs():
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/prepare_native_test_runtime.py'), 'unexpected'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert 'accepts no command-line inputs' in result.stderr


def test_failed_setup_cleanup_removes_only_new_runtime_entries(tmp_path):
    target = tmp_path / 'runtime'
    target.mkdir()
    preserved = tmp_path / 'package.json'
    preserved.write_text('private test harness')
    (target / 'package.json').symlink_to(preserved)
    (target / 'venv').mkdir()
    (target / 'partial-source').write_text('public upstream data')
    _remove_created_runtime(target, {'package.json'})
    assert list(target.iterdir()) == [target / 'package.json']
    assert preserved.read_text() == 'private test harness'


def test_installer_uses_private_workspace_and_runner_certificate_store(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    environment = _child_environment(work, tmp_path / 'runtime')
    assert environment['HOME'] == str(work / 'home')
    assert environment['PIP_CACHE_DIR'].startswith(str(work))
    assert environment['UV_CACHE_DIR'].startswith(str(work))
    assert environment['UV_NATIVE_TLS'] == 'true'
    assert not {'GITHUB_TOKEN', 'GH_TOKEN'} & environment.keys()


def test_uv_bootstrap_uses_private_hash_locked_requirement(tmp_path):
    requirements = _write_uv_requirements(tmp_path, load_runtime_spec(ROOT))
    assert requirements.read_text() == (
        'uv==0.9.28 --hash=sha256:'
        '7b8460a2b624d8ab27cb293a2c9f2393f9efc4e36e0fb886a6c2360e23fb48be\n'
    )
    assert requirements.stat().st_mode & 0o077 == 0


def test_sqlite_version_gate_preserves_native_test_minimum():
    assert validate_sqlite_version('3.51.3') == (3, 51, 3)
    assert validate_sqlite_version('3.52.0') == (3, 52, 0)
    with pytest.raises(RuntimeError, match='3.51.3'):
        validate_sqlite_version('3.51.2')


def test_sqlite_library_export_is_private_and_rejects_newlines(tmp_path):
    runner_temp = tmp_path / 'runner'
    runner_temp.mkdir()
    env_file = runner_temp / 'set_env'
    env_file.touch()
    env_file.chmod(0o644)
    library = runner_temp / 'sqlite/lib'
    library.mkdir(parents=True)
    (library / 'libsqlite3.so.3.51.3').write_bytes(b'synthetic library marker')
    (library / 'libsqlite3.so.0').symlink_to('libsqlite3.so.3.51.3')
    environment = {'GITHUB_ENV': str(env_file), 'LD_LIBRARY_PATH': '/system/lib'}
    value = publish_sqlite_library(environment, runner_temp, library)
    assert value == f'{library}:/system/lib'
    assert env_file.read_text() == f'LD_LIBRARY_PATH={value}\n'
    env_file.chmod(0o666)
    with pytest.raises(RuntimeError):
        publish_sqlite_library(environment, runner_temp, library)
    env_file.chmod(0o644)
    with pytest.raises(RuntimeError, match='Unsafe inherited library path'):
        publish_sqlite_library(
            dict(environment, LD_LIBRARY_PATH='/bad\nLD_PRELOAD=/bad'),
            runner_temp,
            library,
        )
