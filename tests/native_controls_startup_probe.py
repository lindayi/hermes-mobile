"""Isolated real native HTTP startup; no agent/model construction or user state."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='hermes-native-startup-') as d:
    home=Path(d)
    # Establish the isolated home before importing any installed native modules.
    os.environ.clear()
    os.environ.update(HOME=d,HERMES_HOME=d,PATH='/usr/local/bin:/usr/bin:/bin',LANG='C.UTF-8')
    sys.path.insert(0,str(root))
    from backend import native_controls_service as service
    service.OWNER_HOME=home
    key='isolated-native-http-key'
    config=home/'config.json';config.write_text(json.dumps({'upstream_token':key,'state_dir':str(home/'app-state')}));config.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    service.listener_settings=lambda _:dict(host='127.0.0.1',port=port,key=key)
    sys.path.insert(0,'/usr/local/lib/hermes-agent')
    from gateway.platforms.api_server import APIServerAdapter
    original=APIServerAdapter.connect
    async def check():
        ready=asyncio.Event(); holder={}
        async def connected(self):
            result=await original(self)
            if result:holder['adapter']=self;ready.set()
            return result
        APIServerAdapter.connect=connected
        task=asyncio.create_task(service.serve(config))
        try:
            await asyncio.wait_for(ready.wait(),15)
            import aiohttp
            async with aiohttp.ClientSession() as client:
                url=f'http://127.0.0.1:{port}'
                async with client.get(url+'/v1/capabilities') as response:
                    assert response.status==401
                headers={'Authorization':'Bearer '+key}
                async with client.get(url+'/v1/capabilities',headers=headers) as response:
                    assert response.status==200
                    data=await response.json()
                    assert data['mobile_run_controls']==dict(version=1,steering=True,live_commentary=True,
                                                             clarifications=True)
                    assert data['mobile_native_maintenance']==dict(version=1,scope='dedicated-listener',atomic_drain=False)
                async with client.get(url+'/health/detailed',headers=headers) as response:
                    data=await response.json()
                    assert data['native_maintenance']['status']=='ok', data.get('native_maintenance')
                    assert not any(data['native_maintenance']['work'].values())
                from types import SimpleNamespace
                from tools import approval
                adapter=holder['adapter'];adapter._set_run_status('fixture-approval','waiting_for_approval')
                adapter._run_approval_sessions['fixture-approval']='isolated-approval-session'
                with approval._lock:
                    approval._gateway_queues['isolated-approval-session']=[SimpleNamespace(data={'request_id':'approval-key','command':'PRIVATE SYNTHETIC SENTINEL'})]
                async with client.get(url+'/v1/runs/fixture-approval',headers=headers) as response:
                    data=await response.json()
                    assert data.get('pending_approvals')==[{'run_id':'fixture-approval','request_id':'approval-key'}]
                    assert 'PRIVATE SYNTHETIC SENTINEL' not in json.dumps(data)
                with approval._lock:
                    approval._gateway_queues.pop('isolated-approval-session')
                async with client.get(url+'/v1/runs/fixture-approval',headers=headers) as response:
                    assert (await response.json())['pending_approvals']==[]
                async with client.post(url+'/v1/runs/missing/steer',headers=headers,json={'input':'fixture','idempotency_key':'fixture-key'}) as response:
                    assert response.status==404
                captured={}
                class CapturingAgent:
                    def run_conversation(self, user_message, conversation_history, task_id):
                        captured.update(user_message=user_message,history=conversation_history)
                        return {'final_response':'synthetic-no-model'}
                adapter=holder['adapter']
                adapter._create_agent=lambda **kwargs: CapturingAgent()
                encoded_image='A'*((2*1024*1024+2)//3*4-1)+'='
                image_part={'type':'image_url','image_url':{
                    'url':'data:image/jpeg;base64,'+encoded_image}}
                payload={
                    'session_id':'photo-native-synthetic',
                    'input':[{'role':'user','content':[
                        {'type':'text','text':'Inspect four synthetic photos.'},
                        image_part,image_part,image_part,image_part,
                    ]}],
                    'conversation_history':[],
                    'mobile_attachment_ids':[f'{index:032x}' for index in range(4)],
                }
                import base64
                assert len(base64.b64decode(encoded_image))==2*1024*1024
                request_size=len(json.dumps(payload).encode())
                from gateway.platforms.api_server import MAX_REQUEST_BYTES
                assert 10_000_000<request_size<MAX_REQUEST_BYTES
                async with client.post(url+'/v1/runs',headers=headers,json=payload) as response:
                    assert response.status==202
                    run_id=(await response.json())['run_id']
                for _ in range(100):
                    if captured:
                        break
                    await asyncio.sleep(.05)
                assert captured['user_message'][1]['image_url']['url']==image_part['image_url']['url']
                assert len(captured['user_message'])==5
            os.kill(os.getpid(),signal.SIGTERM)
            await asyncio.wait_for(task,10)
            print(json.dumps({'native_http_startup':True,'anonymous_denied':True,'versioned_controls':True,'missing_run_rejected':True,'models_invoked':0}))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            APIServerAdapter.connect=original
    asyncio.run(check())
