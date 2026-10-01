#!/usr/bin/env python3
"""Provision the pinned public Hermes runtime on a disposable GitHub-hosted runner."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.install_core import patch_targets, validate_baselines


REPOSITORY = 'https://github.com/NousResearch/hermes-agent'
REVISION = '8911e2e0edf750b104edbdc106d63d6cdac88524'
RUNTIME_PATH = Path('/usr/local/lib/hermes-agent')
PYTHON_VERSION = '3.11'
UV_VERSION = '0.8.22'
UPSTREAM_HASHES = {
    'pyproject.toml': '1f928b1560b0669291b3f7d562aa78c99ac4f927375939ca97fd3c3e7494cb91',
    'uv.lock': '8fd868b9da8b6bc2f4aa94a845e210eccdd5e31be7a0b404f0a8527ced0fddec',
}
TARGETS = {
    'run_agent.py': {
        'installed_sha256': 'a26e5264738f1c62347e63c1265e562d3cfae439dadc313db48572f3e9cc751b',
        'staged_sha256': 'fb58e81ac57c0f49370d72146d21250d1cfeaa0b964c9996e972954bbc343609',
    },
    'gateway/platforms/api_server.py': {
        'installed_sha256': '2893fba247bbe1523eaaf0b90646238c3cfb4208a9a69f3f80fadbc7dd1ddbdd',
        'staged_sha256': '187c92509b3769c04756f0dc800d3597ea891ea21262e8a32ceaf3972ac95300',
    },
    'cron/scheduler.py': {
        'installed_sha256': 'ac0d2f0edcfaf26ffa21aa4e7478b44bb78e6f43e3072697e2849f2b1b13b7e7',
        'staged_sha256': '4c75d873de809f72e865b9aab2a9f6e1c1008859b60373f9d721d0847d21e9fe',
    },
    'tools/send_message_tool.py': {
        'installed_sha256': '5c0f0898b5a16d5c63cd083281ac5c307541ed704c46acfcfbc368b5eea1dc21',
        'staged_sha256': '56ed3549db505cb38c9e000cb56ca13017f386c9080c3e5467f34cf9ab12e31c',
    },
}
PATCHES = (
    {
        'path': 'patches/native-compat.patch',
        'sha256': '2add04e93a5ea74eeeb407dc9d5556bb38c1454b5c8bf307c3e8490e9b72dd32',
        'targets': ('gateway/platforms/api_server.py', 'run_agent.py'),
    },
    {
        'path': 'patches/cron-delivery.patch',
        'sha256': '444af4887abcea020baaf8c8cfbf4679670d38cdc2fc302a97ad5c54d68fc1ff',
        'targets': ('cron/scheduler.py', 'tools/send_message_tool.py'),
    },
)
EXISTING_LINKS = ('node_modules', 'package.json')


def _expected_spec():
    return {
        'repository': REPOSITORY,
        'revision': REVISION,
        'runtime_path': str(RUNTIME_PATH),
        'python_version': PYTHON_VERSION,
        'uv_version': UV_VERSION,
        'upstream_sha256': UPSTREAM_HASHES,
        'targets': TARGETS,
        'patches': [
            {'path': entry['path'], 'sha256': entry['sha256'], 'targets': list(entry['targets'])}
            for entry in PATCHES
        ],
    }


def load_runtime_spec(repository_root):
    path = Path(repository_root) / '.github/native-runtime.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or unsafe native runtime manifest')
    try:
        spec = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('Invalid native runtime manifest') from error
    if spec != _expected_spec():
        raise ValueError('Native runtime manifest differs from the approved public pins')
    return spec


def validate_hosted_runner(environ):
    expected = {
        'CI': 'true',
        'GITHUB_ACTIONS': 'true',
        'GITHUB_REPOSITORY': 'lindayi/hermes-mobile',
        'RUNNER_ENVIRONMENT': 'github-hosted',
        'RUNNER_OS': 'Linux',
        'ImageOS': 'ubuntu24',
    }
    if any(environ.get(name) != value for name, value in expected.items()):
        raise RuntimeError('Native runtime provisioning is restricted to hosted Ubuntu Actions')
    workspace = Path(environ.get('GITHUB_WORKSPACE', ''))
    runner_temp = Path(environ.get('RUNNER_TEMP', ''))
    if (workspace != Path('/home/runner/work/hermes-mobile/hermes-mobile')
            or runner_temp != Path('/home/runner/work/_temp')):
        raise RuntimeError('Unexpected GitHub-hosted workspace layout')
    return workspace, runner_temp


def validate_patch_inputs(repository_root, spec):
    root = Path(repository_root)
    if spec != _expected_spec():
        raise ValueError('Native runtime manifest differs from the approved public pins')
    baseline_records = {}
    for name in ('native-compat-baseline.json', 'cron-delivery-baseline.json'):
        path = root / 'patches' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or unsafe patch baseline')
        baseline_records.update(json.loads(path.read_text(encoding='utf-8')))
    if baseline_records != spec['targets']:
        raise ValueError('Patch pre/postimage records do not match the approved hashes')
    for entry in PATCHES:
        patch = root / entry['path']
        if patch.is_symlink() or not patch.is_file():
            raise ValueError('Missing or unsafe public compatibility patch')
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            raise ValueError(f'Patch digest mismatch: {entry["path"]}')
        actual_targets = patch_targets(patch.read_text(encoding='utf-8'))
        if actual_targets != set(entry['targets']):
            raise ValueError(f'Unexpected patch targets: {entry["path"]}')
    return baseline_records


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _child_environment(work, target):
    home = work / 'home'
    cache = work / 'cache'
    paths = {
        'HOME': home,
        'PIP_CACHE_DIR': cache / 'pip',
        'UV_CACHE_DIR': cache / 'uv',
        'XDG_CACHE_HOME': cache / 'xdg',
        'XDG_CONFIG_HOME': work / 'config',
        'XDG_DATA_HOME': work / 'data',
        'TMPDIR': work / 'tmp',
    }
    for path in paths.values():
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    env = {
        'PATH': os.environ.get('PATH', ''),
        'LANG': 'C.UTF-8',
        'LC_ALL': 'C.UTF-8',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_TERMINAL_PROMPT': '0',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'UV_NO_PROGRESS': '1',
        'UV_PROJECT_ENVIRONMENT': str(target / 'venv'),
        'PLAYWRIGHT_SKIP_BROWSER_GC': '1',
    }
    env.update({name: str(path) for name, path in paths.items()})
    return env


def _run(command, *, env, cwd=None):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stdout or '')[-8000:]
        raise RuntimeError(f'{Path(command[0]).name} failed ({result.returncode}):\n{detail}')
    return result.stdout


def _validate_target(target, workspace):
    if target.is_symlink() or not target.is_dir() or target.resolve() != target:
        raise RuntimeError('Expected a real prepared native-runtime directory')
    if target.stat().st_uid != os.getuid():
        raise RuntimeError('Native-runtime directory must belong to the hosted runner user')
    names = set(os.listdir(target))
    if names - set(EXISTING_LINKS):
        raise RuntimeError('Refusing to overwrite an existing native runtime')
    for name in names:
        path = target / name
        if not path.is_symlink() or os.readlink(path) != str(workspace / name):
            raise RuntimeError(f'Unexpected pre-existing runtime entry: {name}')


def _validate_public_tree(source):
    for directory, names, files in os.walk(source, followlinks=False):
        for name in names + files:
            path = Path(directory) / name
            if path.is_symlink():
                link = Path(os.readlink(path))
                if link.is_absolute() or not (path.parent / link).resolve().is_relative_to(source):
                    raise RuntimeError(f'Unsafe upstream symlink: {path.relative_to(source)}')


def _ignore_upstream_metadata(directory, names, source):
    if Path(directory) == source:
        return [name for name in names if name in {'.git', 'node_modules', 'package.json'}]
    return []


def _clone_upstream(source, env):
    git = shutil.which('git', path=env['PATH'])
    if not git:
        raise RuntimeError('Git is required on the hosted runner')
    _run([git, 'init', str(source)], env=env)
    _run([git, '-C', str(source), 'remote', 'add', 'origin', REPOSITORY], env=env)
    _run([git, '-C', str(source), 'fetch', '--depth=1', '--no-tags', 'origin', REVISION], env=env)
    _run([git, '-C', str(source), 'checkout', '--detach', 'FETCH_HEAD'], env=env)
    actual = _run([git, '-C', str(source), 'rev-parse', 'HEAD'], env=env).strip()
    if actual != REVISION:
        raise RuntimeError('Fetched Hermes revision did not match the approved commit')


def prepare_runtime(repository_root, environ):
    workspace, runner_temp = validate_hosted_runner(environ)
    root = Path(repository_root)
    if root != workspace or root.resolve() != root or not root.is_dir():
        raise RuntimeError('Provisioning must run from the canonical GitHub workspace')
    spec = load_runtime_spec(root)
    records = validate_patch_inputs(root, spec)
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError('Native runtime setup requires the pinned Python 3.11 action')
    target = Path(spec['runtime_path'])
    _validate_target(target, workspace)
    work = Path(tempfile.mkdtemp(prefix='hermes-native-', dir=runner_temp))
    os.chmod(work, 0o700)
    stage = work / 'upstream'
    stage.mkdir(mode=0o700)
    try:
        env = _child_environment(work, target)
        _clone_upstream(stage, env)
        for name, expected in spec['upstream_sha256'].items():
            if _hash(stage / name) != expected:
                raise RuntimeError(f'Pinned upstream input hash mismatch: {name}')
        validate_baselines(stage, records)
        patch = shutil.which('patch', path=env['PATH'])
        if not patch:
            raise RuntimeError('The hosted Ubuntu patch utility is required')
        for entry in PATCHES:
            _run(
                [patch, '--batch', '--fuzz=0', '-p1', '-i', str(root / entry['path'])],
                cwd=stage,
                env=env,
            )
        validate_baselines(stage, records, patched_only=True)
        _validate_public_tree(stage)
        python = str(Path(sys.executable).resolve())
        pip = [python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
               '--cache-dir', env['PIP_CACHE_DIR'], f'uv=={spec["uv_version"]}']
        _run(pip, env=env)
        uv = str(Path(sys.executable).parent / ('uv.exe' if os.name == 'nt' else 'uv'))
        if not Path(uv).is_file():
            raise RuntimeError('Pinned uv installer did not provide its executable')
        _run(
            [uv, 'sync', '--locked', '--no-dev', '--no-install-project',
             '--python', python, '--project', str(stage)],
            cwd=stage,
            env=env,
        )
        native_python = target / 'venv/bin/python'
        if not native_python.is_file():
            raise RuntimeError('Locked dependency sync did not create the expected venv/bin/python')
        shutil.copytree(
            stage,
            target,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=lambda directory, names: _ignore_upstream_metadata(
                directory, names, stage
            ),
        )
        validate_baselines(target, records, patched_only=True)
        print(f'Installed public Hermes revision {REVISION}')
        print(f'upstream pyproject.toml sha256={spec["upstream_sha256"]["pyproject.toml"]}')
        print(f'upstream uv.lock sha256={spec["upstream_sha256"]["uv.lock"]}')
        for entry in PATCHES:
            print(f'{entry["path"]} sha256={entry["sha256"]}')
        print('Verified all four installed and staged source hashes; no optional extras installed.')
    finally:
        shutil.rmtree(work)


def main():
    if len(sys.argv) != 1:
        raise SystemExit('Runtime provisioning accepts no command-line inputs')
    prepare_runtime(ROOT, os.environ)


if __name__ == '__main__':
    main()
