"""Photo upload regressions use only small deterministic in-memory images."""
import binascii
import base64
import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing, contextmanager
import errno
import io
import json
import os
import sqlite3
import struct
import threading
import time
import zlib

import anyio
import httpx
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from fastapi.testclient import TestClient

from backend import attachments as attachments_module
from backend.app import Settings, create_app
from backend.hermes_client import GatewayClient
from backend.runs import RunConflict
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db


def png_fixture(seed=0):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff))

    pixels = (b'\x00' + bytes((32 + seed, 96, 160)) * 2
              + b'\x00' + bytes((160, 96, 32 + seed)) * 2)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>2I5B', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels))
            + chunk(b'IEND', b''))


def test_retained_metadata_admission_is_atomic_and_survives_restart(tmp_path):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        store.user_metadata_rows = 96
        store.global_metadata_rows = 128
        user = client.get(BASE + '/auth/me').json()['user']

        def cycle(index):
            async def chunks():
                yield png_fixture(index % 8)
            try:
                row = client.portal.call(store.upload, user, 'wa-1', f'metadata-{index}', chunks())
            except attachments_module.AttachmentError as exc:
                assert exc.status == 413
                return None
            store.release(user, 'wa-1', row['id'])
            return row['id']

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = [value for value in pool.map(cycle, range(160)) if value]
        assert len(ids) == 96
        with store.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 96
            assert store._usage(db) == 0
        restarted = attachments_module.AttachmentStore(
            store.database, store.root, user_metadata_rows=96, global_metadata_rows=128,
            min_free_bytes=0)
        with pytest.raises(attachments_module.AttachmentError, match='metadata'):
            restarted.begin(user, 'wa-1', 'over-limit')
        # Existing tombstones and immutable bindings are not admission eviction targets.
        with pytest.raises(attachments_module.AttachmentError) as expired:
            restarted.metadata(user, 'wa-1', ids[0])
        assert expired.value.status == 410
        restarted.cleanup(limit=256, now=time.time() + 32 * 86400)
        assert restarted.begin(user, 'wa-1', 'after-retention')[1]


def test_metadata_global_limit_counts_receiving_and_linked_tombstones(tmp_path):
    with photo_client(tmp_path, attachment_user_metadata_rows=8,
                      attachment_global_metadata_rows=12) as (app, client):
        store = app.state.attachments
        owner = client.get(BASE + '/auth/me').json()['user']
        users = [owner, {'id': 'synthetic-second-owner', 'profile': 'default'}]
        rows = [store.begin(users[index % 2], 'wa-1', f'global-{index}')[0]
                for index in range(12)]
        with store.connection() as db:
            db.execute("UPDATE attachments SET state='expired',reserved_bytes=0,run_id='immutable-run' "
                       "WHERE id=?", (rows[0]['id'],))
            db.commit()
        with pytest.raises(attachments_module.AttachmentError, match='metadata'):
            store.begin(owner, 'wa-1', 'over-global')
        # Even below today's footprint limit, legacy over-limit stores allow recovery.
        store.user_metadata_rows = store.global_metadata_rows = 1
        assert store.begin(users[1], 'wa-1', 'global-1', worker=True)[0]['id'] == rows[1]['id']
        store._release_upload_lease(rows[1]['id'])
        store.abort(rows[1]['id'])
        with store.connection() as db:
            assert db.execute("SELECT run_id FROM attachments WHERE id=?",
                              (rows[0]['id'],)).fetchone()[0] == 'immutable-run'


@pytest.mark.parametrize('field', ['id', 'profile', 'session'])
def test_metadata_admission_bounds_persisted_identifier_bytes(tmp_path, field):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        session = 'wa-1'
        if field == 'session':
            session = '\u00e9' * 257
        else:
            user[field] = '\u00e9' * 257
        with pytest.raises(attachments_module.AttachmentError) as rejected:
            store.begin(user, session, 'bounded-input')
        assert rejected.value.status == 422
        with store.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0


@pytest.mark.parametrize('blocked_state', ['receiving', 'releasing', 'bound', 'pending'])
@pytest.mark.parametrize('target_state', ['receiving', 'releasing', 'bound', 'pending', 'expired'])
def test_cleanup_cursor_passes_a_full_blocked_batch_across_restart(
        tmp_path, blocked_state, target_state):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        now = time.time()
        locks = []
        try:
            with store.connection() as db:
                run, _ = app.state.journal.submit(user['id'], user['profile'], 'wa-1',
                                                 'synthetic', 'cleanup-cursor-run')
                db.execute("UPDATE runs SET status='completed' WHERE id=?", (run['id'],))
                for index in range(66):
                    attachment_id = f'{index + 1:032x}'
                    name = attachment_id + '.png' if blocked_state != 'receiving' else None
                    if name:
                        (store.objects / name).write_bytes(b'charged')
                        descriptor = os.open(store.objects / name, os.O_RDONLY)
                        attachments_module.fcntl.flock(descriptor, attachments_module.fcntl.LOCK_SH)
                        locks.append(descriptor)
                    else:
                        (store.staging / (attachment_id + '.part')).write_bytes(b'charged')
                        locks.append(store._acquire_upload_lease(attachment_id))
                    db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                        size,reserved_bytes,stored_name,state,run_id,created_at,expires_at,metadata_expires_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (attachment_id, user['id'], user['profile'], 'wa-1', f'blocked-{index}',
                         7 if name else 0, 0 if name else attachments_module.RESERVATION_BYTES,
                         name, blocked_state, run['id'] if blocked_state == 'bound' else None,
                         now - 2 * 86400 + index, now - 86400, now + 86400))
                target = 'f' * 32
                name = target + '.png' if target_state not in ('receiving', 'expired') else None
                if name:
                    (store.objects / name).write_bytes(b'reclaim')
                elif target_state == 'receiving':
                    (store.staging / (target + '.part')).write_bytes(b'reclaim')
                db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                    size,reserved_bytes,stored_name,state,run_id,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (target, user['id'], user['profile'], 'wa-1', 'unblocked',
                     7 if name else 0,
                     attachments_module.RESERVATION_BYTES if target_state == 'receiving' else 0,
                     name, target_state, run['id'] if target_state == 'bound' else None,
                     now - 86400, now - 1, now - 1))
                db.commit()
            store.cleanup(limit=64)
            restarted = attachments_module.AttachmentStore(store.database, store.root)
            restarted.cleanup(limit=64)
            assert not (store.objects / (target + '.png')).exists()
            assert not (store.staging / (target + '.part')).exists()
            with store.connection() as db:
                rows = db.execute("SELECT * FROM attachments WHERE id!=?", (target,)).fetchall()
                assert len(rows) == 66
                assert all(row['size'] + row['reserved_bytes'] > 0 for row in rows)
                target_row = db.execute('SELECT * FROM attachments WHERE id=?', (target,)).fetchone()
                assert target_row is None or target_row['state'] == 'expired'
            for index in range(66):
                attachment_id = f'{index + 1:032x}'
                path = (store.staging / (attachment_id + '.part') if blocked_state == 'receiving'
                        else store.objects / (attachment_id + '.png'))
                assert path.read_bytes() == b'charged'
            assert all(os.fstat(descriptor) for descriptor in locks)
        finally:
            for descriptor in locks:
                os.close(descriptor)


@pytest.mark.parametrize('failure', [False, True])
def test_cancelled_decoder_keeps_staging_reservation_and_lease_until_exit(
        tmp_path, monkeypatch, failure):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started, finish = threading.Event(), threading.Event()
        normalize = attachments_module._normalize_image

        def blocked_decode(path):
            with open(path, 'rb') as source:
                started.set()
                assert finish.wait(5)
                assert source.read()
            if failure:
                raise attachments_module.AttachmentError(422, 'synthetic decode failure')
            return normalize(path)

        monkeypatch.setattr(attachments_module, '_normalize_image', blocked_decode)

        async def chunks():
            yield png_fixture()

        async def exercise():
            task = asyncio.create_task(store.upload(user, 'wa-1', 'decoder-cancel', chunks()))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(.05)
            try:
                assert not task.done()
                with store.connection() as db:
                    row = db.execute('SELECT * FROM attachments').fetchone()
                    assert row and row['reserved_bytes'] == attachments_module.RESERVATION_BYTES
                assert (store.staging / (row['id'] + '.part')).exists()
                assert store._upload_is_live(row['id'])
                task.cancel()  # Repeated cancellation cannot relinquish worker ownership.
                await asyncio.sleep(.01)
                assert not task.done()
            finally:
                finish.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
        asyncio.run(exercise())
        assert not list(store.staging.iterdir())
        with store.connection() as db:
            assert store._usage(db) == 0
        monkeypatch.setattr(attachments_module, '_normalize_image', normalize)
        assert asyncio.run(store.upload(user, 'wa-1', 'decoder-cancel', chunks()))['size'] > 0


