"""A visible finish state must imply that its journal event is already durable."""
from contextlib import closing
import sqlite3

import pytest

from backend.runs import RunJournal


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'unknown',
                                   'stopping', 'waiting_for_approval'])
def test_finish_state_and_event_become_visible_together(tmp_path, monkeypatch, status):
    journal = RunJournal(tmp_path/'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'session', 'test', 'key')
    rid = run['id']
    journal.set_upstream('owner', rid, 'synthetic-upstream')
    connect = journal.connect
    observed = []

    def observe_before_insert(sql):
        if sql.startswith('INSERT INTO events'):
            # A different real SQLite connection sees only committed state.
            # The SQL trace callback runs before the event insert executes.
            with closing(connect()) as reader:
                state = reader.execute('SELECT status FROM runs WHERE id=?', (rid,)).fetchone()[0]
                count = reader.execute('SELECT COUNT(*) FROM events WHERE run_id=?', (rid,)).fetchone()[0]
            observed.append((state, count))

    def traced_connect():
        connection = connect()
        connection.set_trace_callback(observe_before_insert)
        return connection

    monkeypatch.setattr(journal, 'connect', traced_connect)
    journal.finish('owner', rid, status, output='Final answer', error='test detail')
    assert observed == [('running', 0)], 'Never expose finish status before its event commits'
    assert journal.get('owner', rid)['status'] == status
    events = journal.events('owner', rid)
    assert len(events) == 1
    assert events[0]['name'] == ('done' if status in ('completed', 'failed', 'cancelled', 'unknown') else 'status')
    assert events[0]['data'] == {'status': status, 'output': 'Final answer', 'error': 'test detail'}


def test_failed_finish_event_insert_rolls_back_state_and_output(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'session', 'test', 'key')
    rid = run['id']
    journal.set_upstream('owner', rid, 'synthetic-upstream')
    before = journal.get('owner', rid)
    with closing(journal.connect()) as connection, connection:
        connection.execute("""CREATE TRIGGER reject_finish BEFORE INSERT ON events
                              BEGIN SELECT RAISE(ABORT, 'injected write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='injected write failure'):
        journal.finish('owner', rid, 'completed', output='Final answer')
    assert journal.get('owner', rid) == before
    assert journal.events('owner', rid) == []
