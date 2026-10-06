"""Photo upload regressions use only small deterministic in-memory images."""
import binascii
import base64
from contextlib import contextmanager
import io
import json
import struct
import threading
import time
import zlib

import httpx
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.hermes_client import GatewayClient
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


@contextmanager
def photo_client(tmp_path, *, gateway_client=None, **settings_options):
    home = tmp_path / 'native'
    home.mkdir(parents=True)
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP, **settings_options),
                     gateway_client=gateway_client)
    with TestClient(app, base_url=ORIGIN) as client:
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
    native_payloads = []
    image_threads, gateway_threads = [], []

    async def upstream(request):
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'session_id': 'wa-1', 'data': []})
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

        def observed_images(*args):
            image_threads.append(threading.get_ident())
            return run_images(*args)

        monkeypatch.setattr(app.state.attachments, 'run_images', observed_images)
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