def test_decoder_does_not_consume_photo_read_worker_slots(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started, finish = threading.Event(), threading.Event()
        normalize = attachments_module._normalize_image
        def blocked_decode(path):
            started.set()
            assert finish.wait(5)
            return normalize(path)
        monkeypatch.setattr(attachments_module, '_normalize_image', blocked_decode)
        async def chunks():
            yield png_fixture()
        async def exercise():
            task = asyncio.create_task(store.upload(user, 'wa-1', 'isolated-decoder', chunks()))
            assert await asyncio.to_thread(started.wait, 2)
            try:
                assert store._upload_io_slots._value == attachments_module.UPLOAD_IO_WORKERS
            finally:
                finish.set()
                await task
        asyncio.run(exercise())


def test_anyio_cancelled_decoder_shields_followup_cleanup_under_io_contention(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started, finish = threading.Event(), threading.Event()
        normalize = attachments_module._normalize_image
        def blocked_decode(path):
            started.set()
            assert finish.wait(5)
            return normalize(path)
        monkeypatch.setattr(attachments_module, '_normalize_image', blocked_decode)
        async def chunks():
            yield png_fixture()
        async def exercise():
            scopes = []
            async def upload():
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await store.upload(user, 'wa-1', 'anyio-decoder', chunks())
            task = asyncio.create_task(upload())
            assert await asyncio.to_thread(started.wait, 2)
            held = 0
            while not store._upload_io_slots.locked():
                await store._upload_io_slots.acquire()
                held += 1
            release = asyncio.Event()
            async def occupy():
                async with store._upload_io_slots:
                    await release.wait()
            occupier = asyncio.create_task(occupy())
            await asyncio.sleep(0)
            scopes[0].cancel()
            await asyncio.sleep(.02)
            finish.set()
            await asyncio.sleep(.05)
            try:
                assert not task.done()
            finally:
                release.set()
                for _ in range(held):
                    store._upload_io_slots.release()
                await occupier
                await task
            assert not store._upload_leases
            with store.connection() as db:
                assert db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] == 0
            assert not list(store.staging.iterdir())
            assert (await store.upload(user, 'wa-1', 'anyio-decoder', chunks()))['size'] > 0
        asyncio.run(exercise())


def test_cleanup_wraps_with_new_eligible_rows_ahead_of_late_expiry(tmp_path):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        now = time.time()
        with store.connection() as db:
            for index in range(3):
                db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                    state,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,'pending',?,?,?)''',
                    (f'{index + 1:032x}', user['id'], user['profile'], 'wa-1', f'cycle-{index}',
                     now - 100 + index, now + 100 if index == 0 else now - 1, now + 86400))
            db.commit()
        store.cleanup(limit=1)
        with store.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (f'{1:032x}',))
            db.commit()
        for index in range(10):
            with store.connection() as db:
                db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                    state,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,'pending',?,?,?)''',
                    (f'{index + 10:032x}', user['id'], user['profile'], 'wa-1', f'new-{index}',
                     now + index, now - 1, now + 86400))
                db.commit()
            restarted = attachments_module.AttachmentStore(store.database, store.root)
            restarted.cleanup(limit=1)
            with store.connection() as db:
                if db.execute('SELECT state FROM attachments WHERE id=?',
                              (f'{1:032x}',)).fetchone()[0] == 'expired':
                    break
        else:
            pytest.fail('Newer eligible rows starved a late-expiring row across restart')


def _photo_scope(client, attachment_id):
    path = BASE + '/sessions/wa-1/attachments/' + attachment_id
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
            'path': path, 'raw_path': path.encode(), 'query_string': b'',
            'root_path': '', 'server': ('testserver', 80), 'client': ('127.0.0.1', 1),
            'headers': [(b'cookie', '; '.join(f'{k}={v}' for k, v in client.cookies.items()).encode())]}


@pytest.mark.parametrize('after_open', [False, True])
def test_cancelled_read_open_drains_worker_and_closes_exact_descriptor(
        tmp_path, monkeypatch, after_open):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        row = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                          headers={'Idempotency-Key': 'read-cancel'}).json()
        started, finish = threading.Event(), threading.Event()
        original = store.open_image
        opened = []

        def blocked_open(*args):
            if after_open:
                result = original(*args)
                opened.append(result[0])
            started.set()
            assert finish.wait(5)
            if not after_open:
                result = original(*args)
                opened.append(result[0])
            return result

        monkeypatch.setattr(store, 'open_image', blocked_open)
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            pass
        async def exercise():
            task = asyncio.create_task(app(_photo_scope(client, row['id']), receive, send))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(.05)
            try:
                assert not task.done()
                task.cancel()
                await asyncio.sleep(.01)
                assert not task.done()
            finally:
                finish.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
        asyncio.run(exercise())
        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
        unrelated = os.open(store.database, os.O_RDONLY)
        try:
            store.release(user, 'wa-1', row['id'])
            store.cleanup()
            assert os.fstat(unrelated)
            with store.connection() as db:
                assert store._usage(db) == 0
        finally:
            os.close(unrelated)


def test_photo_disconnect_drains_read_before_descriptor_can_be_reused(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        row = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                          headers={'Idempotency-Key': 'disconnect-reader'}).json()
        started, finish = threading.Event(), threading.Event()
        original_read = os.read
        readers = []
        def blocked_read(descriptor, count):
            readers.append(descriptor)
            started.set()
            assert finish.wait(5)
            return original_read(descriptor, count)
        monkeypatch.setattr(os, 'read', blocked_read)

        request_sent = False
        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            assert await asyncio.to_thread(started.wait, 2)
            return {'type': 'http.disconnect'}
        async def send(message):
            pass
        async def exercise():
            scope = _photo_scope(client, row['id'])
            scope['asgi']['spec_version'] = '2.0'
            task = asyncio.create_task(app(scope, receive, send))
            assert await asyncio.to_thread(started.wait, 2)
            await asyncio.sleep(.05)
            try:
                assert not task.done()
                assert os.fstat(readers[0])
                store.release(user, 'wa-1', row['id'])
                with store.connection() as db:
                    assert store._usage(db) == row['size']
            finally:
                finish.set()
                await asyncio.wait_for(task, 2)
        asyncio.run(exercise())
        assert len(readers) == 1
        with pytest.raises(OSError):
            os.fstat(readers[0])
        unrelated = os.open(store.database, os.O_RDONLY)
        try:
            store.cleanup()
            assert os.fstat(unrelated)
            with store.connection() as db:
                assert store._usage(db) == 0
        finally:
            os.close(unrelated)


@pytest.mark.parametrize('failure', ['start-error', 'start-cancel', 'body-error',
                                     'body-cancel', 'normal'])
def test_photo_response_finalizes_reader_even_before_first_iteration(tmp_path, monkeypatch, failure):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        row = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                          headers={'Idempotency-Key': 'response-finalize'}).json()
        original = store.open_image
        opened = []
        closed = []
        original_close = os.close
        def record_open(*args):
            result = original(*args)
            opened.append(result[0])
            return result
        def record_close(descriptor):
            if descriptor in opened:
                closed.append(descriptor)
            original_close(descriptor)
        monkeypatch.setattr(store, 'open_image', record_open)
        monkeypatch.setattr(os, 'close', record_close)
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            kind = 'start' if message['type'] == 'http.response.start' else 'body'
            if failure == kind + '-error':
                raise RuntimeError('synthetic send failure')
            if failure == kind + '-cancel':
                raise asyncio.CancelledError()
        async def exercise():
            if failure == 'normal':
                await app(_photo_scope(client, row['id']), receive, send)
            else:
                with pytest.raises((RuntimeError, asyncio.CancelledError)):
                    await app(_photo_scope(client, row['id']), receive, send)
        asyncio.run(exercise())
        assert len(opened) == 1
        assert closed == opened
        with pytest.raises(OSError):
            os.fstat(opened[0])
        store.release(user, 'wa-1', row['id'])
        with store.connection() as db:
            assert store._usage(db) == 0


