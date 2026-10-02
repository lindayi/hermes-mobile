"""Real killed SQLite writes; no production paths or invented journal bytes."""
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3

import pytest

from deploy import workflow_notifications as adapter
from deploy.workflow_events import event_digest
from test_workflow_notifications import NOW, adapter_fixture, event


def killed_adapter(tmp_path, phase):
    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'owner-user')
    with sqlite3.connect(state) as db:
        for n in range(200):
            item = event(event_id=f'history:{n}')
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (item['event_id'], event_digest(item), 'owner-user', 'acked', 'i' * 36, 1, 1))
        history = db.execute('SELECT * FROM events ORDER BY event_id').fetchall()
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Event()
    wait = ctx.Event()

    def interrupted():
        original = adapter._state_connection
        commits = [0]

        class Connection:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def commit(self):
                commits[0] += 1
                if commits[0] == (1 if phase == 'reservation' else 2):
                    # Force real dirty-page spill before commit, including the
                    # actual adapter reservation/ACK. Historical values do not
                    # change, but a tiny cache makes the rollback journal hot.
                    self.db.execute('UPDATE events SET updated_at=updated_at')
                    ready.set()
                    wait.wait(30)
                self.db.commit()

        def connection(path):
            db = original(path)
            db.execute('PRAGMA cache_size=2')
            return Connection(db)

        adapter._state_connection = connection
        adapter.process(paths, apply=True, now=NOW)

    child = ctx.Process(target=interrupted)
    child.start()
    try:
        assert ready.wait(10), 'Child never reached an uncommitted adapter write'
        child.kill()
        child.join(10)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join()
    journal = Path(str(state) + '-journal')
    assert journal.read_bytes()[:8] == bytes.fromhex('d9d505f920a163d7')
    assert journal.stat().st_size > 512
    return paths, state_dir, inbox, state, journal, history


