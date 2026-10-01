"""Provisioning uses real auth/SQLite and a fake subprocess/filesystem boundary only."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthService, build_auth_router
from test_auth import BASE, ORIGIN, BOOTSTRAP, enroll


@pytest.fixture
def provision_env(tmp_path, monkeypatch):
    assert importlib.util.find_spec('backend.profiles'), 'Profile provisioning missing'
    from backend import profiles
    root = tmp_path / 'profiles'
    root.mkdir()
    monkeypatch.setattr(profiles, 'PROFILE_ROOT', root)
    now = [1_800_000_000.0]
    auth = AuthService(tmp_path / 'auth.sqlite', clock=lambda: now[0], bootstrap_secret=BOOTSTRAP)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        target = root / argv[3]
        target.mkdir()
        (target / '.env').write_text('')
        (target / 'config.yaml').write_text('{}')
        (target / 'SOUL.md').write_text('Clean bundled identity')
        return SimpleNamespace(returncode=0)

    service = profiles.ProfileProvisioner(auth, runner=runner)
    app = FastAPI()
    app.include_router(build_auth_router(auth), prefix=BASE)
    app.include_router(profiles.build_profiles_router(service), prefix=BASE)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['Origin'] = ORIGIN
    enroll(client)
    with auth.store.transaction() as db:
        db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                   ('abc123', 'member', 'member_abc123', 'pending', 'Family', now[0]))
    return service, auth, client, calls, root, now


def test_owner_provisions_clean_member_with_argv_and_keeps_access_pending(provision_env):
    service, auth, client, calls, root, _ = provision_env
    response = client.post(BASE + '/members/abc123/provision', json={})
    assert response.status_code == 200, response.text
    assert response.json() == {'id': 'abc123', 'profile': 'member_abc123',
                               'status': 'pending', 'provisioning_status': 'provisioned', 'ready': False}
    argv, kwargs = calls[0]
    assert argv == ['hermes', 'profile', 'create', 'member_abc123', '--no-alias', '--no-skills']
    assert kwargs['shell'] is False
    assert kwargs['timeout'] == 60
    assert kwargs['env'] == {'HOME': '/home/lindayi', 'HERMES_HOME': '/home/lindayi/.hermes',
                             'PATH': '/usr/local/bin:/usr/bin:/bin'}
    assert (root / 'member_abc123' / '.env').read_text() == ''
    with auth.store.transaction() as db:
        assert db.execute('SELECT status FROM users WHERE id=?', ('abc123',)).fetchone()[0] == 'pending'


def test_status_reports_verified_ready_only_with_complete_native_profile(provision_env):
    service, auth, client, calls, root, _ = provision_env
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 200
    with auth.store.transaction() as db:
        db.execute("UPDATE users SET status='ready' WHERE id='abc123'")
    assert client.get(BASE + '/members/abc123/provision').json()['ready'] is True
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 409
    assert len(calls) == 1
    (root/'member_abc123/.env').unlink()
    status = client.get(BASE + '/members/abc123/provision').json()
    assert status['ready'] is False
    assert status['provisioning_status'] == 'failed'


def test_repeat_and_restart_report_status_without_creating_again(provision_env):
    service, auth, client, calls, root, _ = provision_env
    first = client.post(BASE + '/members/abc123/provision', json={})
    second = client.post(BASE + '/members/abc123/provision', json={})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1
    from backend.profiles import ProfileProvisioner
    restarted = ProfileProvisioner(auth, runner=service.runner)
    assert restarted.status('abc123') == first.json()
    assert client.get(BASE + '/members/abc123/provision').json() == first.json()


@pytest.mark.parametrize('target,role,profile,status,code', [
    ('missing', None, None, None, 404),
    ('abc123', 'owner', 'default', 'ready', 409),
    ('abc123', 'member', 'default', 'pending', 409),
    ('abc123', 'member', '../escape', 'pending', 409),
    ('abc123', 'member', 'member_other', 'pending', 409),
    ('abc123', 'member', 'member_abc123', 'disabled', 409),
    ('abc123', 'member', 'member_abc123', 'ready', 409),
])
def test_only_pending_own_generated_member_mapping_is_provisioned(provision_env, target, role, profile, status, code):
    service, auth, client, calls, root, _ = provision_env
    if role:
        with auth.store.transaction() as db:
            # Owner/default already exists: delete it only for the malformed owner target fixture.
            if role == 'owner':
                db.execute("UPDATE users SET role='member',profile='old_owner' WHERE role='owner'")
            if profile == 'default' and role != 'owner':
                db.execute("UPDATE users SET profile='old_owner' WHERE role='owner'")
            db.execute('UPDATE users SET role=?,profile=?,status=? WHERE id=?', (role, profile, status, target))
        if role == 'owner':
            # Service-level test keeps a genuine session while DB target role is malformed.
            from starlette.requests import Request
            request = Request({'type': 'http', 'headers': [(b'origin', ORIGIN.encode()),
                (b'x-csrf-token', client.headers['X-CSRF-Token'].encode())]})
            from backend.auth import digest
            with auth.store.transaction() as db:
                row = db.execute('SELECT u.*,s.id session_id,s.csrf_token,s.last_verified_at,s.token_hash FROM users u JOIN sessions s ON s.user_id=u.id WHERE token_hash=?',
                    (digest(client.cookies.get('hermes_session')),)).fetchone()
            user = dict(row, role='owner')
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                service.provision(request, user, target)
            assert exc.value.status_code == code
            assert calls == []
            return
    response = client.post(BASE + '/members/' + target + '/provision', json={})
    assert response.status_code == code, response.text
    assert calls == []


def test_mixed_case_auth_ids_fail_closed_instead_of_cli_silent_lowercase(provision_env):
    service, auth, client, calls, root, _ = provision_env
    with auth.store.transaction() as db:
        db.execute("UPDATE users SET id='AbC123',profile='member_AbC123' WHERE id='abc123'")
    response = client.post(BASE + '/members/AbC123/provision', json={})
    assert response.status_code == 409
    assert calls == []


@pytest.mark.parametrize('kind', ['existing', 'symlink', 'root-symlink'])
def test_does_not_claim_or_write_existing_or_escaped_profile(provision_env, kind):
    service, auth, client, calls, root, _ = provision_env
    if kind == 'existing':
        (root / 'member_abc123').mkdir()
    elif kind == 'symlink':
        (root / 'member_abc123').symlink_to(root.parent)
    else:
        root.rmdir()
        root.symlink_to(root.parent, target_is_directory=True)
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 409
    assert calls == []


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'oserror', 'missing-files'])
def test_failure_is_not_success_and_retry_reuses_same_mapping(provision_env, failure):
    import subprocess
    service, auth, client, calls, root, _ = provision_env
    good_runner = service.runner

    def fail(argv, **kwargs):
        calls.append((argv, kwargs))
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 60, output=b'PRIVATE')
        if failure == 'oserror':
            raise OSError('PRIVATE')
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0, stderr='PRIVATE')

    service.runner = fail
    response = client.post(BASE + '/members/abc123/provision', json={})
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text
    assert service.status('abc123')['provisioning_status'] == 'failed'
    assert service.status('abc123')['status'] == 'pending'
    service.runner = good_runner
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 200
    assert [call[0][3] for call in calls] == ['member_abc123', 'member_abc123']


def test_partial_failure_requires_manual_reconciliation_not_adoption(provision_env):
    service, auth, client, calls, root, _ = provision_env
    good_runner = service.runner

    def partial(argv, **kwargs):
        good_runner(argv, **kwargs)
        return SimpleNamespace(returncode=1)

    service.runner = partial
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 503
    service.runner = good_runner
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 409
    assert len(calls) == 1


def test_missing_runner_is_explicit_unavailable(provision_env):
    service, auth, client, calls, root, _ = provision_env
    service.runner = None
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 503
    assert not (root / 'member_abc123').exists()


def test_durable_creating_claim_is_visible_without_holding_auth_database_lock(provision_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from backend.profiles import ProfileProvisioner
    service, auth, client, calls, root, _ = provision_env
    entered, release = Event(), Event()
    good_runner = service.runner

    def waiting(argv, **kwargs):
        entered.set()
        assert release.wait(5)
        return good_runner(argv, **kwargs)

    service.runner = waiting
    with ThreadPoolExecutor() as pool:
        first = pool.submit(client.post, BASE + '/members/abc123/provision', json={})
        assert entered.wait(2)
        try:
            restarted = ProfileProvisioner(auth, runner=good_runner)
            assert restarted.status('abc123')['provisioning_status'] == 'creating'
            assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 200
    assert len(calls) == 1


def test_unknown_member_status_is_404(provision_env):
    service, auth, client, calls, root, _ = provision_env
    assert client.get(BASE + '/members/missing/provision').status_code == 404


def test_provisioned_files_removed_do_not_report_success(provision_env):
    service, auth, client, calls, root, _ = provision_env
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 200
    (root / 'member_abc123' / '.env').unlink()
    assert client.post(BASE + '/members/abc123/provision', json={}).status_code == 409
    assert service.status('abc123')['provisioning_status'] == 'failed'
    assert len(calls) == 1


@pytest.mark.parametrize('gate', ['anonymous', 'origin', 'csrf', 'stale', 'member', 'revoked', 'body-profile'])
def test_provision_admin_gate(provision_env, gate):
    service, auth, client, calls, root, now = provision_env
    data = {}
    code = 403
    if gate == 'anonymous':
        client.cookies.clear()
        code = 401
    elif gate == 'origin':
        client.headers['Origin'] = 'https://evil.example'
    elif gate == 'csrf':
        client.headers['X-CSRF-Token'] = 'wrong'
    elif gate == 'stale':
        now[0] += 300
    elif gate == 'member':
        with auth.store.transaction() as db:
            db.execute("UPDATE users SET role='member' WHERE role='owner'")
        assert client.get(BASE + '/members/abc123/provision').status_code == 403
    elif gate == 'revoked':
        with auth.store.transaction() as db:
            db.execute('UPDATE sessions SET revoked=1')
        code = 401
    else:
        data['profile'] = 'default'
        code = 422
    assert client.post(BASE + '/members/abc123/provision', json=data).status_code == code
    assert calls == []


def test_clean_native_profile_has_no_config_until_operator_setup(provision_env):
    service, auth, client, calls, root, _ = provision_env
    original = service.runner

    def native_layout(argv, **kwargs):
        result = original(argv, **kwargs)
        (root / argv[3] / 'config.yaml').unlink()
        return result

    service.runner = native_layout
    response = client.post(BASE + '/members/abc123/provision', json={})
    assert response.status_code == 200, response.text
    assert response.json()['provisioning_status'] == 'provisioned'
    assert response.json()['status'] == 'pending'
    assert response.json()['ready'] is False
    assert not (root / 'member_abc123' / 'config.yaml').exists()
