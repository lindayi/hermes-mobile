"""Assembled bridge seam; temporary native/profile/auth state only."""
from fastapi.testclient import TestClient
from backend.app import create_app, Settings
from test_auth import enroll, ORIGIN, BASE, BOOTSTRAP
from test_native_catalog import create_native_db


def test_background_route_requires_auth_and_reads_only_bound_existing_session(tmp_path):
    home=tmp_path/'home';home.mkdir();create_native_db(home/'state.db')
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP))
    with TestClient(app,base_url=ORIGIN) as client:
        assert client.get(BASE+'/sessions/wa-1/background').status_code==401
        client.headers['Origin']=ORIGIN;enroll(client)
        reply=client.get(BASE+'/sessions/wa-1/background')
        assert reply.status_code==200,reply.text
        assert reply.json()=={'items':[]}
        assert client.get(BASE+'/sessions/missing/background').status_code==404


def test_existing_lifespan_delivers_without_connected_phone_and_acks_after_receipt(tmp_path, monkeypatch):
    import httpx,json,threading
    from backend.hermes_client import GatewayClient
    from test_background_delivery import envelope
    delivered=threading.Event();calls=[]
    item=envelope(origin_session_id='wa-1',parent_session_id='wa-1')
    async def upstream(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200,json={'mobile_notifications': {
                'version': 1, 'delivery': 'durable-inbox', 'automatic_model_wake': False}})
        if request.url.path.endswith('/claim'):
            return httpx.Response(200,json={'items':[item]})
        if request.url.path.endswith('/ack'):
            body=json.loads(request.content)
            with app.state.notifications._db() as db:
                assert db.execute('SELECT 1 FROM inbox WHERE id=?',(body['receipt_id'],)).fetchone()
            delivered.set()
            return httpx.Response(200,json=dict(status='delivered',event_id=body['event_id'],receipt_id=body['receipt_id']))
        return httpx.Response(404)
    home=tmp_path/'home';home.mkdir();create_native_db(home/'state.db')
    import backend.app as app_module
    import backend.model_controls as controls
    monkeypatch.setattr(app_module,'MODEL_OWNER_HOME',home)
    monkeypatch.setattr(controls,'standalone_owner_verified',lambda: True)
    gateway=GatewayClient('http://127.0.0.1:18642','test-only',transport=httpx.MockTransport(upstream))
    app=create_app(Settings(state_dir=tmp_path/'state',profiles={'default':home},bootstrap_secret=BOOTSTRAP),gateway_client=gateway)
    client=TestClient(app,base_url=ORIGIN);client.headers['Origin']=ORIGIN;enroll(client)
    uid=client.get(BASE+'/auth/me').json()['user']['id']
    run,_=app.state.journal.submit(uid,'default','wa-1','fixture input','fixture-key')
    app.state.journal.set_upstream(uid,run['id'],'native-run')
    app.state.journal.finish(uid,run['id'],'completed',output='fixture done')
    attention=app.state.notifications.ingest(uid,'scheduled-update','Scheduled update','Review this',category='scheduled')
    with client:
        assert delivered.wait(2),'background result never delivered by application lifecycle'
        result=client.get(BASE+'/sessions/wa-1/background').json()['items']
        assert len(result)==1 and result[0]['body']=='Full public report'
        assert result[0]['agent_context_state']=='not_injected'
        page=client.get(BASE+'/inbox').json()
        assert result[0]['id'] not in {row['id'] for row in page['items']}
        assert attention['id'] in {row['id'] for row in page['items']}
        assert page['read_count']==0
        with app.state.notifications._db() as db:
            assert db.execute('SELECT body,read FROM inbox WHERE id=?',(result[0]['id'],)).fetchone()[:]==('Full public report',0)
            assert db.execute('SELECT inbox_id FROM background_receipts').fetchone()[0]==result[0]['id']
            assert db.execute('SELECT count(*) FROM outbox WHERE inbox_id=?',(result[0]['id'],)).fetchone()[0]==0
    assert calls==[('GET','/v1/capabilities'), ('POST','/v1/mobile/notifications/claim'),
                   ('POST','/v1/mobile/notifications/ack')]
