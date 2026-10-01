import json
import httpx
from fastapi.testclient import TestClient
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db
from backend.app import create_app, Settings
from backend.hermes_client import GatewayClient


def fixture(tmp_path, monkeypatch, responses=None):
    import backend.model_controls as controls
    monkeypatch.setattr(controls,'standalone_owner_verified',lambda:True,raising=False)
    home=tmp_path/'hermes'; home.mkdir(); create_native_db(home/'state.db'); calls=[]
    async def upstream(request):
        calls.append((request.method,request.url.path,json.loads(request.content) if request.content else None))
        if responses and request.url.path in responses:
            return httpx.Response(200, content=json.dumps(responses[request.url.path]), headers={'content-type':'application/json'})
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'hermes-agent'}]})
        if request.url.path=='/v1/capabilities':
            return httpx.Response(200,json={'features':{'model_options':True,'run_submission':True}})
        if request.url.path=='/api/model/options':
            return httpx.Response(200,json={'model':'gpt-6-astra','provider':'copilot','providers':[
                {'slug':'copilot','authenticated':True,'models':['gpt-6-astra','other']},
                {'slug':'moa','authenticated':True,'models':['default']},
                {'slug':'foreign','authenticated':False,'models':['bad']}]})
        if request.url.path.endswith('/messages'):
            return httpx.Response(200,json={'session_id':'wa-1','data':[]})
        if request.method=='POST':
            raise httpx.ReadTimeout('ambiguous fixture dispatch')
        raise AssertionError(request.url.path)
    gateway=GatewayClient('http://127.0.0.1:18642','fixture-token',execution_ready=True,transport=httpx.MockTransport(upstream))
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=gateway)
    return app,calls


import pytest


def test_selection_journal_conflict_gate_and_atomic_rollback(tmp_path):
    from backend.runs import RunJournal, RunConflict
    j=RunJournal(tmp_path/'journal.sqlite')
    selection={'model':'other','provider':'copilot'}
    r,created=j.submit('u','default','s','text','key',selection=selection)
    assert created and j.submit('u','default','s','text','key',selection=dict(selection))[1] is False
    with pytest.raises(RunConflict): j.submit('u','default','s','text','key',selection=None)
    j.finish('u',r['id'],'unknown')
    with pytest.raises(RunConflict): j.submit('u','default','s','text','otherkey',selection=selection)
    j.set_deployment_gate('deploy')
    with pytest.raises(RunConflict): j.submit('u','default','new','text','newkey',selection=selection)
    j.clear_deployment_gate('deploy')
    def broken(): raise RuntimeError('anchor unavailable')
    with pytest.raises(RuntimeError): j.submit('u','default','new','text','newkey',selection=selection,history_anchor=broken)
    with j.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM run_selections').fetchone()[0]==1


