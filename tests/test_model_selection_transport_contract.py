"""Installed-native transport characterization; never import/execute providers.

These are passing-baseline contract tests, not evidence of a backend repair.
Only two pure parser functions are compiled from the pinned installed module.
"""
import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.hermes_client import GatewayClient
from test_auth import BASE, ORIGIN, enroll
from test_model_controls import fixture


def native_parser():
    source = Path('/usr/local/lib/hermes-agent/gateway/platforms/api_server.py')
    if not source.is_file():
        pytest.skip('Installed native API contract is unavailable')
    tree = ast.parse(source.read_text())
    names = {'_clean_request_string', '_request_agent_overrides'}
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                      names=[ast.alias(name='annotations')], level=0), *functions], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    return namespace['_request_agent_overrides']


def test_authenticated_selection_reaches_installed_native_parser(tmp_path, monkeypatch):
    import backend.app as app_module
    app, calls = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', app.state.catalog.profiles['default'])
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        catalog = client.get(BASE + '/sessions/wa-1/model-options').json()
        choice = catalog['models'][1]
        body = {'session_id': 'wa-1', 'input': 'transport fixture only',
                'idempotency_key': 'native-parser-contract',
                'selection': {'model': choice['id'], 'provider': choice['provider']}}
        response = client.post(BASE + '/runs', json=body)
        assert response.status_code == 200
        assert client.post(BASE + '/runs', json=body).json()['id'] == response.json()['id']
    posts = [call for call in calls if call[0] == 'POST']
    assert len(posts) == 1
    assert posts[0][1] == '/v1/runs'
    assert native_parser()(posts[0][2], virtual_model='hermes-agent') == {
        'requested_model': choice['id'], 'requested_provider': choice['provider']}


def test_default_transport_omits_native_overrides():
    captured = []
    async def transport(request):
        captured.append(json.loads(request.content))
        assert request.method == 'POST' and request.url.path == '/v1/runs'
        return httpx.Response(200, json={'id': 'fixture-run'})

    async def exercise():
        gateway = GatewayClient('http://127.0.0.1:18642', 'fixture-only',
                                execution_ready=True, transport=httpx.MockTransport(transport))
        try:
            await gateway.start('fixture-session', 'transport fixture only')
        finally:
            await gateway.close()
    asyncio.run(exercise())
    assert len(captured) == 1
    assert 'model' not in captured[0] and 'provider' not in captured[0]
    assert native_parser()(captured[0], virtual_model='hermes-agent') == {}
