"""Photo persistence and vision behavior against the pinned native runtime."""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import textwrap
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import Settings, create_app
from backend.hermes_client import GatewayClient
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll

ROOT = Path(__file__).resolve().parents[1]
NATIVE_PYTHON = '/usr/local/lib/hermes-agent/venv/bin/python'


def run_native_probe(tmp_path, code):
    home = tmp_path / 'isolated-home'
    home.mkdir()
    environment = {
        'HOME': str(home),
        'HERMES_HOME': str(home),
        'LANG': 'C.UTF-8',
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
        'PYTHONPATH': f'{ROOT}:/usr/local/lib/hermes-agent',
    }
    result = subprocess.run(
        [NATIVE_PYTHON, '-c', textwrap.dedent(code)],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_enabled_native_background_review_gets_only_sanitized_snapshot(tmp_path):
    run_native_probe(tmp_path, r'''
        import json
        from unittest.mock import patch
        from run_agent import AIAgent
        from backend.native_run_controls import private_photo_agent

        image = 'data:image/png;base64,c3ludGhldGljLXBob3Rv'
        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Keep this text'},
            {'type': 'image_url', 'image_url': {'url': image}},
        ]}]
        original = json.dumps(messages)
        captured = []
        def spawn(agent, snapshot, **kwargs):
            captured.append((snapshot, kwargs))
            return (lambda: None), 'synthetic review prompt'

        agent = private_photo_agent(AIAgent.__new__(AIAgent))
        with patch('agent.background_review.load_background_review_settings',
                   return_value=(True, {})) as enabled, \
             patch('agent.background_review.spawn_background_review_thread',
                   side_effect=spawn), \
             patch('run_agent.threading.Thread') as thread:
            agent._spawn_background_review(messages, review_memory=True, review_skills=True)
        enabled.assert_called_once()
        thread.return_value.start.assert_called_once()
        assert len(captured) == 1
        snapshot, options = captured[0]
        assert image not in json.dumps(snapshot)
        assert 'c3ludGhldGljLXBob3Rv' not in json.dumps(snapshot)
        assert 'Keep this text' in json.dumps(snapshot)
        assert options['review_memory'] and options['review_skills']
        assert json.dumps(messages) == original
    ''')


@contextmanager
def native_photo_api(tmp_path, *, provider_failure=False, provider_echo=False, aliases=False):
    home = tmp_path / 'native-api-home'
    home.mkdir()
    ready = tmp_path / 'native-api-ready.json'
    captured = tmp_path / 'provider-capture.json'
    key = 'synthetic-native-photo-test-key-not-a-credential'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    code = r'''
        import asyncio
        import base64
        import hashlib
        import json
        import os
        from pathlib import Path
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        from hermes_state import SessionDB
        from run_agent import AIAgent
        import hermes_cli.lifecycle as lifecycle
        from backend.native_run_controls import private_photo_agent, run_controls_adapter

        home, ready, capture_path, key, port = (
            Path(os.environ['HERMES_HOME']), Path(os.environ['NATIVE_READY']),
            Path(os.environ['NATIVE_CAPTURE']), os.environ['NATIVE_API_KEY'],
            int(os.environ['NATIVE_API_PORT']))
        db = SessionDB(db_path=home / 'state.db')
        if os.environ.get('NATIVE_ALIASES') == '1':
            db.create_session('photo-alias-one', 'api_server')
            db.end_session('photo-alias-one', 'compression')
            db.create_session('photo-alias-two', 'api_server', parent_session_id='photo-alias-one')
            db.end_session('photo-alias-two', 'compression')
            db.create_session('photo-assembled-session', 'api_server', parent_session_id='photo-alias-two')
        else:
            db.create_session('photo-assembled-session', 'api_server')
        captures, compactions, hooks = [], [], []
        lifecycle.has_hook = lambda name: name in {'pre_api_request', 'pre_llm_call', 'post_llm_call'}
        def capture_hook(name, *args, **kwargs):
            hooks.append({'name': name, 'arguments': json.loads(json.dumps(kwargs, default=str))})
            return []
        lifecycle.invoke_hook = capture_hook

        class ProviderHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                images, image_urls, texts = [], [], []
                for message in payload.get('messages', []):
                    content = message.get('content')
                    if message.get('role') == 'user' and isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):
                        for part in content:
                            if not isinstance(part, dict):
                                continue
                            if part.get('type') == 'text':
                                texts.append(part.get('text', ''))
                            elif part.get('type') == 'image_url':
                                url = part.get('image_url', {}).get('url', '')
                                raw = base64.b64decode(url.split(',', 1)[1], validate=True)
                                images.append(hashlib.sha256(raw).hexdigest())
                                image_urls.append(url)
                captures.append({'images': images, 'texts': texts})
                capture_path.write_text(json.dumps({'captures': captures, 'compactions': compactions, 'hooks': hooks}))
                if os.environ.get('NATIVE_PROVIDER_FAILURE') == '1':
                    response = json.dumps({
                        'error': {'message': 'Synthetic provider echoed ' + image_urls[0]},
                    }).encode()
                    status = 400
                else:
                    response = json.dumps({
                        'id': 'synthetic-completion', 'object': 'chat.completion', 'created': 1,
                        'model': 'synthetic-photo-model',
                        'choices': [{'index': 0, 'finish_reason': 'stop',
                                     'message': {'role': 'assistant',
                                                 'content': ('Synthetic photo description ' + image_urls[0]
                                                     if os.environ.get('NATIVE_PROVIDER_ECHO') == '1'
                                                     else 'Synthetic photo description')}}],
                        'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25},
                    }).encode()
                    status = 200
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        provider_server = HTTPServer(('127.0.0.1', 0), ProviderHandler)
        Thread(target=provider_server.serve_forever, daemon=True).start()

        def create_agent(**kwargs):
            agent = AIAgent(
                model='synthetic-photo-model', provider='custom',
                base_url=f'http://127.0.0.1:{provider_server.server_port}/v1',
                api_key='synthetic-only',
                enabled_toolsets=[], skip_memory=True, skip_background_review=True,
                skip_context_files=True, max_iterations=1, quiet_mode=True,
                session_id=kwargs['session_id'], platform='api_server')
            agent._session_db = db
            agent._session_db_created = True
            agent._model_supports_vision = lambda: True
            agent._disable_streaming = True
            private_photo_agent(agent)
            original_run = agent.run_conversation

            def run_and_archive(*args, **run_kwargs):
                result = original_run(*args, **run_kwargs)
                live = next((item for item in reversed(agent._session_messages)
                             if item.get('role') == 'user'
                             and isinstance(item.get('content'), list)
                             and any(isinstance(part, dict) and part.get('type') == 'image_url'
                                     for part in item['content'])), None)
                if live is None:
                    raise AssertionError('Native agent did not retain the live multimodal user turn')
                compactions.append({
                    'live_images': sum(part.get('type') == 'image_url' for part in live['content']),
                    'timestamp': live.get('timestamp'),
                    'result_failed': bool(isinstance(result, dict) and result.get('failed')),
                    'result_error': result.get('error') if isinstance(result, dict) else None,
                    'metadata': {key: value for key, value in live.items()
                                 if key not in ('content', 'api_content')},
                })
                db.archive_and_compact(agent.session_id, agent._session_messages)
                capture_path.write_text(json.dumps({'captures': captures, 'compactions': compactions, 'hooks': hooks}))
                return result

            agent.run_conversation = run_and_archive
            return agent

        async def main():
            adapter = run_controls_adapter(APIServerAdapter)(PlatformConfig(
                enabled=True, extra={'host': '127.0.0.1', 'port': port, 'key': key}))
            adapter._session_db = db
            adapter._create_agent = create_agent
            if not await adapter.connect():
                raise RuntimeError('Native API server did not start')
            ready.write_text(json.dumps({'port': port}))
            await asyncio.Event().wait()

        asyncio.run(main())
    '''
    environment = {
        'HOME': str(home),
        'HERMES_HOME': str(home),
        'LANG': 'C.UTF-8',
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
        'PYTHONPATH': f'{ROOT}:/usr/local/lib/hermes-agent',
        'NATIVE_READY': str(ready),
        'NATIVE_CAPTURE': str(captured),
        'NATIVE_API_KEY': key,
        'NATIVE_API_PORT': str(port),
        'NATIVE_PROVIDER_FAILURE': '1' if provider_failure else '0',
        'NATIVE_PROVIDER_ECHO': '1' if provider_echo else '0',
        'NATIVE_ALIASES': '1' if aliases else '0',
    }
    process = subprocess.Popen(
        [NATIVE_PYTHON, '-c', textwrap.dedent(code)],
        env=environment, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        if not ready.exists():
            _, error = process.communicate(timeout=2)
            raise AssertionError(f'Native API server failed to start: {error}')
        yield home, f'http://127.0.0.1:{port}', key, captured
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def near_limit_png(seed):
    rng = random.Random(seed)
    pixels = bytes(rng.getrandbits(8) for _ in range(1150 * 1150 * 3))
    output = io.BytesIO()
    Image.frombytes('RGB', (1150, 1150), pixels).save(output, format='PNG', optimize=True)
    return output.getvalue()


def test_successful_native_four_photo_turn_keeps_all_hook_copies_private(tmp_path):
    session = 'photo-assembled-session'
    with native_photo_api(tmp_path) as (home, url, key, capture_path):
        gateway = GatewayClient(url, key, execution_ready=True)
        app = create_app(Settings(
            state_dir=tmp_path / 'mobile', profiles={'default': home},
            bootstrap_secret=BOOTSTRAP, attachment_min_free_bytes=0), gateway_client=gateway)
        with TestClient(app, base_url=ORIGIN) as client:
            client.headers['Origin'] = ORIGIN
            enroll(client)
            uploads = [client.post(BASE + f'/sessions/{session}/attachments',
                content=near_limit_png(index), headers={'Idempotency-Key': f'hook-photo-{index}'})
                for index in range(4)]
            assert [item.status_code for item in uploads] == [201] * 4
            ids = [item.json()['id'] for item in uploads]
            assert all(1_800_000 <= item.json()['size'] <= 2 * 1024 * 1024 for item in uploads)
            sent = client.post(BASE + '/runs', json={'session_id': session,
                'input': 'Inspect four synthetic photos', 'idempotency_key': 'hook-success',
                'attachments': ids})
            assert sent.status_code == 200, sent.text
            for _ in range(400):
                result = client.get(BASE + '/runs/' + sent.json()['id']).json()
                if result['status'] in ('completed', 'failed'):
                    break
                time.sleep(.05)
            assert result['status'] == 'completed', result
            capture = json.loads(capture_path.read_text())
            expected = [hashlib.sha256((app.state.attachments.objects / (item + '.png')).read_bytes()).hexdigest()
                        for item in ids]
            assert [digest for call in capture['captures'] for digest in call['images']] == expected
            assert {'pre_api_request', 'pre_llm_call', 'post_llm_call'} <= {
                hook['name'] for hook in capture['hooks']}
            assert all('data:image/' not in json.dumps(hook) for hook in capture['hooks'])


def test_native_transcript_and_json_snapshot_omit_image_bytes(tmp_path):
    run_native_probe(tmp_path, '''
        import json
        from datetime import datetime
        from pathlib import Path
        import os
        from hermes_state import SessionDB
        from run_agent import AIAgent
        from backend.native_run_controls import private_photo_agent

        home = Path(os.environ['HERMES_HOME'])
        db = SessionDB(home / 'state.db')
        db.create_session('photo-fixture', 'api_server')
        agent = AIAgent.__new__(AIAgent)
        agent._session_db = db
        agent._session_db_created = True
        agent._last_flushed_db_idx = 0
        agent._flushed_db_message_ids = set()
        agent._flushed_db_message_session_id = None
        agent._db_flush_scan_prefix = None
        agent._persist_disabled = False
        agent.session_id = 'photo-fixture'
        agent = private_photo_agent(agent)
        import base64
        marker = base64.b64encode(b'SYNTHETIC_IMAGE_PAYLOAD' * 4096).decode()
        data_url = 'data:image/png;base64,' + marker
        message = {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'Describe this synthetic photo.'},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ],
        }
        message['api_content'] = json.dumps(message)
        agent._flush_messages_to_session_db([message], conversation_history=[])
        row = db._conn.execute(
            'SELECT content, api_content FROM messages WHERE session_id=?',
            ('photo-fixture',),
        ).fetchone()
        assert row is not None
        assert '[screenshot]' in row['content']
        from backend.task_reminder_presentation import matches_user
        photo_run = {'id': 'photo-fixture-run', 'input': 'Describe this synthetic photo.',
                     'attachment_ids': ['a' * 32]}
        assert matches_user({'id': 1, 'role': 'user', 'content': row['content']}, photo_run)

        agent._session_json_enabled = True
        agent.logs_dir = home / 'sessions'
        agent.logs_dir.mkdir(exist_ok=True)
        agent.model = 'synthetic-model'
        agent.base_url = 'http://127.0.0.1'
        agent.platform = 'api_server'
        agent.session_start = datetime.now()
        agent._cached_system_prompt = ''
        agent.tools = []
        agent.verbose_logging = False
        agent._save_session_log([message])
        snapshot = (agent.logs_dir / 'session_photo-fixture.json').read_text()
        assert marker not in snapshot
        assert 'data:image/' not in snapshot
        assert '[photo attachment omitted after processing]' in snapshot
        assert marker not in str(tuple(row))
        assert row['api_content'] is None
        agent.api_mode = 'chat_completions'
        agent.client = None
        agent.log_prefix = ''
        agent._vprint = lambda *args, **kwargs: None
        dump = agent._dump_api_request_debug(
            {'messages': [message]}, reason='provider-error',
            error=RuntimeError('Provider echoed ' + data_url))
        assert dump is not None
        dump_text = dump.read_text()
        assert marker not in dump_text
        assert 'data:image/' not in dump_text
        assert 'Describe this synthetic photo.' in dump_text
        assert message['content'][1]['image_url']['url'] == data_url
        for _ in range(3):
            db.archive_and_compact('photo-fixture', [message])
        db.append_message('photo-fixture', 'user', content=message['content'],
                          api_content=json.dumps(message))
        rows = db._conn.execute(
            'SELECT content, api_content FROM messages WHERE session_id=?',
            ('photo-fixture',)).fetchall()
        assert len(rows) >= 5
        assert marker not in str([tuple(row) for row in rows])
        assert 'data:image/' not in str([tuple(row) for row in rows])
        assert all('Describe this synthetic photo.' in row['content'] for row in rows)
        encoded_photo_rows = [row['content'] for row in rows
                              if row['content'].startswith('\\x00json:')]
        assert not encoded_photo_rows
        assert all(row['content'] == 'Describe this synthetic photo.\\n[screenshot]'
                   for row in rows)
        assert all(matches_user({'id': index + 2, 'role': 'user', 'content': row['content']}, photo_run)
                   for index, row in enumerate(rows))
    ''')


def test_native_photo_exception_is_sanitized_before_handler_logging(tmp_path):
    run_native_probe(tmp_path, '''
        import base64
        import io
        import logging
        from backend.native_run_controls import NativePhotoFailure, private_photo_agent

        marker = base64.b64encode(b'SYNTHETIC_LOGGED_PHOTO' * 4096).decode()
        url = 'data:image/png;base64,' + marker
        class Agent:
            def run_conversation(self, message, *args, **kwargs):
                raise RuntimeError('provider failure echoed ' + url)

        agent = private_photo_agent(Agent())
        stream = io.StringIO()
        logger = logging.getLogger('synthetic.native.photo.handler')
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            try:
                agent.run_conversation([{'type': 'image_url', 'image_url': {'url': url}}])
            except NativePhotoFailure:
                logger.exception('native run failed')
            else:
                raise AssertionError('photo exception was not normalized')
        finally:
            logger.removeHandler(handler)
        output = stream.getvalue()
        assert marker not in output
        assert 'data:image/' not in output
        assert 'photo run failed' in output
    ''')


@pytest.mark.parametrize('location', ['history', 'body'])
def test_installed_authenticated_runs_handler_rejects_unbound_images(tmp_path, location):
    run_native_probe(tmp_path, '''
        import asyncio
        import os
        from types import SimpleNamespace
        from backend.native_run_controls import run_controls_adapter
        from gateway.platforms.api_server import APIServerAdapter

        image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}
        payload = {
            'input': [{'role': 'user', 'content': [
                {'type': 'text', 'text': 'Inspect this photo'}, image]}],
            'mobile_attachment_ids': ['a' * 32],
        }
        if LOCATION == 'history':
            payload['conversation_history'] = [{'role': 'user', 'content': [image]}]
        else:
            payload['request_metadata'] = {'input_image': image}
        class Request:
            headers = {'Authorization': '******'}
            async def json(self):
                import json
                return json.loads(json.dumps(payload))
        handler = object.__new__(run_controls_adapter(APIServerAdapter))
        def authenticate(request):
            assert request.headers['Authorization'] == '******'
            return None
        handler._check_auth = authenticate
        response = asyncio.run(handler._handle_runs(Request()))
        assert response.status == 413
    '''.replace('LOCATION', repr(location)))


def test_native_provider_error_dump_omits_inline_photo_and_echoed_error(tmp_path):
    run_native_probe(tmp_path, '''
        import base64
        import json
        import os
        from pathlib import Path
        from run_agent import AIAgent
        from backend.native_run_controls import private_photo_agent

        agent = private_photo_agent(AIAgent.__new__(AIAgent))
        agent.logs_dir = Path(os.environ['HERMES_HOME']) / 'sessions'
        agent.logs_dir.mkdir(exist_ok=True)
        agent.session_id = 'photo-provider-error'
        agent.base_url = 'http://127.0.0.1'
        agent.api_mode = 'chat_completions'
        agent.client = None
        agent.log_prefix = ''
        agent._vprint = lambda *args, **kwargs: None
        marker = base64.b64encode(b'SYNTHETIC_ERROR_PHOTO' * 4096).decode()
        url = 'data:image/png;base64,' + marker
        request = {'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Inspect synthetic error photo'},
            {'type': 'image_url', 'image_url': {'url': url}},
        ]}]}
        dump = agent._dump_api_request_debug(
            request, reason='provider-error', error=RuntimeError('Provider echoed ' + url))
        assert dump is not None
        assert marker not in dump.read_text()
        assert 'data:image/' not in dump.read_text()
        assert 'Inspect synthetic error photo' in dump.read_text()
        assert marker in json.dumps(request)
    ''')


def test_native_vision_failure_is_reported_without_synthetic_description(tmp_path):
    run_native_probe(tmp_path, '''
        import asyncio
        import sys
        import types
        from run_agent import AIAgent
        from backend.native_run_controls import private_photo_agent

        async def unavailable(**kwargs):
            raise RuntimeError('synthetic provider unavailable')

        sys.modules['tools.vision_tools'] = types.SimpleNamespace(
            vision_analyze_tool=unavailable,
        )
        agent = AIAgent.__new__(AIAgent)
        agent = private_photo_agent(agent)
        agent._mobile_photo_turn = True
        agent._anthropic_image_fallback_cache = {}
        agent._materialize_data_url_for_vision = lambda source: (source, None)
        try:
            agent._describe_image_for_anthropic_fallback(
                'data:image/png;base64,SYNTHETIC_IMAGE_PAYLOAD',
                'user',
            )
        except RuntimeError as error:
            assert str(error) == 'The configured vision analyzer could not process the attached image.'
        else:
            raise AssertionError('Vision failure was converted into a text-only result')
    ''')


def test_native_tool_image_fallback_is_preserved_outside_mobile_photo_turn(tmp_path):
    run_native_probe(tmp_path, '''
        import run_agent
        from backend.native_run_controls import NativePhotoFailure, private_photo_agent

        calls = []
        original = run_agent.AIAgent._describe_image_for_anthropic_fallback
        run_agent.AIAgent._describe_image_for_anthropic_fallback = (
            lambda self, image_url, role: calls.append((image_url, role)) or 'native result')
        try:
            agent = private_photo_agent(run_agent.AIAgent.__new__(run_agent.AIAgent))
            assert agent._describe_image_for_anthropic_fallback('tool-image', 'tool') == 'native result'
            assert calls == [('tool-image', 'tool')]
            agent._mobile_photo_turn = True
            try:
                agent._describe_image_for_anthropic_fallback('mobile-photo', 'user')
            except NativePhotoFailure as error:
                assert 'vision analyzer' in str(error)
            else:
                raise AssertionError('Mobile photo fallback did not fail explicitly')
            assert calls == [('tool-image', 'tool')]
        finally:
            run_agent.AIAgent._describe_image_for_anthropic_fallback = original
    ''')


def test_native_photo_pre_api_hooks_get_sanitized_copies_and_text_hooks_stay_unchanged(tmp_path):
    run_native_probe(tmp_path, '''
        import base64
        import json
        import socket
        import struct
        import zlib
        from unittest.mock import Mock, patch
        import httpx
        from openai import BadRequestError
        import hermes_cli.lifecycle as lifecycle
        import run_agent
        from backend.native_run_controls import private_photo_agent

        def chunk(kind, data):
            return (struct.pack('!I', len(data)) + kind + data
                    + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
        png = (b'\\x89PNG\\r\\n\\x1a\\n'
               + chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(bytes([0, 200, 30, 10])))
               + chunk(b'IEND', b''))
        marker = base64.b64encode(png).decode()
        url = 'data:image/png;base64,' + marker
        observed = []
        lifecycle.has_hook = lambda name: name == 'pre_api_request'
        lifecycle.invoke_hook = lambda name, *args, **kwargs: observed.append((name, kwargs))
        client = Mock()
        client.chat.completions.create.side_effect = [
            BadRequestError(
                'Provider echoed ' + url,
                response=httpx.Response(400, request=httpx.Request('POST', 'http://127.0.0.1/synthetic')),
                body={'error': 'Provider echoed ' + url}),
            BadRequestError(
                'Text-only provider failure',
                response=httpx.Response(400, request=httpx.Request('POST', 'http://127.0.0.1/synthetic')),
                body={'error': 'Text-only provider failure'}),
        ]
        def no_network(*args, **kwargs):
            raise AssertionError('Synthetic native test attempted a network connection')
        with patch.object(socket.socket, 'connect', no_network), \
             patch.object(run_agent, 'OpenAI', return_value=client):
            agent = run_agent.AIAgent(
                model='synthetic-photo-model', provider='custom',
                base_url='http://127.0.0.1:1/v1', api_key='synthetic-not-a-provider-key',
                enabled_toolsets=[], skip_memory=True, skip_background_review=True,
                skip_context_files=True, max_iterations=1, quiet_mode=True,
                session_id='photo-hook-fixture', platform='api_server')
            agent = private_photo_agent(agent)
            agent.client = client
            agent._model_supports_vision = lambda: True
            message = [
                {'type': 'text', 'text': 'Inspect four synthetic photos'},
                *[{'type': 'image_url', 'image_url': {'url': url}} for _ in range(4)],
            ]
            original_message = json.dumps(message)
            try:
                result = agent.run_conversation(
                    message, conversation_history=[{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': url}}]}],
                    task_id='photo-hook-fixture')
                assert result.get('failed') is True
                encoded_result = json.dumps(result)
                assert marker not in encoded_result
                assert 'data:image/' not in encoded_result
                assert 'Provider echoed' in encoded_result
            except RuntimeError as error:
                assert 'photo' in str(error).lower() or 'vision' in str(error).lower()
            assert json.dumps(message) == original_message
            photo_provider_messages = client.chat.completions.create.call_args.kwargs['messages']
            assert marker in json.dumps(photo_provider_messages)
            assert not agent._mobile_photo_turn
            assert not agent._mobile_photo_failed

            try:
                agent.run_conversation('Ordinary text request', conversation_history=[],
                                       task_id='photo-hook-text-fixture')
            except RuntimeError as error:
                assert 'provider' in str(error).lower()

        hooks = [kwargs for name, kwargs in observed if name == 'pre_api_request']
        assert len(hooks) >= 2
        assert all(marker not in json.dumps(hook) for hook in hooks)
        assert all('data:image/' not in json.dumps(hook) for hook in hooks)
        photo_hook = next(hook for hook in hooks
                          if isinstance(hook.get('user_message'), list))
        text_hook = next(hook for hook in hooks
                         if hook.get('user_message') == 'Ordinary text request')
        assert photo_hook['user_message'][0]['text'] == 'Inspect four synthetic photos'
        assert sum('photo attachment omitted' in item.get('text', '')
                   for item in photo_hook['user_message']) == 4
        assert text_hook['user_message'] == 'Ordinary text request'
        assert text_hook['request']['body']['messages'][-1]['content'] == 'Ordinary text request'
        assert len(client.chat.completions.create.call_args_list) == 2
        other_agent = private_photo_agent(run_agent.AIAgent.__new__(run_agent.AIAgent))
        assert not getattr(other_agent, '_mobile_photo_turn', False)
        assert not getattr(other_agent, '_mobile_photo_failed', False)
    ''')


@pytest.mark.parametrize('supports_vision', [True, False])
def test_native_conversation_loop_rejects_image_4xx_without_text_retry(tmp_path, supports_vision):
    run_native_probe(tmp_path, '''
        import base64
        import json
        import io
        import logging
        import socket
        from unittest.mock import Mock, patch
        from agent import conversation_loop
        import httpx
        from openai import BadRequestError
        import run_agent
        from backend.native_run_controls import private_photo_agent

        marker = base64.b64encode(b'SYNTHETIC_4XX_PHOTO' * 4096).decode()
        url = 'data:image/png;base64,' + marker
        client = Mock()
        client.chat.completions.create.side_effect = BadRequestError(
            'Provider echoed ' + url,
            response=httpx.Response(400, request=httpx.Request('POST', 'http://127.0.0.1/synthetic')),
            body={'error': 'Provider echoed ' + url})
        def no_network(*args, **kwargs):
            raise AssertionError('Synthetic native test attempted a network connection')
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger(conversation_loop.__name__)
        previous_level = logger.level
        logger.setLevel(logging.ERROR)
        logger.addHandler(handler)
        try:
            with patch.object(socket.socket, 'connect', no_network), \
                 patch.object(run_agent, 'OpenAI', return_value=client):
                agent = run_agent.AIAgent(
                    model='synthetic-photo-model', provider='custom',
                    base_url='http://127.0.0.1:1/v1', api_key='synthetic-not-a-provider-key',
                    enabled_toolsets=[], skip_memory=True, skip_background_review=True,
                    skip_context_files=True, max_iterations=1, quiet_mode=True,
                    session_id='photo-4xx-fixture', platform='api_server')
                agent = private_photo_agent(agent)
                agent.client = client
                agent._model_supports_vision = lambda: SUPPORTS_NATIVE_VISION
                message = [
                    {'type': 'text', 'text': 'Inspect synthetic photo'},
                    {'type': 'image_url', 'image_url': {'url': url}},
                ]
                try:
                    result = agent.run_conversation(message, conversation_history=[], task_id='photo-4xx-fixture')
                except RuntimeError as error:
                    assert 'vision' in str(error).lower() or 'photo' in str(error).lower()
                else:
                    assert result.get('failed') is True, result
                    if SUPPORTS_NATIVE_VISION:
                        encoded_result = json.dumps(result)
                        assert marker not in encoded_result
                        assert 'data:image/' not in encoded_result
                        assert 'Provider echoed' in encoded_result
                assert client.chat.completions.create.call_count == int(SUPPORTS_NATIVE_VISION)
                assert message[1]['type'] == 'image_url'
                assert marker in json.dumps(message)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
        logged = stream.getvalue()
        assert marker not in logged
        assert 'data:image/' not in logged
        if SUPPORTS_NATIVE_VISION:
            assert 'Non-retryable client error' in logged
    '''.replace('SUPPORTS_NATIVE_VISION', repr(supports_vision)))


def test_native_model_boundary_receives_four_distinct_near_limit_photos_in_order(tmp_path):
    run_native_probe(tmp_path, '''
        import base64
        import hashlib
        import socket
        import struct
        import zlib
        from unittest.mock import Mock, patch
        import httpx
        from openai import BadRequestError
        import run_agent
        from backend.native_run_controls import private_photo_agent

        def chunk(kind, data):
            return (struct.pack('!I', len(data)) + kind + data
                    + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))

        images = []
        for index in range(4):
            png = (b'\\x89PNG\\r\\n\\x1a\\n'
                   + chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(bytes([0, index, 17, 239])))
                   + chunk(b'tEXt', b'Synthetic\\0fixture-' + str(index).encode()
                           + b':' + b'A' * (2 * 1024 * 1024 - 256))
                   + chunk(b'IEND', b''))
            assert png.startswith(b'\\x89PNG\\r\\n\\x1a\\n')
            assert 2 * 1024 * 1024 - 256 <= len(png) <= 2 * 1024 * 1024
            images.append(png)

        client = Mock()
        client.chat.completions.create.side_effect = BadRequestError(
            'Synthetic model-boundary capture',
            response=httpx.Response(400, request=httpx.Request('POST', 'http://127.0.0.1/synthetic')),
            body={'error': 'Synthetic provider boundary'})
        def no_network(*args, **kwargs):
            raise AssertionError('Synthetic native test attempted a network connection')
        with patch.object(socket.socket, 'connect', no_network), \
             patch.object(run_agent, 'OpenAI', return_value=client):
            agent = run_agent.AIAgent(
                model='synthetic-photo-model', provider='custom',
                base_url='http://127.0.0.1:1/v1', api_key='synthetic-not-a-provider-key',
                enabled_toolsets=[], skip_memory=True, skip_background_review=True,
                skip_context_files=True, max_iterations=1, quiet_mode=True,
                session_id='photo-model-boundary-fixture', platform='api_server')
            agent = private_photo_agent(agent)
            agent.client = client
            agent._model_supports_vision = lambda: True
            user_message = [
                {'type': 'text', 'text': 'Inspect four synthetic normalized photos'},
                *[{'type': 'image_url', 'image_url': {
                    'url': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}}
                  for image in images],
            ]
            try:
                result = agent.run_conversation(
                    user_message, conversation_history=[], task_id='photo-model-boundary-fixture')
            except RuntimeError as error:
                assert 'photo' in str(error).lower() or 'vision' in str(error).lower()
            else:
                assert result.get('failed') is True, result

        assert client.chat.completions.create.call_count == 1
        captured = client.chat.completions.create.call_args.kwargs['messages']
        actual_urls = [part['image_url']['url'] for message in captured
                       for part in message.get('content', [])
                       if isinstance(part, dict) and part.get('type') == 'image_url']
        assert len(actual_urls) == 4
        actual = [base64.b64decode(url.split(',', 1)[1], validate=True) for url in actual_urls]
        assert [hashlib.sha256(image).hexdigest() for image in actual] == [
            hashlib.sha256(image).hexdigest() for image in images]
        assert actual == images
    ''')


@pytest.mark.parametrize('aliases', [False, True])
def test_authenticated_runs_reach_native_agent_and_compact_live_photos_without_copies(tmp_path, aliases):
    session_id = 'photo-assembled-session'
    with native_photo_api(tmp_path, aliases=aliases) as (native_home, upstream_url, upstream_key, capture_path):
        gateway = GatewayClient(
            upstream_url, upstream_key, execution_ready=True,
            transport=httpx.AsyncHTTPTransport(retries=0))
        app = create_app(Settings(
            state_dir=tmp_path / 'mobile-state', profiles={'default': native_home},
            bootstrap_secret=BOOTSTRAP, upstream_url=upstream_url,
            upstream_token=upstream_key, execution_ready=True,
            attachment_min_free_bytes=0), gateway_client=gateway)
        with TestClient(app, base_url=ORIGIN) as client:
            client.headers['Origin'] = ORIGIN
            enroll(client)
            originals = [near_limit_png(index) for index in range(4)]
            uploads = [
                client.post(
                    BASE + f'/sessions/{("photo-alias-one" if index < 2 else "photo-alias-two") if aliases else session_id}/attachments', content=png,
                    headers={'Idempotency-Key': f'assembled-photo-{index}'})
                for index, png in enumerate(originals)
            ]
            assert [response.status_code for response in uploads] == [201] * 4
            attachment_ids = [response.json()['id'] for response in uploads]
            assert all(1_800_000 <= response.json()['size'] <= 2 * 1024 * 1024
                       for response in uploads)

            requests = [
                {'session_id': 'photo-alias-one' if aliases else session_id,
                 'input': 'Please describe the attached image(s), including any visible text.',
                 'idempotency_key': 'assembled-image-only',
                 'attachments': attachment_ids[:2]},
                {'session_id': 'photo-alias-two' if aliases else session_id, 'input': 'Compare these two synthetic images.',
                 'idempotency_key': 'assembled-text-and-photos',
                 'attachments': attachment_ids[2:]},
            ]
            submitted = []
            for request in requests:
                response = client.post(BASE + '/runs', json=request)
                assert response.status_code == 200, response.text
                submitted.append(response.json())
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = client.get(BASE + '/runs/' + response.json()['id']).json()
                    if current['status'] in {'completed', 'failed', 'cancelled'}:
                        break
                    time.sleep(.05)
                assert current['status'] in {'completed', 'failed'}, current['status']

            capture_deadline = time.monotonic() + 5
            while not capture_path.exists() and time.monotonic() < capture_deadline:
                time.sleep(.05)
            capture = json.loads(capture_path.read_text())
            expected_hashes = [
                hashlib.sha256((app.state.attachments.objects / (attachment_id + '.png')).read_bytes()).hexdigest()
                for attachment_id in attachment_ids]
            assert [digest for call in capture['captures'] for digest in call['images']] == expected_hashes
            assert [item['live_images'] for item in capture['compactions']] == [2, 2], capture
            assert all(isinstance(item['timestamp'], (int, float))
                       for item in capture['compactions'])
            assert not any(item['result_failed'] for item in capture['compactions']), capture

            full = client.get(BASE + f'/sessions/{session_id}/messages?limit=500')
            assert full.status_code == 200, full.text
            all_users = [item for item in full.json()['items'] if item['role'] == 'user']
            assert len(all_users) == 2, full.text
            normalized_inputs = [
                request['input'] + '\n[screenshot]\n[screenshot]' for request in requests]
            assert sum(item['content'] == normalized_inputs[0] for item in all_users) == 1
            assert sum(item['content'] == normalized_inputs[1] for item in all_users) == 1
            bound = {item['content']: item for item in all_users}
            assert bound[normalized_inputs[0]]['attachment_ids'] == attachment_ids[:2]
            assert bound[normalized_inputs[1]]['attachment_ids'] == attachment_ids[2:]
            latest = client.get(
                BASE + f'/sessions/{session_id}/messages?limit=2&latest=true')
            assert latest.status_code == 200, latest.text
            assert any(item.get('attachment_ids') == attachment_ids[2:]
                       for item in latest.json()['items']), json.dumps(latest.json())
            for request in requests:
                reopened = client.get(BASE + f"/sessions/{request['session_id']}/messages?limit=500")
                assert reopened.status_code == 200, reopened.text
                page = reopened.json()
                visible_ids = [item['attachment_ids'] for item in page['items']
                               if item['role'] == 'user' and item.get('attachment_ids')]
                if page.get('run') and page['run'].get('attachment_ids') not in visible_ids:
                    visible_ids.append(page['run']['attachment_ids'])
                assert visible_ids == [attachment_ids[:2], attachment_ids[2:]]
                assert client.get(BASE + f'/sessions/{session_id}/messages?limit=500').json() == full.json()
            for request, ids in zip(requests, (attachment_ids[:2], attachment_ids[2:])):
                for attachment_id in ids:
                    for identity in (request['session_id'], session_id):
                        assert client.get(BASE + f'/sessions/{identity}/attachments/{attachment_id}').status_code == 200
                    assert client.get(BASE + f'/sessions/unproven-alias/attachments/{attachment_id}').status_code == 404
            for request, expected_ids in zip(requests, (attachment_ids[:2], attachment_ids[2:])):
                metadata = bound[request['input'] + '\n[screenshot]\n[screenshot]']['attachments']
                assert [item['id'] for item in metadata] == expected_ids
                assert all(item['status'] == 'bound' and item['content_type'] == 'image/png'
                           for item in metadata)

            pages = []
            for offset in range(full.json()['total']):
                page = client.get(
                    BASE + f'/sessions/{session_id}/messages?limit=1&offset={offset}')
                assert page.status_code == 200, page.text
                pages.extend(page.json()['items'])
            page_users = [item for item in pages if item['role'] == 'user']
            assert [item['content'] for item in page_users if item['content'] in {
                *normalized_inputs}] == normalized_inputs

            journal_bytes = app.state.journal.path.read_bytes()
            assert b'data:image/' not in journal_bytes
            for path in native_home.rglob('*'):
                if path.is_file():
                    assert b'data:image/' not in path.read_bytes(), path


@pytest.mark.parametrize('provider_failure', [True, False])
def test_photo_result_stays_private_in_status_events_sse_and_history(tmp_path, provider_failure):
    session_id = 'photo-assembled-session'
    with native_photo_api(tmp_path, provider_failure=provider_failure,
                          provider_echo=not provider_failure) as (
            native_home, upstream_url, upstream_key, capture_path):
        gateway = GatewayClient(
            upstream_url, upstream_key, execution_ready=True,
            transport=httpx.AsyncHTTPTransport(retries=0))
        app = create_app(Settings(
            state_dir=tmp_path / 'mobile-state', profiles={'default': native_home},
            bootstrap_secret=BOOTSTRAP, upstream_url=upstream_url,
            upstream_token=upstream_key, execution_ready=True,
            attachment_min_free_bytes=0), gateway_client=gateway)
        with TestClient(app, base_url=ORIGIN) as client:
            client.headers['Origin'] = ORIGIN
            enroll(client)
            image = io.BytesIO()
            Image.new('RGB', (16, 16), 'red').save(image, format='PNG')
            upload = client.post(
                BASE + f'/sessions/{session_id}/attachments', content=image.getvalue(),
                headers={'Idempotency-Key': 'failed-result-photo'})
            assert upload.status_code == 201, upload.text
            attachment_id = upload.json()['id']
            path = app.state.attachments.objects / (attachment_id + '.png')
            marker = base64.b64encode(path.read_bytes()).decode()

            submitted = client.post(BASE + '/runs', json={
                'session_id': session_id,
                'input': 'Inspect this synthetic failed-result photo.',
                'idempotency_key': 'failed-result-photo-run',
                'attachments': [attachment_id],
            })
            assert submitted.status_code == 200, submitted.text
            run_id = submitted.json()['id']
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = client.get(BASE + '/runs/' + run_id).json()
                if status['status'] in {'completed', 'failed', 'cancelled'}:
                    break
                time.sleep(.05)
            expected_status = 'failed' if provider_failure else 'completed'
            assert status['status'] == expected_status, status

            events = client.get(BASE + f'/runs/{run_id}/events?after=0')
            snapshot = client.get(
                BASE + f'/sessions/{session_id}/messages?limit=500').json()
            surfaces = json.dumps([status, events.text, snapshot])
            assert expected_status in surfaces
            assert marker not in surfaces
            assert 'data:image/' not in surfaces
            assert events.headers['content-type'].startswith('text/event-stream')

            captured = json.loads(capture_path.read_text())
            assert captured['compactions'][0]['result_failed'] is provider_failure
            assert marker not in json.dumps(captured['compactions'])
            assert 'failed' in json.dumps(captured['compactions'])
            assert marker not in app.state.journal.path.read_bytes().decode(errors='ignore')
            for path in native_home.rglob('*'):
                if path.is_file():
                    data = path.read_bytes()
                    assert marker.encode() not in data, path
                    assert b'data:image/' not in data, path
