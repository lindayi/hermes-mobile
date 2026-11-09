"""Synthetic async admission and request-local reasoning authority."""
import asyncio
import threading
from types import SimpleNamespace

from aiohttp import web
import pytest
from test_native_run_controls import Base
from backend import native_run_controls as controls


def make_agent(model):
    return SimpleNamespace(model=model, provider='copilot',
        base_url='https://api.githubcopilot.com', api_mode='chat_completions',
        _get_transport=lambda: SimpleNamespace(build_kwargs=lambda **kw: {'model': kw['model']}),
        steer=lambda text: True, run_conversation=lambda *args: {}, clear_interrupt=lambda: True)


class Request:
    def __init__(self, model, effort):
        self.body = {'model': model, 'provider': 'copilot', 'input': 'synthetic',
                     'model_options': {'reasoning_effort': effort}}

    async def json(self):
        return self.body


class AsyncBase(Base):
    async def _handle_runs(self, request):
        body = request.body
        async def create():
            callback = lambda *args: None
            callback._mobile_run_id = body['model']
            return self._create_agent(requested_model=body['model'],
                requested_provider=body['provider'], model_options=body['model_options'],
                tool_progress_callback=callback)
        self.tasks.append(asyncio.create_task(create()))
        return web.json_response({'accepted': True})

    def _create_agent(self, **kwargs):
        return make_agent(kwargs['requested_model'])


def test_catalog_validation_leaves_event_loop_and_binds_parallel_run_tasks(monkeypatch):
    loop_thread = threading.get_ident()
    calls = []
    def validate(model, provider, options):
        calls.append((model, options['reasoning_effort'], threading.get_ident()))
        assert threading.get_ident() != loop_thread, 'catalog I/O must not block native listener'
        return options['reasoning_effort']
    monkeypatch.setattr(controls, '_selected_mobile_reasoning_effort', validate)

    async def check():
        adapter = controls.run_controls_adapter(AsyncBase)()
        adapter.tasks = []
        responses = await asyncio.gather(adapter._handle_runs(Request('model-a', 'low')),
                                         adapter._handle_runs(Request('model-b', 'high')))
        assert [response.status for response in responses] == [200, 200]
        agents = await asyncio.gather(*adapter.tasks)
        assert {agent.model: agent._get_transport().build_kwargs(model=agent.model)['extra_body']['reasoning']
                for agent in agents} == {'model-a': {'effort': 'low'}, 'model-b': {'effort': 'high'}}
        assert sorted((model, effort) for model, effort, _ in calls) == [('model-a', 'low'), ('model-b', 'high')]
        callback = lambda *args: None
        callback._mobile_run_id = 'unbound'
        with pytest.raises(ValueError, match='validated'):
            adapter._create_agent(requested_model='model-a', requested_provider='copilot',
                model_options={'reasoning_effort': 'low'}, tool_progress_callback=callback)
    asyncio.run(check())


def test_rejected_catalog_validation_never_admits_native_run(monkeypatch):
    def validate(*args):
        raise ValueError('unsupported selection')
    monkeypatch.setattr(controls, '_selected_mobile_reasoning_effort', validate)
    async def check():
        adapter = controls.run_controls_adapter(AsyncBase)()
        adapter.tasks = []
        response = await adapter._handle_runs(Request('model-a', 'high'))
        assert response.status == 400
        assert adapter.tasks == []
    asyncio.run(check())


def test_bridge_recognizes_native_reasoning_rejection_before_admission():
    import httpx
    from backend.hermes_client import GatewayClient, NativeRunRejected
    async def check():
        def transport(request):
            return httpx.Response(400, json={'error': {'code': 'model_selection_rejected'}})
        client = GatewayClient('http://127.0.0.1:18642', 'synthetic', execution_ready=True,
                               transport=httpx.MockTransport(transport))
        try:
            with pytest.raises(NativeRunRejected, match='reasoning'):
                await client.start('synthetic', 'text', model='model-a', provider='copilot',
                                   reasoning_effort='high')
        finally:
            await client.close()
    asyncio.run(check())
