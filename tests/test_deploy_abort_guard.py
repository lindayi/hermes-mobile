"""Offline prepublication abort regressions; only temporary databases and files."""
from contextlib import closing
import json

import pytest

from backend.runs import RunConflict, RunJournal
from test_self_deploy import deploy_fixture


@pytest.fixture
def baseline(tmp_path):
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    previous = module.deploy(paths, bootstrap=True, checks=lambda _: None,
                             verify=lambda *a, **kw: None, run=lambda *a, **kw: None)
    return module, paths, journal, previous


def gate_owner(journal):
    with closing(journal.connect()) as connection:
        row = connection.execute('SELECT owner FROM deployment_gate').fetchone()
        return row[0] if row else None


def status(paths):
    return json.loads((paths.state / 'status.json').read_text())


@pytest.mark.parametrize('drift', ['source', 'public', 'dropin', 'current', 'process'])
def test_drain_abort_with_baseline_drift_does_not_reopen(baseline, monkeypatch, drift):
    module, paths, journal, previous = baseline
    process = {'healthy': True}
    verified = []

    def wait_idle(*a, **kw):
        assert gate_owner(journal)
        if drift == 'source':
            (previous / 'backend/serve.py').write_text('UNREVIEWED = True\n')
        elif drift == 'public':
            (paths.webroot / 'index.html').write_text('unreviewed public bytes')
        elif drift == 'dropin':
            paths.dropin.write_text('[Service]\nWorkingDirectory=/wrong\n')
        elif drift == 'current':
            replacement = paths.state / 'releases' / 'foreign'
            replacement.mkdir()
            module.point_current(paths.state / 'current', replacement)
        else:
            process['healthy'] = False
        raise RuntimeError('injected drain failure')

    def verify(root, backend, **kw):
        verified.append((root, backend))
        assert root == previous and backend is True
        if not process['healthy']:
            raise RuntimeError('baseline process unavailable')

    monkeypatch.setattr(module, 'wait_idle', wait_idle)
    with pytest.raises(RuntimeError):
        module.deploy(paths, checks=lambda _: None, verify=verify,
                      run=lambda *a, **kw: pytest.fail('abort must not restart'))
    assert gate_owner(journal), 'unverified abort must retain admission gate'
    assert status(paths)['status'] == 'rollback_failed'
    assert not (paths.state / 'backups' / status(paths)['release']).exists()
    with pytest.raises(RunConflict, match='deploy'):
        journal.submit('u', 'p', 'new', 'blocked', 'blocked')
    if drift == 'process':
        assert verified == [(previous, True)]


@pytest.mark.parametrize('gate_change', ['foreign', 'absent', 'ignore_delete'])
def test_abort_requires_exactly_one_owned_gate_deletion(baseline, monkeypatch, gate_change):
    module, paths, journal, previous = baseline

    def wait_idle(*a, **kw):
        with closing(journal.connect()) as connection, connection:
            if gate_change == 'foreign':
                connection.execute("UPDATE deployment_gate SET owner='foreign'")
            elif gate_change == 'absent':
                connection.execute('DELETE FROM deployment_gate')
            else:
                connection.execute('''CREATE TRIGGER ignore_gate_delete
                    BEFORE DELETE ON deployment_gate BEGIN SELECT RAISE(IGNORE); END''')
        raise RuntimeError('injected drain failure')

    monkeypatch.setattr(module, 'wait_idle', wait_idle)
    with pytest.raises(RuntimeError):
        module.deploy(paths, checks=lambda _: None, verify=lambda *a, **kw: None,
                      run=lambda *a, **kw: pytest.fail('must not restart'))
    assert status(paths)['status'] == 'rollback_failed', 'gate mismatch is not a verified rollback'
    assert 'gate' in status(paths)['rollback_error'].lower()
    if gate_change == 'foreign':
        assert gate_owner(journal) == 'foreign'
    elif gate_change == 'ignore_delete':
        assert gate_owner(journal) == status(paths)['release']
    else:
        assert gate_owner(journal) is None  # Never invent ownership or rewind the database.


def test_gate_acquisition_failure_does_not_attempt_abort_recovery(baseline, monkeypatch):
    module, paths, journal, previous = baseline
    journal.set_deployment_gate('foreign')
    monkeypatch.setattr(module, 'wait_idle', lambda *a, **kw: pytest.fail('gate was not acquired'))
    with pytest.raises(RunConflict, match='deployment'):
        module.deploy(paths, checks=lambda _: None,
                      verify=lambda *a, **kw: pytest.fail('must not recover another owner'),
                      run=lambda *a, **kw: pytest.fail('must not restart'))
    assert gate_owner(journal) == 'foreign'
    assert status(paths)['status'] == 'failed'


