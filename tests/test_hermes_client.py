import importlib.util
import httpx
import pytest


def test_private_gateway_explicitly_ignores_environment_proxies(monkeypatch):
    from backend.hermes_client import GatewayClient
    options={}
    class Client:
        def __init__(self,**kwargs):options.update(kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    GatewayClient('http://127.0.0.1:8642','private-secret')
    assert options.get('trust_env',True) is False


@pytest.mark.asyncio
async def test_gateway_client_never_executes_without_verified_contract():
    assert importlib.util.find_spec('backend.hermes_client') is not None, 'Gateway client not implemented'
    from backend.hermes_client import GatewayClient, IntegrationUnavailable
    calls=[]
    async def handle(request):
        calls.append(request)
        return httpx.Response(200,json={})
    client=GatewayClient('http://127.0.0.1:8642','secret',transport=httpx.MockTransport(handle))
    with pytest.raises(IntegrationUnavailable):
        await client.start('s','hello')
    assert calls == []
    await client.close()


@pytest.mark.asyncio
async def test_verified_gateway_uses_run_api_and_keeps_token_server_side():
    from backend.hermes_client import GatewayClient
    calls=[]
    async def handle(request):
        calls.append(request)
        if request.url.path == '/v1/runs':
            return httpx.Response(202,json={'run_id':'r1','status':'started'})
        return httpx.Response(200,json={'items':[]})
    client=GatewayClient('http://127.0.0.1:8642','secret',execution_ready=True,transport=httpx.MockTransport(handle))
    assert (await client.start('s','hello',history=[]))['run_id']=='r1'
    assert calls[0].headers['authorization']=='Bearer secret'
    assert b'"session_id":"s"' in calls[0].content
    await client.close()


@pytest.mark.asyncio
async def test_gateway_stream_parses_events_and_routes_control():
    from backend.hermes_client import GatewayClient, IntegrationUnavailable
    seen=[]
    async def handle(req):
        seen.append((req.method,req.url.path))
        if req.url.path.endswith('/events'):
            return httpx.Response(200,text='event: message.delta\ndata: {"delta":"Hello"}\n\nevent: run.completed\ndata: {"output":"Hello"}\n\n',headers={'Content-Type':'text/event-stream'})
        return httpx.Response(200,json={'status':'stopping'})
    c=GatewayClient('http://localhost:8642','secret',execution_ready=True,transport=httpx.MockTransport(handle))
    assert [event async for event in c.events('r1')] == [{'event':'message.delta','delta':'Hello'},{'event':'run.completed','output':'Hello'}]
    await c.stop('r1')
    assert ('POST','/v1/runs/r1/stop') in seen
    await c.close()


@pytest.mark.parametrize(('method', 'path', 'status', 'expected'), [
    ('GET', '/v1/runs/native-run', 404, 'NativeRunNotFound'),
    ('GET', '/v1/runs/native-run', 503, 'IntegrationUnavailable'),
    ('GET', '/v1/runs/native-run', 401, 'IntegrationUnavailable'),
    ('GET', '/v1/runs/native-run/events', 404, 'IntegrationUnavailable'),
    ('POST', '/v1/runs/native-run', 404, 'IntegrationUnavailable'),
    ('GET', '/v1/runs/native-run', 200, 'IntegrationUnavailable'),
])
@pytest.mark.asyncio
async def test_gateway_types_only_authenticated_native_run_not_found(method, path, status, expected):
    from backend.hermes_client import GatewayClient, IntegrationUnavailable
    seen = []

    async def handle(request):
        seen.append((request.method, request.url.path,
                     request.headers.get('authorization') is not None))
        if status == 200:
            return httpx.Response(status, text='malformed')
        return httpx.Response(status, json={'detail': 'synthetic'})

    client = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(IntegrationUnavailable) as error:
            await client.request(method, path)
        assert error.value.__class__.__name__ == expected
        assert seen == [(method, path, True)]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_gateway_timeout_is_not_classified_as_native_run_not_found():
    from backend.hermes_client import GatewayClient, IntegrationUnavailable

    async def handle(_request):
        raise httpx.ReadTimeout('synthetic timeout')

    client = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(IntegrationUnavailable) as error:
            await client.request('GET', '/v1/runs/native-run')
        assert error.value.__class__ is IntegrationUnavailable
    finally:
        await client.close()


@pytest.mark.parametrize('outcome', ['413', 'timeout', '503'])
@pytest.mark.asyncio
async def test_native_photo_rejection_is_distinct_from_ambiguous_dispatch(outcome):
    from backend.hermes_client import GatewayClient, IntegrationUnavailable, NativeRunRejected

    async def handle(request):
        if outcome == 'timeout':
            raise httpx.ReadTimeout('synthetic timeout')
        return httpx.Response(int(outcome), json={'error': {'code': 'body_too_large'}})

    client = GatewayClient('http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
                           transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(IntegrationUnavailable) as error:
            await client.start('synthetic-session', 'Inspect a photo')
        assert type(error.value) is (NativeRunRejected if outcome == '413' else IntegrationUnavailable)
    finally:
        await client.close()


@pytest.mark.parametrize('capability', ['missing', 'unverified', 'private'])
@pytest.mark.asyncio
async def test_photo_dispatch_requires_versioned_private_native_listener(capability):
    from backend.hermes_client import GatewayClient, NativeRunRejected
    calls = []

    async def handle(request):
        calls.append(request.method)
        if capability == 'unverified':
            raise httpx.ReadTimeout('synthetic capability timeout')
        return httpx.Response(200, json={'mobile_photos': {
            'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
            'max_request_bytes': 20_000_000, 'private_persistence': False}}
            if capability == 'private' else {})

    client = GatewayClient('http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
                           transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(NativeRunRejected):
            await client.start('synthetic-session', 'Inspect photo',
                attachments=[{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}],
                attachment_ids=['0' * 32])
        assert calls == ['GET']
    finally:
        await client.close()


@pytest.mark.parametrize(('status', 'body', 'typed'), [
    (409, {}, False),
    (409, {'error': {'code': 'clarification_conflict'}}, False),
    (503, {}, False),
    (409, {'run_id': 'foreign'}, False),
    (409, {'question_id': 'foreign'}, False),
    (409, {'status': 'answered', 'answer': 'Keep'}, False),
    (409, {'answer': 'Keep'}, False),
    (409, {'accepted': True}, False),
    (409, {'error': {'code': 'unrecognized'}}, False),
    (409, None, True),
])
@pytest.mark.asyncio
async def test_clarification_rejection_requires_positive_bound_evidence(status, body, typed):
    from backend.hermes_client import (
        GatewayClient, IntegrationUnavailable, NativeClarificationRejected)

    reply = {
        'object': 'hermes.run.clarification', 'run_id': 'native-run',
        'question_id': 'a' * 32, 'status': 'rejected',
        'error': {'code': 'clarification_conflict'},
    }
    if body is not None:
        reply = body if body == {} or set(body) == {'error'} else {**reply, **body}
    client = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=reply)))
    try:
        with pytest.raises(IntegrationUnavailable) as error:
            await client.answer_clarification('native-run', 'a' * 32, 'Change', False)
        assert type(error.value) is (NativeClarificationRejected if typed else IntegrationUnavailable)
    finally:
        await client.close()
