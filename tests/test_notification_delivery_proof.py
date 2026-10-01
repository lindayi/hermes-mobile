"""Promotion proof rejects count-only or half-acknowledged delivery evidence."""
import hashlib,json
import pytest


def fixture():
    event={'type':'async_delegation','delegation_id':'deleg_fixture','origin_session_id':'chat','session_key':'run','summary':'Public result'}
    result={'summary':'Public result','private':'retained but not exposed'}
    canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    sha=lambda value:hashlib.sha256(canonical(value).encode()).hexdigest()
    key='async:deleg_fixture';digest=sha(event)
    manifest={'complete':True,'expected_count':1,'captured_count':1,'records':[{'event_id':key,'payload_sha256':digest,'result_sha256':sha(result)}]}
    native={'pending':0,'quarantined':0,'conflicts':0,'active_workers':0,'shutdown_publications':0,'foreign':2,'foreign_retained':2,'delivered':1}
    records=[{'event_id':key,'event_json':canonical(event),'result_json':canonical(result),'payload_sha256':digest,'historical':1,'route':'owned','state':'delivered','receipt_id':'receipt'}]
    receipts=[{'event_id':key,'scope':'scope','digest':digest,'event_json':canonical(event),'user_id':'owner','origin':'chat','inbox_id':'receipt','acknowledged':1}]
    inbox=[{'id':'receipt','user_id':'owner','title':'Background result','body':'Public result','session_id':'chat'}]
    return dict(manifest=manifest,native_status=native,native_records=records,receipts=receipts,inbox=inbox,owner_id='owner',scope='scope')


def test_exact_payload_receipt_and_ack_are_required_not_aggregate_counts():
    from deploy.notification_delivery_proof import verify_delivery
    data=fixture()
    assert verify_delivery(**data)=={'status':'delivered','verified_count':1}
    data['receipts']=[]
    with pytest.raises(ValueError):verify_delivery(**data)


@pytest.mark.parametrize('section,key,value',[
    ('native_status','pending',1),('native_status','quarantined',1),
    ('native_status','conflicts',1),('native_status','active_workers',1),
    ('native_status','shutdown_publications',1),
    ('native_status','pending',False),('native_status','foreign_retained',1),
    ('native_status','delivered',0),('manifest','complete',False),
    ('manifest','expected_count',True),('manifest','captured_count',0)])
def test_incomplete_or_untyped_status_never_promotes(section,key,value):
    from deploy.notification_delivery_proof import verify_delivery
    data=fixture();data[section][key]=value
    with pytest.raises(ValueError):verify_delivery(**data)


@pytest.mark.parametrize('section,key,value',[
    ('native_records','state','pending'),('native_records','route','foreign'),
    ('native_records','historical',0),('native_records','receipt_id','wrong'),
    ('native_records','event_json','{}'),('native_records','result_json','{}'),
    ('native_records','payload_sha256','0'*64),('receipts','acknowledged',0),
    ('receipts','user_id','other'),('receipts','scope','wrong'),
    ('receipts','digest','0'*64),('receipts','origin','wrong'),
    ('receipts','event_json','{}'),('inbox','user_id','other'),
    ('inbox','body','Different result'),('inbox','title','Wrong kind')])
def test_each_link_and_payload_is_bound_to_the_reviewed_delivery(section,key,value):
    from deploy.notification_delivery_proof import verify_delivery
    data=fixture();data[section][0][key]=value
    with pytest.raises(ValueError):verify_delivery(**data)


def test_duplicate_manifest_and_missing_persistence_are_not_delivery():
    from deploy.notification_delivery_proof import verify_delivery
    data=fixture();data['manifest']['records']*=2
    data['manifest'].update(expected_count=2,captured_count=2);data['native_status']['delivered']=2
    with pytest.raises(ValueError):verify_delivery(**data)
    for section in ('native_records','inbox'):
        data=fixture();data[section]=[]
        with pytest.raises(ValueError):verify_delivery(**data)


def test_proof_accepts_real_outbox_bridge_http_and_sqlite_receipt_chain(tmp_path):
    import asyncio,httpx
    from fastapi import FastAPI,Request
    from backend.native_notifications import NotificationOutbox,canonical
    from backend.background_delivery import BackgroundDeliveryService
    from backend.hermes_client import GatewayClient
    from deploy.notification_delivery_proof import verify_delivery
    from test_background_delivery import Fixture
    f=Fixture(tmp_path)
    event=f.items[0]['event'];result={'summary':'Private retained full result'}
    box=NotificationOutbox(tmp_path/'native-notifications.sqlite')
    event_id=box.capture(event,result,route='owned',historical=True,provenance='isolated fixture')
    app=FastAPI()
    @app.post('/v1/mobile/notifications/claim')
    async def claim(request:Request):
        assert request.headers['authorization']=='Bearer test-only'
        return {'items':box.claim((await request.json())['limit'])}
    @app.post('/v1/mobile/notifications/ack')
    async def ack(request:Request):
        return box.ack(**(await request.json()))
    gateway=GatewayClient('http://127.0.0.1','test-only',transport=httpx.ASGITransport(app=app))
    service=BackgroundDeliveryService(f.notifications,f.journal,f.catalog,gateway,owner=lambda:f.user,binding=lambda _:True)
    async def run():
        try:assert await service.tick()=={'status':'ok','rejected':0}
        finally:await gateway.close();await f.gateway.close()
    asyncio.run(run())
    with box.transaction() as db:native_records=[dict(row) for row in db.execute('SELECT * FROM notification_outbox')]
    with f.notifications._db() as db:
        receipts=[dict(row) for row in db.execute('SELECT * FROM background_receipts')]
        inbox=[dict(row) for row in db.execute('SELECT * FROM inbox')]
    manifest={'complete':True,'expected_count':1,'captured_count':1,'records':[{'event_id':event_id,'payload_sha256':hashlib.sha256(canonical(event).encode()).hexdigest(),'result_sha256':hashlib.sha256(canonical(result).encode()).hexdigest()}]}
    assert verify_delivery(manifest=manifest,native_status={**box.status(),'active_workers':0,'shutdown_publications':0},native_records=native_records,receipts=receipts,inbox=inbox,owner_id='owner',scope=service.scope)=={'status':'delivered','verified_count':1}
