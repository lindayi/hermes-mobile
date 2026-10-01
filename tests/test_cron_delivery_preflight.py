"""Native agent-job preflight regression, no production homes or network."""
import json
from pathlib import Path
import subprocess
import pytest

def probe(tmp_path,mode):
    result=subprocess.run(['/usr/local/lib/hermes-agent/venv/bin/python',
        str(Path(__file__).with_name('cron_delivery_preflight_probe.py')),str(tmp_path/'home'),mode],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr[-3000:]
    return json.loads(result.stdout)

def test_real_native_preflight_accepts_registered_mobile_and_keeps_both_targets(tmp_path):
    data=probe(tmp_path,'enabled')
    assert data['reason'] is None,data
    assert data['cron_env']=='HERMES_MOBILE_HOME_CHANNEL'
    assert data['targets']==[['whatsapp','test-whatsapp-origin'],['mobile_delivery','owner']]
    assert data['implicit_home']=='','Explicit owner target must not become an implicit cross-profile default'

@pytest.mark.parametrize('mode,expected',[('disabled','not connected'),('unknown','not a known cron')])
def test_real_native_preflight_still_rejects_disabled_or_unknown_targets(tmp_path,mode,expected):
    data=probe(tmp_path,mode)
    assert expected in data['reason']
    assert not data['targets']
