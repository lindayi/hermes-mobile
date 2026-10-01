import importlib.util
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_delivery_ingress_requires_profile_secret_and_deduplicates_run(tmp_path):
    assert importlib.util.find_spec('backend.delivery') is not None, 'Cron delivery ingress missing'
    from backend.delivery import build_delivery_router
    from backend.notifications import NotificationService
    notices=NotificationService(tmp_path/'n.db')
    app=FastAPI()
    app.include_router(build_delivery_router(notices,{'default':'private-token'},lambda profile,job:'owner' if profile=='default' and job=='a'*12 else None))
    client=TestClient(app)
    body={'profile':'default','job_id':'a'*12,'run_id':'stable-run-1','title':'Price watch','body':'Result'}
    assert client.post('/internal/deliver',json=body).status_code==401
    response=client.post('/internal/deliver',json=body,headers={'Authorization':'Bearer private-token'})
    assert response.status_code==200,response.text
    assert client.post('/internal/deliver',json=body,headers={'Authorization':'Bearer private-token'}).json()['id']==response.json()['id']
    assert len(notices.list_inbox('owner'))==1
    assert client.post('/internal/deliver',json={**body,'profile':'member_other'},headers={'Authorization':'Bearer private-token'}).status_code==401
    assert client.post('/internal/deliver',json={**body,'job_id':'b'*12},headers={'Authorization':'Bearer private-token'}).status_code==404


def test_delivery_client_rejects_missing_stable_identity():
    from backend.delivery import delivery_payload
    import pytest
    with pytest.raises(ValueError):
        delivery_payload('default',{'job_id':'a'*12},'Result')
    payload=delivery_payload('default',{'job_id':'a'*12,'run_id':'stable'},'Result')
    assert payload['run_id']=='stable'
    assert payload['body']=='Result'
