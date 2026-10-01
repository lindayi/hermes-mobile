"""Assembled deletion admission with both forms of historical live-account state."""
from contextlib import closing
import json
import time

import pytest

from backend.runs import RunJournal, RunConflict
from backend.steering import SteeringJournal


def legacy_history(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    steering = SteeringJournal(journal)
    user = {'id': 'owner', 'profile': 'default'}
    run, _ = journal.submit('owner', 'default', 'guided-chat', 'hello', 'turn-key')
    journal.set_upstream('owner', run['id'], 'native-run')
    run = journal.get('owner', run['id'])
    attempt, _ = steering.claim(user, run, {'input': 'Guidance', 'idempotency_key': 'steer-key'})
    steering.finish(attempt['id'], 'accepted_unconfirmed', 'steer-key')
    # The real native wrapper closes controls and emits unknown/accepted=True
    # at termination; unknown describes model consumption, not pending HTTP I/O.
    steering.observe(user, run, {'run_id': 'native-run', 'steer_receipts': [
        {'run_id': 'native-run', 'steer_id': 'steer-key', 'status': 'unknown', 'accepted': True}]})
    journal.finish('owner', run['id'], 'completed')
    # Legacy persisted row from before terminal-expiry cleanup existed.
    with closing(journal.connect()) as c, c:
        c.execute('CREATE TABLE orchestration_approvals(id TEXT PRIMARY KEY,run_id TEXT,request_id TEXT,action TEXT,status TEXT,expires_at REAL)')
        c.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                  ('expired-approval', run['id'], 'request-key', '{}', 'pending', time.time()-60))
    return journal, steering, user, run, attempt


def test_legacy_expiry_and_acknowledged_terminal_steering_allow_admission_without_rewriting_history(tmp_path):
    j, steering, user, run, attempt = legacy_history(tmp_path)
    before = steering.attempts(user, run['id'])
    evidence = steering.evidence(user, run['id'])
    claim = j.claim_deletion('owner', 'default', 'unrelated-chat')
    assert claim['state'] == 'prepared'
    assert steering.attempts(user, run['id']) == before
    assert steering.evidence(user, run['id']) == evidence
    assert before[0]['status'] == 'unknown'
    with closing(j.connect()) as c:
        assert c.execute('SELECT status FROM orchestration_approvals').fetchone()[0] == 'expired'


@pytest.mark.parametrize('fault', ['missing-receipt', 'wrong-upstream', 'accepted-false', 'busy-worker'])
def test_unacknowledged_or_active_control_still_blocks_and_rolls_back_expiry(tmp_path, fault):
    j, steering, user, run, attempt = legacy_history(tmp_path)
    with closing(j.connect()) as c, c:
        data = json.loads(c.execute('SELECT data FROM steering_evidence').fetchone()[0])
        if fault == 'missing-receipt': data['steer_receipts'] = []
        if fault == 'wrong-upstream': data['steer_receipts'][0]['run_id'] = 'other-native-run'
        if fault == 'accepted-false': data['steer_receipts'][0]['accepted'] = False
        c.execute('UPDATE steering_evidence SET data=?', (json.dumps(data),))
    with pytest.raises(RunConflict):
        j.claim_deletion('owner', 'default', 'unrelated-chat',
                         busy_run_ids=(run['id'],) if fault == 'busy-worker' else ())
    with closing(j.connect()) as c:
        assert c.execute('SELECT status FROM orchestration_approvals').fetchone()[0] == 'pending'
        assert c.execute('SELECT count(*) FROM session_deletion_operations').fetchone()[0] == 0
