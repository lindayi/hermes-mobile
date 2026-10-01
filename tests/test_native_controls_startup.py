import json
import subprocess
from pathlib import Path


def test_real_installed_native_http_startup_isolated_owner_entrypoint():
    probe=Path(__file__).with_name('native_controls_startup_probe.py')
    result=subprocess.run(['/usr/local/lib/hermes-agent/venv/bin/python',str(probe)],text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr[-2500:]+result.stdout[-1000:]
    summary=json.loads(result.stdout.strip().splitlines()[-1])
    assert summary=={'native_http_startup':True,'anonymous_denied':True,'versioned_controls':True,'missing_run_rejected':True,'models_invoked':0}