@pytest.mark.parametrize('phase', ['reservation', 'ack'])
def test_killed_write_recovers_only_in_locked_apply_and_replays(tmp_path, monkeypatch, phase):
    import fcntl
    paths, state_dir, inbox, state, journal, history = killed_adapter(tmp_path, phase)
    before = {p: p.read_bytes() for p in (state, journal, inbox)}
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, now=NOW)
    assert {p: p.read_bytes() for p in before} == before
    original = adapter.sqlite3.connect
    observed = []

    def connect(database, *args, **kwargs):
        if str(state) in str(database) and 'mode=rw' in str(database):
            check = os.open(state_dir / adapter.LOCK_NAME, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(check)
            observed.append(True)
        return original(database, *args, **kwargs)

    monkeypatch.setattr(adapter.sqlite3, 'connect', connect)
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 1
    assert observed
    assert not journal.exists()
    with sqlite3.connect(state) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute("SELECT * FROM events WHERE event_id LIKE 'history:%' ORDER BY event_id").fetchall() == history
        assert db.execute('SELECT status FROM events WHERE event_id=?', (event()['event_id'],)).fetchone() == ('acked',)
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 0


@pytest.mark.parametrize('hazard', [
    'journal_public', 'journal_symlink', 'journal_hardlink', 'journal_directory',
    'db_public', 'db_symlink', 'db_hardlink', 'wal', 'shm',
    'journal_magic', 'journal_checksum', 'journal_truncated', 'master_journal',
    'foreign_db', 'unbound_db', 'schema',
])
def test_recovery_rejects_unsafe_foreign_or_malformed_inputs_unchanged(tmp_path, hazard):
    paths, _, inbox, state, journal, _ = killed_adapter(tmp_path, 'reservation')
    if hazard in ('journal_public', 'db_public'):
        (journal if hazard == 'journal_public' else state).chmod(0o644)
    elif hazard.endswith('_symlink'):
        target = journal if hazard == 'journal_symlink' else state
        moved = target.with_name(target.name + '.target')
        target.rename(moved)
        target.symlink_to(moved)
    elif hazard.endswith('_hardlink'):
        target = journal if hazard == 'journal_hardlink' else state
        os.link(target, target.with_name(target.name + '.alias'))
    elif hazard == 'journal_directory':
        journal.unlink()
        journal.mkdir(mode=0o700)
    elif hazard in ('wal', 'shm'):
        sidecar = Path(str(state) + '-' + hazard)
        sidecar.write_bytes(b'synthetic unsupported sidecar')
        sidecar.chmod(0o600)
    elif hazard in ('foreign_db', 'unbound_db', 'schema'):
        # Alter an isolated copy without letting SQLite touch the real journal.
        copy = state.with_name('foreign.sqlite')
        copy.write_bytes(state.read_bytes())
        with sqlite3.connect(copy) as db:
            if hazard == 'foreign_db':
                db.execute("UPDATE binding SET owner_user_id='other-owner'")
            elif hazard == 'unbound_db':
                db.execute('DELETE FROM binding')
            else:
                db.execute('CREATE TABLE foreign_data(value TEXT)')
        state.write_bytes(copy.read_bytes())
    else:
        data = bytearray(journal.read_bytes())
        if hazard == 'journal_magic':
            data[0] ^= 1
        elif hazard == 'journal_checksum':
            sector = int.from_bytes(data[20:24], 'big')
            size = int.from_bytes(data[24:28], 'big')
            data[sector + size + 7] ^= 1
        elif hazard == 'journal_truncated':
            del data[600:]
        else:
            data.extend(b'foreign-path' + bytes.fromhex('d9d505f920a163d7'))
        journal.write_bytes(data)
    files = [p for p in state.parent.iterdir() if p.is_file()]
    before = {p: p.read_bytes() for p in files}
    for apply in (False, True):
        with pytest.raises(adapter.Blocked):
            adapter.process(paths, apply=apply, now=NOW)
        assert {p: p.read_bytes() for p in before} == before
    assert not list(state.parent.glob('.workflow-notifications-recovery-*'))


def test_recovery_checks_complete_batch_before_canonical_rollback(tmp_path):
    import json
    paths, state_dir, inbox, state, journal, _ = killed_adapter(tmp_path, 'ack')
    # First event would be eligible, but a later retained identity conflicts.
    from test_workflow_notifications import export
    snapshot = export(event(), event(event_id='history:1', reason='issue_failed'))
    export_path = state_dir / adapter.EVENT_NAME
    export_path.write_text(json.dumps(snapshot))
    os.utime(export_path, (NOW.timestamp(), NOW.timestamp()))
    before = {p: p.read_bytes() for p in (inbox, state, journal)}
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert journal.exists(), 'Batch conflict must not consume the recovery journal'
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('old_binding', ['foreign_owner', 'unbound'])
def test_recovered_binding_validated_before_touching_canonical_state(tmp_path, old_binding):
    paths, state_dir, _, inbox, _, _ = adapter_fixture(tmp_path)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    adapter._initialize_state(state, 'other-owner')
    with sqlite3.connect(state) as db:
        if old_binding == 'unbound':
            db.execute('DELETE FROM binding')
        for n in range(200):
            item = event(event_id=f'history:{n}')
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                       (item['event_id'], event_digest(item), 'owner-user', 'acked', 'i' * 36, 1, 1))
    ctx = multiprocessing.get_context('fork')
    ready, wait = ctx.Event(), ctx.Event()

    def write():
        db = sqlite3.connect(state)
        db.execute('PRAGMA cache_size=2')
        db.execute('BEGIN IMMEDIATE')
        if old_binding == 'unbound':
            db.execute("INSERT INTO binding VALUES('repository',?,?,?)",
                       (adapter.SCHEMA_VERSION, adapter.REPOSITORY_ID, 'owner-user'))
        else:
            db.execute("UPDATE binding SET owner_user_id='owner-user'")
        db.execute('UPDATE events SET updated_at=2')
        ready.set()
        wait.wait(30)

    child = ctx.Process(target=write)
    child.start()
    try:
        assert ready.wait(10)
        child.kill()
        child.join(10)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join()
    journal = Path(str(state) + '-journal')
    assert journal.read_bytes()[:8] == bytes.fromhex('d9d505f920a163d7')
    # The uncommitted main image appears owned. Only real rollback in the
    # disposable copy reveals that the durable binding was foreign/absent.
    with sqlite3.connect(state.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        assert db.execute('SELECT owner_user_id FROM binding').fetchone() == ('owner-user',)
    before = {p: p.read_bytes() for p in (state, journal, inbox)}
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert {p: p.read_bytes() for p in before} == before
    assert not list(state_dir.glob('.workflow-notifications-recovery-*'))


@pytest.mark.parametrize('database', ['auth.sqlite', 'notifications.sqlite'])
def test_apply_never_recovers_other_database_journals(tmp_path, database):
    paths, state_dir, inbox, state, journal, _ = killed_adapter(tmp_path, 'reservation')
    target = state_dir / (database + '-journal')
    target.write_bytes(journal.read_bytes())
    target.chmod(0o600)
    before = {p: p.read_bytes() for p in (state, journal, inbox, target)}
    with pytest.raises(adapter.Blocked):
        adapter.process(paths, apply=True, now=NOW)
    assert {p: p.read_bytes() for p in before} == before
