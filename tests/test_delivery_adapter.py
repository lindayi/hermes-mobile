"""Real installed plugin contract; only HTTP transport is replaced."""
import asyncio
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/usr/local/lib/hermes-agent')
sys.path.insert(0, str(NATIVE))
sys.path.insert(0, str(ROOT / 'hermes-plugin'))


def load_plugin():
    assert (ROOT / 'hermes-plugin/mobile_delivery/__init__.py').exists(), 'Supported platform plugin missing'
    return importlib.import_module('mobile_delivery')


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'delivery.token').write_text('test-private-token')
    (tmp_path / 'delivery.token').chmod(0o600)
    from gateway.config import PlatformConfig
    return PlatformConfig(enabled=True, extra={
        'profile': 'default', 'token_file': 'delivery.token',
        'bindings': {'owner': ['a' * 12]},
    })


def adapter(config):
    plugin = load_plugin()
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from gateway.platform_registry import platform_registry
    ctx = PluginContext(PluginManifest(name='mobile-delivery'), PluginManager())
    plugin.register(ctx)
    entry = platform_registry.get('mobile_delivery')
    assert entry is not None and entry.check_fn()
    return entry.adapter_factory(config)


@pytest.mark.parametrize('chat,metadata', [
    ('other', {'job_id': 'a'*12, 'run_id': 'run'}),
    ('owner', {'job_id': 'b'*12, 'run_id': 'run'}),
    ('owner', {'job_id': 'a'*12}),
    ('owner', {'job_id': 'a'*12, 'run_id': ''}),
    ('owner', None),
])
def test_adapter_fails_closed_before_network_for_missing_identity_or_binding(configured, monkeypatch, chat, metadata):
    mock_http(monkeypatch, lambda request: pytest.fail('Must not send unbound result'))
    result = asyncio.run(adapter(configured).send(chat, 'Result', metadata=metadata))
    assert not result.success and not result.retryable


