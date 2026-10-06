"""Photo upload regressions use only small deterministic in-memory images."""
import binascii
from contextlib import contextmanager
from pathlib import Path
import struct
import zlib

from fastapi.testclient import TestClient

from backend.app import Settings, create_app
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
def photo_client(tmp_path, **settings_options):
    home = tmp_path / 'native'
    home.mkdir(parents=True)
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP, **settings_options))
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
