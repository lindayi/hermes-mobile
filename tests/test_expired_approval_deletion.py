"""Expired grant lifecycle: disposable SQLite only, never native operations."""
from contextlib import closing
from types import SimpleNamespace

import pytest

from backend.orchestration import Orchestrator
from backend.runs import RunConflict, RunJournal


NOW = 1790704811.0
EXPIRED = 1790624643.860239
FINISHED = 1790624818.212597


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.runs.time.time', lambda: NOW)
    journal = RunJournal(tmp_path / 'runs.sqlite')
    Orchestrator(journal, SimpleNamespace(), SimpleNamespace())
    return journal


def legacy_run(journal, *, owner='owner', profile='default', status='completed', key='old'):
    run, _ = journal.submit(owner, profile, key, 'input', key)
    with closing(journal.connect()) as c, c:
        c.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?', (status, FINISHED, run['id']))
    return run


def grant(journal, run, *, aid='approval', status='pending', expiry=EXPIRED):
    with closing(journal.connect()) as c, c:
        c.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                  (aid, run['id'], 'request-' + aid, '{"command":"fixture"}', status, expiry))


def approval(journal, aid='approval'):
    with closing(journal.connect()) as c:
        return dict(c.execute('SELECT * FROM orchestration_approvals WHERE id=?', (aid,)).fetchone())


def test_legacy_expired_completed_approval_no_longer_blocks_unrelated_session(journal):
    run = legacy_run(journal)
    grant(journal, run)
    before = approval(journal)
    # Restart with exactly the historical shape: terminal run, stale pending grant.
    reopened = RunJournal(journal.path)
    claim = reopened.claim_deletion('owner', 'default', 'unrelated-session')
    assert claim['state'] == 'prepared'
    assert approval(reopened) == dict(before, status='expired')
    assert reopened.get('owner', run['id'])['status'] == 'completed'


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_terminal_finish_expires_only_its_own_pending_grants(journal, status):
    other = legacy_run(journal, key='other')
    grant(journal, other, aid='other')
    run = legacy_run(journal, status='running')
    grant(journal, run)
    grant(journal, run, aid='future', expiry=NOW + 1)
    for state in ('sending', 'unknown'):
        grant(journal, run, aid=state, status=state)
    before = approval(journal)
    journal.finish('owner', run['id'], status)
    assert approval(journal) == dict(before, status='expired')
    assert approval(journal, 'other')['status'] == 'pending'
    assert approval(journal, 'future')['status'] == 'pending'
    for state in ('sending', 'unknown'):
        assert approval(journal, state)['status'] == state
    assert journal.get('owner', run['id'])['status'] == status
    assert journal.events('owner', run['id'])[-1]['data']['status'] == status