@contextmanager
def photo_client(tmp_path, *, gateway_client=None, raise_server_exceptions=True, **settings_options):
    home = tmp_path / 'native'
    home.mkdir(parents=True)
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP, **settings_options),
                     gateway_client=gateway_client)
    with TestClient(app, base_url=ORIGIN,
                    raise_server_exceptions=raise_server_exceptions) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        yield app, client


def test_photo_upload_returns_private_metadata_for_the_owned_session(tmp_path):
    with photo_client(tmp_path) as (_, client):
        response = client.post(
            BASE + '/sessions/wa-1/attachments',
            content=png_fixture(),
            headers={'Content-Type': 'image/svg+xml', 'Idempotency-Key': 'photo-upload-1'},
        )

        assert response.status_code == 201, response.text
        attachment = response.json()
        assert attachment['content_type'] == 'image/png'
        assert (attachment['width'], attachment['height']) == (2, 2)
        assert attachment['status'] == 'pending'
        assert 'filename' not in attachment and 'data' not in attachment
        assert len(attachment['id']) >= 32
        assert attachment['url'].endswith('/attachments/' + attachment['id'])


def test_upload_retry_reuses_the_same_private_file_and_rejects_changed_data(tmp_path):
    with photo_client(tmp_path) as (app, client):
        path = BASE + '/sessions/wa-1/attachments'
        headers = {'Content-Type': 'application/octet-stream', 'Idempotency-Key': 'stable-photo-key'}

        first = client.post(path, content=png_fixture(), headers=headers)
        retry = client.post(path, content=png_fixture(), headers=headers)
        changed = client.post(path, content=png_fixture(1), headers=headers)

        assert first.status_code == retry.status_code == 201
        assert first.json()['id'] == retry.json()['id']
        assert changed.status_code == 409
        assert len(list(app.state.attachments.objects.iterdir())) == 1


def test_uploaded_image_is_private_normalized_and_served_only_to_its_session(tmp_path):
    with photo_client(tmp_path) as (app, client):
        raw = png_fixture()
        response = client.post(BASE + '/sessions/wa-1/attachments', content=raw,
                               headers={'Idempotency-Key': 'private-photo'})
        attachment = response.json()
        stored_path = app.state.attachments.objects / (attachment['id'] + '.png')

        served = client.get(attachment['url'])
        wrong_session = client.get(BASE + '/sessions/not-owned/attachments/' + attachment['id'])

        assert response.status_code == 201
        assert stored_path.stat().st_mode & 0o777 == 0o600
        assert app.state.attachments.root.stat().st_mode & 0o777 == 0o700
        assert served.status_code == 200
        assert served.headers['content-type'] == 'image/png'
        assert served.headers['x-content-type-options'] == 'nosniff'
        assert served.headers['cache-control'] == 'no-store'
        assert served.content == stored_path.read_bytes()
        assert raw not in app.state.journal.path.read_bytes()
        assert wrong_session.status_code == 404


def test_invalid_image_and_missing_csrf_leave_no_upload_reservation(tmp_path):
    with photo_client(tmp_path) as (app, client):
        path = BASE + '/sessions/wa-1/attachments'
        invalid = client.post(path, content=b'<svg onload="alert(1)"></svg>',
                              headers={'Content-Type': 'image/png', 'Idempotency-Key': 'invalid-photo'})
        csrf = client.headers.pop('X-CSRF-Token')
        denied = client.post(path, content=png_fixture(),
                             headers={'Content-Type': 'image/png', 'Idempotency-Key': 'no-csrf'})
        client.headers['X-CSRF-Token'] = csrf

        assert invalid.status_code == 415
        assert denied.status_code == 403
        with app.state.attachments.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0
        assert list(app.state.attachments.staging.iterdir()) == []


def test_quota_and_minimum_free_space_fail_before_receiving_bytes(tmp_path):
    with photo_client(tmp_path, attachment_user_quota_bytes=12 * 1024 * 1024,
                      attachment_min_free_bytes=0) as (_, client):
        path = BASE + '/sessions/wa-1/attachments'
        headers = {'Idempotency-Key': 'first-photo'}
        first = client.post(path, content=png_fixture(), headers=headers)
        full = client.post(path, content=png_fixture(1),
                           headers={'Idempotency-Key': 'second-photo'})
        assert first.status_code == 201
        assert full.status_code == 413

    with photo_client(tmp_path / 'low-space', attachment_min_free_bytes=2**62) as (_, low_space):
        rejected = low_space.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                                  headers={'Idempotency-Key': 'low-space'})
        assert rejected.status_code == 507


def test_concurrent_photo_uploads_serialize_image_decoding(tmp_path, monkeypatch):
    active = maximum_active = 0
    counter_lock = threading.Lock()
    normalize = attachments_module._normalize_image

    def observed_normalize(path):
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(.05)
            return normalize(path)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(attachments_module, '_normalize_image', observed_normalize)
    with photo_client(tmp_path) as (_, client):
        def upload(index):
            return client.post(
                BASE + '/sessions/wa-1/attachments', content=png_fixture(index),
                headers={'Idempotency-Key': f'concurrent-decode-{index}'})

        with ThreadPoolExecutor(max_workers=6) as executor:
            responses = list(executor.map(upload, range(6)))

        assert [response.status_code for response in responses] == [201] * 6
        assert maximum_active == 1


