"""Independent review regressions, retained with a separate runtime writer.

Adapted from /tmp/hermes_integration_independent_review/test_independent_seam.py.
A peer runtime has its own local locks but shares the real SQLite journal. This
keeps the transactional fence test meaningful even when local admission rejects
an in-flight observation. No fabricated active-run tombstones or event scrubbing.
"""
from contextlib import closing
import sqlite3

import pytest

from backend.orchestration import Orchestrator
from backend.runs import RunConflict, RunJournal
from backend.session_deletion import SessionDeletion
from test_orchestration import USER, BODY, runtime_at
from test_run_tracking_recovery import existing_run


@pytest.mark.asyncio
@pytest.mark.parametrize('deletion_state', ['prepared', 'native_unknown', 'deleted'])
async def test_refresh_steering_writer_cannot_cross_real_deletion_claim(tmp_path, monkeypatch, deletion_state):
    runtime, journal, gateway = runtime_at(tmp_path)
    peer = Orchestrator(RunJournal(journal.path), gateway, runtime.catalog)
    run = existing_run(runtime, journal)
    gateway.status = dict(run_id='upstream', status='running', pending_steer='late native steering content')
    original_connect = journal.connect
    snapshot = {}
    injected = False

    def state():
        with closing(original_connect()) as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('runs', 'events', 'steering_evidence', 'steering_attempts')}

    class Interleaved(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal injected
            if sql == 'BEGIN IMMEDIATE' and not injected:
                injected = True
                # Another authorized writer finishes and claims deletion after
                # refresh's ownership check, before its steering transaction.
                peer.journal.finish(USER['id'], run['id'], 'completed', output='authoritative')
                assert runtime._observation_locks[run['id']].locked()
                claim = peer.claim_deletion(USER, BODY['session_id'])
                if deletion_state != 'prepared':
                    SessionDeletion(peer.journal)._outcome(USER, claim['operation_id'], deletion_state,
                        [BODY['session_id']] if deletion_state == 'deleted' else ())
                snapshot.update(state())
            return super().execute(sql, parameters)

    def interleaved_connect():
        db = sqlite3.connect(journal.path, factory=Interleaved)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(journal, 'connect', interleaved_connect)
    try:
        with pytest.raises(RunConflict):
            await runtime.refresh(USER, run['id'])
        assert injected and snapshot
        assert not gateway.starts and not gateway.stops
        assert state() == snapshot, 'Refresh republished steering evidence/events AFTER actual deletion claim'
    finally:
        await runtime.close()
        await peer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['running', 'waiting_for_approval', 'placeholder_upgrade'])
async def test_real_terminal_deletion_fences_new_writes(tmp_path, monkeypatch, branch):
    runtime, journal, gateway = runtime_at(tmp_path)
    peer = Orchestrator(RunJournal(journal.path), gateway, runtime.catalog)
    run = existing_run(runtime, journal)
    if branch == 'placeholder_upgrade':
        journal.finish(USER['id'], run['id'], 'waiting_for_approval')
        runtime._missing_approval(USER, journal.get(USER['id'], run['id']), 'req')
    gateway.status = dict(run_id='upstream', status=branch, pending_approvals=[])
    if branch == 'waiting_for_approval':
        gateway.status['pending_approvals'] = [dict(run_id='upstream', request_id='req')]
    original_connect = journal.connect
    snapshot = {}
    writes = 0

    def state():
        with closing(original_connect()) as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('runs', 'events', 'orchestration_approvals', 'approval_notification_intents')}

    class Interleaved(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal writes
            if sql == 'BEGIN IMMEDIATE':
                writes += 1
                if writes == (1 if branch == 'placeholder_upgrade' else 2):
                    peer.journal.finish(USER['id'], run['id'], 'completed')
                    claim = peer.claim_deletion(USER, BODY['session_id'])
                    SessionDeletion(peer.journal)._outcome(USER, claim['operation_id'], 'deleted', [BODY['session_id']])
                    snapshot.update(state())
            return super().execute(sql, parameters)

    def interleaved_connect():
        db = sqlite3.connect(journal.path, factory=Interleaved)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(journal, 'connect', interleaved_connect)
    try:
        with pytest.raises(RunConflict):
            if branch == 'placeholder_upgrade':
                runtime._approval(USER, run, dict(event='approval.request', run_id='upstream', request_id='req', command='pwd'))
            else:
                await runtime.refresh(USER, run['id'])
        assert snapshot and state() == snapshot
        assert not gateway.starts and not gateway.stops
    finally:
        await runtime.close()
        await peer.close()
