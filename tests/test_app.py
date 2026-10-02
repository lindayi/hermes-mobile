import importlib.util
import pytest
from fastapi.testclient import TestClient
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db


def test_app_requires_auth_and_reads_native_sessions_without_mutations(tmp_path):
    assert importlib.util.find_spec('backend.app') is not None, 'Application assembly missing'
    from backend.app import create_app, Settings
    home=tmp_path/'hermes'; home.mkdir(); create_native_db(home/'state.db')
    settings=Settings(state_dir=tmp_path/'app-state', profiles={'default':home}, bootstrap_secret=BOOTSTRAP)
    with TestClient(create_app(settings),base_url=ORIGIN) as client:
        client.headers['Origin']=ORIGIN
        assert client.get(BASE+'/sessions').status_code==401
        enroll(client)
        response=client.get(BASE+'/sessions')
        assert response.status_code==200, response.text
        assert response.json()['total']==2
        assert client.get(BASE+'/sessions/wa-1/messages').json()['items'][0]['content']=='Retained answer'
        assert client.get(BASE+'/sessions/not-found/messages').status_code==404
        assert client.post(BASE+'/runs',json={'session_id':'wa-1','input':'hello','idempotency_key':'key'}).status_code==503
        assert client.get(BASE+'/sessions').headers['cache-control']=='no-store'


def test_security_headers_origin_and_no_open_admin_proxy(tmp_path):
    from backend.app import create_app, Settings
    home=tmp_path/'hermes'; home.mkdir(); create_native_db(home/'state.db')
    with TestClient(create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP)),base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        c.headers['Origin']='https://evil.example'
        assert c.post(BASE+'/runs',json={'session_id':'wa-1','input':'hello','idempotency_key':'key'}).status_code==403
        assert c.get(BASE+'/admin/config').status_code==404
        assert c.get(BASE+'/sessions').headers['x-content-type-options']=='nosniff'
        assert "frame-ancestors 'none'" in c.get(BASE+'/sessions').headers['content-security-policy']


def test_app_serves_only_public_frontend_and_owned_inbox(tmp_path):
    from backend.app import create_app, Settings
    home=tmp_path/'hermes'; home.mkdir(); create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as c:
        page=c.get('/hermes/')
        assert page.status_code==200
        assert 'Hermes' in page.text
        assert c.get('/hermes/backend/auth.py').status_code==404
        assert c.get('/hermes/state/auth.sqlite').status_code==404
        assert c.get(BASE+'/inbox').status_code==401
        c.headers['Origin']=ORIGIN; enroll(c)
        me=c.get(BASE+'/auth/me').json()
        app.state.notifications.ingest(me['user']['id'],'run-result-1','An update','Real stored result')
        assert c.get(BASE+'/inbox').json()['items'][0]['body']=='Real stored result'


def test_app_orchestrates_upstream_once_and_returns_real_result(tmp_path):
    import httpx
    import time
    from backend.app import create_app,Settings
    from backend.hermes_client import GatewayClient
    calls=[]
    async def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/messages'):
            return httpx.Response(200,json={'session_id':'wa-1','data':[{'role':'user','content':'Earlier native message'}]})
        if request.url.path=='/v1/runs':
            return httpx.Response(202,json={'run_id':'upstream-1'})
        if request.url.path.endswith('/events'):
            return httpx.Response(200,text='event: message.delta\ndata: {"delta":"A result"}\n\nevent: run.completed\ndata: {"run_id":"upstream-1","output":"A result"}\n\n')
        return httpx.Response(404)
    gateway=GatewayClient('http://127.0.0.1:8642','test-token',execution_ready=True,transport=httpx.MockTransport(upstream))
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=gateway)
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        body={'session_id':'wa-1','input':'Continue','idempotency_key':'same-key'}
        response=c.post(BASE+'/runs',json=body)
        assert response.status_code==200,response.text
        rid=response.json()['id']
        for _ in range(100):
            result=c.get(BASE+'/runs/'+rid).json()
            if result['status']=='completed':break
            time.sleep(.01)
        assert result['output']=='A result'
        assert c.post(BASE+'/runs',json=body).json()['id']==rid
        assert calls.count('/v1/runs')==1
        events=c.get(BASE+'/runs/'+rid+'/events')
        assert 'event: done' in events.text and 'A result' in events.text


