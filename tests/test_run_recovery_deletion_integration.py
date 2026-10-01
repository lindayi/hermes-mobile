"""Recovery/deletion seam: no writes beyond a durable tombstone.

The connection hook commits a deletion fence immediately before the recovery
writer transaction, after prior reads. It intentionally leaves run versions
unchanged: the tombstone, not a coincidental status/version change, must guard
writes. All state and transports are disposable test fixtures.
"""
from contextlib import closing
import sqlite3

import pytest

from backend.runs import RunConflict
from test_orchestration import USER, BODY, runtime_at
from test_run_tracking_recovery import existing_run


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['running', 'waiting_for_approval', 'missing_approval', 'placeholder_upgrade'])
@pytest.mark.parametrize('deletion_state', ['prepared', 'native_unknown', 'deleted'])
async def test_recovery_writer_rechecks_deletion_inside_transaction(tmp_path, monkeypatch, branch, deletion_state):
    runtime, journal, gateway = runtime_at(tmp_path)
    run = existing_run(runtime, journal)
    if branch in ('missing_approval', 'placeholder_upgrade'):
        journal.finish(USER['id'], run['id'], 'waiting_for_approval')
        if branch == 'placeholder_upgrade':
            runtime._missing_approval(USER, journal.get(USER['id'], run['id']), 'new-request')
            assert runtime.approvals(USER)['items'][0]['request_id'] == 'new-request'
    else:
        with closing(journal.connect()) as db, db:
            db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                       ('old', run['id'], 'old-request', '{}', 'details_unavailable', 0))
    run = journal.get(USER['id'], run['id'])
    gateway.status = dict(run_id='upstream', status=branch, pending_approvals=[])
    if branch == 'waiting_for_approval':
        gateway.status['pending_approvals'] = [dict(run_id='upstream', request_id='new-request')]
    original_connect = journal.connect
    writes = 0
    snapshot = {}

    def state():
        with closing(original_connect()) as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('runs', 'events', 'orchestration_approvals', 'approval_notification_intents')}

    class InterleavedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal writes
            if sql == 'BEGIN IMMEDIATE':
                writes += 1
                # Reconciliation first stores steering evidence, then observes
                # the run/approvals. Placeholder creation has only one writer.
                if writes == (1 if branch in ('missing_approval', 'placeholder_upgrade') else 2):
                    with closing(original_connect()) as db, db:
                        db.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                                   (USER['id'], USER['profile'], BODY['session_id'],
                                    'deletion-operation', deletion_state, 1))
                        db.execute('DELETE FROM events WHERE run_id=?', (run['id'],))
                    snapshot.update(state())
            return super().execute(sql, parameters)

    def interleaved_connect():
        db = sqlite3.connect(journal.path, timeout=10, factory=InterleavedConnection)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(journal, 'connect', interleaved_connect)
    try:
        try:
            if branch == 'missing_approval':
                runtime._missing_approval(USER, run, 'new-request')
            elif branch == 'placeholder_upgrade':
                runtime._approval(USER, run, dict(event='approval.request', run_id='upstream',
                                                  request_id='new-request', tool='terminal', command='pwd'))
            else:
                await runtime._reconcile(USER, run['id'])
        except RunConflict:
            pass  # The caller may surface the deletion conflict, never write.
        assert snapshot, 'the deletion fence interleaving must execute'
        assert state() == snapshot, 'recovery wrote run/approval/event data past deletion fence'
        assert not gateway.starts and not gateway.stops
    finally:
        await runtime.close()


@pytest.mark.parametrize('operation', ['finish', 'event'])
@pytest.mark.parametrize('deletion_state', ['prepared', 'native_unknown', 'deleted'])
def test_merged_journal_keeps_transactional_deletion_fences(tmp_path, operation, deletion_state):
    runtime, journal, _ = runtime_at(tmp_path)
    run = existing_run(runtime, journal)
    journal.finish(USER['id'], run['id'], 'completed')
    journal.claim_deletion(USER['id'], USER['profile'], BODY['session_id'])
    with closing(journal.connect()) as db, db:
        db.execute('UPDATE session_deletions SET state=?', (deletion_state,))
        db.execute('DELETE FROM events')
    with pytest.raises(RunConflict):
        if operation == 'finish':
            journal.finish(USER['id'], run['id'], 'completed', output='must not republish')
        else:
            journal.event(USER['id'], run['id'], 'delta', {'text': 'must not republish'})
    with closing(journal.connect()) as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT output FROM runs').fetchone()[0] is None
