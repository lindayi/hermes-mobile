"""Authenticated owner attestation and validation, never live listeners."""
import pytest
from contextlib import closing
from test_session_deletion import deletion_app
from test_auth import BASE


@pytest.mark.parametrize('version', [None, False, True, '1', 0, 2, 1])
def test_deletion_availability_requires_exact_native_version(deletion_app, version):
    app, client, user, calls, behavior = deletion_app
    behavior['version'] = version
    allowed = type(version) is int and version == 1
    assert client.get(BASE+'/sessions').json().get('deletion_available', False) is allowed
    result = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True})
    assert result.status_code == (200 if allowed else 503)
    assert any(method == 'DELETE' for method, _ in calls) is allowed


@pytest.mark.parametrize('body', [{}, {'confirm': False}, {'confirm': 1}, {'confirm': 'true'}, {'confirm': True, 'profile': 'default'}, None, []])
def test_delete_requires_literal_confirmation_only(deletion_app, body):
    app, client, user, calls, _ = deletion_app
    result = client.request('DELETE', BASE+'/sessions/cli-1', json=body)
    assert result.status_code == 422
    assert not calls
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['attestation', 'home', 'role', 'listener', 'execution', 'csrf', 'origin', 'anonymous'])
def test_delete_requires_owner_runtime_and_mutation_authority(deletion_app, monkeypatch, tmp_path, failure):
    from backend import app as app_module, model_controls
    app, client, user, calls, _ = deletion_app
    expected = 503
    if failure == 'attestation': monkeypatch.setattr(model_controls, 'standalone_owner_verified', lambda: False)
    if failure == 'home': monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', tmp_path/'wrong')
    if failure == 'role':
        with app.state.auth.store.transaction() as c:
            c.execute("UPDATE users SET role='member' WHERE id=?", (user['id'],))
        expected = 409  # Existing runtime binding rejects member/default before the route.
    if failure == 'listener':
        import httpx
        app.state.gateway.client.base_url = httpx.URL('http://127.0.0.1:8642')
    if failure == 'execution': app.state.gateway.execution_ready = False
    if failure == 'csrf': client.headers.pop('X-CSRF-Token'); expected = 403
    if failure == 'origin': client.headers['Origin'] = 'https://wrong.invalid'; expected = 403
    if failure == 'anonymous': client.cookies.clear(); expected = 401
    response = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True})
    assert response.status_code == expected
    assert not any(method == 'DELETE' for method, _ in calls)


@pytest.mark.parametrize('sid', ['..bad', '*', 'a%20b', 'a%5Cb', 'a'*201])
def test_delete_validates_fixed_native_id(deletion_app, sid):
    app, client, user, calls, _ = deletion_app
    assert client.request('DELETE', BASE+'/sessions/'+sid, json={'confirm': True}).status_code == 422
    assert not calls
