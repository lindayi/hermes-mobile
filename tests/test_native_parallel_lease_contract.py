"""Real installed native lease regressions under a private, credential-free home."""
import os
from pathlib import Path
import subprocess


def test_installed_native_cross_process_lease_remains_authoritative(tmp_path):
    home = tmp_path / 'isolated-home'
    native = home / '.hermes'
    native.mkdir(parents=True)
    env = {'HOME':str(home), 'HERMES_HOME':str(native),
           'PATH':'/usr/local/bin:/usr/bin:/bin', 'LANG':'C.UTF-8',
           'PYTHONPATH':'/usr/local/lib/hermes-agent', 'PYTHONDONTWRITEBYTECODE':'1',
           'XDG_CACHE_HOME':str(home/'.cache'), 'XDG_CONFIG_HOME':str(home/'.config')}
    result = subprocess.run([
        os.environ.get('HERMES_TEST_PYTHON', '/home/lindayi/projects/hermes-mobile/.venv/bin/python'),
        '-c', "import sys; sys.path.append('/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages'); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))",
        '/usr/local/lib/hermes-agent/tests/run_agent/test_cross_process_turn_lease.py',
        '-q','-p','no:cacheprovider','--basetemp',str(tmp_path/'native-pytest')],
        cwd=tmp_path,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
    assert result.returncode == 0, result.stdout
    assert 'passed' in result.stdout, result.stdout
    assert 'failed' not in result.stdout, result.stdout
