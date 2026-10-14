import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
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


def _photo_operator_state(tmp_path):
    from backend.photo_policy import PhotoPolicy
    from backend.manage import photo_policy_for_operator
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    policy = PhotoPolicy(state/'runs.sqlite')
    policy.initialize(True)
    config = state/'config.json'
    config.write_text(json.dumps({
        'state_dir': str(state), 'photos_enabled': True, 'origin': 'https://mobile.example',
    }))
    config.chmod(0o600)
    return state, policy, config, photo_policy_for_operator


def _photo_cli(command, config, *options):
    return subprocess.run(
        [sys.executable, '-m', 'backend.manage', f'photo-{command}',
         '--config', str(config), *options],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)


def test_photo_operator_status_is_read_only_and_changes_require_confirmation(tmp_path):
    _, policy, config, _ = _photo_operator_state(tmp_path)
    before = policy.database.stat().st_mtime_ns

    status = _photo_cli('status', config)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout) == {
        'photos_enabled': True, 'persisted': True, 'live': True,
    }
    assert policy.database.stat().st_mtime_ns == before

    missing_confirmation = _photo_cli('disable', config)
    assert missing_confirmation.returncode == 2
    assert policy.enabled() is True

    disabled = _photo_cli('disable', config, '--confirm')
    assert disabled.returncode == 0, disabled.stderr
    assert json.loads(disabled.stdout)['photos_enabled'] is False
    enabled = _photo_cli('enable', config, '--confirm')
    assert enabled.returncode == 0, enabled.stderr
    assert json.loads(enabled.stdout)['photos_enabled'] is True


def test_photo_operator_rejects_hardlinked_config_without_side_effects(tmp_path):
    state, policy, config, _ = _photo_operator_state(tmp_path)
    alias = state/'config-alias.json'
    os.link(config, alias)
    assert config.stat().st_nlink == 2
    before_config = config.read_bytes()
    before_config_mtime = config.stat().st_mtime_ns
    before_database = policy.database.read_bytes()
    before_database_mtime = policy.database.stat().st_mtime_ns

    results = [
        _photo_cli(command, config, *options)
        for command, options in (
            ('status', ()),
            ('disable', ('--confirm',)),
            ('enable', ('--confirm',)),
        )
    ]

    assert all(result.returncode == 1 for result in results), [
        (result.returncode, result.stderr) for result in results
    ]
    assert all('hard-linked' in result.stderr for result in results)
    assert config.read_bytes() == before_config
    assert config.stat().st_mtime_ns == before_config_mtime
    assert policy.database.read_bytes() == before_database
    assert policy.database.stat().st_mtime_ns == before_database_mtime
    assert policy.enabled() is True


def test_photo_operator_rejects_hardlinked_journal_without_side_effects(tmp_path):
    state, policy, config, _ = _photo_operator_state(tmp_path)
    alias = state/'runs-alias.sqlite'
    os.link(policy.database, alias)
    assert policy.database.stat().st_nlink == 2
    before_files = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in state.iterdir() if path.is_file()
    }

    results = [
        _photo_cli(command, config, *options)
        for command, options in (
            ('status', ()),
            ('disable', ('--confirm',)),
            ('enable', ('--confirm',)),
        )
    ]

    assert all(result.returncode == 1 for result in results), [
        (result.returncode, result.stderr) for result in results
    ]
    assert all('hard-linked' in result.stderr for result in results)
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in state.iterdir() if path.is_file()
    } == before_files
    assert policy.enabled() is True


def test_photo_operator_rejects_symlinks_wrong_owner_and_malformed_policy(
        tmp_path, monkeypatch):
    state, policy, config, operator = _photo_operator_state(tmp_path)
    alias = tmp_path/'state-alias'
    alias.symlink_to(state, target_is_directory=True)
    config.write_text(json.dumps({
        'state_dir': str(alias), 'photos_enabled': True, 'origin': 'https://mobile.example',
    }))
    config.chmod(0o600)
    with pytest.raises(ValueError, match='symlinks'):
        operator(config)

    config.write_text(json.dumps({
        'state_dir': str(state), 'photos_enabled': True, 'origin': 'https://mobile.example',
    }))
    config.chmod(0o600)
    actual_uid = os.geteuid()
    monkeypatch.setattr(os, 'geteuid', lambda: actual_uid + 1)
    with pytest.raises(ValueError, match='wrong owner'):
        operator(config)
    monkeypatch.setattr(os, 'geteuid', lambda: actual_uid)

    with sqlite3.connect(policy.database) as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute('UPDATE photo_admission_policy SET enabled=2')
    malformed = _photo_cli('status', config)
    assert malformed.returncode == 1
    assert 'malformed' in malformed.stderr


def test_photo_operator_reconciles_a_lost_commit_acknowledgement(tmp_path, monkeypatch):
    _, policy, _, _ = _photo_operator_state(tmp_path)
    connect = policy.connect
    calls = 0

    class LostCommit:
        def __init__(self, database):
            self.database = database

        def __enter__(self):
            self.database.__enter__()
            return self

        def __exit__(self, *args):
            return self.database.__exit__(*args)

        def commit(self):
            self.database.commit()
            raise sqlite3.OperationalError('synthetic lost acknowledgement')

        def __getattr__(self, name):
            return getattr(self.database, name)

    def connect_once_with_lost_ack(*, read_only=False):
        nonlocal calls
        database = connect(read_only=read_only)
        calls += 1
        return LostCommit(database) if calls == 1 and not read_only else database

    monkeypatch.setattr(policy, 'connect', connect_once_with_lost_ack)
    assert policy.set_enabled(False) is False
    assert policy.enabled() is False
