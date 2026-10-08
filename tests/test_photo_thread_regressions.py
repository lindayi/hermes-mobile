"""Focused regressions for the remaining PR86 inline findings; synthetic only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sqlite3
import threading

import httpx
import pytest

from backend import attachments as attachments_module
from backend import native_run_controls as controls
from backend.hermes_client import GatewayClient, NativeRunRejected
from test_auth import BASE
from test_photo_attachments import photo_client, png_fixture


def test_text_only_native_413_does_not_misdiagnose_photos():
    async def scenario():
        gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-token',
            execution_ready=True, transport=httpx.MockTransport(
                lambda request: httpx.Response(413)))
        try:
            with pytest.raises(NativeRunRejected) as caught:
                await gateway.start('synthetic-session', 'text only')
            assert 'photo' not in str(caught.value).lower()
            assert 'before admission' in str(caught.value)
        finally:
            await gateway.close()
    asyncio.run(scenario())


def test_photo_picker_accept_matches_supported_formats():
    source = (Path(__file__).resolve().parents[1] / 'frontend/ui.mjs').read_text()
    accept = re.search(r"class:'photo-input'.*?accept:'([^']+)'", source).group(1)
    assert set(accept.split(',')) == {
        'image/jpeg', 'image/png', 'image/webp', '.jpg', '.jpeg', '.png', '.webp'}
    # Defensive validation must still reject pasted or otherwise supplied HEIC.
    assert "'image/heic'" in source
    assert "'image/heif'" in source


def test_upload_lease_teardown_holds_writer_lock_until_path_removed(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        row, fresh = store.begin(user, 'wa-1', 'lease-teardown', worker=True)
        assert fresh
        attachment_id = row['id']
        descriptor = store._upload_leases[attachment_id]
        # Retain the receiving row as happens when abort cannot remove its files.
        monkeypatch.setattr(store, '_remove_receiving_files', lambda _id: False)
        store.abort(attachment_id)
        original_close = attachments_module.os.close
        original_unlink = store._unlink
        observed = []

        def observe_writer_lock(stage):
            with store.connection() as competing:
                competing.execute('PRAGMA busy_timeout=0')
                try:
                    competing.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as exc:
                    assert 'locked' in str(exc).lower()
                    observed.append((stage, True))
                else:
                    competing.rollback()
                    observed.append((stage, False))

        def closing(fd):
            if fd == descriptor:
                observe_writer_lock('close')
            return original_close(fd)

        def unlinking(path):
            if path.name == attachment_id + '.lease':
                observe_writer_lock('unlink')
            return original_unlink(path)

        with monkeypatch.context() as patcher:
            patcher.setattr(attachments_module.os, 'close', closing)
            patcher.setattr(store, '_unlink', unlinking)
            store._release_upload_lease(attachment_id)
        assert observed == [('close', True), ('unlink', True)]
        assert attachment_id not in store._upload_leases
        assert not (store.staging / (attachment_id + '.lease')).exists()
        # The failed-abort row can subsequently be reclaimed using the same key.
        monkeypatch.setattr(store, '_remove_receiving_files', lambda _id: True)
        recovered, fresh = store.begin(user, 'wa-1', 'lease-teardown', worker=True)
        assert fresh and recovered['id'] == attachment_id
        store.abort(attachment_id)
        store._release_upload_lease(attachment_id)


def test_background_review_receives_private_copy_not_live_photo_messages():
    captured = []

    class Base:
        def _spawn_background_review(self, messages_snapshot, review_memory=False,
                                     review_skills=False, focus=None):
            captured.append((messages_snapshot, review_memory, review_skills, focus))
            # Model a plain background agent serializing the received history on error.
            return json.dumps(messages_snapshot)

    image_url = 'data:image/png;base64,c3ludGhldGljLXBob3Rv'
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Remember the useful text'},
        {'type': 'image_url', 'image_url': {'url': image_url}},
    ]}]
    before = json.dumps(messages)
    agent = controls._private_photo_class(Base)()
    result = agent._spawn_background_review(messages, True, True, 'keep focus')
    assert image_url not in result and 'c3ludGhldGljLXBob3Rv' not in result
    assert 'Remember the useful text' in result
    assert json.dumps(messages) == before, 'foreground model input must remain unchanged'
    assert captured[0][0] is not messages
    assert captured[0][1:] == (True, True, 'keep focus')
    text = [{'role': 'user', 'content': 'ordinary text'}]
    agent._spawn_background_review(text, review_skills=True)
    assert captured[-1][0] == text
    assert captured[-1][1:3] == (False, True)


def test_failed_capability_probe_returns_concurrently_admitted_same_key(tmp_path):
    entered, release = threading.Event(), threading.Event()
    capability_calls = 0
    native_posts = []

    async def upstream(request):
        nonlocal capability_calls
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'session_id': 'wa-1', 'data': []})
        if request.url.path == '/v1/capabilities':
            capability_calls += 1
            if capability_calls == 1:
                entered.set()
                assert await asyncio.to_thread(release.wait, 8)
                raise httpx.ReadTimeout('synthetic late timeout', request=request)
            return httpx.Response(200, json={'mobile_photos': {
                'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                'max_request_bytes': 20_000_000, 'private_persistence': True}})
        if request.url.path == '/v1/runs':
            native_posts.append(request)
            return httpx.Response(202, json={'run_id': 'synthetic-concurrent'})
        if request.url.path.endswith('/events'):
            return httpx.Response(200, text='event: run.completed\n'
                'data: {"run_id":"synthetic-concurrent","output":"Synthetic completion"}\n\n')
        return httpx.Response(404)

    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-token',
        execution_ready=True, transport=httpx.MockTransport(upstream))
    with photo_client(tmp_path, gateway_client=gateway) as (app, client):
        uploaded = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'concurrent-photo'}).json()
        body = {'session_id': 'wa-1', 'input': 'same photo request',
                'idempotency_key': 'concurrent-submit', 'attachments': [uploaded['id']]}
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, BASE + '/runs', json=body)
            try:
                assert entered.wait(5)
                admitted = client.post(BASE + '/runs', json=body)
                assert admitted.status_code == 200
            finally:
                release.set()
            late = first.result(timeout=10)
        assert late.status_code == 200, late.text
        assert late.json()['id'] == admitted.json()['id']
        with app.state.attachments.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
            linked = db.execute('SELECT run_id,state FROM attachments WHERE id=?',
                                (uploaded['id'],)).fetchone()
            assert linked['run_id'] == admitted.json()['id']
            assert linked['state'] == 'bound'
        assert len(native_posts) == 1
