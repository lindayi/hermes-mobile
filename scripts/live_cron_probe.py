"""Execute one harmless script-only cron job to the mobile sink; remove it afterward."""
import json
import sys
import uuid
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/hermes-agent')
from hermes_cli.plugins import discover_plugins
discover_plugins()
from cron.jobs import create_job,get_job,remove_job
from cron.scheduler import run_one_job

script=Path('/home/lindayi/.hermes/scripts')/('mobile-smoke-'+uuid.uuid4().hex+'.py')
script.write_text('print("MOBILE_CRON_DELIVERY_OK")\n')
job=None
try:
    job=create_job(prompt='Harmless mobile delivery test',schedule='2099-01-01T09:00:00',name='Mobile delivery verification',script=script.name,no_agent=True,deliver='mobile_delivery:owner')
    ok=run_one_job(job)
    current=get_job(job['id'])
    report={'job_id':job['id'],'executed':ok,'status':current.get('last_status'),'delivery_error':current.get('last_delivery_error')}
    print(json.dumps(report))
    assert ok and not report['delivery_error'], 'Real cron mobile delivery failed'
finally:
    if job:remove_job(job['id'])
    script.unlink(missing_ok=True)
