"""Real virtual authenticator: P-256 COSE keys, packed self-attestation, signed assertions.
No production verification is patched or mocked. Test keys are ephemeral.
"""
import base64
import hashlib
import json
import secrets
import struct
import importlib.util
from pathlib import Path

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://lindayi.me'
BASE = '/hermes/app-api'
BOOTSTRAP = 'test-only-bootstrap-' + 'b' * 32


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def unb64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class VirtualAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.counter = 0
        self.user_handle = None

    def client_data(self, options, kind, origin=ORIGIN, **kwargs):
        return json.dumps({'type': kind, 'challenge': options['challenge'],
                           'origin': origin, 'crossOrigin': False}).encode()

    def register(self, options, origin=ORIGIN, rp_id='lindayi.me', uv=True):
        self.user_handle = options['user']['id']
        data = self.client_data(options, 'webauthn.create', origin)
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, 'big'),
                            -3: numbers.y.to_bytes(32, 'big')})
        auth = (hashlib.sha256(rp_id.encode()).digest() + bytes([0x41 | (4 if uv else 0)])
                + struct.pack('>I', 0) + bytes(16) + struct.pack('>H', len(self.id)) + self.id + cose)
        signature = self.key.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key',
                'response': {'clientDataJSON': b64(data), 'attestationObject': b64(cbor2.dumps(
                    {'fmt': 'packed', 'authData': auth, 'attStmt': {'alg': -7, 'sig': signature}}))}}

    def assert_(self, options, origin=ORIGIN, rp_id='lindayi.me', uv=True, counter=None):
        self.counter = self.counter + 1 if counter is None else counter
        data = self.client_data(options, 'webauthn.get', origin)
        auth = (hashlib.sha256(rp_id.encode()).digest() + bytes([1 | (4 if uv else 0)])
                + struct.pack('>I', self.counter))
        signature = self.key.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key', 'response': {
            'clientDataJSON': b64(data), 'authenticatorData': b64(auth), 'signature': b64(signature),
            'userHandle': self.user_handle}}


@pytest.fixture
def env(tmp_path):
    assert (ROOT / 'backend/auth.py').exists(), 'AuthService implementation is missing'
    from backend.auth import AuthService, build_auth_router
    now = [1_800_000_000.0]
    service = AuthService(tmp_path / 'auth.sqlite', clock=lambda: now[0], bootstrap_secret=BOOTSTRAP)
    app = FastAPI()
    app.include_router(build_auth_router(service), prefix=BASE)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    return service, client, now


def enroll(client, code=BOOTSTRAP, authenticator=None):
    authenticator = authenticator or VirtualAuthenticator()
    begin = client.post(BASE + '/auth/register/options', json={'code': code, 'display_name': 'Phone'})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    result = client.post(BASE + '/auth/register/verify', json={
        'enrollment_id': body['enrollment_id'], 'credential': authenticator.register(body['options'])})
    assert result.status_code == 200, result.text
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']
    return authenticator, result


def test_bootstrap_registration_verifies_real_attestation_and_sets_secure_session(env):
    service, client, _ = env
    assert client.get(BASE + '/auth/me').status_code == 401
    authenticator, result = enroll(client)
    assert result.json()['user']['role'] == 'owner'
    assert result.json()['user']['profile'] == 'default'
    assert result.json()['user']['status'] == 'ready'
    cookie = result.headers['set-cookie']
    assert all(x in cookie for x in ['Secure', 'HttpOnly', 'SameSite=strict', 'Path=/hermes'])
    assert 'Domain=' not in cookie
    assert client.get(BASE + '/auth/me').json() == result.json()
    raw = client.cookies.get('hermes_session')
    assert raw.encode() not in service.store.path.read_bytes()
    assert BOOTSTRAP.encode() not in service.store.path.read_bytes()
    assert client.post(BASE + '/auth/register/options', json={
        'code': BOOTSTRAP, 'display_name': 'Second owner'}).status_code == 400


def login(client, authenticator, **kwargs):
    begin = client.post(BASE + '/auth/login/options', json={})
    assert begin.status_code == 200, begin.text
    body = begin.json()
    result = client.post(BASE + '/auth/login/verify', json={
        'challenge_id': body['challenge_id'], 'credential': authenticator.assert_(body['options'], **kwargs)})
    if result.status_code == 200:
        client.headers['X-CSRF-Token'] = result.json()['csrf_token']
    return result


def test_passkey_login_logout_and_revoked_cookie(env):
    _, client, _ = env
    authenticator, original = enroll(client)
    old_cookie = client.cookies.get('hermes_session')
    assert client.post(BASE + '/auth/logout').status_code == 200
    assert client.get(BASE + '/auth/me').status_code == 401
    client.cookies.set('hermes_session', old_cookie, domain='lindayi.me', path='/hermes')
    assert client.get(BASE + '/auth/me').status_code == 401
    result = login(client, authenticator)
    assert result.status_code == 200, result.text
    assert result.json()['user'] == original.json()['user']
    assert client.cookies.get('hermes_session') != old_cookie
