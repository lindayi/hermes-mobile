"""Passkey-only authentication. All cryptographic verification uses py_webauthn."""
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from webauthn import (generate_registration_options, options_to_json,
                      verify_registration_response, generate_authentication_options, verify_authentication_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
    ResidentKeyRequirement, UserVerificationRequirement, PublicKeyCredentialDescriptor)

from .auth_store import AuthStore

COOKIE = 'hermes_session'
DAY = 86400


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token():
    return secrets.token_urlsafe(32)


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RegisterOptions(Body):
    code: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)


class RegisterVerify(Body):
    enrollment_id: str = Field(max_length=128)
    credential: dict


class AssertionVerify(Body):
    challenge_id: str = Field(max_length=128)
    credential: dict


class InviteBody(Body):
    label: str = Field(default='', max_length=80)
    expires_days: int = Field(default=7, ge=1, le=30)


class PasskeyBody(Body):
    label: str = Field(default='Passkey', min_length=1, max_length=80)


class AuthService:
    def __init__(self, db_path, origin='https://lindayi.me', rp_id='lindayi.me',
                 clock=time.time, bootstrap_secret=None):
        self.store = AuthStore(db_path)
        self.origin, self.rp_id, self.clock = origin, rp_id, clock
        if bootstrap_secret:
            with self.store.transaction() as db:
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('bootstrap_hash', digest(bootstrap_secret)))
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('bootstrap_expiry', str(clock() + 900)))

    def rate_limit(self, request):
        # Trust only ASGI peer address. Proxy-header trust belongs to the server configuration.
        peer = request.client.host if request.client else 'unknown'
        key = digest(peer + ':' + request.url.path)
        now = self.clock()
        with self.store.transaction() as db:
            db.execute('DELETE FROM rate_limits WHERE started_at<=?', (now-60,))
            db.execute('DELETE FROM challenges WHERE expires_at<=?', (now,))
            db.execute('INSERT OR IGNORE INTO rate_limits VALUES (?,?,0)', (key, now))
            attempts = db.execute('UPDATE rate_limits SET attempts=attempts+1 WHERE key=? RETURNING attempts', (key,)).fetchone()[0]
        if attempts > 20:
            raise HTTPException(429, 'Too many authentication attempts', headers={'Retry-After': '60'})

    @staticmethod
    def validate_client_context(credential):
        from webauthn.helpers import base64url_to_bytes
        data = json.loads(base64url_to_bytes(credential['response']['clientDataJSON']))
        if data.get('crossOrigin', False) is not False or 'topOrigin' in data:
            raise ValueError('Cross-origin ceremony forbidden')

    def require_origin(self, request):
        if request.headers.get('origin') != self.origin:
            raise HTTPException(403, 'Origin rejected')

    def require_user(self, request: Request):
        raw = request.cookies.get(COOKIE, '')
        with self.store.transaction() as db:
            row = db.execute('''SELECT u.*, s.id session_id, s.csrf_token, s.last_verified_at,
                s.created_at session_created_at, s.last_active_at, s.revoked, s.kind, s.token_hash
                FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?''', (digest(raw),)).fetchone()
        now = self.clock()
        if (not row or row['revoked'] or row['status'] == 'disabled'
            or row['kind'] != 'full' or now >= row['session_created_at'] + 90 * DAY
            or now >= row['last_active_at'] + 30 * DAY):
            raise HTTPException(401, 'Authentication required')
        return dict(row)

    def require_mutation(self, request: Request, user):
        self.require_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), user['csrf_token'].encode()):
            raise HTTPException(403, 'CSRF rejected')
        with self.authorized_transaction(user, kind=user.get('kind', 'full')) as db:
            db.execute('UPDATE sessions SET last_active_at=? WHERE id=?', (self.clock(), user['session_id']))

    @staticmethod
    def me(user):
        return {'user': {key: user[key] for key in ('id', 'role', 'profile', 'status')},
                'csrf_token': user['csrf_token']}

    def new_session(self, db, user_id, kind='full', extra=None):
        raw, sid, csrf = token(), token(), token()
        now = self.clock()
        db.execute('''INSERT INTO sessions(id,token_hash,user_id,csrf_token,created_at,last_active_at,last_verified_at)
                      VALUES (?,?,?,?,?,?,?)''', (sid, digest(raw), user_id, csrf, now, now, now))
        user = dict(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
        user['csrf_token'] = csrf
        if kind == 'recovery':
            db.execute("UPDATE sessions SET kind='recovery',last_verified_at=0 WHERE id=?", (sid,))
            payload = {'csrf_token': csrf, 'recovery': True, **(extra or {})}
            # Bind replacement ceremony to this restricted session.
            row = db.execute('SELECT data FROM challenges WHERE id=?', (extra['enrollment_id'],)).fetchone()
            data = json.loads(row['data'])
            data['session_id'] = sid
            db.execute('UPDATE challenges SET data=? WHERE id=?', (json.dumps(data), extra['enrollment_id']))
        else:
            payload = self.me(user)
        response = JSONResponse(payload, headers={'Cache-Control': 'no-store'})
        response.set_cookie(COOKIE, raw, max_age=600 if kind == 'recovery' else 90 * DAY, secure=True, httponly=True,
                            samesite='strict', path='/hermes')
        return response

    def bootstrap_valid(self, db, code_hash):
        config = dict(db.execute('SELECT key,value FROM settings').fetchall())
        return (hmac.compare_digest(config.get('bootstrap_hash', ''), code_hash)
                and self.clock() < float(config.get('bootstrap_expiry', 0))
                and not db.execute("SELECT 1 FROM users WHERE role='owner'").fetchone())

    def enrollment_role(self, db, code_hash):
        if self.bootstrap_valid(db, code_hash):
            return 'owner'
        invite = db.execute('SELECT * FROM invites WHERE token_hash=? AND revoked=0 AND used_at IS NULL AND expires_at>?',
                            (code_hash, self.clock())).fetchone()
        if not invite:
            raise HTTPException(400, 'Invalid enrollment code')
        return 'member'

    def require_fresh(self, user):
        if self.clock() - user['last_verified_at'] >= 300:
            raise HTTPException(403, 'Fresh passkey verification required')

    @staticmethod
    def require_owner(user):
        if user['role'] != 'owner':
            raise HTTPException(403, 'Owner required')

    def challenge(self, db, kind, challenge, data):
        cid = token()
        db.execute('INSERT INTO challenges VALUES (?,?,?,?,?)',
                   (cid, kind, challenge, self.clock() + 300, json.dumps(data)))
        return cid

    def take_challenge(self, cid, kind):
        with self.store.transaction() as db:
            row = db.execute('DELETE FROM challenges WHERE id=? RETURNING *', (cid,)).fetchone()
        if not row or row['kind'] != kind or row['expires_at'] <= self.clock():
            raise HTTPException(400, 'Invalid or expired ceremony')
        return row, json.loads(row['data'])

    def register_options(self, body):
        with self.store.transaction() as db:
            code_hash = digest(body.code)
            self.enrollment_role(db, code_hash)
            # Canonical profile-safe alphabet, preserving all 256 random bits.
            # Only account IDs change encoding; bearer/session tokens do not.
            uid = base64.b32encode(secrets.token_bytes(32)).decode('ascii').rstrip('=').lower()
            options = generate_registration_options(rp_id=self.rp_id, rp_name='Hermes',
                user_name=uid, user_id=uid.encode(), user_display_name=body.display_name,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED))
            cid = self.challenge(db, 'register', options.challenge,
                                 {'user_id': uid, 'code_hash': code_hash, 'display_name': body.display_name})
        return {'enrollment_id': cid, 'options': json.loads(options_to_json(options))}

    def verify_registration(self, credential, challenge):
        try:
            self.validate_client_context(credential)
            return verify_registration_response(credential=credential,
                expected_challenge=challenge['challenge'], expected_rp_id=self.rp_id,
                expected_origin=self.origin, require_user_verification=True)
        except Exception:
            raise HTTPException(400, 'Passkey verification failed') from None

    def register_verify(self, body):
        challenge, data = self.take_challenge(body.enrollment_id, 'register')
        result = self.verify_registration(body.credential, challenge)
        with self.store.transaction() as db:
            role = self.enrollment_role(db, data['code_hash'])
            uid = data['user_id']
            if db.execute('SELECT 1 FROM credentials WHERE id=?', (body.credential.get('id'),)).fetchone():
                raise HTTPException(400, 'Passkey already registered')
            if role == 'member':
                db.execute('UPDATE invites SET used_at=? WHERE token_hash=?', (self.clock(), data['code_hash']))
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                       (uid, role, 'default' if role == 'owner' else 'member_' + uid,
                        'ready' if role == 'owner' else 'pending', data['display_name'], self.clock()))
            from webauthn.helpers import bytes_to_base64url
            db.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?)',
                (bytes_to_base64url(result.credential_id), uid, result.credential_public_key,
                 result.sign_count, data['display_name'], self.clock()))
            return self.new_session(db, uid)


    def require_recovery(self, request: Request):
        with self.store.transaction() as db:
            row = db.execute('SELECT s.*,u.status,u.id user_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?',
                             (digest(request.cookies.get(COOKIE, '')),)).fetchone()
        if (not row or row['revoked'] or row['status'] == 'disabled' or row['kind'] != 'recovery'
                or self.clock() >= row['created_at'] + 600):
            raise HTTPException(401, 'Recovery session required')
        return {'id': row['user_id'], 'session_id': row['id'], 'csrf_token': row['csrf_token'],
                'kind': 'recovery'}

    def audit(self, db, event, user_id):
        db.execute('INSERT INTO audit(event,user_id,created_at) VALUES (?,?,?)', (event, user_id, self.clock()))

    def recovery_options(self, body):
        with self.store.transaction() as db:
            code = db.execute('SELECT r.*,u.status FROM recovery_codes r JOIN users u ON u.id=r.user_id WHERE token_hash=? AND used_at IS NULL',
                              (digest(body.code),)).fetchone()
            if not code or code['status'] == 'disabled':
                raise HTTPException(400, 'Invalid recovery code')
            uid = code['user_id']
            db.execute('UPDATE recovery_codes SET used_at=? WHERE token_hash=?', (self.clock(), digest(body.code)))
            db.execute('UPDATE sessions SET revoked=1 WHERE user_id=?', (uid,))
            options = generate_registration_options(rp_id=self.rp_id, rp_name='Hermes',
                user_name=uid, user_id=uid.encode(), user_display_name=body.display_name,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED))
            cid = self.challenge(db, 'recovery', options.challenge, {'user_id': uid, 'label': body.display_name})
            self.audit(db, 'recovery_started', uid)
            return self.new_session(db, uid, kind='recovery', extra={
                'enrollment_id': cid, 'options': json.loads(options_to_json(options))})

    def recovery_verify(self, user, body):
        challenge, data = self.take_challenge(body.enrollment_id, 'recovery')
        if data['session_id'] != user['session_id'] or data['user_id'] != user['id']:
            raise HTTPException(400, 'Invalid ceremony')
        result = self.verify_registration(body.credential, challenge)
        from webauthn.helpers import bytes_to_base64url
        cid = bytes_to_base64url(result.credential_id)
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM credentials WHERE id=?', (cid,)).fetchone():
                raise HTTPException(400, 'Passkey already registered')
            self.assert_current_session(db, user, kind='recovery')
            db.execute('DELETE FROM credentials WHERE user_id=?', (user['id'],))
            db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user['id'],))
            db.execute('UPDATE sessions SET revoked=1 WHERE user_id=?', (user['id'],))
            db.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?)',
                       (cid, user['id'], result.credential_public_key, result.sign_count, data['label'], self.clock()))
            self.audit(db, 'recovery_completed', user['id'])
            return self.new_session(db, user['id'])

    def assert_current_session(self, db, user, kind='full'):
        row = db.execute('SELECT s.*,u.status FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.user_id=?',
                         (user['session_id'], user['id'])).fetchone()
        now = self.clock()
        lifetime = 600 if kind == 'recovery' else 90 * DAY
        # Full-session authorization carries the bearer generation resolved by
        # the dependency. Recheck under BEGIN IMMEDIATE so a stale in-flight
        # request cannot rotate again (or mutate) after another request wins.
        if (not row or row['revoked'] or row['status'] == 'disabled' or row['kind'] != kind
                or (kind == 'full' and row['token_hash'] != user.get('token_hash'))
                or now >= row['created_at'] + lifetime or now >= row['last_active_at'] + 30 * DAY):
            raise HTTPException(401, 'Authentication required')

    @contextmanager
    def authorized_transaction(self, user, kind='full'):
        with self.store.transaction() as db:
            self.assert_current_session(db, user, kind)
            yield db

    def is_session_active(self, user_id, session_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT s.*,u.status FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.user_id=?',
                             (session_id, user_id)).fetchone()
        now = self.clock()
        return bool(row and not row['revoked'] and row['status'] != 'disabled' and row['kind'] == 'full'
                    and now < row['created_at'] + 90 * DAY and now < row['last_active_at'] + 30 * DAY)

    def add_passkey_options(self, user, label):
        from webauthn.helpers import base64url_to_bytes
        with self.store.transaction() as db:
            creds = db.execute('SELECT id FROM credentials WHERE user_id=?', (user['id'],)).fetchall()
            options = generate_registration_options(rp_id=self.rp_id, rp_name='Hermes',
                user_name=user['id'], user_id=user['id'].encode(), user_display_name=label,
                exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c['id'])) for c in creds],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED))
            cid = self.challenge(db, 'add', options.challenge, {
                'user_id': user['id'], 'session_id': user['session_id'], 'label': label})
        return {'enrollment_id': cid, 'options': json.loads(options_to_json(options))}

    def add_passkey_verify(self, user, body):
        challenge, data = self.take_challenge(body.enrollment_id, 'add')
        if data['user_id'] != user['id'] or data['session_id'] != user['session_id']:
            raise HTTPException(400, 'Invalid ceremony')
        result = self.verify_registration(body.credential, challenge)
        from webauthn.helpers import bytes_to_base64url
        cid = bytes_to_base64url(result.credential_id)
        with self.authorized_transaction(user) as db:
            if db.execute('SELECT 1 FROM credentials WHERE id=?', (cid,)).fetchone():
                raise HTTPException(400, 'Passkey already registered')
            db.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?)',
                       (cid, user['id'], result.credential_public_key, result.sign_count, data['label'], self.clock()))
        return {'ok': True}

    def stepup_options(self, user):
        from webauthn.helpers import base64url_to_bytes
        with self.store.transaction() as db:
            creds = db.execute('SELECT id FROM credentials WHERE user_id=?', (user['id'],)).fetchall()
            options = generate_authentication_options(rp_id=self.rp_id,
                user_verification=UserVerificationRequirement.REQUIRED,
                allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c['id'])) for c in creds])
            cid = self.challenge(db, 'stepup', options.challenge,
                                 {'user_id': user['id'], 'session_id': user['session_id']})
        return {'challenge_id': cid, 'options': json.loads(options_to_json(options))}

    def stepup_verify(self, user, body):
        challenge, data = self.take_challenge(body.challenge_id, 'stepup')
        if data['user_id'] != user['id'] or data['session_id'] != user['session_id']:
            raise HTTPException(400, 'Invalid ceremony')
        with self.authorized_transaction(user) as db:
            uid = self.verify_assertion(db, body.credential, challenge)
            if uid != user['id']:
                raise HTTPException(400, 'Invalid ceremony')
            self.assert_current_session(db, user)
            now, raw = self.clock(), token()
            db.execute('UPDATE sessions SET last_verified_at=?,token_hash=? WHERE id=?',
                       (now, digest(raw), user['session_id']))
        response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(COOKIE, raw, max_age=int(user['session_created_at'] + 90 * DAY - now),
                            secure=True, httponly=True, samesite='strict', path='/hermes')
        return response

    def login_options(self):
        options = generate_authentication_options(rp_id=self.rp_id,
            user_verification=UserVerificationRequirement.REQUIRED)
        with self.store.transaction() as db:
            cid = self.challenge(db, 'login', options.challenge, {})
        return {'challenge_id': cid, 'options': json.loads(options_to_json(options))}

    def verify_assertion(self, db, credential, challenge):
        if not isinstance(credential.get('id'), str):
            raise HTTPException(400, 'Passkey verification failed')
        cred = db.execute('SELECT c.*,u.status FROM credentials c JOIN users u ON u.id=c.user_id WHERE c.id=?',
                          (credential.get('id', ''),)).fetchone()
        if not cred or cred['status'] == 'disabled':
            raise HTTPException(400, 'Passkey verification failed')
        from webauthn.helpers import base64url_to_bytes
        try:
            self.validate_client_context(credential)
            handle = credential.get('response', {}).get('userHandle')
            if handle is not None and base64url_to_bytes(handle) != cred['user_id'].encode():
                raise ValueError('User handle mismatch')
            result = verify_authentication_response(credential=credential,
                expected_challenge=challenge['challenge'], expected_rp_id=self.rp_id,
                expected_origin=self.origin, credential_public_key=cred['public_key'],
                credential_current_sign_count=cred['sign_count'], require_user_verification=True)
        except Exception:
            raise HTTPException(400, 'Passkey verification failed') from None
        db.execute('UPDATE credentials SET sign_count=? WHERE id=?', (result.new_sign_count, cred['id']))
        return cred['user_id']

    def login_verify(self, body):
        challenge, _ = self.take_challenge(body.challenge_id, 'login')
        with self.store.transaction() as db:
            uid = self.verify_assertion(db, body.credential, challenge)
            return self.new_session(db, uid)