@pytest.mark.parametrize('failure', [503, 429, 'timeout'])
def test_transient_retry_preserves_identity(configured, monkeypatch, failure):
    bodies = []
    def serve(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            if failure == 'timeout':
                raise httpx.ReadTimeout('unknown receipt', request=request)
            return httpx.Response(failure)
        return httpx.Response(200, json={'id': 'receipt', 'status': 'stored'})
    mock_http(monkeypatch, serve)
    result = asyncio.run(adapter(configured).send('owner', 'same text', metadata={'job_id':'a'*12,'run_id':'durable'}))
    assert result.success and len(bodies) == 2
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize('status,expected_attempts', [(401,1),(404,1),(302,1),(503,3),(429,3)])
def test_http_failure_not_reported_as_delivery(configured, monkeypatch, status, expected_attempts):
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location':'https://evil.invalid'}, json={'id':'not-a-receipt'})
    mock_http(monkeypatch, serve)
    result = asyncio.run(adapter(configured).send('owner', 'body', metadata={'job_id':'a'*12,'run_id':'durable'}))
    assert not result.success and not result.retryable
    assert len(calls) == expected_attempts
    assert 'test-private-token' not in result.error


@pytest.mark.parametrize('content', ['', '  ', '[SILENT]', 'SILENT', '[SILENT]\nNo changes'])
def test_silent_tick_has_no_delivery(configured, monkeypatch, content):
    mock_http(monkeypatch, lambda request: pytest.fail('Silent result sent'))
    result = asyncio.run(adapter(configured).send('owner', content, metadata={'job_id':'a'*12,'run_id':'quiet'}))
    assert result.success and result.raw_response['delivered'] is False


@pytest.mark.parametrize('receipt', [{}, {'id':'fake'}, {'status':'stored','id':''}, {'silent':True}])
def test_unconfirmed_receipt_is_not_success(configured, monkeypatch, receipt):
    mock_http(monkeypatch, lambda request: httpx.Response(200,json=receipt))
    result = asyncio.run(adapter(configured).send('owner','body', metadata={'job_id':'a'*12,'run_id':'run'}))
    assert not result.success and not result.retryable


@pytest.mark.parametrize('change', ['no_profile','no_bindings','escape_token','blank_token','public_token'])
def test_server_configuration_fails_closed(configured, tmp_path, change):
    (tmp_path/'delivery.token').chmod(0o600)
    if change == 'no_profile': configured.extra['profile'] = ''
    if change == 'no_bindings': configured.extra['bindings'] = {}
    if change == 'escape_token': configured.extra['token_file'] = '../delivery.token'
    if change == 'blank_token': (tmp_path/'delivery.token').write_text('  ')
    if change == 'public_token': (tmp_path/'delivery.token').chmod(0o644)
    with pytest.raises(ValueError):
        adapter(configured)


def test_adapter_connection_lifecycle(configured):
    obj = adapter(configured)
    async def run():
        assert await obj.connect()
        assert obj.is_connected
        assert (await obj.get_chat_info('owner'))['type'] == 'dm'
        await obj.disconnect()
        assert not obj.is_connected
    asyncio.run(run())


def test_profile_binding_accepts_new_native_jobs_without_reload(configured, tmp_path, monkeypatch):
    configured.extra['bindings'] = {'owner': 'profile'}
    obj = adapter(configured)
    from cron.jobs import create_job, use_cron_store
    with use_cron_store(tmp_path):
        job = create_job('test', 'every 1h', deliver='local')
    calls = []
    mock_http(monkeypatch, lambda request: (calls.append(json.loads(request.content)) or
        httpx.Response(200,json={'id':'dynamic','status':'stored'})))
    result = asyncio.run(obj.send('owner','new job result',metadata={'job_id':job['id'],'run_id':'new-run'}))
    assert result.success and calls[0]['job_id'] == job['id']


def test_profile_binding_rejects_foreign_and_nonexistent_jobs(configured, tmp_path, monkeypatch):
    configured.extra['bindings'] = {'owner': 'profile'}
    obj = adapter(configured)
    from cron.jobs import create_job, use_cron_store
    with use_cron_store(tmp_path/'other-profile'):
        foreign = create_job('other', 'every 1h', deliver='local')
    mock_http(monkeypatch, lambda request: pytest.fail('Unbound job must not reach bridge'))
    for job_id in [foreign['id'], 'f'*12]:
        result = asyncio.run(obj.send('owner','body',metadata={'job_id':job_id,'run_id':'run'}))
        assert not result.success


def test_standalone_does_not_claim_unsupported_attachments_delivered(configured, monkeypatch):
    adapter(configured)
    mock_http(monkeypatch, lambda request: pytest.fail('Unsupported attachment must fail explicitly'))
    result = asyncio.run(load_plugin().standalone_send(configured, 'owner', 'caption',
        metadata={'job_id':'a'*12,'run_id':'media-run'}, media_files=[('/tmp/report.pdf',False)]))
    assert result.get('error') and not result.get('success')


def mock_http(monkeypatch, handler):
    factory = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: factory(
        **kwargs, transport=httpx.MockTransport(handler)))


def test_registered_adapter_delivers_authenticated_bound_cron_result(configured, monkeypatch):
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 'stored-1', 'status': 'stored'})
    mock_http(monkeypatch, serve)
    obj = adapter(configured)
    async def run():
        assert await obj.connect()
        result = await obj.send('owner', 'A real cron result', metadata={'job_id': 'a'*12, 'run_id': 'execution-1'})
        await obj.disconnect()
        return result
    result = asyncio.run(run())
    assert result.success and result.message_id == 'stored-1'
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'http://127.0.0.1:9120/hermes/app-api/internal/deliver'
    assert request.headers['Authorization'] == 'Bearer test-private-token'
    assert request.headers['Origin'] == 'https://lindayi.me'
    assert json.loads(request.content) == {
        'profile': 'default', 'job_id': 'a'*12, 'run_id': 'execution-1',
        'title': 'Scheduled update', 'body': 'A real cron result', 'historical': False,
    }
