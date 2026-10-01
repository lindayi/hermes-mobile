"""Registration/profile alphabet regression with real ephemeral passkey signatures."""
import base64
import re

from fastapi.testclient import TestClient
from backend.profiles import ProfileProvisioner
from test_auth import env, enroll, login, BASE, ORIGIN


def test_new_member_identity_preserves_256_bits_and_passes_profile_validation(env):
    service, owner, _ = env
    enroll(owner)
    code = owner.post(BASE + '/invites', json={'label': 'Identity regression'}).json()['code']
    member = TestClient(owner.app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    authenticator, result = enroll(member, code)
    user = result.json()['user']

    # Lowercase unpadded base32 keeps all 32 random bytes within the 57-char suffix limit.
    assert re.fullmatch(r'[a-z2-7]{52}', user['id'])
    assert len(base64.b32decode(user['id'].upper() + '====')) == 32
    assert user['profile'] == 'member_' + user['id']
    status = ProfileProvisioner(service).status(user['id'])
    assert status['provisioning_status'] == 'pending'
    assert status['ready'] is False
    assert user['status'] == 'pending'
    assert login(member, authenticator).json()['user'] == user

    # Identity encoding must not change bearer-token generation.
    assert len(member.cookies.get('hermes_session')) == 43
    assert len(result.json()['csrf_token']) == 43
