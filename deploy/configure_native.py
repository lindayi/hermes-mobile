"""Stage loopback API settings with Hermes's supported config writer. No restart."""
import json
import secrets


def configuration_changes(config, existing_key=None):
    token=existing_key or secrets.token_urlsafe(48)
    tools=config.get('platform_toolsets',{}).get('cli',[])
    if not isinstance(tools,list) or not tools:
        raise ValueError('Explicit CLI toolset required before enabling remote agent runtime')
    changes={'gateway.api_server.enabled':'false','gateway.api_server.host':'127.0.0.1',
             'gateway.api_server.port':'8642','gateway.api_server.max_concurrent_runs':'1',
             'platform_toolsets.api_server':json.dumps(tools),'API_SERVER_KEY':token}
    return changes,token


def main():
    import sys, os, shutil, contextlib, io
    from pathlib import Path
    from datetime import datetime
    sys.path.insert(0,'/usr/local/lib/hermes-agent')
    from hermes_cli.config import load_config, set_config_value
    from dotenv import dotenv_values
    home=Path('/home/lindayi/.hermes')
    state=Path('/home/lindayi/.local/share/hermes-mobile-live')
    if os.getuid()!=home.stat().st_uid:
        raise PermissionError('Run as lindayi, not root')
    app_path=state/'config.json'
    app=json.loads(app_path.read_text())
    env=dotenv_values(home/'.env')
    if env.get('API_SERVER_ENABLED','').lower() in ('false','0','no'):
        raise ValueError('Existing API_SERVER_ENABLED override needs explicit reconciliation')
    changes,token=configuration_changes(load_config(),env.get('API_SERVER_KEY'))
    backup=state/('before-native-api-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    for filename in ('config.yaml','.env'):
        shutil.copy2(home/filename,backup/filename)
        (backup/filename).chmod(0o600)
    # The writer may print settings; keep the entire output private and never log credentials.
    with contextlib.redirect_stdout(io.StringIO()):
        for key,value in changes.items():set_config_value(key,value)
    app['upstream_url']='http://127.0.0.1:18642'
    app['upstream_token']=token
    app['execution_ready']=False
    temp=state/'config.pending.json'
    fd=os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(app,stream,indent=2)
    os.replace(temp,app_path)
    print('Loopback API settings staged; app execution remains off until live verification.')
    print('Private settings backup:',backup)


if __name__=='__main__':main()
