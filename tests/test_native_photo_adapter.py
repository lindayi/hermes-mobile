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


def test_four_photos_fit_complete_native_payload_without_expanding_text_limit():
    payload = photo_payload()
    assert 10_000_000 < len(json.dumps(payload).encode()) < 20_000_000
    controls.validate_photo_payload(payload)
    payload['conversation_history'] = [{'role': 'user', 'content': 'x' * 10_000_000}]
    with pytest.raises(ValueError, match='text'):
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


def test_photo_agent_refuses_strip_retry_and_analyzer_fallback():
    class Agent:
        _vision_supported = True

    agent = controls.private_photo_agent(Agent())
    agent._mobile_photo_turn = True
    with pytest.raises(RuntimeError, match='vision'):
        agent._vision_supported = False
    with pytest.raises(RuntimeError, match='vision'):
        agent._describe_image_for_anthropic_fallback('data:image/png;base64,YQ==', 'user')