def test_operator_reconciliation_repairs_exact_owned_record_without_deleting(journal):
    run = legacy_run(journal)
    grant(journal, run)
    grant(journal, run, aid='same-run-other-request')
    for key, owner, profile in [('other-run', 'owner', 'default'),
                                ('foreign-owner', 'stranger', 'default'),
                                ('foreign-profile', 'owner', 'other')]:
        other = legacy_run(journal, key=key, owner=owner, profile=profile)
        grant(journal, other, aid=key)
    before = approval(journal)
    ids = dict(run_id=run['id'], approval_id='approval', request_id='request-approval')
    assert journal.reconcile_expired_approvals('owner', 'default', **ids) == ['approval']
    assert approval(journal) == dict(before, status='expired')
    assert journal.reconcile_expired_approvals('owner', 'default', **ids) == []
    for aid in ('same-run-other-request', 'other-run', 'foreign-owner', 'foreign-profile'):
        assert approval(journal, aid)['status'] == 'pending'
    with closing(journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM session_deletion_operations').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 0


@pytest.mark.parametrize('field', ['user_id', 'profile', 'run_id', 'approval_id', 'request_id'])
@pytest.mark.parametrize('value', ['', ' ', 1, True, [], {}])
def test_operator_rejects_malformed_identity_filters(journal, field, value):
    run = legacy_run(journal)
    grant(journal, run)
    kwargs = dict(user_id='owner', profile='default', run_id=run['id'],
                  approval_id='approval', request_id='request-approval')
    kwargs[field] = value
    with pytest.raises(ValueError):
        journal.reconcile_expired_approvals(**kwargs)
    assert approval(journal)['status'] == 'pending'


@pytest.mark.parametrize('field', ['user_id', 'profile', 'run_id', 'approval_id', 'request_id'])
def test_operator_mismatched_identity_is_a_noop(journal, field):
    run = legacy_run(journal)
    grant(journal, run)
    kwargs = dict(user_id='owner', profile='default', run_id=run['id'],
                  approval_id='approval', request_id='request-approval')
    kwargs[field] = 'not-the-record'
    assert journal.reconcile_expired_approvals(**kwargs) == []
    assert approval(journal)['status'] == 'pending'


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('expiry', [EXPIRED, NOW])
def test_all_terminal_states_and_exact_expiry_boundary(journal, status, expiry):
    run = legacy_run(journal, status=status)
    grant(journal, run, expiry=expiry)
    assert journal.reconcile_expired_approvals('owner', 'default') == ['approval']
    assert approval(journal)['status'] == 'expired'
    assert journal.claim_deletion('owner', 'default', 'unrelated')['state'] == 'prepared'


@pytest.mark.parametrize('status,expiry', [('pending', NOW + 1), ('sending', EXPIRED),
                                          ('unknown', EXPIRED), ('sending', NOW + 1),
                                          ('unknown', NOW + 1)])
def test_unexpired_or_unresolved_grants_remain_blockers(journal, status, expiry):
    run = legacy_run(journal)
    grant(journal, run, status=status, expiry=expiry)
    before = approval(journal)
    assert journal.reconcile_expired_approvals('owner', 'default') == []
    with pytest.raises(RunConflict, match='approval'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal) == before


@pytest.mark.parametrize('status', ['queued', 'running', 'waiting_for_approval', 'stopping',
                                    'unknown', 'unrecognized'])
def test_nonterminal_runs_are_never_expired_or_unblocked(journal, status):
    run = legacy_run(journal, status=status)
    grant(journal, run)
    assert journal.reconcile_expired_approvals('owner', 'default') == []
    with pytest.raises(RunConflict, match='active|unresolved'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == 'pending'


@pytest.mark.parametrize('expiry', ['unknown', 'NaN', b'1790624643', float('inf'),
                                    float('-inf'), 0, -1, None, float('nan')])
def test_untrusted_expiry_fails_closed(journal, expiry):
    run = legacy_run(journal)
    # SQLite rejects NULL/NaN under today's NOT NULL schema. Model nullable legacy
    # corruption too: sqlite3 binds NaN as NULL, never a trustworthy timestamp.
    with closing(journal.connect()) as c, c:
        c.execute('DROP TABLE orchestration_approvals')
        c.execute('''CREATE TABLE orchestration_approvals(id TEXT PRIMARY KEY, run_id TEXT,
            request_id TEXT, action TEXT, status TEXT, expires_at REAL)''')
    grant(journal, run, expiry=expiry)
    assert journal.reconcile_expired_approvals('owner', 'default') == []
    with pytest.raises(RunConflict, match='approval'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == 'pending'


@pytest.mark.parametrize('now', ['unknown', True, 0, -1, float('inf'), float('-inf'), float('nan')])
def test_untrusted_clock_cannot_expire_grant(journal, monkeypatch, now):
    run = legacy_run(journal)
    grant(journal, run)
    monkeypatch.setattr('backend.runs.time.time', lambda: now)
    assert journal.reconcile_expired_approvals('owner', 'default') == []
    with pytest.raises(RunConflict, match='approval'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == 'pending'


def test_foreign_profile_untouched_by_deletion_cleanup(journal):
    own = legacy_run(journal)
    grant(journal, own)
    foreign = legacy_run(journal, profile='other', key='foreign')
    grant(journal, foreign, aid='foreign')
    journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == 'expired'
    assert approval(journal, 'foreign')['status'] == 'pending'


def test_foreign_owner_pending_grant_is_not_cleaned_or_ignored(journal):
    foreign = legacy_run(journal, owner='stranger')
    grant(journal, foreign)
    assert journal.reconcile_expired_approvals('owner', 'default') == []
    with pytest.raises(RunConflict, match='approval'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == 'pending'


@pytest.mark.parametrize('gate', ['deployment', 'deletion', 'tombstone', 'busy', 'steering', 'active'])
def test_cleanup_does_not_bypass_existing_gates_and_rolls_back(journal, gate):
    run = legacy_run(journal)
    grant(journal, run)
    kwargs = {}
    if gate == 'deployment':
        journal.set_deployment_gate('fixture')
    elif gate == 'busy':
        kwargs['busy_run_ids'] = (run['id'],)
    elif gate == 'active':
        legacy_run(journal, status='running', key='active')
    else:
        with closing(journal.connect()) as c, c:
            if gate == 'deletion':
                c.execute('INSERT INTO session_deletion_operations VALUES(?,?,?,?,?,?)',
                          ('op', 'owner', 'default', 'other', 'native_unknown', NOW))
            elif gate == 'tombstone':
                c.execute('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                          ('owner', 'default', 'unrelated', 'op', 'deleted', NOW))
            else:
                c.execute('INSERT INTO steering_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          ('steer', 'owner', 'default', run['id'], 'upstream', 'key',
                           'input', 'unknown', None, NOW, NOW))
    with pytest.raises(RunConflict):
        journal.claim_deletion('owner', 'default', 'unrelated', **kwargs)
    assert approval(journal)['status'] == 'pending'


def test_journal_without_approval_table_remains_supported(tmp_path):
    bare = RunJournal(tmp_path / 'bare.sqlite')
    run, _ = bare.submit('owner', 'default', 'chat', 'input', 'key')
    bare.finish('owner', run['id'], 'completed')
    assert bare.reconcile_expired_approvals('owner', 'default') == []
    assert bare.claim_deletion('owner', 'default', 'chat')['state'] == 'prepared'


@pytest.mark.parametrize('status', ['unknown', 'stopping', 'waiting_for_approval'])
def test_nonterminal_finish_does_not_expire_grants(journal, status):
    run = legacy_run(journal, status='running')
    grant(journal, run)
    journal.finish('owner', run['id'], status)
    assert approval(journal)['status'] == 'pending'


def test_failed_finish_compare_and_swap_does_not_expire_grants(journal):
    run = legacy_run(journal, status='running')
    grant(journal, run)
    journal.finish('owner', run['id'], 'completed', expected={'status': 'unknown'})
    assert approval(journal)['status'] == 'pending'
    assert journal.get('owner', run['id'])['status'] == 'running'


@pytest.mark.parametrize('mutation', ['sending', 'unknown', 'future'])
def test_reconciliation_reads_only_after_acquiring_writer_claim(journal, mutation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    run = legacy_run(journal)
    grant(journal, run)
    reached_writer = Event()
    original_connect = journal.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(lambda sql: reached_writer.set() if sql == 'BEGIN IMMEDIATE' else None)
        return connection

    # A genuine competing writer changes the grant before cleanup can inspect it.
    with closing(original_connect()) as writer, ThreadPoolExecutor(1) as pool:
        writer.execute('BEGIN IMMEDIATE')
        if mutation == 'future':
            writer.execute('UPDATE orchestration_approvals SET expires_at=?', (NOW + 1,))
        else:
            writer.execute('UPDATE orchestration_approvals SET status=?', (mutation,))
        journal.connect = traced_connect
        future = pool.submit(journal.reconcile_expired_approvals, 'owner', 'default')
        try:
            assert reached_writer.wait(3), 'cleanup did not claim the writer lock'
            assert not future.done(), 'cleanup escaped the competing writer lock'
        finally:
            writer.commit()
        assert future.result(timeout=3) == []
    with pytest.raises(RunConflict, match='approval'):
        journal.claim_deletion('owner', 'default', 'unrelated')
    assert approval(journal)['status'] == ('pending' if mutation == 'future' else mutation)


@pytest.mark.parametrize('iteration', range(8))
def test_new_native_grant_and_deletion_serialize_without_losing_pending_work(journal, iteration):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    run = legacy_run(journal)
    grant(journal, run)
    with closing(journal.connect()) as c, c:
        c.execute('UPDATE runs SET upstream_id=? WHERE id=?', ('native', run['id']))
    runtime = Orchestrator(journal, SimpleNamespace(), SimpleNamespace())
    user = {'id': 'owner', 'profile': 'default'}
    barrier = Barrier(2)

    def native_request():
        barrier.wait(timeout=3)
        try:
            runtime._approval(user, run, dict(run_id='native', request_id='new-request',
                                             command='fixture', expires_at=NOW + 60))
            return 'grant'
        except RunConflict:
            return 'refused'

    def deletion():
        barrier.wait(timeout=3)
        try:
            journal.claim_deletion('owner', 'default', run['session_id'])
            return 'delete'
        except RunConflict:
            return 'refused'

    with ThreadPoolExecutor(2) as pool:
        request, delete = pool.submit(native_request), pool.submit(deletion)
        outcomes = sorted([request.result(timeout=5), delete.result(timeout=5)])
    assert outcomes in [['delete', 'refused'], ['grant', 'refused']]
    with closing(journal.connect()) as c:
        new = c.execute("SELECT status FROM orchestration_approvals WHERE request_id='new-request'").fetchone()
        if outcomes == ['delete', 'refused']:
            assert new is None
            assert approval(journal)['status'] == 'expired'
        else:
            assert new['status'] == 'pending'
            assert c.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0
            assert approval(journal)['status'] == 'pending'  # aborted cleanup rolled back


def test_terminal_state_expiration_and_done_event_commit_atomically(journal):
    import sqlite3

    run = legacy_run(journal, status='running')
    grant(journal, run)
    with closing(journal.connect()) as c, c:
        c.execute("""CREATE TRIGGER reject_done BEFORE INSERT ON events
            WHEN NEW.name='done' BEGIN SELECT RAISE(ABORT, 'fixture event failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='fixture event failure'):
        journal.finish('owner', run['id'], 'completed')
    assert journal.get('owner', run['id'])['status'] == 'running'
    assert approval(journal)['status'] == 'pending'
    assert journal.events('owner', run['id']) == []


def test_reconciliation_never_rewrites_notification_or_run_audit(journal):
    run = legacy_run(journal)
    grant(journal, run)
    with closing(journal.connect()) as c, c:
        c.execute('INSERT INTO approval_notification_intents VALUES(?,?)', ('approval', 'sent'))
        before = dict(c.execute('SELECT * FROM runs WHERE id=?', (run['id'],)).fetchone())
    assert journal.reconcile_expired_approvals('owner', 'default') == ['approval']
    with closing(journal.connect()) as c:
        assert dict(c.execute('SELECT * FROM runs WHERE id=?', (run['id'],)).fetchone()) == before
        assert tuple(c.execute('SELECT * FROM approval_notification_intents').fetchone()) == ('approval', 'sent')


def test_grant_expiring_after_terminal_finish_is_reconciled_on_later_deletion(journal, monkeypatch):
    run = legacy_run(journal, status='running')
    grant(journal, run, expiry=NOW + 1)
    journal.finish('owner', run['id'], 'completed')
    assert approval(journal)['status'] == 'pending'
    monkeypatch.setattr('backend.runs.time.time', lambda: NOW + 1)
    assert journal.claim_deletion('owner', 'default', 'unrelated')['state'] == 'prepared'
    assert approval(journal)['status'] == 'expired'


@pytest.mark.asyncio
async def test_expired_audit_grant_cannot_be_decided_or_replayed(journal):
    run = legacy_run(journal)
    grant(journal, run)
    runtime = Orchestrator(journal, SimpleNamespace(), SimpleNamespace())
    user = {'id': 'owner', 'profile': 'default'}
    assert runtime.approvals(user)['items'] == []
    assert journal.reconcile_expired_approvals('owner', 'default') == ['approval']
    for decision in ('once', 'deny'):
        with pytest.raises(RunConflict, match='stale'):
            await runtime.decide(user, 'approval', decision)
    assert approval(journal)['status'] == 'expired'