@pytest.mark.parametrize('bootstrap', [False, True])
def test_unchanged_abort_verifies_backend_before_reopening_without_rewinding_runs(tmp_path, monkeypatch, bootstrap):
    module, paths = deploy_fixture(tmp_path)
    journal = RunJournal(paths.database)
    previous = paths.source
    if not bootstrap:
        previous = module.deploy(paths, bootstrap=True, checks=lambda _: None,
                                 verify=lambda *a, **kw: None, run=lambda *a, **kw: None)
    record, _ = journal.submit('u', 'p', 'old', 'uncertain work', 'existing')
    journal.finish('u', record['id'], 'unknown')
    with closing(journal.connect()) as connection:
        rows_before = [tuple(row) for row in connection.execute('SELECT * FROM runs')]
    verified = []

    def verify(root, backend):
        assert gate_owner(journal)
        assert (root, backend) == (previous, True)
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'p', 'other', 'blocked', 'during-verification')
        verified.append(root)

    with pytest.raises(RuntimeError, match='unknown'):
        module.deploy(paths, bootstrap=bootstrap, checks=lambda _: None, verify=verify,
                      run=lambda *a, **kw: pytest.fail('abort must not restart'))
    assert verified == [previous]
    assert gate_owner(journal) is None
    assert status(paths)['status'] == 'rolled_back'
    with closing(journal.connect()) as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM runs')] == rows_before


@pytest.mark.parametrize('drift', ['source', 'public', 'dropin', 'foreign_gate'])
def test_abort_rechecks_evidence_after_backend_verification(baseline, monkeypatch, drift):
    module, paths, journal, previous = baseline

    def wait_idle(*a, **kw):
        raise RuntimeError('injected drain failure')

    def verify(root, backend):
        assert gate_owner(journal)
        assert (root, backend) == (previous, True)
        if drift == 'foreign_gate':
            with closing(journal.connect()) as connection, connection:
                connection.execute("UPDATE deployment_gate SET owner='foreign'")
        else:
            target = {'source': previous / 'backend/serve.py',
                      'public': paths.webroot / 'index.html', 'dropin': paths.dropin}[drift]
            target.write_text('changed during verification')

    monkeypatch.setattr(module, 'wait_idle', wait_idle)
    with pytest.raises(RuntimeError, match='rollback'):
        module.deploy(paths, checks=lambda _: None, verify=verify,
                      run=lambda *a, **kw: pytest.fail('abort must not restart'))
    assert gate_owner(journal) == ('foreign' if drift == 'foreign_gate' else status(paths)['release'])
    assert status(paths)['status'] == 'rollback_failed'


def test_unreadable_gate_does_not_reopen_or_claim_verified_recovery(baseline, monkeypatch):
    import sqlite3
    module, paths, journal, previous = baseline
    original_connect = RunJournal.connect
    denied = []

    def restricted_connect(self):
        connection = original_connect(self)

        def authorize(action, table, *rest):
            if action == sqlite3.SQLITE_READ and table == 'deployment_gate':
                denied.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    def wait_idle(*a, **kw):
        monkeypatch.setattr(RunJournal, 'connect', restricted_connect)
        raise RuntimeError('injected drain failure')

    monkeypatch.setattr(module, 'wait_idle', wait_idle)
    with pytest.raises(RuntimeError, match='unverified'):
        module.deploy(paths, checks=lambda _: None, verify=lambda *a, **kw: None,
                      run=lambda *a, **kw: pytest.fail('abort must not restart'))
    monkeypatch.setattr(RunJournal, 'connect', original_connect)
    assert denied and gate_owner(journal) == status(paths)['release']
    assert status(paths)['status'] == 'rollback_failed'


def test_abort_final_evidence_check_holds_sqlite_write_lock(baseline, monkeypatch):
    import sqlite3
    module, paths, journal, previous = baseline
    original_baseline = module._abort_baseline
    checked = []

    def wait_idle(*a, **kw):
        raise RuntimeError('injected drain failure')

    def verify(root, backend):
        def locked_baseline(*a, **kw):
            # This real competing connection cannot replace ownership between
            # the final baseline observation and the controller's DELETE.
            with closing(sqlite3.connect(paths.database, timeout=0)) as other, other:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    other.execute("UPDATE deployment_gate SET owner='competitor'")
            checked.append(True)
            return original_baseline(*a, **kw)
        monkeypatch.setattr(module, '_abort_baseline', locked_baseline)

    monkeypatch.setattr(module, 'wait_idle', wait_idle)
    with pytest.raises(RuntimeError, match='injected drain failure'):
        module.deploy(paths, checks=lambda _: None, verify=verify,
                      run=lambda *a, **kw: pytest.fail('abort must not restart'))
    assert checked == [True]
    assert gate_owner(journal) is None
    assert status(paths)['status'] == 'rolled_back'
