"""Actual native plugin/preflight probe, fresh HOME before any native imports."""
import json
import os
from pathlib import Path
import sys
home=Path(sys.argv[1]);mode=sys.argv[2]
root=Path(__file__).resolve().parents[1]
os.environ.clear()
os.environ.update(HOME=str(home),HERMES_HOME=str(home),PATH='/usr/local/bin:/usr/bin:/bin',LANG='C.UTF-8')
home.mkdir(parents=True,exist_ok=True)
(home/'delivery.token').write_text('test-private-token');(home/'delivery.token').chmod(0o600)
config={'platforms':{'mobile_delivery':{'enabled':mode!='disabled','extra':{
    'profile':'default','token_file':'delivery.token','bindings':{'owner':['a'*12]}}}}}
(home/'config.yaml').write_text(json.dumps(config));(home/'config.yaml').chmod(0o600)
sys.path[:0]=['/usr/local/lib/hermes-agent',str(root/'hermes-plugin')]
from hermes_cli.plugins import discover_plugins,get_plugin_manager,PluginContext,PluginManifest
discover_plugins()
import mobile_delivery
mobile_delivery.register(PluginContext(PluginManifest(name='mobile-delivery'),get_plugin_manager()))
from gateway.platform_registry import platform_registry
from cron.scheduler import _preflight_check_delivery,_resolve_delivery_targets,_get_home_target_chat_id
job={'id':'a'*12,'deliver':'origin,mobile_delivery:owner',
     'origin':{'platform':'whatsapp','chat_id':'test-whatsapp-origin'}}
if mode=='unknown':job['deliver']='origin,not_a_real_platform:owner'
reason=_preflight_check_delivery(job)
targets=_resolve_delivery_targets(job) if reason is None else []
print(json.dumps({'reason':reason,'cron_env':platform_registry.get('mobile_delivery').cron_deliver_env_var,
                  'targets':[(t['platform'],t['chat_id']) for t in targets],
                  'implicit_home':_get_home_target_chat_id('mobile_delivery')}))
