"""Hermes Mobile ASGI assembly. Private state lives outside the public document root."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import os
import sqlite3
import json
import subprocess
from urllib.parse import quote

from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers, MutableHeaders

from .auth import AuthService, build_auth_router
from .hermes_client import (GatewayClient, IntegrationUnavailable, PhotoRequestTooLarge,
                           PhotoUnavailableBeforeAdmission)
from .native_catalog import NativeCatalog
from .runs import RunJournal, RunConflict
from .notifications import NotificationService, build_notifications_router
from .orchestration import ClarificationNotSent, Orchestrator, PhotosDisabledBeforeAdmission
from .jobs import JobService, build_jobs_router
from .profiles import ProfileProvisioner, build_profiles_router
from .delivery import build_delivery_router
from .runtime_binding import RuntimeBinding
from .background_delivery import BackgroundDeliveryService
from .request_notifications import RequestNotificationService
from .operational_notifications import OperationalNotificationService

BASE='/hermes/app-api'
FRONTEND_ROOT=Path(__file__).resolve().parents[1]/'frontend'
PUBLIC_ROOTS=(Path('/var/www'), FRONTEND_ROOT)


def private_path(path):
    """Use the same resolved roots as the static mount, including symlink aliases."""
    path=Path(path).resolve()
    if any(path.is_relative_to(root.resolve()) for root in PUBLIC_ROOTS):
        raise ValueError('Private config/state must not live under a public document root')
    return path


CSP="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"


class PhotoResponse(StreamingResponse):
    def __init__(self, descriptor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.descriptor = descriptor

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            descriptor, self.descriptor = self.descriptor, None
            if descriptor is not None:
                os.close(descriptor)


class SecurityMiddleware:
    """Bound ingress before routing/parsing; never drain an oversized upload."""
    def __init__(self, app, origin):
        self.app = app
        self.origin = origin

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                MutableHeaders(scope=message).update({
                    'Content-Security-Policy':CSP, 'X-Content-Type-Options':'nosniff',
                    'X-Frame-Options':'DENY', 'Referrer-Policy':'no-referrer',
                    'Cache-Control':'no-store'})
            await send(message)

        headers = Headers(scope=scope)
        if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and headers.get('origin') != self.origin:
            return await JSONResponse({'detail':'Origin rejected'},403)(scope, receive, secure_send)
        path = scope.get('path', '')
        segments = path.split('/')
        photo_upload = (scope['method'] == 'POST' and len(segments) == 6
                        and segments[:4] == ['', 'hermes', 'app-api', 'sessions']
                        and segments[4] and segments[5] == 'attachments')
        limit = 10 * 1024 * 1024 if photo_upload else 1048576
        # Compare decimal strings without an unbounded int conversion.
        length = headers.get('content-length', '').lstrip('0') or '0'
        if length.isascii() and length.isdigit() and (len(length) > len(str(limit)) or
                (len(length) == len(str(limit)) and length > str(limit))):
            return await JSONResponse({'detail':'Request too large'},413)(scope, receive, secure_send)

        if photo_upload:
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await receive()
                if message['type'] == 'http.request':
                    received += len(message.get('body', b''))
                    if received > limit:
                        raise RequestTooLarge
                return message

            try:
                await self.app(scope, bounded_receive, secure_send)
            except RequestTooLarge:
                return await JSONResponse({'detail':'Photo upload exceeds the 10 MiB input limit'},413)(scope, receive, secure_send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > limit:
                return await JSONResponse({'detail':'Request too large'},413)(scope, receive, secure_send)
            body.extend(chunk)
            if not message.get('more_body', False):
                break

        pending = True
        async def bounded_receive():
            nonlocal pending
            if pending:
                pending = False
                payload = bytes(body)
                body.clear()
                return {'type':'http.request', 'body':payload, 'more_body':False}
            # Preserve real disconnect notifications for streaming responses.
            return await receive()

        await self.app(scope, bounded_receive, secure_send)


class RequestTooLarge(Exception):
    pass


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path.home()/'.local/share/hermes-mobile')
    profiles: dict = field(default_factory=lambda: {'default':Path.home()/'.hermes'})
    origin: str = 'https://lindayi.me'
    rp_id: str = 'lindayi.me'
    bootstrap_secret: str | None = None
    upstream_url: str = 'http://127.0.0.1:8642'
    upstream_token: str = ''
    execution_ready: bool = False
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    gateway_profiles: dict = field(default_factory=dict)
    delivery_tokens: dict = field(default_factory=dict)
    job_delivery_targets: dict = field(default_factory=dict)
    profile_creation_enabled: bool = False
    deployment: str = 'local-development'
    attachment_user_quota_bytes: int = 128 * 1024 * 1024
    attachment_global_quota_bytes: int = 512 * 1024 * 1024
    attachment_min_free_bytes: int = 1024 * 1024 * 1024
    attachment_user_metadata_rows: int | None = None
    attachment_global_metadata_rows: int | None = None
    photos_enabled: bool = True


class SessionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(default='New chat',min_length=1,max_length=200)


MODEL_OWNER_HOME = Path('/home/lindayi/.hermes')


class RunInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: str=Field(min_length=1,max_length=200)
    input: str=Field(min_length=1,max_length=100000)
    idempotency_key: str=Field(min_length=1,max_length=128)
    selection: dict | None = None
    attachments: list[str]=Field(default_factory=list,max_length=4)


def create_app(settings=None, *, gateway_client=None):
    settings=settings or Settings()
    if type(settings.photos_enabled) is not bool:
        raise ValueError('Photo feature setting must be boolean')
    settings.state_dir=private_path(settings.state_dir)
    settings.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    settings.state_dir.chmod(0o700)
    auth=AuthService(settings.state_dir/'auth.sqlite',origin=settings.origin,rp_id=settings.rp_id,bootstrap_secret=settings.bootstrap_secret)
    catalog=NativeCatalog(settings.profiles)
    journal=RunJournal(settings.state_dir/'runs.sqlite')
    from .attachments import AttachmentStore, AttachmentError
    attachments=AttachmentStore(
        journal.path, settings.state_dir/'attachments',
        user_quota_bytes=settings.attachment_user_quota_bytes,
        global_quota_bytes=settings.attachment_global_quota_bytes,
        min_free_bytes=settings.attachment_min_free_bytes,
        user_metadata_rows=settings.attachment_user_metadata_rows,
        global_metadata_rows=settings.attachment_global_metadata_rows)
    gateway=gateway_client or GatewayClient(settings.upstream_url,settings.upstream_token,settings.execution_ready)
    notifications=NotificationService(settings.state_dir/'notifications.sqlite',vapid_private_key=settings.vapid_private_key,vapid_public_key=settings.vapid_public_key,session_validator=auth.is_session_active)
    orchestrator=Orchestrator(journal,gateway,catalog,history_loader=gateway.history,
                              attachments=attachments,photos_enabled=settings.photos_enabled)
    gateways={'default':gateway}
    runtimes={'default':orchestrator}
    for profile,entry in settings.gateway_profiles.items():
        if profile=='default' or profile not in catalog.profiles:
            raise ValueError('Invalid configured profile gateway')
        client=GatewayClient(entry['url'],entry['token'],entry.get('execution_ready',False))
        gateways[profile]=client
        runtimes[profile]=Orchestrator(journal,client,catalog,history_loader=client.history,
                                       attachments=attachments,profile=profile,
                                       photos_enabled=settings.photos_enabled)

    binding=RuntimeBinding(auth.store,catalog.profiles,settings.gateway_profiles)

    def background_owner():
        with auth.store.transaction() as db:
            owners=db.execute("SELECT id,role,profile,status FROM users WHERE role='owner' AND profile='default'").fetchall()
        return owners[0] if len(owners)==1 else None

    background=BackgroundDeliveryService(notifications,journal,catalog,gateway,
                                         owner=background_owner,binding=binding.matches)

    def notification_user(user_id):
        # AuthStore.transaction takes a writer lock; close it before journal/catalog reads.
        with auth.store.transaction() as db:
            user=db.execute('SELECT id,role,profile,status FROM users WHERE id=?',(user_id,)).fetchone()
        if (not user or user['status']!='ready' or user['profile'] not in catalog.profiles
                or user['profile'] not in runtimes or not binding.matches(user)):
            return None
        return user

    def notification_session(user, session_id):
        from contextlib import closing
        from .session_visibility import classification_sql
        current=notification_user(user['id'])
        if not current or current['profile']!=user['profile'] or not isinstance(session_id,str):
            return None
        try:
            # Reuse verified native compression lineage, never a title/history search.
            with closing(catalog._connect(current['profile'])) as db:
                exists=db.execute('SELECT 1 FROM sessions WHERE id=?',(session_id,)).fetchone()
            origin=session_id
            if not exists:
                with closing(journal.connect()) as db:
                    aliases=db.execute('''SELECT DISTINCT a.canonical_session_id FROM run_history_anchors a
                        JOIN runs r ON r.id=a.run_id WHERE r.user_id=? AND r.profile=?
                        AND (r.session_id=? OR a.session_id=?) LIMIT 2''',
                        (current['id'],current['profile'],session_id,session_id)).fetchall()
                if len(aliases)!=1:
                    return None
                origin=aliases[0][0]
            chain,tip=background._lineage(current,origin)
            chain.add(session_id)
            with closing(journal.connect()) as db:
                # Include journal-proven canonical aliases, even after native archival.
                while True:
                    if len(chain)>100:
                        return None
                    marks=','.join('?' for _ in chain)
                    rows=db.execute('''SELECT DISTINCT r.session_id,a.session_id,a.canonical_session_id
                        FROM runs r JOIN run_history_anchors a ON a.run_id=r.id
                        WHERE r.user_id=? AND r.profile=? AND (r.session_id IN ('''+marks+
                        ') OR a.session_id IN ('+marks+') OR a.canonical_session_id IN ('+marks+')) LIMIT 101',
                        (current['id'],current['profile'],*chain,*chain,*chain)).fetchall()
                    if len(rows)>100:
                        return None
                    expanded=chain | {value for row in rows for value in row if value}
                    if expanded==chain:
                        break
                    chain=expanded
                background._fence(db,current,chain)
            with closing(catalog._connect(current['profile'])) as db:
                columns={row[1] for row in db.execute('PRAGMA table_info(sessions)')}
                expression,params=classification_sql(columns)
                eligible=db.execute('SELECT 1 FROM sessions WHERE id=? AND ('+expression+")='chats'",
                                    (tip,*params)).fetchone()
            return tip if eligible else None
        except (KeyError,ValueError,PermissionError,sqlite3.Error):
            return None

    def resolve_presence(user_id,session_id):
        user=notification_user(user_id)
        return notification_session(user,session_id) if user else None

    def valid_presence(user_id,session_id):
        return resolve_presence(user_id,session_id) is not None

    def valid_notification_event(user_id,profile,session_id,category):
        user=notification_user(user_id)
        if not user or (profile is not None and profile!=user['profile']):
            return False
        if category in ('completion','attention'):
            return bool(session_id and notification_session(user,session_id) is not None)
        if session_id is not None:
            return notification_session(user,session_id) is not None
        return category in ('operational','scheduled','test','approval')

    def valid_request_notification(user_id,delivery_id,session_id,category):
        from contextlib import closing
        user=notification_user(user_id)
        if not user or not isinstance(delivery_id,str) or not delivery_id.startswith('request:'):
            return False
        parts=delivery_id.split(':')
        if len(parts)!=3 or parts[2] not in ('terminal','unknown'):
            return False
        with closing(journal.connect()) as db:
            row=db.execute('''SELECT r.user_id,r.profile,r.session_id,r.status FROM runs r
                WHERE r.id=? AND r.user_id=? AND NOT EXISTS
                (SELECT 1 FROM run_stop_intents WHERE run_id=r.id)''',(parts[1],user_id)).fetchone()
        if not row or row['profile']!=user['profile']:
            return False
        expected=('unknown' if parts[2]=='unknown' and category=='attention' else
                  'completed' if parts[2]=='terminal' and category=='completion' else
                  'failed' if parts[2]=='terminal' and category=='attention' else None)
        if row['status']!=expected:
            return False
        canonical=notification_session(user,row['session_id'])
        return bool(canonical and canonical==notification_session(user,session_id))

    notifications.presence_validator=valid_presence
    notifications.request_validator=valid_request_notification
    notifications.presence_resolver=resolve_presence
    notifications.event_validator=valid_notification_event
    request_notifications=RequestNotificationService(notifications,journal,
        resolve_user=notification_user,session_validator=lambda user,sid: notification_session(user,sid)==sid,
        session_resolver=notification_session)

    def operational_owner():
        user=background_owner()
        return notification_user(user['id']) if user else None

    import shutil
    operational_notifications=OperationalNotificationService(notifications,owner=operational_owner,
        disk_free=lambda: shutil.disk_usage(settings.state_dir).free,
        push_worker_error=lambda: getattr(app.state,'push_transport_error',None),
        background_worker_error=lambda: getattr(app.state,'background_worker_error',None),
        deployment_status_path=settings.state_dir.parent/'hermes-mobile-deploy'/'status.json')

    async def background_available():
        from .model_controls import standalone_owner_verified
        owner=background_owner()
        if (not owner or owner['status']!='ready' or not binding.matches(owner)
                or Path(catalog.profiles['default'])!=MODEL_OWNER_HOME
                or not isinstance(gateway,GatewayClient)
                or str(gateway.client.base_url).rstrip('/')!='http://127.0.0.1:18642'):
            return False
        if not await asyncio.to_thread(standalone_owner_verified):
            return False
        current=background_owner()
        if (not current or current['id']!=owner['id'] or current['status']!='ready'
                or not binding.matches(current)):
            return False
        app.state.background_observation_attempted=True
        async with asyncio.timeout(background.timeout):
            result=await gateway.request('GET','/v1/capabilities',timeout=background.timeout)
        capability=result.get('mobile_notifications') if isinstance(result,dict) else None
        return (isinstance(capability,dict) and type(capability.get('version')) is int
                and capability['version']==1 and capability.get('delivery')=='durable-inbox'
                and capability.get('automatic_model_wake') is False)

    def require_binding(user):
        if not binding.matches(user):
            raise HTTPException(409,'Profile runtime activation does not match loaded configuration')

    def runtime_for(user):
        require_binding(user)
        if user['profile'] not in runtimes:
            raise IntegrationUnavailable('No verified gateway for this profile')
        return runtimes[user['profile']]

    def valid_approval(user_id,run_id,request_id,approval_id):
        import time
        from contextlib import closing
        with auth.store.transaction() as db:
            owner=db.execute('SELECT id,role,profile,status FROM users WHERE id=?',(user_id,)).fetchone()
        if (not owner or owner['status']!='ready' or owner['profile'] not in catalog.profiles
                or owner['profile'] not in runtimes or not binding.matches(owner)):
            return False
        with closing(journal.connect()) as db:
            row=db.execute('''SELECT r.status FROM orchestration_approvals a JOIN runs r ON r.id=a.run_id
                WHERE a.id=? AND a.request_id=? AND a.run_id=? AND a.status='pending'
                AND a.expires_at>? AND r.user_id=? AND r.profile=?
                AND r.upstream_id IS NOT NULL''',
                (approval_id,request_id,run_id,time.time(),user_id,owner['profile'])).fetchone()
        if row and row['status']=='unknown':
            return None  # Defer; not permission to ingest, push, or decide.
        return row is not None and row['status']=='waiting_for_approval'

    def valid_recovery(user):
        with auth.store.transaction() as db:
            owner = db.execute('SELECT id,role,profile,status FROM users WHERE id=?', (user['id'],)).fetchone()
        return bool(owner and owner['status'] == 'ready' and owner['profile'] == user['profile']
                    and owner['profile'] in catalog.profiles and binding.matches(owner))

    notifications.approval_validator=valid_approval
    for runtime in runtimes.values():
        runtime.approval_notifier=notifications.ingest_approval
        runtime.approval_validator=valid_approval
        runtime.recovery_validator=valid_recovery

    def delivery_owner(profile,job_id):
        with auth.store.transaction() as db:
            row=db.execute("SELECT id,role,profile,status FROM users WHERE profile=? AND status='ready'",(profile,)).fetchone()
        # Validate before even opening the loaded native cron catalogue.
        if not row or not binding.matches(row):
            return None
        if profile not in catalog.profiles or not any(j['id']==job_id for j in catalog.jobs(profile)['items']):
            return None
        return row['id']

    @asynccontextmanager
    async def lifespan(app):
        journal.recover()
        orchestrator.steering.recover()
        await asyncio.to_thread(attachments.cleanup, limit=256, rescan=True)
        for runtime in runtimes.values():
            runtime.start_recovery()
        stop=asyncio.Event()
        async def drain_push():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(request_notifications.tick)
                    app.state.request_notification_error=False
                except Exception:
                    app.state.request_notification_error=True
                app.state.approval_notification_error=False
                for runtime in runtimes.values():
                    try:
                        await runtime.reconcile_approval_notifications()
                        await asyncio.to_thread(runtime.drain_approval_notifications)
                    except Exception:
                        app.state.approval_notification_error=True
                try:
                    await asyncio.to_thread(notifications.flush)
                    app.state.push_worker_error=app.state.approval_notification_error
                    # Empty, unavailable and pruning-only flushes are unobserved.
                    # The monitor reads durable per-owner sent evidence locally.
                    app.state.push_transport_error=None
                except Exception:
                    # Preserve the aggregate flag, but monitor transport independently.
                    app.state.push_worker_error=True
                    app.state.push_transport_error=True
                app.state.background_observation_attempted=False
                app.state.background_worker_error=None
                try:
                    if await background_available():
                        result=await background.tick()
                        app.state.background_worker_error=False if result.get('status')=='ok' else None
                        try:
                            await asyncio.to_thread(notifications.flush)
                        except Exception:
                            app.state.push_worker_error=True
                            app.state.push_transport_error=True
                except Exception:
                    # Only errors after a trusted existing attempt are observations.
                    # Optional capability absence/unready ownership is not an outage.
                    if app.state.background_observation_attempted:
                        app.state.background_worker_error=True
                try:
                    await asyncio.to_thread(operational_notifications.tick)
                    app.state.operational_notification_error=False
                except Exception:
                    app.state.operational_notification_error=True
                try:
                    await asyncio.to_thread(attachments.cleanup, limit=64, rescan=True)
                    app.state.attachment_cleanup_error=False
                except Exception:
                    app.state.attachment_cleanup_error=True
                try:
                    await asyncio.wait_for(stop.wait(),timeout=15)
                except asyncio.TimeoutError:
                    pass
        worker=asyncio.create_task(drain_push())
        try:
            yield
        finally:
            stop.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            for runtime in runtimes.values():
                await runtime.close()
            for client in gateways.values():
                await client.close()

    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.auth=auth
    app.state.catalog=catalog
    app.state.journal=journal
    app.state.attachments=attachments
    app.state.gateway=gateway
    app.state.settings=settings
    app.state.notifications=notifications
    app.state.background_delivery=background
    app.state.request_notifications=request_notifications
    app.state.operational_notifications=operational_notifications
    app.state.orchestrator=orchestrator
    app.state.runtimes=runtimes

    app.add_middleware(SecurityMiddleware, origin=settings.origin)

    @app.exception_handler(IntegrationUnavailable)
    async def integration_error(request,exc):
        return JSONResponse({'detail':str(exc)},503)

    @app.exception_handler(PhotosDisabledBeforeAdmission)
    async def photos_disabled_error(request,exc):
        return JSONResponse({'detail':str(exc),'code':'photos_disabled_before_admission'},503)

    @app.exception_handler(PhotoUnavailableBeforeAdmission)
    async def photos_unavailable_error(request,exc):
        return JSONResponse({'detail':str(exc),'code':'photos_unavailable_before_admission'},503)

    @app.exception_handler(AttachmentError)
    async def attachment_error(request,exc):
        return JSONResponse({'detail':str(exc),'code':'attachment_error'},exc.status)

    @app.exception_handler(KeyError)
    async def not_found(request,exc):
        return JSONResponse({'detail':'Not found'},404)

    @app.exception_handler(RunConflict)
    async def run_conflict(request,exc):
        return JSONResponse({'detail':str(exc)},409)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request,exc):
        return JSONResponse({'detail':'Native state temporarily unavailable'},503)

    app.include_router(build_auth_router(auth),prefix=BASE)
    app.include_router(build_notifications_router(notifications,auth),prefix=BASE)
    app.include_router(build_profiles_router(ProfileProvisioner(auth,runner=subprocess.run if settings.profile_creation_enabled else None)),prefix=BASE)
    app.include_router(build_jobs_router(JobService(auth,gateways,delivery_targets=settings.job_delivery_targets,runtime_validator=require_binding)),prefix=BASE)
    app.include_router(build_delivery_router(notifications,settings.delivery_tokens,delivery_owner),prefix=BASE)

    def ready_user(user=Depends(auth.require_user)):
        if user['status']!='ready' or user['profile'] not in catalog.profiles:
            raise HTTPException(409,'Profile provisioning is pending; no fallback to owner profile')
        require_binding(user)
        return user

    @app.get(BASE+'/health')
    async def health():
        return {'status':'ok','agent_execution':settings.execution_ready,'deployment':settings.deployment}

    from .session_deletion import SessionDeletion
    deletion = SessionDeletion(journal)

    async def deletion_available(user):
        from .model_controls import standalone_owner_verified
        client = runtime_for(user).gateway
        if (user['role'] != 'owner' or user['profile'] != 'default'
                or Path(catalog.profiles['default']) != MODEL_OWNER_HOME
                or not isinstance(client, GatewayClient)
                or str(client.client.base_url).rstrip('/') != 'http://127.0.0.1:18642'):
            return False
        if not await asyncio.to_thread(standalone_owner_verified):
            return False
        return await deletion.available(client)

    @app.get(BASE+'/sessions')
    async def sessions(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=200),kind:str=Query('chats',pattern='^(chats|all|cron|tests)$'),user=Depends(ready_user)):
        available = await deletion_available(user)
        page = await deletion.read_list(catalog,user,limit,offset,q,kind)
        if available:
            page['deletion_available'] = True
        return page

    @app.get(BASE+'/sessions/{sid}')
    async def session_metadata(sid:str,user=Depends(ready_user)):
        # Exact, profile-bound metadata only; never search a list or fetch a
        # transcript to guess the notification's conversation title.
        from contextlib import closing
        journal.require_session(user['id'],user['profile'],sid)
        def read_metadata():
            with closing(catalog._connect(user['profile'])) as db:
                row=db.execute('SELECT id,title FROM sessions WHERE id=?',(sid,)).fetchone()
                if row is None:
                    raise KeyError(sid)
                return dict(row)
        result=await asyncio.to_thread(read_metadata)
        with auth.authorized_transaction(user) as db:
            current=dict(db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())
            if current['profile'] != user['profile']:
                raise HTTPException(409,'Profile changed during conversation read')
        # Member binding opens its own auth transaction; never nest writers.
        ready_user(current)
        journal.require_session(user['id'],user['profile'],sid)
        return result

    @app.delete(BASE+'/sessions/{sid}')
    async def delete_session(sid:str,request:Request,user=Depends(ready_user)):
        import re
        auth.require_mutation(request,user)
        if user['role'] != 'owner' or user['profile'] != 'default':
            raise HTTPException(403,'Session deletion is owner-only')
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422,'Explicit session deletion confirmation is required')
        if (not isinstance(body, dict) or set(body) != {'confirm'} or body['confirm'] is not True
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', sid) is None):
            raise HTTPException(422,'Explicit confirmation and an exact session ID are required')
        if not await deletion_available(user):
            raise IntegrationUnavailable('Verified native session deletion is unavailable')
        return await deletion.delete(user,sid,runtime_for(user))

    @app.get(BASE+'/sessions/{sid}/deletion')
    async def deletion_receipt(sid:str,user=Depends(ready_user)):
        if user['role'] != 'owner' or user['profile'] != 'default':
            raise HTTPException(403,'Session deletion is owner-only')
        if not await deletion_available(user):
            raise IntegrationUnavailable('Verified native session deletion is unavailable')
        return await deletion.reconcile(user,sid,runtime_for(user).gateway)

    @app.get(BASE+'/sessions/{sid}/model-options')
    async def model_options(sid:str,user=Depends(ready_user)):
        journal.require_session(user['id'],user['profile'],sid)
        catalog.messages(user['profile'],sid,limit=1)
        from .model_controls import options, UNAVAILABLE, standalone_owner_verified
        client=runtime_for(user).gateway
        if (user['role']!='owner' or user['profile']!='default'
                or Path(catalog.profiles['default'])!=MODEL_OWNER_HOME
                or not isinstance(client,GatewayClient)
                or str(client.client.base_url).rstrip('/')!='http://127.0.0.1:18642'):
            return dict(UNAVAILABLE)
        if not await asyncio.to_thread(standalone_owner_verified):
            return dict(UNAVAILABLE)
        result = await options(client)
        journal.require_session(user['id'],user['profile'],sid)
        return result

    @app.get(BASE+'/sessions/{sid}/telemetry')
    async def telemetry(sid:str,user=Depends(ready_user)):
        from .session_telemetry import session_telemetry
        journal.require_session(user['id'],user['profile'],sid)
        result = await asyncio.to_thread(session_telemetry,catalog,journal,user,sid)
        journal.require_session(user['id'],user['profile'],sid)
        return result

    @app.get(BASE+'/sessions/{sid}/messages')
    async def messages(sid:str,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),latest:bool=Query(False),turn_boundary:bool=Query(False),user=Depends(ready_user)):
        from .chat_snapshot import conversation_snapshot
        journal.require_session(user['id'],user['profile'],sid)
        result = await asyncio.to_thread(conversation_snapshot,catalog,journal,user,sid,limit,offset,latest,turn_boundary=turn_boundary)
        journal.require_session(user['id'],user['profile'],sid)
        owners = [item for item in result.get('items', []) if item.get('attachment_ids')]
        owners.extend(run for key in ('run','last_run')
                      if (run := result.get(key)) and run.get('attachment_ids'))
        if owners:
            ids = [attachment_id for owner in owners for attachment_id in owner['attachment_ids']]
            metadata = await asyncio.to_thread(
                attachments.metadata_for_history_batch, user, sid, ids)
            for owner in owners:
                owner['attachments'] = [
                    metadata.get(attachment_id, {'id': attachment_id, 'status': 'expired'})
                    for attachment_id in owner['attachment_ids'][:4]]
        return result

    @app.post(BASE+'/sessions/{sid}/attachments')
    async def upload_photo(sid:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        journal.require_session(user['id'],user['profile'],sid)
        if not settings.photos_enabled:
            raise HTTPException(503,'New photo uploads are disabled; existing photos and text runs remain available.')
        try:
            await asyncio.to_thread(catalog.messages,user['profile'],sid,limit=1)
        except KeyError:
            raise HTTPException(404,'Session not found') from None
        journal.require_session(user['id'],user['profile'],sid)
        metadata=await attachments.upload(
            user,sid,request.headers.get('idempotency-key',''),request.stream())
        metadata['url']=BASE+'/sessions/'+quote(sid,safe='')+'/attachments/'+metadata['id']
        return JSONResponse(metadata,status_code=201)

    @app.get(BASE+'/sessions/{sid}/attachments/{attachment_id}')
    async def read_photo(sid:str,attachment_id:str,user=Depends(ready_user)):
        journal.require_session(user['id'],user['profile'],sid)
        try:
            await asyncio.to_thread(catalog.messages,user['profile'],sid,limit=1)
        except KeyError:
            raise HTTPException(404,'Session not found') from None
        journal.require_session(user['id'],user['profile'],sid)
        descriptor,content_type,size=await attachments._upload_io(
            attachments.open_image,user,sid,attachment_id,
            cancel_result=lambda result: os.close(result[0]))

        async def image_body():
            remaining=size
            while remaining:
                chunk=await attachments._upload_io(os.read,descriptor,min(65536,remaining))
                if not chunk:
                    break
                remaining-=len(chunk)
                yield chunk

        try:
            return PhotoResponse(descriptor,image_body(),media_type=content_type,
                headers={'Content-Length':str(size),'X-Content-Type-Options':'nosniff',
                         'Cache-Control':'no-store'})
        except BaseException:
            os.close(descriptor)
            raise

    @app.delete(BASE+'/sessions/{sid}/attachments/{attachment_id}')
    async def release_photo(sid:str,attachment_id:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        journal.require_session(user['id'],user['profile'],sid)
        await asyncio.to_thread(attachments.release,user,sid,attachment_id)
        return {'released':True}

    @app.get(BASE+'/sessions/{sid}/background')
    def background_results(sid:str,user=Depends(ready_user)):
        try:
            return background.session_items(user,sid)
        except PermissionError:
            raise HTTPException(404,'Background results unavailable') from None

    @app.get(BASE+'/jobs')
    async def jobs(user=Depends(ready_user)):
        return await asyncio.to_thread(catalog.jobs,user['profile'])

    @app.post(BASE+'/sessions')
    async def new_session(body:SessionInput,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        client=runtime_for(user).gateway
        client.require_execution()
        payload={} if body.title=='New chat' else {'title':body.title}
        result=await client.request('POST','/api/sessions',json=payload)
        session=result.get('session',{})
        if not session.get('id'):
            raise IntegrationUnavailable('Native session creation could not be confirmed')
        response={'id':session['id']}
        title=session.get('title')
        if isinstance(title,str) and title.strip():
            response['title']=title
        elif 'title' not in session and body.title!='New chat':
            response['title']=body.title
        return response

    @app.patch(BASE+'/sessions/{sid}')
    async def rename_session(sid:str,body:SessionInput,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        journal.require_session(user['id'],user['profile'],sid)
        catalog.messages(user['profile'],sid,limit=1)
        client=runtime_for(user).gateway
        client.require_execution()
        result=await client.request('PATCH','/api/sessions/'+quote(sid,safe=''),json={'title':body.title})
        session=result.get('session',{})
        if session.get('id')!=sid:
            raise IntegrationUnavailable('Native session update could not be confirmed')
        return {'id':sid,'title':session.get('title')}

    @app.post(BASE+'/runs')
    async def start_run(body:RunInput,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        runtime=runtime_for(user)
        runtime.model_options=model_options
        try:
            run=await runtime.submit(user,body.model_dump(exclude_none=True))
            if run.get('attachment_ids'):
                run['attachments']=await asyncio.to_thread(
                    attachments.metadata_for_history,user,run['session_id'],run['attachment_ids'])
            return run
        except AttachmentError:
            raise
        except RunConflict:
            raise
        except PhotoRequestTooLarge as exc:
            raise HTTPException(413,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get(BASE+'/runs/{rid}')
    async def get_run(rid:str,user=Depends(ready_user)):
        run=await runtime_for(user).refresh(user,rid)
        if run.get('attachment_ids'):
            run['attachments']=await asyncio.to_thread(
                attachments.metadata_for_history, user, run['session_id'], run['attachment_ids'])
        return run

    @app.get(BASE+'/runs/{rid}/controls')
    async def run_controls(rid:str,user=Depends(ready_user)):
        return await runtime_for(user).controls(user,rid)

    @app.get(BASE+'/runs/{rid}/clarifications')
    async def run_clarifications(rid:str,user=Depends(ready_user)):
        return await runtime_for(user).clarifications_for_run(user,rid)

    @app.post(BASE+'/runs/{rid}/clarifications/{question_id}/answer')
    async def answer_clarification(rid:str,question_id:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        try:
            body=await request.json()
            return await runtime_for(user).answer_clarification(user,rid,question_id,body)
        except ClarificationNotSent:
            return JSONResponse(
                {'detail': 'Native clarification controls are unavailable; no answer was sent.',
                 'code': 'clarification_not_sent'}, status_code=503)
        except RunConflict:
            raise
        except KeyError:
            raise HTTPException(404,'Clarification unavailable') from None
        except ValueError as exc:
            raise HTTPException(422,'Invalid clarification answer') from exc

    @app.post(BASE+'/runs/{rid}/steer')
    async def steer_run(rid:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        try:
            body=await request.json()
            return await runtime_for(user).steer(user,rid,body)
        except RunConflict:
            raise
        except ValueError as exc:
            raise HTTPException(422,'Invalid steering request') from exc

    @app.post(BASE+'/runs/{rid}/stop')
    async def stop_run(rid:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        return await runtime_for(user).stop(user,rid)

    @app.get(BASE+'/runs/{rid}/events')
    async def events(rid:str,request:Request,after:int | None=Query(None,ge=0,le=9223372036854775807),user=Depends(ready_user)):
        runtime=runtime_for(user)
        runtime.get(user,rid)
        # An explicit query cursor (including zero) overrides the reconnect header.
        start=after
        if start is None:
            value=request.headers.get('last-event-id','0')
            if (not 1 <= len(value) <= 19 or not value.isascii() or not value.isdigit()
                    or int(value) > 9223372036854775807):
                raise HTTPException(422,'Invalid Last-Event-ID')
            start=int(value)
        async def stream():
            cursor=start
            while auth.is_session_active(user['id'],user['session_id']):
                # Observe terminal state BEFORE reading its tail. A producer can
                # finish while we yield a batch; that batch is not proof of EOF.
                try:
                    run=runtime.get(user,rid)
                    batch=journal.events(user['id'],rid,cursor)
                except RunConflict:
                    return  # A deletion claim closes an already-open stream.
                for item in batch:
                    try:
                        runtime.get(user,rid)
                    except RunConflict:
                        return  # Never drain a buffered pre-deletion content batch.
                    cursor=item['id']
                    data = item['data']
                    if item['name'] in ('tool', 'commentary', 'delta') and isinstance(data, dict):
                        # The journal receipt, not a payload claim or replay time.
                        data = {**data, 'observed_at': item.get('observed_at')}
                    elif item['name'] == 'done' and isinstance(data, dict):
                        # A late background receipt needs the persisted terminal
                        # boundary too; never infer completion from the UI clock.
                        data = {**data, 'updated_at': item.get('observed_at')}
                    yield f"id: {cursor}\nevent: {item['name']}\ndata: {json.dumps(data)}\n\n"
                if run['status'] in ('completed','failed','cancelled','unknown') and not batch:
                    break
                if await request.is_disconnected():
                    break
                if not batch:
                    yield ': keepalive\n\n'
                    await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})

    @app.get(BASE+'/approvals')
    async def approvals(user=Depends(ready_user)):
        return runtime_for(user).approvals(user)

    @app.post(BASE+'/approvals/{aid}/decision')
    async def decide(aid:str,request:Request,user=Depends(ready_user)):
        auth.require_mutation(request,user)
        auth.require_fresh(user)
        body=await request.json()
        if not isinstance(body,dict) or set(body)!={'decision'} or body['decision'] not in ('once','deny'):
            raise HTTPException(422,'Invalid approval decision')
        return await runtime_for(user).decide(user,aid,body['decision'])

    # API routes precede the public-only static mount. No backend/state directory is served.
    app.mount('/hermes',StaticFiles(directory=FRONTEND_ROOT,html=True),name='frontend')
    return app
