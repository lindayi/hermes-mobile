"""Latest snapshot discovery must retain the existing loaded-binding gate."""
from fastapi.testclient import TestClient
from test_auth import BASE, ORIGIN
from test_member_runtime import family
from test_runtime_binding import login, setup_stale


def test_latest_snapshot_checks_binding_before_any_catalog_or_journal_read(family, monkeypatch):
    app, calls, _ = setup_stale(family, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unbound runtime must not read snapshot sources')
    monkeypatch.setattr(app.state.catalog, 'messages', forbidden)
    monkeypatch.setattr(app.state.journal, 'latest', forbidden)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, app)
        response = client.get(BASE + '/sessions/wa-1/messages?latest=true')
        assert response.status_code == 409
        assert calls == []