def test_selection_csrf_and_capability_gate(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'])
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        c.headers['Origin']='https://evil.invalid'
        body={'session_id':'wa-1','input':'hello','idempotency_key':'key','selection':{'model':'other','provider':'copilot'}}
        assert c.post(BASE+'/runs',json=body).status_code==403
        c.headers['Origin']=ORIGIN
        app.state.gateway.execution_ready=False
        assert c.get(BASE+'/sessions/wa-1/model-options').status_code==503
        assert c.post(BASE+'/runs',json=body).status_code==503
    assert calls==[]


def test_runtime_attestation_denies_changed_service(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    import backend.app as module
    import backend.model_controls as controls
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'],raising=False)
    monkeypatch.setattr(controls,'standalone_owner_verified',lambda:False,raising=False)
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        assert c.get(BASE+'/sessions/wa-1/model-options').json()['available'] is False
    assert calls==[]


def test_invalid_selection_is_422_without_dispatch(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        for selection in [{'model':'other','provider':'copilot','reasoning_effort':'high'}, {'model':'bad','provider':'foreign'}, {'model':'other','provider':'copilot','base_url':'https://evil'}]:
            r=c.post(BASE+'/runs',json={'session_id':'wa-1','input':'hello','idempotency_key':'bad','selection':selection})
            assert r.status_code==422,r.text
    assert not any(p[0]=='POST' for p in calls)


def test_selection_is_durable_and_uncertain_retry_does_not_dispatch(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'],raising=False)
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        body={'session_id':'wa-1','input':'hello','idempotency_key':'selection-1','selection':{'model':'other','provider':'copilot'}}
        r=c.post(BASE+'/runs',json=body)
        assert r.status_code==200,r.text
        rid=r.json()['id']
        assert app.state.journal.get(r.json()['user_id'],rid)['selection']==body['selection']
        assert c.post(BASE+'/runs',json=body).json()['id']==rid
        changed=dict(body,selection={'model':'gpt-6-astra','provider':'copilot'})
        assert c.post(BASE+'/runs',json=changed).status_code==409
    posts=[p for p in calls if p[0]=='POST']
    assert len(posts)==1
    assert posts[0][2]=={'session_id':'wa-1','input':'hello','conversation_history':[],'model':'other','provider':'copilot'}


def test_available_owner_catalog(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'],raising=False)
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        data=c.get(BASE+'/sessions/wa-1/model-options').json()
        assert data['available'] is True
        assert ('GET','/v1/models',None) in calls
        assert data['models']==[{'id':m,'provider':'copilot','label':m,'reasoning_efforts':[]} for m in ['gpt-6-astra','other']]


def test_owner_catalog_is_profile_bound_and_default_effort_only(tmp_path,monkeypatch):
    app,calls=fixture(tmp_path,monkeypatch)
    with TestClient(app,base_url=ORIGIN) as c:
        assert c.get(BASE+'/sessions/wa-1/model-options').status_code==401
        c.headers['Origin']=ORIGIN; enroll(c)
        r=c.get(BASE+'/sessions/wa-1/model-options')
        assert r.status_code==200,r.text
        # Temporary homes are not the proven standalone owner runtime.
        assert r.json()=={'available':False,'models':[],'default':None}
        assert c.get(BASE+'/sessions/missing/model-options').status_code==404
    assert calls==[]


def catalog_with(models):
    return {'provider':'copilot','model':'a','providers':[
        {'slug':'copilot','authenticated':True,'models':models}]}


@pytest.mark.parametrize('path,payload', [
    *[('/v1/capabilities', x) for x in [None, [], 'bad', {'features':None}, {'features':[]}, {'features':'bad'}]],
    *[('/v1/models', x) for x in [None, [], {'data':None}, {'data':{}}, {'data':[{'id':'hermes-agent'},None]}, {'data':[None]}, {'data':[{'id':'hermes-agent'},{'id':'hermes-agent'}]}]],
    *[('/api/model/options', x) for x in [None, [], 'bad', {'provider':'copilot','providers':None}, {'provider':'copilot','providers':{}}, {'provider':'copilot','providers':[None]}, {'provider':'copilot','providers':[{'slug':'copilot','authenticated':True,'models':['a']},None]}]],
    *[('/api/model/options', catalog_with(x)) for x in ['abc', {'a':True}, None, [None], ['a',{}], ['a',1]]],
])
def test_malformed_inventory_blocks_asgi_get_and_forged_post(tmp_path, monkeypatch, path, payload):
    app,calls=fixture(tmp_path,monkeypatch,{path:payload})
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'])
    with TestClient(app,base_url=ORIGIN,raise_server_exceptions=False) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        response=c.get(BASE+'/sessions/wa-1/model-options')
        post=c.post(BASE+'/runs',json={'session_id':'wa-1','input':'hello','idempotency_key':'forged','selection':{'model':'a','provider':'copilot'}})
        assert not any(call[0]=='POST' for call in calls)
        assert response.status_code==200, response.text
        assert response.json()=={'available':False,'models':[],'default':None}
        assert post.status_code in (422,503), post.text


@pytest.mark.parametrize('default', [{'secret':'REVIEW_SENTINEL'}, ['a'], None, 12, 'foreign', 'default', 'hermes-agent', 'a/b', 'x'*201])
def test_default_projection_never_reflects_untrusted_metadata(tmp_path,monkeypatch,default):
    catalog=catalog_with(['a']); catalog['model']=default
    app,calls=fixture(tmp_path,monkeypatch,{'/api/model/options':catalog})
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'])
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        data=c.get(BASE+'/sessions/wa-1/model-options').json()
        assert data['default'] is None
        assert 'REVIEW_SENTINEL' not in json.dumps(data)


@pytest.mark.parametrize('models,expected', [([],[]), (['default','hermes-agent','a/b','x'*201],[]), (['a','a','b','x'*200,'x'*201],['a','b','x'*200])])
def test_inventory_empty_bounds_and_dedup(tmp_path,monkeypatch,models,expected):
    app,calls=fixture(tmp_path,monkeypatch,{'/api/model/options':catalog_with(models)})
    import backend.app as module
    monkeypatch.setattr(module,'MODEL_OWNER_HOME',app.state.catalog.profiles['default'])
    with TestClient(app,base_url=ORIGIN) as c:
        c.headers['Origin']=ORIGIN; enroll(c)
        data=c.get(BASE+'/sessions/wa-1/model-options').json()
        assert [m['id'] for m in data['models']]==expected
        assert data['available']==bool(expected)
        assert data['default']==({'model':'a','provider':'copilot'} if expected else None)