def test_upload_filesystem_work_keeps_the_event_loop_responsive(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        original_fsync = attachments_module.os.fsync

        def slow_fsync(descriptor):
            time.sleep(.12)
            return original_fsync(descriptor)

        monkeypatch.setattr(attachments_module.os, 'fsync', slow_fsync)

        async def chunks():
            yield png_fixture()

        async def upload_with_heartbeat():
            upload = asyncio.create_task(store.upload(user, 'wa-1', 'heartbeat-photo', chunks()))
            heartbeats = 0
            while not upload.done():
                heartbeats += 1
                await asyncio.sleep(.01)
            return heartbeats, await upload

        heartbeats, metadata = asyncio.run(upload_with_heartbeat())

        assert heartbeats >= 10
        assert metadata['status'] == 'pending'


def test_cancelled_photo_upload_waits_for_worker_write_before_cleanup(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started = threading.Event()
        write_all = attachments_module._write_all

        def slow_write(descriptor, value):
            started.set()
            time.sleep(.1)
            write_all(descriptor, value)

        monkeypatch.setattr(attachments_module, '_write_all', slow_write)

        async def chunks():
            yield png_fixture()

        async def cancel_upload():
            upload = asyncio.create_task(store.upload(user, 'wa-1', 'cancel-photo', chunks()))
            assert await asyncio.to_thread(started.wait, 2)
            upload.cancel()
            with pytest.raises(asyncio.CancelledError):
                await upload

        asyncio.run(cancel_upload())

        with store.connection() as db:
            assert db.execute(
                "SELECT count(*) FROM attachments WHERE state='receiving'").fetchone()[0] == 0
        assert list(store.staging.iterdir()) == []
        assert list(store.objects.iterdir()) == []


def test_cancelled_photo_upload_closes_descriptor_returned_by_delayed_open(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started, resume = threading.Event(), threading.Event()
        opened = []
        original_open = attachments_module.os.open

        def delayed_open(path, *args, **kwargs):
            descriptor = original_open(path, *args, **kwargs)
            if str(path).endswith('.part'):
                opened.append(descriptor)
                started.set()
                assert resume.wait(5)
            return descriptor

        monkeypatch.setattr(attachments_module.os, 'open', delayed_open)

        async def chunks():
            yield png_fixture()

        async def cancel_upload():
            upload = asyncio.create_task(store.upload(user, 'wa-1', 'cancel-open', chunks()))
            assert await asyncio.to_thread(started.wait, 2)
            upload.cancel()
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await upload

        asyncio.run(cancel_upload())

        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
        with store.connection() as db:
            assert db.execute(
                "SELECT count(*) FROM attachments WHERE state='receiving'").fetchone()[0] == 0


def test_cancelled_reservation_worker_releases_its_lease_and_reservation(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        started = threading.Event()
        begin = store.begin

        def slow_begin(*args, **kwargs):
            result = begin(*args, **kwargs)
            started.set()
            time.sleep(.1)
            return result

        monkeypatch.setattr(store, 'begin', slow_begin)

        async def chunks():
            yield png_fixture()

        async def cancel_upload():
            upload = asyncio.create_task(store.upload(user, 'wa-1', 'cancel-reservation', chunks()))
            assert await asyncio.to_thread(started.wait, 2)
            upload.cancel()
            with pytest.raises(asyncio.CancelledError):
                await upload

        asyncio.run(cancel_upload())

        with store.connection() as db:
            assert db.execute(
                "SELECT count(*) FROM attachments WHERE state='receiving'").fetchone()[0] == 0
        assert list(store.staging.iterdir()) == []
        assert store._upload_leases == {}


def test_orphan_cleanup_preserves_active_temp_and_new_reservation_files(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        user = client.get(BASE + '/auth/me').json()['user']
        active, _ = store.begin(user, 'wa-1', 'orphan-active-temp')
        temporary = store.objects / (active['id'] + '.tmp')
        temporary.write_bytes(b'active normalized output')
        store._cleanup_orphans(16)
        assert temporary.exists()

        entered_scan = threading.Event()
        resume_scan = threading.Event()
        begin_started = threading.Event()
        reserved = threading.Event()
        original_iterdir = type(store.objects).iterdir

        def paused_iterdir(directory):
            if (directory == store.objects
                    and threading.current_thread().name == 'orphan-sweep'):
                entered_scan.set()
                assert resume_scan.wait(5)
            return original_iterdir(directory)

        monkeypatch.setattr(type(store.objects), 'iterdir', paused_iterdir)
        sweep = threading.Thread(target=lambda: store.cleanup(limit=16, rescan=True),
                                 name='orphan-sweep')
        sweep.start()
        assert entered_scan.wait(2)

        reservation = {}

        def begin_upload():
            begin_started.set()
            reservation['row'] = store.begin(user, 'wa-1', 'orphan-reservation-race')[0]
            reserved.set()

        begin = threading.Thread(target=begin_upload)
        begin.start()
        assert begin_started.wait(2)
        reserved_during_scan = reserved.wait(.25)
        raced_stage = None
        if reserved_during_scan:
            raced_stage = store.staging / (reservation['row']['id'] + '.part')
            raced_stage.write_bytes(b'active upload')
        resume_scan.set()
        sweep.join(5)
        begin.join(5)
        assert not sweep.is_alive() and not begin.is_alive()
        assert reserved.is_set()
        if not reserved_during_scan:
            raced_stage = store.staging / (reservation['row']['id'] + '.part')
            raced_stage.write_bytes(b'active upload')
        assert raced_stage.exists()


def test_expiry_reclaims_only_attachment_bytes_and_returns_controlled_placeholder(tmp_path):
    with photo_client(tmp_path) as (app, client):
        uploaded = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                               headers={'Idempotency-Key': 'expiring-photo'}).json()
        with app.state.attachments.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (uploaded['id'],))
            db.commit()

        assert app.state.attachments.cleanup(now=1) == 1
        expired = client.get(uploaded['url'])
        assert expired.status_code == 410
        assert 'message text is still available' in expired.json()['detail']
        assert not (app.state.attachments.objects / (uploaded['id'] + '.png')).exists()


@pytest.mark.parametrize('status', ['queued', 'running', 'unknown'])
def test_run_images_honor_nonterminal_attachment_pins_after_expiry(tmp_path, status):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        uploaded = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': f'pinned-{status}'}).json()
        run, _ = app.state.journal.submit(
            user['id'], 'default', 'wa-1', 'Use this photo', f'pinned-{status}',
            attachment_ids=[uploaded['id']], attachment_store=app.state.attachments)
        if status == 'running':
            app.state.journal.set_upstream(user['id'], run['id'], 'native-pinned')
        elif status == 'unknown':
            app.state.journal.finish(user['id'], run['id'], 'unknown')
        with app.state.attachments.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (uploaded['id'],))
            db.commit()

        images = app.state.attachments.run_images(
            user['id'], 'default', 'wa-1', run['id'], [uploaded['id']])

        assert len(images) == 1
        assert base64.b64decode(images[0]['image_url']['url'].split(',', 1)[1]) == (
            app.state.attachments.objects / (uploaded['id'] + '.png')).read_bytes()


@pytest.mark.parametrize('error_number', [errno.ENOSPC, errno.EDQUOT])
def test_disk_full_upload_returns_actionable_error_and_releases_reservation(
        tmp_path, monkeypatch, error_number):
    with photo_client(tmp_path, raise_server_exceptions=False) as (app, client):
        def disk_full(*_args):
            raise OSError(error_number, 'synthetic full disk')

        monkeypatch.setattr(attachments_module.os, 'write', disk_full)
        response = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'disk-full'})

        assert response.status_code == 507
        assert 'storage' in response.json()['detail'].lower()
        with app.state.attachments.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0
        assert list(app.state.attachments.staging.iterdir()) == []
        assert list(app.state.attachments.objects.iterdir()) == []


def test_publication_failure_removes_final_file_and_reservation(tmp_path, monkeypatch):
    with photo_client(tmp_path, raise_server_exceptions=False) as (app, client):
        store = app.state.attachments
        original_connection = store.connection
        failed = False

        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                nonlocal failed
                state = self.connection.execute(
                    'SELECT state FROM attachments ORDER BY rowid DESC LIMIT 1').fetchone()
                if state and state['state'] == 'pending' and not failed:
                    failed = True
                    raise OSError(errno.ENOSPC, 'synthetic publication failure')
                self.connection.commit()

        @contextmanager
        def failing_connection():
            connection = store.connect()
            try:
                yield CommitFailure(connection)
            finally:
                connection.close()

        monkeypatch.setattr(store, 'connection', failing_connection)
        response = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'publish-full'})

        assert response.status_code == 507
        assert failed
        with original_connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0
        assert list(store.staging.iterdir()) == []
        assert list(store.objects.iterdir()) == []


def test_crashed_receiving_reservation_is_reclaimed_for_same_upload_key(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        store = app.state.attachments
        reservation, _ = store.begin(user, 'wa-1', 'retry-dead-worker')
        (store.staging / (reservation['id'] + '.part')).write_bytes(b'interrupted')
        (store.objects / (reservation['id'] + '.tmp')).write_bytes(b'interrupted')

        response = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'retry-dead-worker'})

        assert response.status_code == 201
        assert response.json()['id'] == reservation['id']
        assert not (store.staging / (reservation['id'] + '.part')).exists()
        assert not (store.objects / (reservation['id'] + '.tmp')).exists()
        with store.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 1


def test_fsync_disk_full_returns_507_cleans_reservation_and_allows_same_key_retry(
        tmp_path, monkeypatch):
    with photo_client(tmp_path, raise_server_exceptions=False) as (app, client):
        store = app.state.attachments
        original_fsync = attachments_module.os.fsync

        def fail_fsync(_descriptor):
            raise OSError(errno.ENOSPC, 'synthetic full disk')

        monkeypatch.setattr(attachments_module.os, 'fsync', fail_fsync)
        response = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'retry-after-fsync'})

        assert response.status_code == 507
        with store.connection() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0
        assert list(store.staging.iterdir()) == []
        assert list(store.objects.iterdir()) == []

        monkeypatch.setattr(attachments_module.os, 'fsync', original_fsync)
        retry = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'retry-after-fsync'})
        assert retry.status_code == 201