def test_lifespan_drains_push_outbox_without_a_connected_phone(tmp_path):
    import time
    from backend.app import create_app,Settings
    app=create_app(Settings(state_dir=tmp_path/'state'))
    called=[]
    app.state.notifications.flush=lambda:called.append(True)
    with TestClient(app,base_url=ORIGIN):
        for _ in range(100):
            if called:break
            time.sleep(.01)
        assert called, 'Push outbox has no background drain'


def test_control_routes_exist_but_fail_closed_when_not_provisioned(tmp_path):
    from backend.app import create_app,Settings
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        assert c.post(BASE+'/sessions',json={'title':'New chat'}).status_code==503
        assert c.patch(BASE+'/sessions/wa-1',json={'title':'Rename'}).status_code==503
        assert c.post(BASE+'/jobs',json={'name':'Example','schedule':'every 1h','prompt':'Hello','confirm':True}).status_code==503
        assert c.get(BASE+'/members/not-a-member/provision').status_code==404
        assert c.post(BASE+'/internal/deliver',json={'profile':'default','job_id':'a'*12,'run_id':'tick','body':'Result'}).status_code==401


def test_app_wires_verified_notification_destination_into_new_jobs(tmp_path):
    import httpx
    import json
    from backend.app import create_app,Settings
    from backend.hermes_client import GatewayClient
    sent=[]
    async def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'job':{'id':'a'*12,'name':'Test'}})
    gateway=GatewayClient('http://127.0.0.1:8642','test',execution_ready=True,transport=httpx.MockTransport(upstream))
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db')
    settings=Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP,job_delivery_targets={'default':'mobile:default'})
    with TestClient(create_app(settings,gateway_client=gateway),base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        response=c.post(BASE+'/jobs',json={'name':'Test','prompt':'Test prompt','schedule':'every 6h','confirm':True})
        assert response.status_code==200,response.text
        assert sent[0]['deliver']=='mobile:default'


def test_new_chat_leaves_native_title_available_for_auto_naming_and_repeat_creation(tmp_path):
    import httpx,json,sqlite3
    from backend.app import create_app,Settings
    from backend.hermes_client import GatewayClient
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db')
    requests=[]
    async def upstream(request):
        payload=json.loads(request.content);requests.append(payload)
        session_id='native-'+str(len(requests))
        with sqlite3.connect(home/'state.db') as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(session_id,None,'cli',5,6,None))
        return httpx.Response(200,json={'session':{'id':session_id}})
    g=GatewayClient('http://localhost:8642','test',execution_ready=True,transport=httpx.MockTransport(upstream))
    with TestClient(create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=g),base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        created=[c.post(BASE+'/sessions',json={'title':'New chat'}) for _ in range(2)]
        named=c.post(BASE+'/sessions',json={'title':'Explicit title'})
        assert [response.status_code for response in created+[named]]==[200,200,200]
        assert [response.json() for response in created]==[{'id':'native-1'},{'id':'native-2'}]
        assert named.json()=={'id':'native-3','title':'Explicit title'}
        assert requests==[{}, {}, {'title':'Explicit title'}]
        with sqlite3.connect(home/'state.db') as db:
            assert db.execute('SELECT title FROM sessions WHERE id=?',('native-1',)).fetchone()==(None,)
            db.execute('UPDATE sessions SET title=? WHERE id=?',('Automatically titled fixture','native-1'))
        assert c.get(BASE+'/sessions/native-1').json()=={'id':'native-1','title':'Automatically titled fixture'}


@pytest.mark.parametrize('native_title',[None,'','   '])
def test_explicit_unresolved_native_creation_title_does_not_fall_back_to_requested_title(tmp_path,native_title):
    import httpx
    from backend.app import create_app,Settings
    from backend.hermes_client import GatewayClient
    async def upstream(request):
        return httpx.Response(200,json={'session':{'id':'native-untitled','title':native_title}})
    gateway=GatewayClient('http://localhost:8642','test',execution_ready=True,transport=httpx.MockTransport(upstream))
    home=tmp_path/'hermes';home.mkdir();create_native_db(home/'state.db')
    with TestClient(create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=gateway),base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN;enroll(c)
        response=c.post(BASE+'/sessions',json={'title':'Requested title'})
        assert response.status_code==200,response.text
        assert response.json()=={'id':'native-untitled'}
