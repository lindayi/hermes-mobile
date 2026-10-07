"""Photo persistence and vision behavior against the pinned native runtime."""
import os
from pathlib import Path
import subprocess
import textwrap
import pytest

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
        assert encoded_photo_rows
        assert all(matches_user({'id': index + 2, 'role': 'user', 'content': content}, photo_run)
                   for index, content in enumerate(encoded_photo_rows))
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


@pytest.mark.parametrize('supports_vision', [True, False])
def test_native_conversation_loop_rejects_image_4xx_without_text_retry(tmp_path, supports_vision):
    run_native_probe(tmp_path, '''
        import base64
        import json
        import socket
        from unittest.mock import Mock, patch
        import httpx
        from openai import BadRequestError
        import run_agent
        from backend.native_run_controls import private_photo_agent

        client = Mock()
        client.chat.completions.create.side_effect = BadRequestError(
            "Only 'text' content type is supported.",
            response=httpx.Response(400, request=httpx.Request('POST', 'http://127.0.0.1/synthetic')),
            body={'error': "Only 'text' content type is supported."})
        def no_network(*args, **kwargs):
            raise AssertionError('Synthetic native test attempted a network connection')
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
            marker = base64.b64encode(b'SYNTHETIC_4XX_PHOTO' * 4096).decode()
            message = [
                {'type': 'text', 'text': 'Inspect synthetic photo'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + marker}},
            ]
            try:
                result = agent.run_conversation(message, conversation_history=[], task_id='photo-4xx-fixture')
            except RuntimeError as error:
                assert 'vision' in str(error).lower() or 'photo' in str(error).lower()
            else:
                assert result.get('failed') is True, result
            assert client.chat.completions.create.call_count == int(SUPPORTS_NATIVE_VISION)
            assert message[1]['type'] == 'image_url'
            assert marker in json.dumps(message)
    '''.replace('SUPPORTS_NATIVE_VISION', repr(supports_vision)))
