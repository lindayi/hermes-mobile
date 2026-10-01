"""Existing rename API contract for the compact header; only temporary fixture data."""
import json
import sqlite3
import httpx
from fastapi.testclient import TestClient
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db
from backend.app import create_app, Settings
from backend.hermes_client import GatewayClient


def test_header_rename_uses_native_patch_and_refreshed_inventory(tmp_path):
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db');calls=[]
    async def upstream(request):
        calls.append((request.method,request.url.path))
        assert request.method=='PATCH' and request.url.path=='/api/sessions/wa-1'
        title=json.loads(request.content)['title']
        with sqlite3.connect(home/'state.db') as connection:
            connection.execute('UPDATE sessions SET title=? WHERE id=?',(title,'wa-1'))
        return httpx.Response(200,json={'session':{'id':'wa-1','title':title}})
    gateway=GatewayClient('http://127.0.0.1:8642','fixture-token',execution_ready=True,transport=httpx.MockTransport(upstream))
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=gateway)
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN
        assert client.patch(BASE+'/sessions/wa-1',json={'title':'No access'}).status_code==401
        enroll(client)
        assert client.patch(BASE+'/sessions/missing',json={'title':'Missing'}).status_code==404
        assert client.patch(BASE+'/sessions/wa-1',json={'title':''}).status_code==422
        assert client.patch(BASE+'/sessions/wa-1',json={'title':'x'*201}).status_code==422
        response=client.patch(BASE+'/sessions/wa-1',json={'title':'Header-renamed fixture'})
        assert response.status_code==200,response.text
        assert response.json()=={'id':'wa-1','title':'Header-renamed fixture'}
        inventory=client.get(BASE+'/sessions').json()['items']
        assert next(item for item in inventory if item['id']=='wa-1')['title']=='Header-renamed fixture'
        client.headers['Origin']='https://untrusted.invalid'
        assert client.patch(BASE+'/sessions/wa-1',json={'title':'Rejected'}).status_code==403
        assert calls==[('PATCH','/api/sessions/wa-1')]
