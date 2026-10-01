"""Approval integration uses real HTTP adapter and SQLite, synthetic upstream only."""
import asyncio
import json
import httpx
import pytest

from backend.hermes_client import GatewayClient, IntegrationUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', ['once', 'deny'])
async def test_gateway_approval_forwards_exact_identity_only_after_capability(choice):
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'features': {'run_approval_request_id': True}})
        assert json.loads(request.content) == {'request_id': 'action-1', 'choice': choice}
        return httpx.Response(200, json={'run_id': 'run-1', 'request_id': 'action-1', 'choice': choice, 'resolved': 1})
    gateway = GatewayClient('http://localhost:8642', 'fixture', execution_ready=True, transport=httpx.MockTransport(handler))
    try:
        assert hasattr(gateway, 'approve'), 'safe approval forwarding is missing'
        reply = await gateway.approve('run-1', 'action-1', choice)
        assert reply['request_id'] == 'action-1'
        assert [r.method for r in seen] == ['GET', 'POST']
        assert gateway.action_approvals_ready is True
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('features', [{}, {'run_approval_request_id': False}, {'run_approval_request_id': 'true'}, None])
async def test_unsafe_or_malformed_capability_never_posts(features):
    seen = []
    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json={'features': features})
    gateway = GatewayClient('http://localhost:8642', 'fixture', execution_ready=True, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IntegrationUnavailable):
            await gateway.approve('run', 'action', 'once')
        assert seen == ['GET']
        assert gateway.action_approvals_ready is False
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['network', 'request_id', 'run_id', 'choice', 'resolved', 'malformed'])
async def test_uncertain_or_mismatched_ack_is_never_reported_as_success(failure):
    seen = []
    def handler(request):
        seen.append(request.method)
        if request.method == 'GET':
            return httpx.Response(200, json={'features': {'run_approval_request_id': True}})
        if failure == 'network':
            raise httpx.ReadTimeout('private internals')
        body = {'run_id': 'run', 'request_id': 'action', 'choice': 'once', 'resolved': 1}
        body[failure] = 'wrong'
        return httpx.Response(200, json=[] if failure == 'malformed' else body)
    gateway = GatewayClient('http://localhost:8642', 'fixture', execution_ready=True, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IntegrationUnavailable, match='outcome is unresolved'):
            await gateway.approve('run', 'action', 'once')
        assert seen == ['GET', 'POST']
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('choice,request_id', [('always', 'id'), ('session', 'id'), ('once', ''), ('deny', None)])
async def test_gateway_never_sends_unsafe_decisions(choice, request_id):
    seen = []
    gateway = GatewayClient('http://localhost:8642', 'fixture', execution_ready=True,
        transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json={'features': {'run_approval_request_id': True}}))[1]))
    try:
        with pytest.raises(ValueError):
            await gateway.approve('run', request_id, choice)
        assert not seen
    finally:
        await gateway.close()


from test_orchestration import runtime_at, USER, BODY, until
from backend.runs import RunConflict


async def pending_approval(tmp_path, *, fail=False):
    o, journal, g = runtime_at(tmp_path)
    g.decisions = []
    g.action_approvals_ready = False
    async def require():
        g.require_execution()
        g.action_approvals_ready = True
    async def approve(rid, request_id, choice):
        g.decisions.append((rid, request_id, choice))
        await asyncio.sleep(0.01)
        if fail:
            raise IntegrationUnavailable('Approval outcome is unresolved')
        return {'run_id': rid, 'request_id': request_id, 'choice': choice, 'resolved': 1}
    g.require_action_approvals, g.approve = require, approve
    run = await o.submit(USER, BODY)
    await g.started.wait()
    await g.queue.put({'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'action', 'command': 'exact command'})
    await until(lambda: bool(o.approvals(USER)['items']))
    return o, journal, g, run, o.approvals(USER)['items'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', ['once', 'deny'])
async def test_orchestrator_claims_action_durably_before_single_forward(tmp_path, choice):
    o, j, g, run, item = await pending_approval(tmp_path)
    try:
        assert item['executable'] is True
        results = await asyncio.gather(o.decide(USER, item['id'], choice), o.decide(USER, item['id'], choice), return_exceptions=True)
        assert sum(isinstance(r, RunConflict) for r in results) == 1
        accepted = next(r for r in results if isinstance(r, dict))
        assert accepted['status'] == 'resolved'
        assert accepted['request_id'] == 'action'
        assert g.decisions == [('upstream', 'action', choice)]
        assert o.approvals(USER)['items'] == []
        assert o.get(USER, run['id'])['status'] == 'running'
        with pytest.raises(RunConflict):
            await o.decide(USER, item['id'], choice)
    finally:
        await o.close()
