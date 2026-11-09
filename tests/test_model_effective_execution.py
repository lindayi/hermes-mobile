"""Offline execution through authenticated mobile admission and pinned /v1/runs.

Only credential/catalog discovery and provider HTTP are synthetic. The native
handler, factory, AIAgent constructor, conversation loop and fallback stay real.
This covers chat-completions, not live Copilot auth or the Responses API.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/usr/local/lib/hermes-agent')
NATIVE_PYTHON = NATIVE / 'venv/bin/python'
SESSION = 'synthetic-model-session'
KEY = 'synthetic-execution-key-not-a-credential'
MODEL_A, MODEL_B = 'synthetic-model-a', 'synthetic-model-b'
DEFAULT, FALLBACK = 'synthetic-default', 'synthetic-fallback'


@contextmanager
def native_execution(tmp_path, *, failure=False, stale=False, remove_lock=False):
    if not NATIVE_PYTHON.is_file():
        import pytest
        pytest.skip('Pinned native runtime is unavailable')
    home = tmp_path / 'native'
    home.mkdir()
    # No inherited credentials, native home, project context, or model cache.
    env = {
        'HOME': str(home), 'HERMES_HOME': str(home), 'TMPDIR': str(home),
        'XDG_CACHE_HOME': str(home / 'cache'), 'LANG': 'C.UTF-8',
        'PATH': '/usr/local/bin:/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': f'{ROOT}:{NATIVE}',
        'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
        'HERMES_TEST_RUN_ID': os.environ.get('HERMES_TEST_RUN_ID', ''),
    }
    args = [str(NATIVE_PYTHON), str(Path(__file__).resolve()),
            str(int(failure)), str(int(stale)), str(int(remove_lock))]
    log = tmp_path / 'native.log'
    with log.open('w') as output:
        process = subprocess.Popen(args, cwd=home, env=env, stdout=output, stderr=output)
        try:
            ready = home / 'ready.json'
            deadline = time.monotonic() + 40
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert ready.exists(), log.read_text()
            yield home, json.loads(ready.read_text())['url']
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def exercise_mobile(tmp_path, monkeypatch, *, selections, failure=False, stale=False,
                    remove_lock=False):
    from fastapi.testclient import TestClient
    from backend.app import Settings, create_app
    from backend.hermes_client import GatewayClient
    import backend.app as app_module
    import backend.model_controls as controls
    from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll

    with native_execution(tmp_path, failure=failure, stale=stale,
                          remove_lock=remove_lock) as (home, url):
        monkeypatch.setattr(controls, 'standalone_owner_verified', lambda: True)
        monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', home)
        import httpx
        # Preserve the bridge's fixed owner URL check, but send every request to
        # this disposable listener. Nothing connects to the installed service.
        class IsolatedListener(httpx.AsyncHTTPTransport):
            async def handle_async_request(self, request):
                request.url = request.url.copy_with(port=httpx.URL(url).port)
                return await super().handle_async_request(request)
        app = create_app(Settings(state_dir=tmp_path / 'mobile', profiles={'default': home},
                                  bootstrap_secret=BOOTSTRAP),
                         gateway_client=GatewayClient('http://127.0.0.1:18642', KEY,
                            execution_ready=True, transport=IsolatedListener()))
        results = []
        with TestClient(app, base_url=ORIGIN) as client:
            client.headers['Origin'] = ORIGIN
            assert client.get(BASE + f'/sessions/{SESSION}/model-options').status_code == 401
            enroll(client)
            catalog = client.get(BASE + f'/sessions/{SESSION}/model-options')
            assert catalog.status_code == 200 and catalog.json()['available'], catalog.text
            for index, selection in enumerate(selections):
                body = {'session_id': SESSION, 'input': 'Synthetic execution fixture',
                        'idempotency_key': f'synthetic-turn-{index}'}
                if selection is not None:
                    body['selection'] = selection
                response = client.post(BASE + '/runs', json=body)
                assert response.status_code == 200, response.text
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    result = client.get(BASE + '/runs/' + response.json()['id']).json()
                    if result['status'] in {'completed', 'failed'}:
                        break
                    time.sleep(.05)
                assert result['status'] in {'completed', 'failed'}, result
                native_status = httpx.get(url + '/v1/runs/' + result['upstream_id'],
                    headers={'Authorization': 'Bearer ' + KEY}).json()
                assert native_status['status'] == result['status'], native_status
                if result['status'] == 'failed':
                    assert native_status.get('error'), native_status
                results.append(result)
        captures = json.loads((home / 'wire.json').read_text())
    return results, captures


def selection(model, effort=None):
    value = {'model': model, 'provider': 'copilot'}
    if effort is not None:
        value['reasoning_effort'] = effort
    return value


def test_pinned_factory_executes_next_turn_model_and_effort(tmp_path, monkeypatch):
    results, wire = exercise_mobile(tmp_path, monkeypatch, stale=True,
        selections=[selection(MODEL_A, 'low'), selection(MODEL_B, 'high')])
    assert [r['status'] for r in results] == ['completed', 'completed'], results
    assert [(r['model'], r.get('reasoning')) for r in wire] == [
        (MODEL_A, {'effort': 'low'}), (MODEL_B, {'effort': 'high'})]


def test_explicit_failure_never_executes_configured_fallback(tmp_path, monkeypatch):
    results, wire = exercise_mobile(tmp_path, monkeypatch, failure=True,
                                    selections=[selection(MODEL_A)])
    assert results[0]['status'] == 'failed', results
    assert [r['model'] for r in wire] == [MODEL_A], wire


def test_default_keeps_native_fallback_execution(tmp_path, monkeypatch):
    results, wire = exercise_mobile(tmp_path, monkeypatch, failure=True, selections=[None])
    assert results[0]['status'] == 'completed', results
    assert [r['model'] for r in wire] == [DEFAULT, FALLBACK], wire
    assert all('reasoning' not in r for r in wire), wire


def test_removing_exact_lock_reproduces_silent_fallback(tmp_path, monkeypatch):
    # Mutation is confined to the child module in memory, never shared source.
    results, wire = exercise_mobile(tmp_path, monkeypatch, failure=True, remove_lock=True,
                                    selections=[selection(MODEL_A)])
    assert results[0]['status'] == 'completed', results
    assert [r['model'] for r in wire] == [MODEL_A, FALLBACK], wire


def _native_main():
    import asyncio
    import socket
    import sys
    from types import SimpleNamespace

    # Fail closed before native imports: only the disposable loopback listener
    # may use sockets. Provider traffic must go through the SDK MockTransport.
    original_connect, original_dns = socket.socket.connect, socket.getaddrinfo
    def connect(sock, address):
        assert isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}, address
        return original_connect(sock, address)
    def dns(host, *args, **kwargs):
        assert host in {'127.0.0.1', '::1', None}, host
        return original_dns(host, *args, **kwargs)
    socket.socket.connect, socket.getaddrinfo = connect, dns

    import httpx
    import openai
    from gateway.config import PlatformConfig
    from gateway.platforms import api_server
    import gateway.run as gateway_run
    from hermes_state import SessionDB
    from run_agent import AIAgent
    import backend.native_run_controls as controls

    failure, stale, remove_lock = (bool(int(value)) for value in sys.argv[1:])
    home = Path(os.environ['HERMES_HOME'])
    fallback = {'provider': 'custom', 'model': FALLBACK,
                'base_url': 'https://fallback.invalid/v1', 'api_key': 'synthetic-only',
                'api_mode': 'chat_completions'}
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'default': DEFAULT, 'provider': 'copilot'},
        'fallback_model': fallback, 'platform_toolsets': {'api_server': []},
        'agent': {'max_turns': 2}, 'auxiliary': {'title_generation': {'enabled': False}},
        'memory': {'memory_enabled': False,
        'user_profile_enabled': False}, 'background_review': {'enabled': False}}))
    def runtime(*args, **kwargs):
        return {'provider': 'copilot', 'api_key': 'synthetic-only',
                'base_url': 'https://api.githubcopilot.com', 'api_mode': 'chat_completions',
                'skip_memory': True, 'skip_background_review': True, 'skip_context_files': True}
    gateway_run._resolve_runtime_agent_kwargs = runtime
    api_server._resolve_request_runtime_agent_kwargs = runtime
    gateway_run._resolve_runtime_agent_kwargs_for_provider = runtime
    # Keep factory precedence, config fallback loading and AIAgent construction real.
    if stale:
        runner = SimpleNamespace(_session_model_overrides={SESSION: {
            'model': 'synthetic-stale-session-model', 'provider': 'copilot'}})
        gateway_run._gateway_runner_ref = lambda: runner
    controls._mobile_model_options = lambda: {
        'available': True, 'default': {'model': DEFAULT, 'provider': 'copilot'},
        'models': [{'id': m, 'label': m, 'provider': 'copilot',
                    'reasoning_efforts': ['low', 'high']} for m in (MODEL_A, MODEL_B, DEFAULT)]}
    if remove_lock:
        source = Path(controls.__file__).read_text()
        needle = "kwargs['confirmed_runtime_lock'] = True"
        assert source.count(needle) == 1
        exec(compile(source.replace(needle, 'pass  # test-only mutation'),
                     controls.__file__, 'exec'), controls.__dict__)
        # exec restores module functions, so reinstall only the synthetic catalog.
        controls._mobile_model_options = lambda: {
            'available': True, 'default': {'model': DEFAULT, 'provider': 'copilot'},
            'models': [{'id': m, 'label': m, 'provider': 'copilot',
                        'reasoning_efforts': ['low', 'high']} for m in (MODEL_A, MODEL_B, DEFAULT)]}

    wire = []
    def provider(request):
        assert request.method == 'POST' and request.url.path.endswith('/chat/completions'), request.url
        payload = json.loads(request.content)
        wire.append(payload)
        (home / 'wire.json').write_text(json.dumps(wire))
        if failure and payload['model'] != FALLBACK:
            return httpx.Response(402, json={'error': {
                'message': 'Synthetic insufficient credits', 'type': 'insufficient_quota',
                'code': 'insufficient_quota'}})
        return httpx.Response(200, json={
            'id': 'synthetic-completion', 'object': 'chat.completion', 'created': 1,
            'model': payload['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'Synthetic fixture response'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    original_init = openai.OpenAI.__init__
    def sdk_init(self, *args, **kwargs):
        kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(provider))
        kwargs['max_retries'] = 0
        original_init(self, *args, **kwargs)
    openai.OpenAI.__init__ = sdk_init
    AIAgent._disable_streaming = True

    async def main():
        db = SessionDB(home / 'state.db')
        db.create_session(SESSION, 'api_server')
        adapter = controls.run_controls_adapter(api_server.APIServerAdapter)(
            PlatformConfig(enabled=True, extra={'host': '127.0.0.1', 'port': 0, 'key': KEY}))
        adapter._session_db = db
        assert await adapter.connect()
        port = adapter._site._server.sockets[0].getsockname()[1]
        (home / 'ready.json').write_text(json.dumps({'url': f'http://127.0.0.1:{port}'}))
        await asyncio.Event().wait()
    asyncio.run(main())


if __name__ == '__main__':
    _native_main()
