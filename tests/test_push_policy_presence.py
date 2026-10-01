"""Exact-conversation leases, sequence fences, and bounded presence state."""
import pytest
from backend.notifications import NotificationService
from test_push_policy import policy, statuses


def presence(client='tab', session='chat', visible=True, sequence=1):
    return dict(client_id=client,session_id=session,visible=visible,sequence=sequence)


def test_visible_exact_conversation_suppresses_across_devices_not_other_events(policy):
    s = policy.service
    assert callable(getattr(s,'record_presence',None)), 'Presence missing'
    s.presence_validator = lambda u, sid: u == 'alice' and sid in ('chat','other')
    s.record_presence('alice','d1',presence())
    s.ingest('alice','same','Finished','Public response',session_id='chat',category='completion')
    s.ingest('alice','other','Finished','Other response',session_id='other',category='completion')
    s.ingest('alice','scheduled','Watch','Result',category='scheduled')
    assert s.flush()['sent'] == 4
    assert len(s.list_inbox('alice')) == 3
    assert sum(status == 'suppressed' for status, reason in statuses(s)) == 2
    policy.now[0] += 46
    assert s.flush()['sent'] == 0, 'Suppressed work must not replay after presence expires'
    s.ingest('alice','after-expiry','Finished','Public response',session_id='chat',category='completion')
    assert s.flush()['sent'] == 2


def test_multitab_presence_monotonic_sequence_survives_hide_expiry_and_navigation(policy):
    s = policy.service
    s.presence_validator = lambda u, sid: u == 'alice' and sid in ('chat','other')
    def is_present(session='chat'):
        with s._db() as db:
            return s._present(db,'alice',session,'completion')
    s.record_presence('alice','d1',presence(sequence=10))
    s.record_presence('alice','d1',presence(visible=False,sequence=12))
    s.record_presence('alice','d1',presence(sequence=11))
    assert not is_present()
    s.record_presence('alice','d1',presence(sequence=13))
    policy.now[0] += 46
    s.record_presence('alice','d1',presence(sequence=13))
    assert not is_present(), 'Equal-sequence heartbeat cannot revive expired lease'
    s.record_presence('alice','d1',presence(sequence=14))
    s.record_presence('alice','d1',presence(client='second',sequence=1))
    s.record_presence('alice','d1',presence(session=None,visible=False,sequence=15))
    assert is_present(), 'Other tab survives hide'
    s.record_presence('alice','d1',presence(client='second',session='other',sequence=2))
    assert not is_present() and is_present('other')
    policy.active.remove(('alice','d1'))
    assert not is_present('other'), 'Revoked presence must not suppress other devices'
    with pytest.raises(PermissionError):
        s.record_presence('alice','d1',presence(sequence=16))


@pytest.mark.parametrize('changes', [ {'visible':1}, {'sequence':True}, {'sequence':-1},
    {'sequence':2**53}, {'client_id':'x'*81}, {'client_id':''}, {'client_id':'bad\nclient'},
    {'session_id':'x'*257}, {'session_id':3}, {'extra':'bad'}])
def test_presence_input_strict_and_bounded(policy, changes):
    s = policy.service
    s.presence_validator = lambda *args: True
    with pytest.raises(ValueError):
        s.record_presence('alice','d1',{**presence(),**changes})


def test_presence_clients_capped_without_eviction_or_sequence_reset(policy):
    s = policy.service
    s.presence_validator = lambda u, sid: u == 'alice' and sid == 'chat'
    for i in range(64):
        s.record_presence('alice','d1',presence(client=f'tab{i}'))
    with pytest.raises(ValueError):
        s.record_presence('alice','d1',presence(client='overflow'))
    policy.now[0] += 46
    with pytest.raises(ValueError):
        s.record_presence('alice','d1',presence(client='overflow'))
    s.record_presence('alice','d1',presence(client='tab0',visible=False,sequence=2))
    s.record_presence('alice','d1',presence(client='tab0',sequence=1))
    with s._db() as db:
        assert db.execute('SELECT count(*) FROM push_presence').fetchone()[0] == 64
        assert not s._present(db,'alice','chat','completion')
    with pytest.raises(PermissionError):
        s.record_presence('alice','d2',presence(session='foreign'))
    s.presence_validator = lambda *args: False
    with s._db() as db:
        assert not s._present(db,'alice','chat','completion')


def test_presence_resolves_positive_canonical_identity_across_compression(policy):
    s = policy.service
    mapping = {'old':'old','unrelated':'unrelated'}
    s.presence_resolver = lambda user, sid: mapping.get(sid)
    s.presence_validator = lambda user, sid: sid in mapping.values()
    s.record_presence('alice','d1',presence(session='old'))
    mapping.update(old='new',new='new')
    s.ingest('alice','completed','Finished','Result',session_id='new',category='completion')
    assert s.flush()['sent'] == 0
    s.ingest('alice','unrelated','Finished','Result',session_id='unrelated',category='completion')
    assert s.flush()['sent'] == 2
    s.record_presence('alice','d1',presence(session='old',sequence=2))
    with s._db() as db:
        assert db.execute('SELECT session_id FROM push_presence').fetchone()[0] == 'new'
    mapping.clear()
    with pytest.raises(PermissionError):
        s.record_presence('alice','d1',presence(session='old',sequence=3))