def test_open_photo_reader_keeps_released_bytes_charged_until_closed(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        result = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'reader-lifetime'}).json()
        store = app.state.attachments
        descriptor, _, size = store.open_image(user, 'wa-1', result['id'])

        store.release(user, 'wa-1', result['id'])
        with store.connection() as db:
            row = db.execute('SELECT state,size,stored_name FROM attachments WHERE id=?',
                             (result['id'],)).fetchone()
        assert row['state'] == 'releasing'
        assert row['size'] == size
        assert row['stored_name']
        with store.connection() as db:
            assert store._usage(db) >= size
        assert os.read(descriptor, size)

        store.cleanup()
        with store.connection() as db:
            assert db.execute('SELECT size FROM attachments WHERE id=?',
                              (result['id'],)).fetchone()[0] == size
        os.close(descriptor)
        store.cleanup()
        with store.connection() as db:
            row = db.execute('SELECT state,size,stored_name FROM attachments WHERE id=?',
                             (result['id'],)).fetchone()
        assert (row['state'], row['size'], row['stored_name']) == ('expired', 0, None)


def test_photo_open_and_release_serialize_until_reader_lock_is_acquired(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        result = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'reader-open-race'}).json()
        store = app.state.attachments
        entered, resume = threading.Event(), threading.Event()
        original_flock = attachments_module.fcntl.flock

        def delayed_flock(descriptor, operation):
            if operation == attachments_module.fcntl.LOCK_SH:
                entered.set()
                assert resume.wait(5)
            return original_flock(descriptor, operation)

        monkeypatch.setattr(attachments_module.fcntl, 'flock', delayed_flock)
        with ThreadPoolExecutor(max_workers=2) as executor:
            opening = executor.submit(store.open_image, user, 'wa-1', result['id'])
            assert entered.wait(2)
            releasing = executor.submit(store.release, user, 'wa-1', result['id'])
            time.sleep(.05)
            assert not releasing.done(), 'release waits until the reader lock is established'
            resume.set()
            descriptor, _, size = opening.result(timeout=2)
            releasing.result(timeout=2)

        with store.connection() as db:
            row = db.execute('SELECT state,size FROM attachments WHERE id=?',
                             (result['id'],)).fetchone()
        assert row['state'] == 'releasing'
        assert row['size'] == size
        os.close(descriptor)
        store.cleanup()
        with store.connection() as db:
            assert db.execute('SELECT size FROM attachments WHERE id=?',
                              (result['id'],)).fetchone()[0] == 0


def test_release_rejects_corrupt_stored_name_without_unlinking_outside_root(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        result = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'corrupt-photo-name'}).json()
        store = app.state.attachments
        sentinel = store.root / 'sentinel'
        sentinel.write_bytes(b'preserve')
        with store.connection() as db:
            db.execute('UPDATE attachments SET stored_name=? WHERE id=?',
                       ('../sentinel', result['id']))
            db.commit()

        with pytest.raises(attachments_module.AttachmentError) as error:
            store.release(user, 'wa-1', result['id'])

        assert error.value.status == 410
        assert sentinel.read_bytes() == b'preserve'
        with store.connection() as db:
            row = db.execute('SELECT state,size FROM attachments WHERE id=?',
                             (result['id'],)).fetchone()
        assert row['state'] == 'pending'
        assert row['size'] > 0


def test_delete_photo_release_keeps_event_loop_responsive_under_writer_lock(tmp_path):
    with photo_client(tmp_path) as (app, client):
        result = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'release-lock'}).json()
        csrf = client.get(BASE + '/auth/me').json()['csrf_token']
        connection = sqlite3.connect(app.state.attachments.database,
                                     timeout=10, check_same_thread=False)
        connection.execute('BEGIN IMMEDIATE')
        unlocked = threading.Event()

        def release_lock():
            time.sleep(.15)
            connection.rollback()
            connection.close()
            unlocked.set()

        unlocker = threading.Thread(target=release_lock)
        unlocker.start()

        async def delete_with_heartbeat():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                    transport=transport, base_url=ORIGIN, cookies=dict(client.cookies),
                    headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}) as async_client:
                request = asyncio.create_task(async_client.delete(
                    BASE + '/sessions/wa-1/attachments/' + result['id']))
                heartbeats = 0
                while not request.done():
                    heartbeats += 1
                    await asyncio.sleep(.01)
                return heartbeats, await request

        heartbeats, response = asyncio.run(delete_with_heartbeat())
        unlocker.join(2)

        assert unlocked.is_set()
        assert response.status_code == 200
        assert heartbeats >= 5


def test_periodic_orphan_rescan_does_not_block_known_storage_admission(tmp_path):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        store.cleanup(limit=256, rescan=True)
        now = time.time()
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for index in range(120):
                attachment_id = f'{index + 1:032x}'
                name = attachment_id + '.png'
                db.execute('''INSERT INTO attachments(
                    id,user_id,profile,session_id,upload_key,content_type,width,height,size,
                    stored_name,state,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,'image/png',1,1,1,?,'bound',?,?,?)''',
                    (attachment_id, 'u', 'default', 'wa-1', f'known-{index}', name,
                     now, now + 1000, now + 2000))
                (store.objects / name).write_bytes(b'x')
            db.commit()
        orphan = store.objects / 'newly-orphaned.tmp'
        orphan.write_bytes(b'unknown bytes')

        store.cleanup(limit=64, rescan=True)
        user = client.get(BASE + '/auth/me').json()['user']
        reservation, fresh = store.begin(user, 'wa-1', 'known-storage-admission')

        assert fresh
        assert reservation['state'] == 'receiving'
        for _ in range(20):
            store.cleanup(limit=64)
            if not orphan.exists():
                break
        assert not orphan.exists()


def test_retry_does_not_reclaim_a_live_upload_lease(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        store = app.state.attachments
        entered, resume = threading.Event(), threading.Event()

        async def chunks():
            yield png_fixture()
            entered.set()
            await asyncio.to_thread(resume.wait)

        with ThreadPoolExecutor(max_workers=1) as executor:
            upload = executor.submit(lambda: asyncio.run(
                store.upload(user, 'wa-1', 'live-worker', chunks())))
            assert entered.wait(2)
            duplicate = client.post(
                BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                headers={'Idempotency-Key': 'live-worker'})
            assert duplicate.status_code == 409
            resume.set()
            result = upload.result(timeout=5)

        assert result['status'] == 'pending'
        assert client.get(BASE + '/sessions/wa-1/attachments/' + result['id']).status_code == 200


def test_upload_admission_waits_for_bounded_orphan_reconciliation(tmp_path):
    with photo_client(tmp_path) as (app, client):
        store = app.state.attachments
        for index in range(300):
            (store.objects / f'orphan-{index:04}.tmp').write_bytes(b'x')
        store.cleanup(limit=16, rescan=True)

        blocked = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'wait-for-sweep'})

        assert blocked.status_code == 503
        for _ in range(20):
            store.cleanup(limit=64)
            if store._orphan_reconciled:
                break
        assert store._orphan_reconciled
        accepted = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'wait-for-sweep'})
        assert accepted.status_code == 201


def test_orphan_sweep_resumes_past_retained_files(tmp_path):
    with photo_client(tmp_path) as (app, _):
        store = app.state.attachments
        now = time.time()
        retained = []
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for index in range(300):
                attachment_id = f'{index + 1:032x}'
                name = attachment_id + '.png'
                retained.append(name)
                db.execute('''INSERT INTO attachments(
                    id,user_id,profile,session_id,upload_key,content_type,width,height,size,
                    stored_name,state,run_id,position,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,'image/png',1,1,1,?,'bound','cleanup-fixture',?, ?, ?, ?)''',
                    (attachment_id, 'u', 'default', 'wa-1', f'cleanup-{index}', name,
                     index, now, now + 1000, now + 2000))
                (store.objects / name).write_bytes(b'x')
            db.commit()
        orphan = store.objects / (f'{999:032x}.png')
        orphan.write_bytes(b'orphan')

        for _ in range(6):
            store.cleanup(limit=64, rescan=True)

        assert all((store.objects / name).exists() for name in retained)
        assert not orphan.exists()


