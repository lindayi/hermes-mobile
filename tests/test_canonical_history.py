import httpx
import pytest
from backend.hermes_client import GatewayClient, IntegrationUnavailable
from test_orchestration import runtime_at, USER, BODY


@pytest.mark.asyncio
async def test_bound_native_resolution_keeps_source_identity_and_canonical_tip():
    def handler(request):
        assert request.url.path == '/api/sessions/old/messages'
        return httpx.Response(200, json={'requested_session_id': 'old', 'session_id': 'tip', 'data': []})
    client = GatewayClient('http://localhost:8642', 'fixture', transport=httpx.MockTransport(handler))
    try:
        context = await client.history('default', 'old')
        assert context['session_id'] == 'old'
        assert context['canonical_session_id'] == 'tip'
        assert context['complete'] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_orchestrator_uses_only_server_derived_tip_without_changing_idempotency(tmp_path):
    o, j, g = runtime_at(tmp_path)
    o.history_loader = lambda profile, sid: {'complete': True, 'profile': profile, 'session_id': sid, 'canonical_session_id': 'tip', 'history': []}
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        assert g.starts[0][0] == 'tip'
        assert run['session_id'] == 'native'
        assert (await o.submit(USER, BODY))['id'] == run['id']
        with pytest.raises(ValueError):
            await o.submit(USER, dict(BODY, canonical_session_id='foreign'))
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['tip', 'source', 'malformed'])
async def test_history_never_combines_pages_from_changed_or_unbound_lineage(change):
    seen = []
    def handler(request):
        seen.append(request.url.params['offset'])
        if len(seen) == 1:
            return httpx.Response(200, json={'requested_session_id': 'old', 'session_id': 'tip', 'data': [{'role': 'user', 'content': 'fixture'}] * 500})
        body = {'requested_session_id': 'foreign' if change == 'source' else 'old', 'session_id': 'next' if change == 'tip' else 'tip', 'data': []}
        return httpx.Response(200, json=[] if change == 'malformed' else body)
    client = GatewayClient('http://localhost:8642', 'fixture', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IntegrationUnavailable):
            await client.history('default', 'old')
        assert seen == ['0', '500']
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('tip', [None, '', {}, 'x' * 201])
async def test_orchestrator_rejects_invalid_server_canonical_id_before_journaling(tmp_path, tip):
    o, j, g = runtime_at(tmp_path)
    o.history_loader = lambda profile, sid: {'complete': True, 'profile': profile, 'session_id': sid, 'canonical_session_id': tip, 'history': []}
    try:
        with pytest.raises(IntegrationUnavailable):
            await o.submit(USER, BODY)
        assert not g.starts
    finally:
        await o.close()
