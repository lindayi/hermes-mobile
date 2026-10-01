import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.prepare_native_test_runtime import (
    _child_environment,
    load_runtime_spec,
    _remove_created_runtime,
    validate_hosted_runner,
    validate_patch_inputs,
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
    ):
        changed = dict(environment, **{key: value})
        with pytest.raises(RuntimeError):
            validate_hosted_runner(changed)


def test_runner_guard_accepts_only_canonical_hosted_layout():
    workspace, temporary = validate_hosted_runner(hosted_environment())
    assert workspace == Path('/home/runner/work/hermes-mobile/hermes-mobile')
    assert temporary == Path('/home/runner/work/_temp')


def test_runtime_spec_pins_public_sources_and_exact_four_preimages():
    spec = load_runtime_spec(ROOT)
    assert spec['repository'] == 'https://github.com/NousResearch/hermes-agent'
    assert spec['revision'] == UPSTREAM_REVISION
    assert spec['python_version'] == '3.11'
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
