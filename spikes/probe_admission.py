"""No-model local probe of native request admission under cap=1."""
import asyncio
import os
from pathlib import Path
import socket
import sys
import tempfile
os.environ['HERMES_HOME']=tempfile.mkdtemp(prefix='hermes-admission-probe-')
sys.path.insert(0,'/usr/local/lib/hermes-agent')
from gateway.platforms import api_server as module
from gateway.config import PlatformConfig
import aiohttp

async def main():
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    a=module.APIServerAdapter(PlatformConfig(enabled=True,extra={'host':'127.0.0.1','port':port,'key':'test-only-admission-key'}))
    a._max_concurrent_runs=1
    original=a._concurrency_limited_response
    def capture():
        print('pending',a._pending_agent_requests,'inflight',a._inflight_agent_runs,'tasks',len(a._active_run_tasks),'reservation',module._api_agent_request_reservation.get())
        return original()
    a._concurrency_limited_response=capture
    await a.connect()
    try:
        async with aiohttp.ClientSession() as c:
            async with c.post(f'http://127.0.0.1:{port}/v1/runs',json={'input':''},headers={'Authorization':'Bearer test-only-admission-key'}) as r:
                print(r.status,await r.text())
                assert r.status==400,'Current request counted against its own only slot'
    finally:await a.disconnect()

asyncio.run(main())
