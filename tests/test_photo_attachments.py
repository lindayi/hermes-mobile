"""Photo upload regressions use only small deterministic in-memory images."""
import binascii
from contextlib import contextmanager
import struct
import zlib

from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db


def png_fixture():
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff))

    pixels = b'\x00' + b'\x20\x60\xa0' * 2 + b'\x00' + b'\xa0\x60\x20' * 2
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>2I5B', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels))
            + chunk(b'IEND', b''))


@contextmanager
def photo_client(tmp_path):
    home = tmp_path / 'native'
    home.mkdir()
    create_native_db(home / 'state.db')
    app = create_app(Settings(state_dir=tmp_path / 'app', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP))
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        yield app, client


def test_photo_upload_returns_private_metadata_for_the_owned_session(tmp_path):
    with photo_client(tmp_path) as (_, client):
        response = client.post(
            BASE + '/sessions/wa-1/attachments',
            content=png_fixture(),
            headers={'Content-Type': 'image/png', 'Idempotency-Key': 'photo-upload-1'},
        )

        assert response.status_code == 201, response.text
        attachment = response.json()
        assert attachment['content_type'] == 'image/png'
        assert (attachment['width'], attachment['height']) == (2, 2)
        assert attachment['status'] == 'pending'
        assert 'filename' not in attachment and 'data' not in attachment
        assert len(attachment['id']) >= 32
