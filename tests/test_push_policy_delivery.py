"""Native cron ingress is explicit scheduled, silent/history stay quiet."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.delivery import build_delivery_router
from test_push_policy import policy


def test_cron_ingress_is_explicit_scheduled_profile_bound_and_silent_history_stay_quiet(policy):
    s = policy.service
    app = FastAPI()
    app.include_router(build_delivery_router(s,{'default':'private-token'},lambda profile,job:'alice'))
    client = TestClient(app)
    headers = {'Authorization':'Bearer private-token'}
    body = dict(profile='default',job_id='a'*12,run_id='one',title='Price watch',body='New price available')
    response = client.post('/internal/deliver',json=body,headers=headers)
    assert response.status_code == 200
    assert s.flush()['sent'] == 2
    with s._db() as db:
        assert tuple(db.execute('SELECT category,profile FROM notification_policy').fetchone()) == ('scheduled','default')
    for i, text in enumerate(('', '   ', '[SILENT]')):
        assert client.post('/internal/deliver',json={**body,'run_id':str(i),'body':text},headers=headers).json() == {'silent':True}
    client.post('/internal/deliver',json={**body,'run_id':'history','historical':True},headers=headers)
    assert s.flush()['sent'] == 0
    assert len(s.list_inbox('alice')) == 2
