"""Test-only JSON-lines bridge: JS UI -> actual FastAPI routers/SQLite/WebAuthn.

All accounts/keys exist only in TemporaryDirectory. No network or real profile
provisioning: ProfileProvisioner intentionally has no subprocess runner.
"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.auth import AuthService, build_auth_router, COOKIE
from backend import profiles
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll


def main():
    with TemporaryDirectory(prefix='hermes-auth-ui-') as temporary:
        root = Path(temporary)
        profiles.PROFILE_ROOT = root / 'profiles'
        profiles.PROFILE_ROOT.mkdir()
        auth = AuthService(root / 'auth.sqlite', bootstrap_secret=BOOTSTRAP)
        app = FastAPI()
        app.include_router(build_auth_router(auth), prefix=BASE)
        app.include_router(profiles.build_profiles_router(profiles.ProfileProvisioner(auth)), prefix=BASE)
        with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
            authenticator, enrolled = enroll(client)
            del client.headers['X-CSRF-Token']  # The real JS API client must send it.
            original_cookie = client.cookies.get(COOKIE)
            with auth.store.transaction() as db:
                original_session = dict(db.execute('SELECT * FROM sessions').fetchone())
                db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                           ('family1', 'member', 'member_family1', 'pending', 'Fixture family', auth.clock()))
            for line in sys.stdin:
                command = json.loads(line)
                if command['op'] == 'request':
                    response = client.request(command['method'], command['path'],
                                              headers=command.get('headers', {}), content=command.get('body'))
                    result = {'status': response.status_code, 'text': response.text}
                elif command['op'] == 'assert':
                    result = authenticator.assert_({'challenge': command['challenge']})
                elif command['op'] == 'inspect':
                    with auth.store.transaction() as db:
                        session = dict(db.execute('SELECT * FROM sessions').fetchone())
                        invites = db.execute('SELECT count(*) FROM invites').fetchone()[0]
                        member_status = db.execute("SELECT status FROM users WHERE id='family1'").fetchone()[0]
                    with TestClient(app, base_url=ORIGIN) as stale:
                        stale.cookies.set(COOKIE, original_cookie, domain='lindayi.me', path='/hermes')
                        stale_status = stale.get(BASE + '/auth/me').status_code
                    result = {'rotated': original_cookie != client.cookies.get(COOKIE),
                              'stable_session': session['id'] == original_session['id'],
                              'stable_csrf': session['csrf_token'] == enrolled.json()['csrf_token'],
                              'stale_status': stale_status, 'invites': invites, 'member_status': member_status,
                              'profile_created': (profiles.PROFILE_ROOT / 'member_family1').exists()}
                else:
                    raise ValueError('Unknown bridge operation')
                print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