def test_normalized_image_drops_source_metadata(tmp_path):
    image = Image.new('RGB', (2, 2), 'navy')
    metadata = PngInfo()
    metadata.add_text('GPSLocation', 'synthetic fixture location')
    source = io.BytesIO()
    image.save(source, format='PNG', pnginfo=metadata)

    with photo_client(tmp_path) as (app, client):
        upload = client.post(BASE + '/sessions/wa-1/attachments', content=source.getvalue(),
                             headers={'Idempotency-Key': 'metadata-photo'})
        normalized = (app.state.attachments.objects / (upload.json()['id'] + '.png')).read_bytes()

        assert upload.status_code == 201
        assert b'GPSLocation' not in normalized
        assert b'synthetic fixture location' not in normalized


def test_owned_photo_reaches_the_same_native_run_as_text_without_journal_bytes(tmp_path, monkeypatch):
    native_payloads, metadata_threads = [], []
    image_threads, gateway_threads = [], []

    async def upstream(request):
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'session_id': 'wa-1', 'data': []})
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'mobile_photos': {
                'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                'max_request_bytes': 20_000_000, 'private_persistence': True}})
        if request.url.path == '/v1/runs':
            gateway_threads.append(threading.get_ident())
            native_payloads.append(json.loads(request.content))
            return httpx.Response(202, json={'run_id': 'native-photo-run'})
        if request.url.path.endswith('/events'):
            return httpx.Response(200, text=(
                'event: run.completed\n'
                'data: {"run_id":"native-photo-run","output":"A red and blue test image."}\n\n'))
        return httpx.Response(404)

    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-test-token',
                            execution_ready=True, transport=httpx.MockTransport(upstream))
    with photo_client(tmp_path, gateway_client=gateway) as (app, client):
        run_images = app.state.attachments.run_images
        metadata_for_history = app.state.attachments.metadata_for_history

        def observed_images(*args):
            image_threads.append(threading.get_ident())
            return run_images(*args)

        def observed_metadata(*args):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                metadata_threads.append(False)
            else:
                metadata_threads.append(True)
            return metadata_for_history(*args)

        monkeypatch.setattr(app.state.attachments, 'run_images', observed_images)
        monkeypatch.setattr(app.state.attachments, 'metadata_for_history', observed_metadata)
        upload = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                             headers={'Idempotency-Key': 'run-photo-upload'})
        attachment_id = upload.json()['id']
        body = {'session_id': 'wa-1', 'input': 'Describe the attached image.',
                'idempotency_key': 'photo-run', 'attachments': [attachment_id]}

        submitted = client.post(BASE + '/runs', json=body)
        duplicate = client.post(BASE + '/runs', json=body)
        for _ in range(100):
            run = client.get(BASE + '/runs/' + submitted.json()['id']).json()
            if run['status'] == 'completed':
                break
            time.sleep(.01)

        assert upload.status_code == 201
        assert submitted.status_code == 200
        assert duplicate.json()['id'] == submitted.json()['id']
        assert len(native_payloads) == 1
        assert len(image_threads) == 1
        assert image_threads[0] != gateway_threads[0], 'image I/O must not run on the orchestration loop'
        assert metadata_threads and not any(metadata_threads), (
            'attachment SQL must not run on the request event loop')
        payload = native_payloads[0]
        assert payload['mobile_attachment_ids'] == [attachment_id]
        content = payload['input'][-1]['content']
        assert content[0] == {'type': 'text', 'text': 'Describe the attached image.'}
        image = content[1]
        assert image['type'] == 'image_url'
        assert base64.b64decode(image['image_url']['url'].split(',', 1)[1]) == (
            app.state.attachments.objects / (attachment_id + '.png')).read_bytes()
        assert run['status'] == 'completed'
        assert image['image_url']['url'].encode() not in app.state.journal.path.read_bytes()


@pytest.mark.parametrize('rejection', ['capacity', 'capability'])
def test_native_predispatch_photo_rejection_is_failed_not_unknown(tmp_path, rejection):
    native_posts = []

    async def upstream(request):
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'session_id': 'wa-1', 'data': []})
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'mobile_photos': {
                'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                'max_request_bytes': 20_000_000, 'private_persistence': True}}
                if rejection == 'capacity' else {})
        if request.method == 'POST':
            native_posts.append(request.url.path)
            return httpx.Response(413, json={'error': {'code': 'body_too_large'}})
        return httpx.Response(404)

    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-test-token',
                            execution_ready=True, transport=httpx.MockTransport(upstream))
    with photo_client(tmp_path, gateway_client=gateway) as (app, client):
        upload = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                             headers={'Idempotency-Key': 'rejected-photo-upload'})
        submitted = client.post(BASE + '/runs', json={
            'session_id': 'wa-1', 'input': 'Inspect synthetic photo',
            'idempotency_key': 'rejected-photo-run', 'attachments': [upload.json()['id']]})
        assert submitted.status_code == 200
        for _ in range(100):
            run = client.get(BASE + '/runs/' + submitted.json()['id']).json()
            if run['status'] == 'failed':
                break
            time.sleep(.01)
        assert run['status'] == 'failed'
        assert run['upstream_id'] is None
        assert run['error']
        assert native_posts == (['/v1/runs'] if rejection == 'capacity' else [])


def test_snapshot_batches_ordered_attachment_ids_across_owned_runs(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        expected = []
        for turn in range(3):
            ids = [client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(index),
                               headers={'Idempotency-Key': f'batch-{turn}-{index}'}).json()['id']
                   for index in range(2 if turn != 1 else 0)]
            ids.reverse()
            run, _ = app.state.journal.submit(
                user['id'], 'default', 'wa-1', 'Synthetic turn', f'batch-run-{turn}',
                attachment_ids=ids, attachment_store=app.state.attachments)
            app.state.journal.finish(user['id'], run['id'], 'completed')
            expected.append(ids)
        queries = []
        connect = app.state.journal.connect

        def traced_connect():
            db = connect()
            db.set_trace_callback(queries.append)
            return db

        monkeypatch.setattr(app.state.journal, 'connect', traced_connect)
        snapshot = app.state.journal.snapshot_state(user['id'], 'default', 'wa-1')
        entries = snapshot['prior'] + [snapshot]
        assert [entry['run'].get('attachment_ids', []) for entry in entries] == expected
        attachment_reads = [query for query in queries if 'FROM attachments' in query]
        assert len(attachment_reads) == 1, attachment_reads


def test_expired_linked_photos_keep_history_and_retry_bindings(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        ids = [client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(index),
                           headers={'Idempotency-Key': f'expired-linked-{index}'}).json()['id']
               for index in range(2)]
        ids.reverse()
        run, _ = app.state.journal.submit(
            user['id'], 'default', 'wa-1', 'Keep this text', 'expired-linked-run',
            attachment_ids=ids, attachment_store=app.state.attachments)
        app.state.journal.finish(user['id'], run['id'], 'completed')
        with app.state.attachments.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0,metadata_expires_at=0 WHERE run_id=?',
                       (run['id'],))
            db.commit()
        assert app.state.attachments.cleanup(now=1) == 2
        assert app.state.attachments.cleanup(now=2) == 0
        assert list(app.state.attachments.objects.iterdir()) == []

        snapshot = app.state.journal.snapshot_state(user['id'], 'default', 'wa-1')
        assert snapshot['run']['attachment_ids'] == ids
        fetched = client.get(BASE + '/runs/' + run['id'])
        assert fetched.status_code == 200
        assert fetched.json()['attachment_ids'] == ids
        expired = [{'id': attachment_id, 'status': 'expired'} for attachment_id in ids]
        assert fetched.json()['attachments'] == expired
        history = client.get(BASE + '/sessions/wa-1/messages')
        assert history.status_code == 200
        turn = history.json()['last_run']
        assert turn['id'] == run['id']
        assert turn['input'] == 'Keep this text'
        assert turn['attachments'] == expired

        retry, created = app.state.journal.submit(
            user['id'], 'default', 'wa-1', 'Keep this text', 'expired-linked-run',
            attachment_ids=ids, attachment_store=app.state.attachments)
        assert not created and retry['id'] == run['id']
        assert retry['attachment_ids'] == ids
        with pytest.raises(RunConflict, match='other photo attachments'):
            app.state.journal.submit(
                user['id'], 'default', 'wa-1', 'Keep this text', 'expired-linked-run')


