"""Isolated operator tests: real SQLite/gate/files; fake OS/CLI/native boundary."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from backend.runs import RunJournal
from deploy.self_deploy import Paths


@pytest.fixture
def rig(tmp_path):
    state = tmp_path / 'deploy'
    old = state / 'releases' / ('a' * 32)
    root = state / 'releases' / ('b' * 32)
    old.mkdir(parents=True)
    root.mkdir()
    (state / 'current').symlink_to(old)
    dropin = tmp_path / 'bridge.conf'
    native_dropin = tmp_path / 'native.conf'
    dropin.write_text('bridge unchanged')
    native_dropin.write_text('native unchanged')
    journal = RunJournal(tmp_path / 'runs.sqlite')
    notices = tmp_path / 'native-notifications.sqlite'
    with sqlite3.connect(notices) as db:
        db.execute('CREATE TABLE notification_outbox(event_id TEXT PRIMARY KEY,event_json TEXT,payload_sha256 TEXT,result_json TEXT,state TEXT)')
        db.execute("INSERT INTO notification_outbox VALUES('retained','event','digest','result','pending')")
    paths = Paths(source=tmp_path, state=state, database=journal.path,
                  dropin=dropin, webroot=tmp_path / 'unused-public')
    events = []
    r = SimpleNamespace(paths=paths, old=old, root=root, journal=journal, notices=notices,
                        native_dropin=native_dropin, cap=1, events=events, pid=10, busy=False,
                        checks=0, verify_failures=0)

    def gate():
        with closing(journal.connect()) as db:
            return [tuple(row) for row in db.execute('SELECT singleton,owner FROM deployment_gate')]
    r.gate = gate

    def run(command, **kwargs):
        events.append(tuple(command))
        if command[:3] == ['hermes', 'config', 'get']:
            return SimpleNamespace(stdout=str(r.cap) + '\n')
        assert gate(), 'mutation without owned admission gate'
        if command[:3] == ['hermes', 'config', 'set']:
            assert command[3] == 'gateway.api_server.max_concurrent_runs'
            assert kwargs['env']['HERMES_HOME'] == str(tmp_path / 'home')
            r.cap = int(command[4])
        else:
            assert command == ['systemctl', '--user', 'restart', 'hermes-mobile-api.service']
            r.pid += 1
        return SimpleNamespace(stdout='')
    r.run = run

    class Native:
        def capture(self, bridge_root, bootstrap):
            assert bridge_root == old and bootstrap is False
            return dict(root=str(root), pid=r.pid, start_ticks=r.pid * 10, legacy=False,
                        source_hashes={'backend/native_notifications.py': 'attested'}, caps={})

        def idle(self, journal, baseline, *, required_ids=()):
            assert gate()
            assert baseline['pid'] == r.pid
            events.append(('idle', tuple(required_ids)))
            return not r.busy

        def verify_unchanged(self, root_arg, *, baseline):
            assert root_arg == root and baseline['pid'] == r.pid
            events.append(('unchanged',))

        def verify(self, root_arg, *, baseline=None):
            assert gate() and root_arg == root
            events.append(('verify',))
            if r.verify_failures:
                r.verify_failures -= 1
                raise RuntimeError('verification failed')
    r.native = Native()
    return r


def module():
    assert importlib.util.find_spec('deploy.native_concurrency') is not None, 'same-source concurrency operator missing'
    from deploy import native_concurrency
    return native_concurrency


def invoke(r, **extra):
    op = module()
    args = dict(native=r.native, config=op.HermesCap(r.paths.source / 'home', run=r.run),
                notifications=r.notices, native_dropin=r.native_dropin, run=r.run,
                approved=True, exclusive_ingress=True, idle_timeout=0)
    args.update(extra)
    return op.activate(r.paths, **args)


@pytest.mark.parametrize('override', [dict(approved=False), dict(exclusive_ingress=False)])
def test_native_concurrency_requires_explicit_review_and_exclusive_ingress(rig, override):
    with pytest.raises(RuntimeError, match='review.*exclusive'):
        invoke(rig, **override)
    assert rig.events == [] and rig.gate() == []


@pytest.mark.parametrize('unsafe', ['cap', 'legacy', 'no-notifications', 'rollback-failed', 'current-outside-releases', 'symlink-dropin'])
def test_native_concurrency_rejects_unsafe_preflight_without_mutation(rig, unsafe):
    if unsafe == 'cap':
        rig.cap = 2
    elif unsafe in ('legacy', 'no-notifications'):
        original = rig.native.capture
        def capture(*args):
            b = original(*args)
            if unsafe == 'legacy':
                b['legacy'] = True
            else:
                b['source_hashes'] = {}
            return b
        rig.native.capture = capture
    elif unsafe == 'rollback-failed':
        (rig.paths.state / 'status.json').write_text('{"status":"rollback_failed"}')
    elif unsafe == 'current-outside-releases':
        (rig.paths.state / 'current').unlink()
        (rig.paths.state / 'current').symlink_to(rig.paths.source)
    else:
        rig.native_dropin.unlink()
        rig.native_dropin.symlink_to(rig.paths.dropin)
    with pytest.raises((RuntimeError, ValueError)):
        invoke(rig)
    assert rig.gate() == []
    assert not any(e[0] == 'systemctl' or e[:3] == ('hermes', 'config', 'set') for e in rig.events)


def test_native_concurrency_busy_abort_reopens_only_unchanged_gate_without_config_write(rig):
    rig.busy = True
    with pytest.raises(RuntimeError, match='Native idle wait timed out'):
        invoke(rig)
    assert rig.gate() == [] and rig.cap == 1
    assert not any(e[0] == 'systemctl' or e[:3] == ('hermes', 'config', 'set') for e in rig.events)
    assert ('unchanged',) in rig.events
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rolled_back'


def test_native_concurrency_failed_activation_restores_one_on_same_source_and_verifies(rig):
    rig.verify_failures = 1
    with pytest.raises(RuntimeError, match='verification failed'):
        invoke(rig)
    assert rig.cap == 1 and rig.pid == 12 and rig.gate() == []
    writes = [e[-1] for e in rig.events if e[:3] == ('hermes', 'config', 'set')]
    assert writes == ['4', '1']
    assert [e[0] for e in rig.events].count('verify') == 2
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rolled_back'
    recovery = json.loads((rig.paths.state / 'native-concurrency-recovery.json').read_text())
    assert recovery['previous'] == 1 and recovery['native']['pid'] == 10
    assert recovery['native']['root'] == str(rig.root)


@pytest.mark.parametrize('drift', ['gate', 'dropin', 'cap', 'busy'])
def test_native_concurrency_rechecks_boundary_after_drain_before_writing(rig, drift):
    calls = []
    def idle(*args, **kwargs):
        calls.append(True)
        if drift == 'gate':
            with rig.journal.connect() as db:
                db.execute("UPDATE deployment_gate SET owner='other'")
        elif drift == 'dropin':
            rig.native_dropin.write_text('external change')
        elif drift == 'cap':
            rig.cap = 7
        return not (drift == 'busy' and len(calls) > 1)
    rig.native.idle = idle
    with pytest.raises(RuntimeError):
        invoke(rig)
    assert not any(e[0] == 'systemctl' or e[:3] == ('hermes', 'config', 'set') for e in rig.events)
    if drift in ('gate', 'dropin', 'cap'):
        assert rig.gate(), 'ambiguous drift must retain the gate'


@pytest.mark.parametrize('damage', ['delete', 'event', 'result'])
def test_native_concurrency_notice_loss_keeps_gate_closed_without_second_restart(rig, damage):
    original = rig.run
    def run(command, **kwargs):
        result = original(command, **kwargs)
        if command[0] == 'systemctl':
            with sqlite3.connect(rig.notices) as db:
                if damage == 'delete':
                    db.execute('DELETE FROM notification_outbox')
                else:
                    field = 'event_json' if damage == 'event' else 'result_json'
                    db.execute(f"UPDATE notification_outbox SET {field}='changed'")
        return result
    with pytest.raises(RuntimeError, match='gate remains closed'):
        invoke(rig, run=run)
    assert rig.cap == 4 and rig.gate()
    assert rig.pid == 11
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rollback_failed'


def test_native_concurrency_requires_fresh_process_not_just_healthy_old_listener(rig):
    original = rig.run
    def run(command, **kwargs):
        if command[0] == 'systemctl':
            return SimpleNamespace(stdout='')  # manager claimed success, PID unchanged
        return original(command, **kwargs)
    with pytest.raises(RuntimeError, match='fresh native process'):
        invoke(rig, run=run)
    assert rig.cap == 1 and rig.pid == 10 and rig.gate() == []


@pytest.mark.parametrize('failure', ['dropin-read', 'recovery-write', 'running-write', 'succeeded-write'])
def test_native_concurrency_reporting_failures_never_strand_unrecorded_gate(rig, monkeypatch, failure):
    op = module()
    original_write = op.bridge.atomic_write
    original_read = Path.read_bytes
    failed = []
    def write(path, text):
        is_target = ((failure == 'recovery-write' and path.name == 'native-concurrency-recovery.json')
                     or (path.name == 'status.json' and json.loads(text)['status'] == failure.removesuffix('-write')))
        if is_target and not failed:
            failed.append(True)
            raise OSError('injected persistence failure')
        return original_write(path, text)
    def read(path):
        if failure == 'dropin-read' and path == rig.native_dropin and not failed:
            failed.append(True)
            raise OSError('injected persistence failure')
        return original_read(path)
    monkeypatch.setattr(op.bridge, 'atomic_write', write)
    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(OSError, match='injected persistence'):
        invoke(rig)
    assert failed and rig.gate() == [] and rig.cap == 1
    if failure == 'succeeded-write':
        assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rolled_back'


def test_native_concurrency_checks_upstream_ids_of_runs_drained_under_gate(rig):
    rig.journal.submit('owner', 'default', 'active-session', 'hello', 'active')
    def finish(_seconds):
        assert rig.gate()
        with rig.journal.connect() as db:
            db.execute("UPDATE runs SET status='completed',upstream_id='drained-native-id'")
    invoke(rig, sleep=finish, idle_timeout=5)
    assert ('idle', ('drained-native-id',)) in rig.events


def test_native_concurrency_new_activity_during_config_write_blocks_restart(rig):
    op = module()
    config = op.HermesCap(rig.paths.source / 'home', run=rig.run)
    original = config.set
    def set_cap(value):
        original(value)
        rig.busy = True
    config.set = set_cap
    with pytest.raises(RuntimeError, match='gate remains closed'):
        invoke(rig, config=config)
    assert rig.gate() and rig.pid == 10
    assert not any(e[0] == 'systemctl' for e in rig.events)


def test_native_concurrency_rollback_requires_fresh_process_before_reopening(rig):
    rig.verify_failures = 1
    original = rig.run
    def run(command, **kwargs):
        if command[0] == 'systemctl' and rig.pid == 11:
            return SimpleNamespace(stdout='')  # failed rollback restart silently no-ops
        return original(command, **kwargs)
    with pytest.raises(RuntimeError, match='gate remains closed'):
        invoke(rig, run=run)
    assert rig.cap == 1 and rig.pid == 11 and rig.gate()
    receipt = json.loads((rig.paths.state / 'status.json').read_text())
    assert receipt['status'] == 'rollback_failed' and 'fresh' in receipt['rollback_error']


@pytest.mark.parametrize('rollback', [False, True])
def test_native_concurrency_reconciles_error_after_committed_gate_deletion(rig, monkeypatch, rollback):
    op = module()
    original = op.Journal.connect
    rig.verify_failures = int(rollback)
    injected = []
    class Connection:
        def __init__(self, db):
            self.db, self.deleting = db, False
        def execute(self, sql, *args):
            self.deleting |= sql.startswith('DELETE FROM deployment_gate')
            return self.db.execute(sql, *args)
        def __enter__(self):
            self.db.__enter__()
            return self
        def __exit__(self, *args):
            result = self.db.__exit__(*args)
            if self.deleting and not injected:
                injected.append(True)
                raise OSError('failure after commit')
            return result
        def close(self):
            self.db.close()
    monkeypatch.setattr(op.Journal, 'connect', lambda self: Connection(original(self)))
    if rollback:
        with pytest.raises(RuntimeError, match='verification failed'):
            invoke(rig)
    else:
        assert invoke(rig)['status'] == 'succeeded'
    assert injected and rig.gate() == []
    assert rig.cap == (1 if rollback else 4)
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == ('rolled_back' if rollback else 'succeeded')


@pytest.mark.parametrize('after_commit', [False, True])
def test_native_concurrency_gate_acquisition_commit_errors_are_reconciled(rig, monkeypatch, after_commit):
    op = module()
    original = op.Journal.connect
    injected = []
    class Connection:
        def __init__(self, db):
            self.db, self.inserting = db, False
        def execute(self, sql, *args):
            self.inserting |= sql.startswith('INSERT INTO deployment_gate')
            return self.db.execute(sql, *args)
        def __enter__(self):
            self.db.__enter__()
            return self
        def __exit__(self, *args):
            if self.inserting and not injected:
                injected.append(True)
                if after_commit:
                    self.db.__exit__(*args)
                else:
                    self.db.__exit__(OSError, OSError('commit refused'), None)
                raise OSError('acquisition commit failure')
            return self.db.__exit__(*args)
        def close(self):
            self.db.close()
    monkeypatch.setattr(op.Journal, 'connect', lambda self: Connection(original(self)))
    with pytest.raises(OSError, match='acquisition commit failure'):
        invoke(rig)
    assert injected and rig.cap == 1 and rig.pid == 10 and rig.gate() == []
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] in ('failed', 'rolled_back')


def test_native_concurrency_does_not_claim_closed_admission_when_gate_disappears(rig):
    def verify(*args, **kwargs):
        with rig.journal.connect() as db:
            db.execute('DELETE FROM deployment_gate')
        raise RuntimeError('unexpected gate loss')
    rig.native.verify = verify
    with pytest.raises(RuntimeError, match='admission gate is open'):
        invoke(rig)
    assert rig.cap == 4 and rig.pid == 11 and rig.gate() == []
    assert json.loads((rig.paths.state / 'status.json').read_text())['admission'] == 'open'


def test_native_concurrency_rollback_status_write_failure_retains_owned_gate(rig, monkeypatch):
    op = module()
    original = op.bridge.atomic_write
    rig.verify_failures = 1
    def write(path, text):
        if path.name == 'status.json' and json.loads(text)['status'] == 'rolled_back':
            raise OSError('rollback status unavailable')
        return original(path, text)
    monkeypatch.setattr(op.bridge, 'atomic_write', write)
    with pytest.raises(RuntimeError, match='gate remains closed'):
        invoke(rig)
    assert rig.cap == 1 and rig.gate()
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rollback_failed'


def test_native_concurrency_precommit_delete_failure_does_not_claim_success(rig):
    with rig.journal.connect() as db:
        db.execute("CREATE TRIGGER keep_gate BEFORE DELETE ON deployment_gate BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(RuntimeError, match='gate remains closed'):
        invoke(rig)
    assert rig.cap == 1 and rig.gate()
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'rollback_failed'


def test_native_concurrency_partial_config_write_restores_without_unnecessary_restart(rig):
    op = module()
    original = rig.run
    failed = []
    def run(command, **kwargs):
        result = original(command, **kwargs)
        if command[:3] == ['hermes', 'config', 'set'] and not failed:
            failed.append(True)
            raise OSError('CLI failed after write')
        return result
    config = op.HermesCap(rig.paths.source / 'home', run=run)
    with pytest.raises(OSError, match='CLI failed after write'):
        invoke(rig, config=config)
    assert rig.cap == 1 and rig.pid == 10 and rig.gate() == []


def test_native_concurrency_notice_delivery_progress_is_allowed(rig):
    original = rig.run
    with sqlite3.connect(rig.notices) as db:
        db.execute('UPDATE notification_outbox SET result_json=NULL')
    def run(command, **kwargs):
        result = original(command, **kwargs)
        if command[0] == 'systemctl':
            with sqlite3.connect(rig.notices) as db:
                db.execute("UPDATE notification_outbox SET result_json='arrived',state='delivered'")
        return result
    assert invoke(rig, run=run)['status'] == 'succeeded'


def test_same_source_activation_gates_sets_four_restarts_only_native_and_keeps_notices(rig):
    before = rig.notices.read_bytes()
    result = invoke(rig)
    assert result['status'] == 'succeeded' and rig.cap == 4
    assert rig.gate() == []
    assert (rig.paths.state / 'current').resolve() == rig.old
    assert rig.native_dropin.read_text() == 'native unchanged'
    assert rig.paths.dropin.read_text() == 'bridge unchanged'
    assert rig.notices.read_bytes() == before
    assert [e for e in rig.events if e[0] == 'systemctl'] == [
        ('systemctl', '--user', 'restart', 'hermes-mobile-api.service')]
    assert rig.events.index(('idle', ())) < rig.events.index(
        ('hermes', 'config', 'set', 'gateway.api_server.max_concurrent_runs', '4'))
    assert json.loads((rig.paths.state / 'status.json').read_text())['status'] == 'succeeded'
