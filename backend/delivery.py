"""Private, profile-bound cron-result ingress. Does not run jobs or send WhatsApp."""
import hmac
import re
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel,ConfigDict,Field


class Delivery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile:str=Field(min_length=1,max_length=64)
    job_id:str=Field(pattern=r'^[a-f0-9]{12}$')
    run_id:str=Field(min_length=1,max_length=200)
    title:str=Field(default='Scheduled update',max_length=200)
    body:str=Field(max_length=100000)
    historical:bool=False


def delivery_payload(profile,metadata,text):
    if (not metadata or not isinstance(metadata.get('run_id'),str)
            or not metadata['run_id'] or not re.fullmatch(r'[a-f0-9]{12}',str(metadata.get('job_id','')))):
        raise ValueError('Native cron delivery lacks stable job/run identity; not safe to fabricate one')
    return Delivery(profile=profile,job_id=metadata['job_id'],run_id=metadata['run_id'],body=text).model_dump()


def build_delivery_router(notifications,tokens,resolve_owner):
    router=APIRouter()
    @router.post('/internal/deliver')
    def ingest(body:Delivery,request:Request):
        expected=tokens.get(body.profile)
        supplied=request.headers.get('authorization','')
        if not expected or not hmac.compare_digest(supplied,'Bearer '+expected):
            raise HTTPException(401,'Delivery authentication required')
        user_id=resolve_owner(body.profile,body.job_id)
        if not user_id:
            raise HTTPException(404,'Delivery binding not found')
        # A successful silent monitor tick is not an inbox notification.
        if not body.body.strip() or body.body.strip()=='[SILENT]':
            return {'silent':True}
        item=notifications.ingest(user_id,body.job_id+':'+body.run_id,body.title,body.body,
                                  historical=body.historical,category='scheduled',profile=body.profile)
        return {'id':item['id'],'status':'stored'}
    return router
