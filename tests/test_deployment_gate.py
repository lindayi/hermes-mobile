import sqlite3

import pytest

from backend.runs import RunConflict, RunJournal


def test_gate_rejects_new_submissions_but_existing_run_can_finish(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    run, _ = journal.submit('u', 'default', 's', 'hello', 'key')
    journal.set_deployment_gate('release-1')
    with pytest.raises(RunConflict, match='deploy'):
        RunJournal(journal.path).submit('u', 'default', 'other', 'hello', 'next')
    journal.finish('u', run['id'], 'completed')
    assert journal.get('u', run['id'])['status'] == 'completed'
    journal.clear_deployment_gate('release-1')
    assert journal.submit('u', 'default', 'other', 'hello', 'next')[1]



def test_gate_commit_wins_race_against_submission_transaction(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    journal = RunJournal(tmp_path / 'runs.sqlite')
    original = journal.connect
    begun = Event()
    def connection():
        result = original()
        result.set_trace_callback(lambda sql: begun.set() if sql == 'BEGIN IMMEDIATE' else None)
        return result
    monkeypatch.setattr(journal, 'connect', connection)
    writer = original()
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("INSERT INTO deployment_gate VALUES(1,'release')")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(journal.submit, 'u', 'p', 's', 'hello', 'key')
            assert begun.wait(2), 'submission must enter its SQLite write transaction'
            writer.commit()
            with pytest.raises(RunConflict, match='deploy'):
                future.result(timeout=3)
    finally:
        writer.close()


def test_unrelated_owner_cannot_clear_a_persisted_gate(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    journal.set_deployment_gate('first')
    with pytest.raises(RunConflict, match='deployment'):
        journal.set_deployment_gate('second')
    journal.clear_deployment_gate('second')
    with pytest.raises(RunConflict, match='deploy'):
        RunJournal(journal.path).submit('u', 'p', 's', 'hello', 'key')


def test_missing_gate_table_fails_closed_instead_of_admitting(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    with journal.connect() as connection:
        connection.execute('DROP TABLE deployment_gate')
    with pytest.raises(sqlite3.OperationalError):
        journal.submit('u', 'p', 's', 'hello', 'key')
    with journal.connect() as connection:
        assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
