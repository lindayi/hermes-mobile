"""The disabled coordinator template must use the app dependency environment."""
import configparser
import os
from pathlib import Path
import shlex
import subprocess
import sys


def test_coordinator_unit_uses_application_python_for_lifecycle_imports(tmp_path):
    source = Path(__file__).resolve().parents[1]
    home = tmp_path / 'home'
    checkout = home / 'projects' / 'hermes-mobile-git'
    checkout.mkdir(parents=True)
    (checkout / '.venv').symlink_to(Path(sys.prefix), target_is_directory=True)
    for name in ('backend', 'deploy', 'scripts'):
        (checkout / name).symlink_to(source / name, target_is_directory=True)
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(source / 'deploy/hermes-mobile-coordinator.service')
    command = shlex.split(unit['Service']['ExecStart'].replace('%h', str(home)))
    assert command[0] == str(checkout / '.venv' / 'bin' / 'python')
    assert command[1] == str(checkout / 'scripts' / 'cloud_coordinator.py')
    # --help alone misses the lazy backend configuration dependency. Import it
    # explicitly without loading settings, opening a database, or making API calls.
    result = subprocess.run(
        [command[0], '-B', '-c',
         'from backend.configuration import load_settings; '
         'from deploy.workflow_lifecycle_sources import LifecycleSourcePaths; '
         'assert callable(load_settings) and callable(LifecycleSourcePaths)'],
        cwd=checkout, env={**os.environ, 'HOME': str(home)},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (home / '.local').exists()
    assert not (home / '.config').exists()
    assert unit['Service']['ProtectSystem'] == 'strict'
    assert unit['Service']['ProtectHome'] == 'read-only'
