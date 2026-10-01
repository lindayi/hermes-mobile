"""Execute actual staged scheduler, real script jobs and adapters, fake HTTP only.
All stores/config/scripts are under pytest tmp_path, never production profiles.
"""
import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest

from test_delivery_adapter import adapter, mock_http

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'patches/cron-delivery-stage'


def load_staged(name, relative):
    spec = importlib.util.spec_from_file_location(name, STAGE / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def native(tmp_path, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('TERMINAL_CWD', raising=False)
    token = set_hermes_home_override(tmp_path)
    from cron.jobs import use_cron_store
    with use_cron_store(tmp_path):
        scheduler = load_staged('cron.mobile_test_scheduler', 'cron/scheduler.py')
        old = sys.modules.get('tools.send_message_tool')
        load_staged('tools.send_message_tool', 'tools/send_message_tool.py')
        try:
            yield scheduler, tmp_path
        finally:
            if old is not None:
                sys.modules['tools.send_message_tool'] = old
            else:
                sys.modules.pop('tools.send_message_tool', None)
            reset_hermes_home_override(token)


def make_job(home, body='print("unchanged result")'):
    from cron.jobs import create_job
    (home/'scripts').mkdir(exist_ok=True)
    (home/'scripts/watch.py').write_text(body)
    job = create_job('', 'every 1h', name='Test real script', script='watch.py', no_agent=True,
                     deliver='mobile_delivery:owner')
    (home/'delivery.token').write_text('test-private-token')
    (home/'delivery.token').chmod(0o600)
    import yaml
    config = {'cron': {'wrap_response': False}, 'platforms': {'mobile_delivery': {
        'enabled': True, 'extra': {'profile':'default', 'token_file':'delivery.token',
                                 'bindings': {'owner': [job['id']]}}}}}
    (home/'config.yaml').write_text(yaml.safe_dump(config))
    from gateway.config import PlatformConfig
    obj = adapter(PlatformConfig.from_dict(config['platforms']['mobile_delivery']))
    return job, obj


def live_run(scheduler, job, obj):
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    def worker():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()
    thread = threading.Thread(target=worker)
    thread.start()
    ready.wait(5)
    try:
        return scheduler.run_one_job(job, adapters={obj.platform: obj}, loop=loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def test_standalone_execution_preserves_metadata(native, monkeypatch):
    scheduler, home = native
    requests = []
    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'id':'standalone-receipt', 'status':'stored'})
    mock_http(monkeypatch, serve)
    job, obj = make_job(home)
    old = sys.modules.get('tools.send_message_tool')
    try:
        load_staged('tools.send_message_tool', 'tools/send_message_tool.py')
        assert scheduler.run_one_job(job)
    finally:
        if old is not None:
            sys.modules['tools.send_message_tool'] = old
        else:
            sys.modules.pop('tools.send_message_tool', None)
    assert len(requests) == 1, 'Standalone delivery must preserve execution metadata'
    from cron.executions import list_executions
    assert requests[0]['run_id'] == list_executions(job_id=job['id'])[0]['id']


def test_long_result_is_atomic_not_truncated(native, monkeypatch):
    scheduler, home = native
    bodies = []
    mock_http(monkeypatch, lambda request: (
        bodies.append(json.loads(request.content)) or httpx.Response(200,json={'id':'long','status':'stored'})))
    job, obj = make_job(home, 'print("x" * 8000)')
    assert live_run(scheduler,job,obj)
    assert bodies[0]['body'] == 'x'*8000


@pytest.mark.parametrize('body', ['pass', 'print("[SILENT]")', 'print("NO_REPLY")'])
def test_scheduler_suppresses_quiet_success_before_any_delivery(native, monkeypatch, body):
    scheduler, home = native
    mock_http(monkeypatch, lambda request: pytest.fail('Silent tick sent'))
    job, obj = make_job(home, body)
    assert live_run(scheduler, job, obj)


def test_live_to_standalone_fallback_reuses_execution_id(native, monkeypatch):
    scheduler, home = native
    bodies = []
    def serve(request):
        bodies.append(json.loads(request.content))
        if len(bodies) <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json={'id':'fallback', 'status':'stored'})
    mock_http(monkeypatch, serve)
    job, obj = make_job(home)
    assert live_run(scheduler, job, obj)
    assert len(bodies) == 4
    assert all(body == bodies[0] for body in bodies)
    from cron.jobs import get_job
    assert not get_job(job['id']).get('last_delivery_error')


