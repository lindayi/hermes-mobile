"""Narrow profile-owned native cron mutations. GET /jobs remains parent-owned."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from .hermes_client import IntegrationUnavailable
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

JOB_ID = re.compile(r'[a-f0-9]{12}')
PUBLIC_JOB_FIELDS = ('id', 'name', 'schedule', 'prompt', 'enabled', 'state',
                     'next_run_at', 'last_run_at', 'last_status', 'no_agent')


class ConfirmJob(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False


class CreateJob(ConfirmJob):
    name: str = Field(min_length=1, max_length=200)
    schedule: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=5000)

    @field_validator('name', 'schedule', 'prompt')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Must not be blank')
        return value


class UpdateJob(ConfirmJob):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    schedule: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=5000)
    enabled: StrictBool | None = None

    @model_validator(mode='after')
    def valid_changes(self):
        fields = self.model_fields_set - {'confirm'}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError('Supply at least one non-null change')
        if any(isinstance(getattr(self, key), str) and not getattr(self, key).strip() for key in fields):
            raise ValueError('Must not be blank')
        return self


class JobService:
    def __init__(self, auth_service, gateways, delivery_targets=None, runtime_validator=None):
        self.auth = auth_service
        self.gateways = dict(gateways)
        self.runtime_validator = runtime_validator
        # Trusted deployment registry, not request data. Bind only after the
        # profile's real recipient/delivery adapter has passed readiness checks.
        self.delivery_targets = dict(delivery_targets or {})

    def require_gateway(self, request, user, body):
        self.auth.require_mutation(request, user)
        self.auth.require_fresh(user)
        if body.confirm is not True:
            raise HTTPException(409, 'Explicit job confirmation required')
        if user['status'] != 'ready':
            raise HTTPException(503, 'Profile runtime is not ready')
        if self.runtime_validator is not None:
            self.runtime_validator(user)
        gateway = self.gateways.get(user['profile'])
        if gateway is None:
            raise HTTPException(503, 'Profile gateway is not configured')
        try:
            gateway.require_execution()
        except IntegrationUnavailable:
            raise HTTPException(503, 'Profile gateway is not ready') from None
        return gateway

    async def create(self, request, user, body):
        gateway = self.require_gateway(request, user, body)
        self.auth.rate_limit(request)
        target = self.delivery_targets.get(user['profile'])
        if not isinstance(target, str) or not target.strip() or target.strip() in ('local', 'origin', 'all'):
            raise HTTPException(503, 'Profile notification delivery is not configured')
        try:
            result = await gateway.request('POST', '/api/jobs',
                json={**body.model_dump(exclude={'confirm'}), 'deliver': target})
        except IntegrationUnavailable:
            raise HTTPException(503, 'Job mutation unavailable; reconcile native state before retrying') from None
        return self.public_job(result)

    @staticmethod
    def public_job(result):
        job = result.get('job') if isinstance(result, dict) else None
        if not isinstance(job, dict) or not isinstance(job.get('id'), str) or not JOB_ID.fullmatch(job['id']):
            raise HTTPException(503, 'Invalid native job response; reconcile before retrying')
        return {'job': {key: value for key, value in job.items() if key in PUBLIC_JOB_FIELDS}}

    async def modify(self, request, user, job_id, body, method):
        gateway = self.require_gateway(request, user, body)
        if not JOB_ID.fullmatch(job_id):
            raise HTTPException(422, 'Invalid job ID')
        try:
            listing = await gateway.request('GET', '/api/jobs', params={'include_disabled': 'true'})
            jobs = listing.get('jobs') if isinstance(listing, dict) else None
            if not isinstance(jobs, list) or any(not isinstance(job, dict) or not isinstance(job.get('id'), str) for job in jobs):
                raise HTTPException(503, 'Invalid native jobs listing')
            if not any(job['id'] == job_id for job in jobs):
                raise HTTPException(404, 'Job not found')
            with self.auth.authorized_transaction(user):
                self.auth.require_fresh(user)
            gateway.require_execution()
            kwargs = {'json': body.model_dump(exclude={'confirm'}, exclude_unset=True)} if method == 'PATCH' else {}
            result = await gateway.request(method, '/api/jobs/' + job_id, **kwargs)
        except IntegrationUnavailable:
            raise HTTPException(503, 'Job mutation unavailable; reconcile native state before retrying') from None
        if method == 'DELETE':
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise HTTPException(503, 'Deletion unconfirmed; reconcile native state before retrying')
            return {'ok': True}
        public = self.public_job(result)
        if public['job']['id'] != job_id:
            raise HTTPException(503, 'Native job identity mismatch; reconcile before retrying')
        return public


def build_jobs_router(service):
    router = APIRouter()

    @router.post('/jobs')
    async def create(body: CreateJob, request: Request, user=Depends(service.auth.require_user)):
        return await service.create(request, user, body)

    @router.patch('/jobs/{job_id}')
    async def update(job_id: str, body: UpdateJob, request: Request, user=Depends(service.auth.require_user)):
        return await service.modify(request, user, job_id, body, 'PATCH')

    @router.delete('/jobs/{job_id}')
    async def delete(job_id: str, body: ConfirmJob, request: Request, user=Depends(service.auth.require_user)):
        return await service.modify(request, user, job_id, body, 'DELETE')

    return router
