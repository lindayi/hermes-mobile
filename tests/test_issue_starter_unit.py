"""Prove the disabled service entrypoint against the documented checkout layout."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import subprocess


def test_starter_unit_uses_canonical_checkout_and_cli_help(tmp_path):
    source = Path(__file__).resolve().parents[1]
    home = tmp_path / 'home'
    checkout = home / 'projects' / 'hermes-mobile-git'
    for name in ('scripts/issue_starter.py', 'deploy/issue_starter.py',
                 'deploy/pull_handoff_binding.py'):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(source / 'deploy/hermes-mobile-issue-starter.service')
    command = shlex.split(parser['Service']['ExecStart'].replace('%h', str(home)))
    assert command == ['/usr/bin/python3', '-B', str(checkout / 'scripts/issue_starter.py'),
                       '--once', '--apply']
    assert parser['Service']['WorkingDirectory'].replace('%h', str(home)) == str(checkout)
    # Exercise only argument/help startup, never the apply path or GitHub access.
    before = sorted(str(p.relative_to(home)) for p in home.rglob('*'))
    result = subprocess.run(command[:3] + ['--help'], cwd=checkout,
                            env={**os.environ, 'HOME': str(home)},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert '--once' in result.stdout and '--apply' in result.stdout
    assert sorted(str(p.relative_to(home)) for p in home.rglob('*')) == before