def test_history_binds_photos_to_proven_native_and_synthetic_user_turns(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        attachment_ids = [
            client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(index),
                        headers={'Idempotency-Key': f'history-binding-{index}'}).json()['id']
            for index in range(3)
        ]

        def submit(key, text, attachment_id):
            return app.state.journal.submit(
                user['id'], 'default', 'wa-1', text, key,
                history_anchor=lambda: app.state.catalog.history_anchor('default', 'wa-1'),
                attachment_ids=[attachment_id], attachment_store=app.state.attachments)[0]

        native_run = submit('history-native', 'Native photo turn', attachment_ids[0])
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.executemany(
                'INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?,?)',
                [(2, 'wa-1', 'user', 'Native photo turn\n[screenshot]', None, 3),
                 (3, 'wa-1', 'assistant', 'Native answer', None, 4)])
        app.state.journal.finish(user['id'], native_run['id'], 'completed', 'Native answer')

        synthetic_run = submit('history-synthetic', 'Synthetic photo turn', attachment_ids[1])
        app.state.journal.finish(user['id'], synthetic_run['id'], 'completed', 'Synthetic answer')

        current_run = submit('history-current', 'Current photo turn', attachment_ids[2])
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.executemany(
                'INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?,?)',
                [(4, 'wa-1', 'user', 'Current photo turn\n[screenshot]', None, 5),
                 (5, 'wa-1', 'assistant', 'Current answer', None, 6)])
        app.state.journal.finish(user['id'], current_run['id'], 'completed', 'Current answer')

        with app.state.attachments.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (attachment_ids[1],))
            db.commit()
        app.state.attachments.cleanup(now=1)
        store = app.state.attachments
        attachment_queries, off_loop = [], []
        connect = store.connect

        def traced_connect():
            db = connect()
            db.set_trace_callback(
                lambda sql: attachment_queries.append(sql)
                if 'FROM attachments a' in sql else None)
            return db

        batch = store.metadata_for_history_batch

        def traced_batch(*args):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                off_loop.append(True)
            else:
                off_loop.append(False)
            return batch(*args)

        monkeypatch.setattr(store, 'connect', traced_connect)
        monkeypatch.setattr(store, 'metadata_for_history_batch', traced_batch)

        response = client.get(BASE + '/sessions/wa-1/messages')
        assert response.status_code == 200
        user_items = [item for item in response.json()['items'] if item['role'] == 'user']
        by_content = {item['content']: item for item in user_items}
        native_photo_turn = by_content['Native photo turn\n[screenshot]']
        assert native_photo_turn['attachments'][0]['id'] == attachment_ids[0]
        assert native_photo_turn['attachments'][0]['status'] == 'bound'
        current_photo_turn = by_content['Current photo turn\n[screenshot]']
        assert current_photo_turn['attachments'][0]['id'] == attachment_ids[2]
        assert current_photo_turn['attachments'][0]['status'] == 'bound'
        assert sum(item['content'].startswith('Current photo turn') for item in user_items) == 1
        assert by_content['Synthetic photo turn']['attachments'] == [
            {'id': attachment_ids[1], 'status': 'expired'}]
        current = response.json()['run'] or response.json()['last_run']
        assert current['input'] == 'Current photo turn'
        assert current['attachments'][0]['id'] == attachment_ids[2]
        assert current['attachments'][0]['status'] == 'bound'
        assert off_loop == [True]
        assert len(attachment_queries) == 1


def test_photo_alias_history_reads_and_delayed_expiry_keep_owned_ids(tmp_path):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        canonical = 'canonical-photo-session'
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                       (canonical, 'Canonical photo session', 'api_server', 7, 8, None))

        upload = client.post(BASE + '/sessions/wa-1/attachments', content=png_fixture(),
                             headers={'Idempotency-Key': 'alias-photo-upload'})
        attachment_id = upload.json()['id']
        run, _ = app.state.journal.submit(
            user['id'], 'default', 'wa-1', 'Alias photo turn', 'alias-photo-run',
            history_anchor=lambda: app.state.catalog.history_anchor(
                'default', 'wa-1', canonical),
            attachment_ids=[attachment_id], attachment_store=app.state.attachments)
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.executemany(
                'INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?,?)',
                [(2, canonical, 'user', 'Alias photo turn\n[screenshot]', None, 9),
                 (3, canonical, 'assistant', 'Alias answer', None, 10)])
        app.state.journal.finish(user['id'], run['id'], 'completed', 'Alias answer')

        reopened = client.get(BASE + f'/sessions/{canonical}/messages?latest=true')
        assert reopened.status_code == 200, reopened.text
        native_user = next(item for item in reopened.json()['items']
                           if item['role'] == 'user' and item['content'].startswith('Alias photo turn'))
        assert native_user['attachment_ids'] == [attachment_id]
        assert native_user['attachments'][0]['status'] == 'bound'
        for session_id in ('wa-1', canonical):
            image = client.get(BASE + f'/sessions/{session_id}/attachments/{attachment_id}')
            assert image.status_code == 200
            assert image.content.startswith(b'\x89PNG')

        store = app.state.attachments
        reverse_id = client.post(
            BASE + f'/sessions/{canonical}/attachments', content=png_fixture(1),
            headers={'Idempotency-Key': 'reverse-alias-photo-upload'}).json()['id']
        reverse_run, _ = app.state.journal.submit(
            user['id'], 'default', canonical, 'Reverse alias turn', 'reverse-alias-photo-run',
            history_anchor=lambda: app.state.catalog.history_anchor(
                'default', canonical, 'wa-1'),
            attachment_ids=[reverse_id], attachment_store=store)
        with sqlite3.connect(app.state.catalog.profiles['default'] / 'state.db') as db:
            db.executemany(
                'INSERT INTO messages(id,session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?,?)',
                [(4, 'wa-1', 'user', 'Reverse alias turn\n[screenshot]', None, 11),
                 (5, 'wa-1', 'assistant', 'Reverse alias answer', None, 12)])
        app.state.journal.finish(user['id'], reverse_run['id'], 'completed', 'Reverse alias answer')
        reverse = client.get(BASE + '/sessions/wa-1/messages?latest=true')
        assert reverse.status_code == 200, reverse.text
        reverse_user = next(item for item in reverse.json()['items']
                            if item['role'] == 'user' and item['content'].startswith('Reverse alias turn'))
        assert reverse_user['attachment_ids'] == [reverse_id]
        assert reverse_user['attachments'][0]['status'] == 'bound'
        reverse_image = client.get(BASE + f'/sessions/wa-1/attachments/{reverse_id}')
        assert reverse_image.status_code == 200

        with pytest.raises(attachments_module.AttachmentError) as other_user:
            store.open_image({'id': 'another-user', 'profile': 'default'}, canonical, attachment_id)
        assert other_user.value.status == 404
        with pytest.raises(attachments_module.AttachmentError) as other_profile:
            store.open_image({'id': user['id'], 'profile': 'other'}, canonical, attachment_id)
        assert other_profile.value.status == 404
        with pytest.raises(attachments_module.AttachmentError) as unknown_alias:
            store.open_image(user, 'unrelated-session', attachment_id)
        assert unknown_alias.value.status == 404

        descriptor, _, _ = store.open_image(user, canonical, attachment_id)
        try:
            with store.connection() as db:
                db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (attachment_id,))
                db.commit()
            cleanup_now = time.time() + 1
            assert store.cleanup(now=cleanup_now) == 0
            with closing(app.state.journal.connect()) as db:
                assert app.state.journal._attachment_ids(db, run['id']) == [attachment_id]
            snapshot = app.state.journal.snapshot_state(user['id'], 'default', canonical)
            prior = next(entry for entry in snapshot['prior'] if entry['run']['id'] == run['id'])
            assert prior['run']['attachment_ids'] == [attachment_id]
            retry, created = app.state.journal.submit(
                user['id'], 'default', 'wa-1', 'Alias photo turn', 'alias-photo-run',
                attachment_ids=[attachment_id], attachment_store=store)
            assert not created and retry['id'] == run['id']
            placeholder = client.get(BASE + f'/sessions/{canonical}/messages?latest=true').json()
            current_user = next(item for item in placeholder['items']
                                if item.get('attachment_ids') == [attachment_id])
            assert current_user['attachments'] == [{'id': attachment_id, 'status': 'expired'}]
        finally:
            os.close(descriptor)
        assert store.cleanup(now=cleanup_now) == 1
        snapshot = app.state.journal.snapshot_state(user['id'], 'default', canonical)
        prior = next(entry for entry in snapshot['prior'] if entry['run']['id'] == run['id'])
        assert prior['run']['attachment_ids'] == [attachment_id]


