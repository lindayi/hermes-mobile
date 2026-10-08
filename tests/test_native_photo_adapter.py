import base64
import json

import pytest
from aiohttp import web

from backend import native_run_controls as controls


def photo_payload():
    image = base64.b64encode((b'synthetic-pixels' * 139811)[:2 * 1024 * 1024]).decode()
    return {
        'input': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Inspect synthetic photos'},
            *[{'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + image}} for _ in range(4)],
        ]}],
        'mobile_attachment_ids': [f'{n:032x}' for n in range(4)],
    }


@pytest.mark.parametrize('character', ['x', '汉'])
def test_gateway_photo_budget_matches_native_compact_utf8_boundaries(character):
    import asyncio
    from backend.hermes_client import GatewayClient
    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-token', execution_ready=True)
    ids = ['1' * 32]
    sizes = [('image/png', 3)]
    history = [{'role': 'user', 'content': ''}]
    payload = gateway._run_payload('s', 'inspect', history, attachment_ids=ids,
        attachments=[{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eHl6'}}])
    overhead = controls._compact_utf8_size(controls.photo_persistence_copy(payload))
    length = (controls.TEXT_REQUEST_BYTES - overhead) // len(character.encode())
    for extra in (0, 1):
        history[0]['content'] = character * (length + extra)
        if extra:
            with pytest.raises(ValueError, match='text'):
                controls.validate_photo_payload(payload)
            with pytest.raises(ValueError, match='text'):
                gateway.validate_run_size('s', 'inspect', history, ids, sizes)
        else:
            controls.validate_photo_payload(payload)
            assert gateway.validate_run_size('s', 'inspect', history, ids, sizes) < 20_000_000
    asyncio.run(gateway.close())


def test_gateway_complete_photo_budget_exact_boundary():
    import asyncio
    from backend.hermes_client import GatewayClient
    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-token', execution_ready=True)
    ids = [f'{index:032x}' for index in range(4)]
    sizes = [('image/png', 2 * 1024 * 1024)] * 4
    history = [{'role': 'user', 'content': ''}]
    overhead = gateway.validate_run_size('s', 'inspect', history, ids, sizes)
    history[0]['content'] = 'x' * (controls.PHOTO_REQUEST_BYTES - overhead)
    assert gateway.validate_run_size('s', 'inspect', history, ids, sizes) == controls.PHOTO_REQUEST_BYTES
    history[0]['content'] += 'x'
    with pytest.raises(ValueError, match='native handler limit'):
        gateway.validate_run_size('s', 'inspect', history, ids, sizes)
    asyncio.run(gateway.close())


def test_four_photos_fit_complete_native_payload_without_expanding_text_limit():
    payload = photo_payload()
    assert 10_000_000 < len(json.dumps(payload).encode()) < 20_000_000
    controls.validate_photo_payload(payload)
    payload['conversation_history'] = [{'role': 'user', 'content': 'x' * 10_000_000}]
    with pytest.raises(ValueError, match='text'):
        controls.validate_photo_payload(payload)


@pytest.mark.parametrize('with_photos', [False, True])
def test_actual_native_run_handler_measures_compact_utf8_text_budget(with_photos):
    text = '汉' * 1_700_000
    if with_photos:
        payload = photo_payload()
        payload['input'][0]['content'][0]['text'] = text
        wire_size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
        assert 10_000_000 < wire_size < controls.PHOTO_REQUEST_BYTES
    else:
        payload = {'input': [{'role': 'user', 'content': text}]}
        compact_size = len(json.dumps(
            payload, ensure_ascii=False, separators=(',', ':')).encode())
        assert compact_size < controls.TEXT_REQUEST_BYTES
        assert len(json.dumps(payload).encode()) > controls.TEXT_REQUEST_BYTES

    class Native:
        admitted = 0

        def _check_auth(self, request):
            return None

        async def _handle_runs(self, request):
            self.admitted += 1
            return web.Response(status=200)

    class Request:
        headers = {}
        match_info = {}

        async def json(self):
            return payload

    handler = controls.run_controls_adapter(Native)()
    response = __import__('asyncio').run(handler._handle_runs(Request()))
    assert response.status == 200
    assert handler.admitted == 1


def test_actual_native_run_handler_rejects_compact_utf8_over_text_budget():
    payload = {'input': [{'role': 'user', 'content': '汉' * 3_333_334}]}
    assert len(json.dumps(
        payload, ensure_ascii=False, separators=(',', ':')).encode()) > controls.TEXT_REQUEST_BYTES

    class Native:
        admitted = 0

        def _check_auth(self, request):
            return None

        async def _handle_runs(self, request):
            self.admitted += 1
            return web.Response(status=200)

    class Request:
        headers = {}
        match_info = {}

        async def json(self):
            return payload

    handler = controls.run_controls_adapter(Native)()
    response = __import__('asyncio').run(handler._handle_runs(Request()))
    assert response.status == 413
    assert handler.admitted == 0


@pytest.mark.parametrize('location', ['history', 'body'])
def test_photo_payload_rejects_image_parts_outside_the_bound_current_turn(location):
    payload = photo_payload()
    image = {'type': 'input_image', 'image_url': {'url': 'data:image/png;base64,YQ=='}}
    if location == 'history':
        payload['conversation_history'] = [{'role': 'user', 'content': [image]}]
    else:
        payload['extra'] = {'content': [image]}
    with pytest.raises(ValueError, match='image|photo|binding'):
        controls.validate_photo_payload(payload)


@pytest.mark.parametrize('part_type', ['image_url', 'input_image', 'image'])
def test_unbound_image_parts_are_rejected_but_image_text_is_ordinary_text(part_type):
    image = {'type': part_type, 'image_url': {'url': 'data:image/png;base64,YQ=='},
             'url': 'data:image/png;base64,YQ=='}
    with pytest.raises(ValueError, match='binding'):
        controls.validate_photo_payload({'input': [{'role': 'user', 'content': [image]}]})
    controls.validate_photo_payload({
        'input': [{'role': 'user', 'content': [{'type': 'text', 'text': 'describe this image'}]}]})


def test_actual_native_run_handler_rejects_unbound_images_before_admission():
    class Native:
        admitted = 0

        def _check_auth(self, request):
            return None

        async def _handle_runs(self, request):
            self.admitted += 1
            return web.Response(status=200)

    class Request:
        headers = {}
        match_info = {}

        async def json(self):
            return {'input': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]}]}

    handler = controls.run_controls_adapter(Native)()
    response = __import__('asyncio').run(handler._handle_runs(Request()))
    assert response.status == 413
    assert handler.admitted == 0