def build_auth_router(service):
    class PrivateAuthRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def bounded_handler(request: Request):
                try:
                    if request.method not in ('GET', 'HEAD'):
                        service.require_origin(request)
                        service.rate_limit(request)
                        chunks, size = [], 0
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > 65536:
                                raise HTTPException(413, 'Authentication payload too large')
                            chunks.append(chunk)
                        request._body = b''.join(chunks)
                    response = await handler(request)
                except HTTPException as exc:
                    response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
                response.headers['Cache-Control'] = 'no-store'
                return response

            return bounded_handler

    router = APIRouter(route_class=PrivateAuthRoute)

    @router.get('/auth/me')
    def me(user=Depends(service.require_user)):
        return service.me(user)

    @router.post('/auth/register/options')
    def register_options(body: RegisterOptions, request: Request):
        service.require_origin(request)
        return service.register_options(body)

    @router.post('/auth/register/verify')
    def register_verify(body: RegisterVerify, request: Request):
        service.require_origin(request)
        return service.register_verify(body)

    @router.post('/auth/login/options')
    def login_options(body: Body, request: Request):
        service.require_origin(request)
        return service.login_options()

    @router.post('/auth/login/verify')
    def login_verify(body: AssertionVerify, request: Request):
        service.require_origin(request)
        return service.login_verify(body)

    @router.post('/auth/logout')
    def logout(request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        with service.authorized_transaction(user) as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (user['session_id'],))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/hermes', secure=True, httponly=True, samesite='strict')
        return response

    @router.post('/auth/verify/options')
    def stepup_options(body: Body, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        return service.stepup_options(user)

    @router.post('/auth/verify/finish')
    def stepup_finish(body: AssertionVerify, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        return service.stepup_verify(user, body)

    @router.get('/invites')
    def invites(user=Depends(service.require_user)):
        service.require_owner(user)
        with service.authorized_transaction(user) as db:
            return {'items': [dict(r) for r in db.execute(
                'SELECT id,label,created_at,expires_at,used_at,revoked FROM invites ORDER BY created_at DESC')]}

    @router.post('/invites')
    def create_invite(body: InviteBody, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_owner(user)
        service.require_fresh(user)
        code, iid, now = token(), token(), service.clock()
        with service.authorized_transaction(user) as db:
            db.execute('INSERT INTO invites(id,token_hash,label,created_at,expires_at) VALUES (?,?,?,?,?)',
                       (iid, digest(code), body.label, now, now + body.expires_days * DAY))
            invite = dict(db.execute('SELECT id,label,created_at,expires_at,used_at,revoked FROM invites WHERE id=?', (iid,)).fetchone())
        return {'code': code, 'invite': invite}

    @router.delete('/invites/{invite_id}')
    def revoke_invite(invite_id: str, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_owner(user)
        service.require_fresh(user)
        with service.authorized_transaction(user) as db:
            if not db.execute('UPDATE invites SET revoked=1 WHERE id=?', (invite_id,)).rowcount:
                raise HTTPException(404, 'Invite not found')
        return {'ok': True}

    @router.get('/members')
    def members(user=Depends(service.require_user)):
        service.require_owner(user)
        with service.authorized_transaction(user) as db:
            return {'items': [dict(r) for r in db.execute("SELECT * FROM users WHERE role='member'")]}

    @router.post('/members/{member_id}/disable')
    def disable_member(member_id: str, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_owner(user)
        service.require_fresh(user)
        with service.authorized_transaction(user) as db:
            if not db.execute("UPDATE users SET status='disabled' WHERE id=? AND role='member'", (member_id,)).rowcount:
                raise HTTPException(404, 'Member not found')
            db.execute('UPDATE sessions SET revoked=1 WHERE user_id=?', (member_id,))
        return {'ok': True}

    @router.get('/devices')
    def devices(user=Depends(service.require_user)):
        now = service.clock()
        with service.authorized_transaction(user) as db:
            return {'items': [dict(r) for r in db.execute(
                "SELECT id,label,created_at,last_active_at,(id=?) AS current FROM sessions WHERE user_id=? AND revoked=0 AND kind='full' AND created_at>? AND last_active_at>?",
                (user['session_id'], user['id'], now-90*DAY, now-30*DAY))]}

    @router.delete('/devices')
    def revoke_devices(request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        with service.authorized_transaction(user) as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE user_id=?', (user['id'],))
        return {'ok': True}

    @router.delete('/devices/{device_id}')
    def revoke_device(device_id: str, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        with service.authorized_transaction(user) as db:
            if not db.execute('UPDATE sessions SET revoked=1 WHERE id=? AND user_id=?', (device_id, user['id'])).rowcount:
                raise HTTPException(404, 'Device not found')
        return {'ok': True}

    @router.get('/passkeys')
    def passkeys(user=Depends(service.require_user)):
        with service.authorized_transaction(user) as db:
            return {'items': [dict(r) for r in db.execute('SELECT id,label,created_at FROM credentials WHERE user_id=?', (user['id'],))]}

    @router.post('/passkeys')
    def add_passkey(body: PasskeyBody, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        return service.add_passkey_options(user, body.label)

    @router.post('/passkeys/verify')
    def add_passkey_verify(body: RegisterVerify, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        return service.add_passkey_verify(user, body)

    @router.delete('/passkeys/{credential_id}')
    def delete_passkey(credential_id: str, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        with service.authorized_transaction(user) as db:
            if not db.execute('SELECT 1 FROM credentials WHERE id=? AND user_id=?', (credential_id, user['id'])).fetchone():
                raise HTTPException(404, 'Passkey not found')
            if db.execute('SELECT count(*) FROM credentials WHERE user_id=?', (user['id'],)).fetchone()[0] <= 1:
                raise HTTPException(409, 'Cannot remove last passkey')
            db.execute('DELETE FROM credentials WHERE id=? AND user_id=?', (credential_id, user['id']))
        return {'ok': True}

    @router.post('/auth/recovery/codes')
    def recovery_codes(body: Body, request: Request, user=Depends(service.require_user)):
        service.require_mutation(request, user)
        service.require_fresh(user)
        codes = [token() for _ in range(10)]
        with service.authorized_transaction(user) as db:
            db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user['id'],))
            db.executemany('INSERT INTO recovery_codes VALUES (?,?,NULL)', [(digest(c), user['id']) for c in codes])
            service.audit(db, 'recovery_codes_issued', user['id'])
        return {'codes': codes}

    @router.post('/auth/recovery/options')
    def recovery_options(body: RegisterOptions, request: Request):
        service.require_origin(request)
        return service.recovery_options(body)

    @router.post('/auth/recovery/verify')
    def recovery_verify(body: RegisterVerify, request: Request, user=Depends(service.require_recovery)):
        service.require_mutation(request, user)
        return service.recovery_verify(user, body)

    return router
