"""Policy changes while transport is in flight must apply before any retry."""
from types import SimpleNamespace
import pytest
from test_push_policy import policy, statuses
from test_push_policy_presence import presence


@pytest.mark.parametrize('change',['master','category','read','presence','event'])
def test_failed_claim_rechecks_changes_before_scheduling_retry(policy, change):
    s = policy.service
    item = s.ingest('alice','race','Finished','Public response',session_id='chat',category='completion')
    eligible = [True]
    s.event_validator = lambda *args: eligible[0]
    s.presence_validator = lambda *args: True
    called = []
    def send(**kwargs):
        called.append(kwargs)
        if change in ('master','category'):
            for device in ('d1','d2'):
                prefs = s.get_preferences('alice',device)
                if change == 'master':
                    prefs['enabled'] = False
                else:
                    prefs['categories']['completion'] = False
                s.set_preferences('alice',device,prefs)
        elif change == 'read':
            s.mark_read('alice',item['id'])
        elif change == 'presence':
            s.record_presence('alice','d1',presence())
        else:
            eligible[0] = False
        return SimpleNamespace(status_code=503)
    s.send_push = send
    s.flush()
    assert len(called) == 1, 'Unclaimed endpoints must see new policy'
    assert all(status == 'suppressed' for status, _ in statuses(s)), 'Failed claim must not reenter retry after policy forbids it'
    policy.now[0] += 61
    assert s.flush()['sent'] == 0
    assert len(called) == 1


def test_final_claim_rechecks_read_changed_by_legacy_reader(policy):
    s = policy.service
    item = s.ingest('alice','legacy-read','Finished','Public response',category='completion')
    with s._db() as db:
        db.execute('UPDATE inbox SET read=1 WHERE id=?',(item['id'],))
    assert s.flush()['sent'] == 0
    assert all(status == 'suppressed' for status, _ in statuses(s))


def test_batch_rechecks_expiry_before_each_claim(policy):
    s = policy.service
    s.ingest('alice','expiry','Finished','Public response',category='completion')
    called = []
    def send(**kw):
        called.append(kw)
        policy.now[0] += 86401
        return SimpleNamespace(status_code=201)
    s.send_push = send
    assert s.flush()['sent'] == 1
    assert len(called) == 1