@pytest.mark.parametrize('location', ['history', 'body'])
def test_actual_native_run_handler_rejects_unbound_authenticated_image_parts(location):
    class Native:
        admitted = 0

        def _check_auth(self, request):
            return None

        async def _handle_runs(self, request):
            self.admitted += 1
            return web.Response(status=200)

    payload = photo_payload()
    alternate = {'type': 'input_image', 'image_url': {'url': 'data:image/png;base64,YQ=='}}
    if location == 'history':
        payload['conversation_history'] = [{'role': 'user', 'content': [alternate]}]
    else:
        payload['request_metadata'] = {'images': [alternate]}

    class Request:
        headers = {}
        match_info = {}

        async def json(self):
            return payload

    handler = controls.run_controls_adapter(Native)()
    response = __import__('asyncio').run(handler._handle_runs(Request()))
    assert response.status == 413
    assert handler.admitted == 0


def test_persistence_sanitization_preserves_live_bytes_and_metadata():
    payload = photo_payload()
    original = json.dumps(payload)
    safe = controls.photo_persistence_copy({
        'messages': payload['input'], 'api_content': original,
        'attachment_ids': payload['mobile_attachment_ids'], 'timestamp': 123,
    })
    assert 'data:image/' not in json.dumps(safe)
    assert '[photo attachment omitted after processing]' in json.dumps(safe)
    assert safe['attachment_ids'] == payload['mobile_attachment_ids']
    assert safe['timestamp'] == 123
    assert json.dumps(payload) == original


def test_photo_run_history_requires_a_photo_marked_exact_user_turn():
    from backend.task_reminder_presentation import matches_user
    run = {'id': 'photo-run', 'input': 'Inspect this photo', 'attachment_ids': ['a' * 32]}
    assert not matches_user({'id': 1, 'role': 'user', 'content': run['input']}, run)
    assert matches_user({'id': 2, 'role': 'user',
                         'content': run['input'] + '\n[screenshot]'}, run)
    encoded = '\x00json:' + json.dumps([
        {'type': 'text', 'text': run['input']},
        {'type': 'text', 'text': '[screenshot] [photo attachment omitted after processing]'},
    ])
    assert matches_user({'id': 3, 'role': 'user', 'content': encoded}, run)
    assert not matches_user({'id': 4, 'role': 'user',
                             'content': run['input'] + '\n[screenshot]\n[screenshot]'}, run)
    text_run = {'id': 'text-run', 'input': run['input'], 'attachment_ids': []}
    assert matches_user({'id': 5, 'role': 'user', 'content': run['input']}, text_run)


def test_photo_history_matcher_accepts_sqlite_rows_for_text_runs():
    import sqlite3
    from backend.task_reminder_presentation import matches_user

    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE runs (id TEXT, input TEXT, status TEXT)')
    db.execute('INSERT INTO runs VALUES (?, ?, ?)', ('text-run', 'Ordinary text', 'completed'))
    db.execute('CREATE TABLE messages (id INTEGER, role TEXT, content TEXT)')
    db.execute('INSERT INTO messages VALUES (?, ?, ?)', (1, 'user', 'Ordinary text'))

    assert matches_user(db.execute('SELECT * FROM messages').fetchone(),
                        db.execute('SELECT * FROM runs').fetchone())


def test_native_photo_database_copy_uses_flush_text_and_preserves_message_metadata():
    from backend.native_run_controls import _canonical_photo_message

    message = {
        'id': 'native-message-id',
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'Inspect this image'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,ZmFrZQ=='}},
        ],
        'timestamp': 42.5,
        'metadata': {'turn': 'same-turn'},
    }

    persisted = _canonical_photo_message(message)

    assert persisted == {
        'id': 'native-message-id',
        'role': 'user',
        'content': 'Inspect this image\n[screenshot]',
        'timestamp': 42.5,
        'metadata': {'turn': 'same-turn'},
    }
    assert message['content'][1]['image_url']['url'].startswith('data:image/')


def test_photo_agent_refuses_strip_retry_and_analyzer_fallback():
    class Agent:
        _vision_supported = True
        def _describe_image_for_anthropic_fallback(self, image_url, role):
            return image_url, role

    agent = controls.private_photo_agent(Agent())
    assert agent._describe_image_for_anthropic_fallback('tool-image', 'tool') == (
        'tool-image', 'tool')
    agent._mobile_photo_turn = True
    with pytest.raises(RuntimeError, match='vision'):
        agent._vision_supported = False
    with pytest.raises(RuntimeError, match='vision'):
        agent._describe_image_for_anthropic_fallback('data:image/png;base64,YQ==', 'user')
