import importlib.util
import json
import stat
import pytest


def test_initialization_provisions_private_keys_and_one_time_bootstrap(tmp_path):
    assert importlib.util.find_spec('backend.manage') is not None, 'Private bootstrap utility missing'
    from backend.manage import initialize
    state=tmp_path/'state'
    config_path,code=initialize(state,tmp_path/'hermes')
    data=json.loads(config_path.read_text())
    assert data['execution_ready'] is False
    assert data['origin']=='https://lindayi.me'
    assert len(data['vapid_public_key']) > 40
    assert code not in config_path.read_text()
    assert code.encode() not in (state/'auth.sqlite').read_bytes()
    assert stat.S_IMODE(config_path.stat().st_mode)==0o600
    assert stat.S_IMODE((state/'vapid.pem').stat().st_mode)==0o600
    with pytest.raises(FileExistsError):
        initialize(state,tmp_path/'hermes')


def test_owner_code_can_be_reissued_only_before_first_owner_exists(tmp_path):
    from backend.manage import initialize, issue_owner_code
    from backend.auth import digest
    import sqlite3
    path, first = initialize(tmp_path/'state',tmp_path/'hermes')
    second = issue_owner_code(path)
    alphabet = set('ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    assert len(first) == len(second) == 6
    assert set(first) <= alphabet and set(second) <= alphabet
    assert second != first
    db = sqlite3.connect(tmp_path/'state/auth.sqlite')
    assert db.execute("SELECT value FROM settings WHERE key='bootstrap_hash'").fetchone()[0] == digest(second)
    db.execute("INSERT INTO users VALUES('owner','owner','default','ready','Owner',0)")
    db.commit(); db.close()
    with pytest.raises(ValueError): issue_owner_code(path)


def test_initialize_rejects_served_frontend_before_writing(tmp_path, monkeypatch):
    from backend.manage import initialize
    import backend.app
    public = tmp_path/'public'
    monkeypatch.setattr(backend.app, 'PUBLIC_ROOTS', (public,))
    with pytest.raises(ValueError): initialize(public/'private-data',tmp_path/'unused')
    assert not (public/'private-data').exists()