def test_failed_mobile_destination_does_not_block_actual_whatsapp_sender(native, monkeypatch):
    scheduler, home = native
    mobile_calls, whatsapp_calls = [], []
    mock_http(monkeypatch, lambda request: (mobile_calls.append(request) or httpx.Response(503)))
    # Only WhatsApp's HTTP client is replaced, not its actual standalone sender.
    import aiohttp
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {'messageId': 'wa-confirmed'}
    class Network:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            whatsapp_calls.append((url,kwargs['json']))
            return Response()
    monkeypatch.setattr(aiohttp, 'ClientSession', Network)
    job, obj = make_job(home)
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from plugins.platforms.whatsapp.adapter import register
    register(PluginContext(PluginManifest(name='whatsapp-platform'), PluginManager()))
    import yaml
    config = yaml.safe_load((home/'config.yaml').read_text())
    config['platforms']['whatsapp'] = {'enabled':True, 'extra':{'bridge_port':3000}}
    (home/'config.yaml').write_text(yaml.safe_dump(config))
    job['deliver'] = 'mobile_delivery:owner,origin'
    job['origin'] = {'platform':'whatsapp','chat_id':'15555550123@s.whatsapp.net'}
    assert scheduler.run_one_job(job)
    assert len(mobile_calls) == 3
    assert whatsapp_calls == [('http://localhost:3000/send', {
        'chatId':'15555550123@s.whatsapp.net','message':'unchanged result'})]
    from cron.jobs import get_job
    from cron.executions import list_executions
    assert 'mobile_delivery' in get_job(job['id'])['last_delivery_error']
    assert len(list_executions(job_id=job['id'])) == 1


def test_real_scheduler_adapter_ingress_dedup_after_lost_ack(native, monkeypatch):
    scheduler, home = native
    job, obj = make_job(home)
    from fastapi import FastAPI
    from backend.delivery import build_delivery_router
    from backend.notifications import NotificationService
    from test_notifications import subscription
    notices = NotificationService(home/'mobile-inbox.db')
    notices.subscribe('owner','device',subscription())
    app = FastAPI()
    app.include_router(build_delivery_router(notices, {'default':'test-private-token'},
        lambda profile, job_id: 'owner' if profile == 'default' and job_id == job['id'] else None),
        prefix='/hermes/app-api')
    transport = httpx.ASGITransport(app=app)
    attempts = []
    async def network(request):
        attempts.append(json.loads(request.content))
        response = await transport.handle_async_request(request)
        await response.aread()
        if len(attempts) == 1:
            raise httpx.ReadTimeout('ack lost after commit', request=request)
        return response
    mock_http(monkeypatch, network)
    assert live_run(scheduler,job,obj)
    assert len(attempts) == 2
    assert len(notices.list_inbox('owner')) == 1
    assert live_run(scheduler,job,obj)
    assert len(notices.list_inbox('owner')) == 2
    import sqlite3
    with sqlite3.connect(home/'mobile-inbox.db') as db:
        assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 2
    # The mobile adapter never creates a native conversation or injects a user turn.
    assert not (home/'state.db').exists()


def test_real_execution_id_reaches_adapter_and_identical_repeats_are_distinct(native, monkeypatch):
    scheduler, home = native
    requests = []
    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'id': str(len(requests)), 'status':'stored'})
    mock_http(monkeypatch, serve)
    job, obj = make_job(home)
    assert live_run(scheduler, job, obj)
    assert len(requests) == 1, 'Native cron metadata must carry its durable execution id'
    from cron.executions import list_executions
    records = list_executions(job_id=job['id'])
    assert requests[0]['run_id'] == records[0]['id']
    assert requests[0]['job_id'] == job['id']
    # A second actual execution of the same job object gets a new ID; no timestamp/hash dedup.
    assert live_run(scheduler, job, obj)
    assert len(requests) == 2
    assert requests[0]['body'] == requests[1]['body']
    assert requests[0]['run_id'] != requests[1]['run_id']
