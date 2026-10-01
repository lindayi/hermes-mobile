"""Provision a private outbound-only plugin binding; never changes jobs."""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import sys


def main():
    home=Path('/home/lindayi/.hermes')
    state=Path('/home/lindayi/.local/share/hermes-mobile-live')
    if os.getuid()!=home.stat().st_uid:raise PermissionError('Run as lindayi')
    sys.path.insert(0,'/usr/local/lib/hermes-agent')
    from hermes_cli.config import set_config_value
    jobdata=json.loads((home/'cron/jobs.json').read_text())
    jobs=jobdata.get('jobs',[]) if isinstance(jobdata,dict) else jobdata
    if isinstance(jobs,dict):jobs=list(jobs.values())
    targets=set()
    for job in jobs:
        for dest in str(job.get('deliver','')).split(','):
            if dest.startswith('whatsapp:'):targets.add(dest)
        origin=job.get('origin') or {}
        if origin.get('platform')=='whatsapp' and origin.get('chat_id'):
            targets.add('whatsapp:'+str(origin['chat_id']))
    if len(targets)!=1:raise ValueError('Cannot unambiguously bind owner WhatsApp recipient')
    directory=home/'secrets';directory.mkdir(mode=0o700,exist_ok=True)
    tokenpath=directory/'mobile-delivery.token'
    if tokenpath.exists():
        token=tokenpath.read_text().strip()
    else:
        token=secrets.token_urlsafe(48)
        fd=os.open(tokenpath,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(token+'\n')
    if tokenpath.stat().st_mode & 0o077:raise ValueError('Delivery token file is not private')
    changes={'platforms.mobile_delivery.extra.profile':'default',
             'platforms.mobile_delivery.extra.token_file':'secrets/mobile-delivery.token',
             'platforms.mobile_delivery.extra.bindings':'{"owner":"profile"}',
             'platforms.mobile_delivery.enabled':'true'}
    with contextlib.redirect_stdout(io.StringIO()):
        for key,value in changes.items():set_config_value(key,value)
    path=state/'config.json';config=json.loads(path.read_text())
    config.setdefault('delivery_tokens',{})['default']=token
    config.setdefault('job_delivery_targets',{})['default']=next(iter(targets))+',mobile_delivery:owner'
    config['profile_creation_enabled']=True
    temp=state/'config.pending.json'
    fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(config,stream,indent=2)
    os.replace(temp,path)
    print('Private mobile delivery binding provisioned; all existing job records unchanged.')


if __name__=='__main__':main()
