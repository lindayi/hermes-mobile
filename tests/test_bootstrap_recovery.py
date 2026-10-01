"""Bootstrap recovery: temporary files/SQLite only, never live services."""
import json
import pytest
from test_self_deploy import deploy_fixture
from backend.runs import RunJournal


def failed_bootstrap(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    def unhealthy(*args, **kwargs):
        raise RuntimeError('public HTTP 403')
    with pytest.raises(RuntimeError, match='rollback failed'):
        module.deploy(paths, bootstrap=True, checks=lambda _: None,
                      verify=unhealthy, run=lambda *a, **k: None)
    status = (paths.state / 'status.json').read_text()
    return module, paths, journal, status


def test_bootstrap_recovers_verified_source_before_checks(tmp_path):
    module, paths, journal, status = failed_bootstrap(tmp_path)
    old = json.loads(status)['release']
    events = []
    def verify(stage, backend, *, assets=None):
        assert backend
        if assets is not None:
            assert stage == paths.source
            assert assets == paths.state / 'backups' / old / 'files'
            assert (paths.state / 'status.json').read_text() == status
            with journal.connect() as c:
                assert c.execute('SELECT owner FROM deployment_gate').fetchone()[0] == old
            events.append('recovered')
        else:
            events.append('deployed')
    def checks(stage):
        assert events == ['recovered']
        with journal.connect() as c:
            assert not c.execute('SELECT * FROM deployment_gate').fetchall()
        events.append('checked')
    module.deploy(paths, bootstrap=True, checks=checks, verify=verify, run=lambda *a, **k: None)
    assert events == ['recovered', 'checked', 'deployed']
    assert json.loads((paths.state / 'status.json').read_text())['status'] == 'succeeded'


@pytest.mark.parametrize('failure', [
    'ordinary', 'frontend', 'owner', 'missing_gate', 'active', 'unknown', 'current', 'dropin',
    'missing_db', 'invalid_release', '403', 'backup_hash', 'both_hash', 'orphan_backup',
    'extra_public', 'missing_index', 'symlink_backup', 'traversal', 'race_owner', 'race_active',
])
def test_recovery_failure_preserves_old_record_and_gate(tmp_path, failure):
    module, paths, journal, status = failed_bootstrap(tmp_path)
    release = json.loads(status)['release']
    backup = paths.state / 'backups' / release
    if failure in ('owner', 'missing_gate'):
        with journal.connect() as c:
            if failure == 'owner':
                c.execute("UPDATE deployment_gate SET owner='another-owner'")
            else:
                c.execute('DELETE FROM deployment_gate')
    if failure in ('active', 'unknown'):
        journal.clear_deployment_gate(release)
        record, _ = journal.submit('u', 'p', 's', 'hello', '1')
        if failure == 'unknown':
            journal.finish('u', record['id'], 'unknown')
        journal.set_deployment_gate(release)
    if failure == 'current':
        (paths.state / 'current').symlink_to(paths.state / 'releases' / release)
    if failure == 'dropin':
        paths.dropin.write_text('preserve me')
    if failure == 'invalid_release':
        status = json.dumps({**json.loads(status), 'release': '../bad'})
        (paths.state / 'status.json').write_text(status)
    if failure in ('backup_hash', 'both_hash'):
        (backup / 'files/index.html').write_text('tampered')
        if failure == 'both_hash':
            (paths.webroot / 'index.html').write_text('tampered')
    if failure == 'orphan_backup':
        (backup / 'files/orphan.js').write_text('orphan')
    if failure == 'extra_public':
        (paths.webroot / 'extra.js').write_text('extra')
    if failure == 'missing_index':
        (paths.webroot / 'index.html').unlink()
    if failure == 'symlink_backup':
        (backup / 'files/index.html').unlink()
        (backup / 'files/index.html').symlink_to(paths.webroot / 'index.html')
    if failure == 'traversal':
        manifest = json.loads((backup / 'manifest.json').read_text())
        manifest['old']['../outside.js'] = '0' * 64
        (backup / 'manifest.json').write_text(json.dumps(manifest))
    with journal.connect() as c:
        before = list(c.iterdump())
    if failure == 'missing_db':
        paths.database.unlink()
    def verify(*args, **kwargs):
        if failure == '403':
            raise RuntimeError('public HTTP 403')
        if failure == 'race_owner':
            with journal.connect() as c:
                c.execute("UPDATE deployment_gate SET owner='racing-owner'")
        elif failure == 'race_active':
            journal.clear_deployment_gate(release)
            journal.submit('u', 'p', 's', 'hello', 'racing')
            journal.set_deployment_gate(release)
        else:
            pytest.fail('unsafe recovery must not reach verification')
    with pytest.raises((RuntimeError, ValueError, OSError)):
        module.deploy(paths, bootstrap=failure not in ('ordinary', 'frontend'),
                      frontend_only=failure == 'frontend',
                      checks=lambda _: pytest.fail('must not run checks'), verify=verify,
                      run=lambda *a, **k: pytest.fail('must not call systemd'))
    assert (paths.state / 'status.json').read_text() == status
    if failure == 'missing_db':
        assert not paths.database.exists()
    elif failure == 'race_owner':
        with journal.connect() as c:
            assert c.execute('SELECT owner FROM deployment_gate').fetchone()[0] == 'racing-owner'
    elif failure == 'race_active':
        with journal.connect() as c:
            assert c.execute('SELECT owner FROM deployment_gate').fetchone()[0] == release
    else:
        with journal.connect() as c:
            assert list(c.iterdump()) == before