def test_snapshot_alias_queries_are_scoped_to_owned_connected_component(tmp_path, monkeypatch):
    with photo_client(tmp_path) as (app, client):
        user = client.get(BASE + '/auth/me').json()['user']
        journal = app.state.journal
        with closing(journal.connect()) as db, db:
            for index, (requested, canonical) in enumerate(
                    [('wa-1', 'middle'), ('tip', 'middle'), ('tip', 'last')]):
                rid = f'connected-{index}'
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, user['id'], 'default', requested, 'Synthetic turn', rid,
                            'completed', 'Synthetic answer', None, None, index, index))
                db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)',
                           (rid, requested, canonical, index))
            # Legacy admissions without an anchor remain eligible only in their own Session.
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('legacy', user['id'], 'default', 'last', 'Legacy', 'legacy',
                        'completed', None, None, None, 4, 4))
        work = []
        connect = journal.connect

        def measured_connect():
            db = connect()
            db.set_progress_handler(lambda: work.append(1) or 0, 100)
            return db

        monkeypatch.setattr(journal, 'connect', measured_connect)
        baseline = journal.snapshot_state(user['id'], 'default', 'wa-1')
        baseline_work = len(work)
        with closing(connect()) as db, db:
            db.executemany('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', [
                (f'unrelated-{index}', user['id'], 'default', f'unrelated-{index}',
                 'Unrelated', f'unrelated-{index}', 'completed', None, None, None, 5, 5)
                for index in range(2000)])
            for rid, owner, profile in [('foreign-owner', 'other', 'default'),
                                        ('foreign-profile', user['id'], 'other')]:
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, owner, profile, 'wa-1', 'Foreign', rid,
                            'completed', None, None, None, 6, 6))
                db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)',
                           (rid, 'wa-1', 'unrelated-0', 6))
        work.clear()
        reopened = journal.snapshot_state(user['id'], 'default', 'wa-1')
        assert reopened == baseline
        assert len(work) <= baseline_work + 10
        entries = [*reopened['prior'], reopened]
        assert [entry['run']['id'] for entry in entries] == [
            'connected-0', 'connected-1', 'connected-2', 'legacy']
        reverse = journal.snapshot_state(user['id'], 'default', 'last')
        assert [entry['run']['id'] for entry in [*reverse['prior'], reverse]] == [
            'connected-0', 'connected-1', 'connected-2', 'legacy']
        denied = journal.snapshot_state('other', 'default', 'last')
        assert denied['run'] is None and denied['prior'] == []


def test_photo_rollback_gate_preserves_text_retries_reads_and_cleanup(tmp_path):
    native_requests = []

    async def upstream(request):
            if request.url.path == '/api/sessions/wa-1/messages':
                return httpx.Response(200, json={
                    'session_id': 'wa-1', 'requested_session_id': 'wa-1', 'data': []})
            if request.url.path == '/v1/capabilities':
                return httpx.Response(200, json={'mobile_photos': {
                    'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                    'max_request_bytes': 20_000_000, 'private_persistence': True}})
            if request.url.path == '/v1/runs':
                payload = json.loads(request.content)
                native_requests.append(payload)
                run_id = f'rollback-run-{len(native_requests)}'
                return httpx.Response(202, json={'run_id': run_id})
            if request.url.path.endswith('/events'):
                run_id = request.url.path.split('/')[-2]
                return httpx.Response(200, text=(
                    'event: run.completed\n'
                    f'data: {{"run_id":"{run_id}","output":"Synthetic answer"}}\n\n'))
            return httpx.Response(404)

    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-test-token',
                                execution_ready=True, transport=httpx.MockTransport(upstream))
    with photo_client(tmp_path, gateway_client=gateway) as (app, client):
        first_id = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(),
            headers={'Idempotency-Key': 'rollback-existing-photo'}).json()['id']
        original = {'session_id': 'wa-1', 'input': 'Analyze retained photo',
                    'idempotency_key': 'rollback-original', 'attachments': [first_id]}
        history_loader = app.state.orchestrator.history_loader
        second_waiting, release_second = threading.Event(), threading.Event()
        loads = 0

        async def barrier_history(profile, session):
            nonlocal loads
            loads += 1
            if loads == 1:
                await anyio.to_thread.run_sync(second_waiting.wait, 5)
                assert second_waiting.is_set()
            else:
                second_waiting.set()
                await anyio.to_thread.run_sync(release_second.wait, 5)
                assert release_second.is_set()
            return await history_loader(profile, session)

        app.state.orchestrator.history_loader = barrier_history
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, BASE + '/runs', json=original)
            second = pool.submit(client.post, BASE + '/runs', json=original)
            done, pending = wait((first, second), timeout=10, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            accepted = next(iter(done)).result()
            release_second.set()
            concurrent = next(iter(pending)).result(timeout=10)
        app.state.orchestrator.history_loader = history_loader
        assert concurrent.status_code == 200, concurrent.text
        assert concurrent.json()['id'] == accepted.json()['id']
        assert accepted.status_code == 200, accepted.text
        for _ in range(100):
            completed = client.get(BASE + '/runs/' + accepted.json()['id']).json()
            if completed['status'] == 'completed':
                break
            time.sleep(.01)

        pending_id = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(1),
            headers={'Idempotency-Key': 'rollback-pending-photo'}).json()['id']
        app.state.settings.photos_enabled = False
        app.state.orchestrator.photos_enabled = False

        blocked_upload = client.post(
            BASE + '/sessions/wa-1/attachments', content=png_fixture(2),
            headers={'Idempotency-Key': 'rollback-new-photo'})
        assert blocked_upload.status_code == 503
        assert 'disabled' in blocked_upload.json()['detail']
        blocked_run = client.post(BASE + '/runs', json={
            'session_id': 'wa-1', 'input': 'New photo run',
            'idempotency_key': 'rollback-new-photo-run', 'attachments': [pending_id]})
        assert blocked_run.status_code == 503
        assert blocked_run.json()['code'] == 'photos_disabled_before_admission'
        assert 'disabled' in blocked_run.json()['detail']
        text_run = client.post(BASE + '/runs', json={
            'session_id': 'wa-1', 'input': 'Text still works',
            'idempotency_key': 'rollback-text-only'})
        assert text_run.status_code == 200, text_run.text
        retried = client.post(BASE + '/runs', json=original)
        assert retried.status_code == 200
        assert retried.json()['id'] == accepted.json()['id']
        for changed in ({'input': 'Different text'}, {'attachments': []},
                        {'session_id': 'other'}, {'selection': {'model': 'other', 'provider': 'other'}}):
            mismatch = client.post(BASE + '/runs', json={**original, **changed})
            assert mismatch.status_code == 409, mismatch.text
        assert len(native_requests) == 2
        assert 'mobile_attachment_ids' not in native_requests[-1]
        assert client.get(BASE + f'/sessions/wa-1/attachments/{first_id}').status_code == 200

        with app.state.attachments.connection() as db:
            db.execute('UPDATE attachments SET expires_at=0 WHERE id=?', (pending_id,))
            db.commit()
        assert app.state.attachments.cleanup(now=time.time() + 1) == 1
        assert not (app.state.attachments.objects / f'{pending_id}.png').exists()
