import importlib.util
import pytest


def test_submission_is_durable_and_idempotent(tmp_path):
    assert importlib.util.find_spec('backend.runs') is not None, 'Durable run journal missing'
    from backend.runs import RunJournal
    path = tmp_path / 'runs.db'
    journal = RunJournal(path)
    one, created = journal.submit('user','default','native','Hello','request-1')
    assert created and one['status'] == 'queued'
    other, created = journal.submit('user','default','native','Hello','request-1')
    assert not created and other['id'] == one['id']
    assert RunJournal(path).get('user', one['id'])['input'] == 'Hello'
    with pytest.raises(KeyError):
        journal.get('stranger',one['id'])


def test_conflicting_key_and_busy_session_are_rejected(tmp_path):
    from backend.runs import RunJournal, RunConflict
    j = RunJournal(tmp_path/'r.db')
    one, _ = j.submit('u','p','s','hello','key')
    with pytest.raises(RunConflict):
        j.submit('u','p','s','different','key')
    with pytest.raises(RunConflict):
        j.submit('u','p','s','another','key-2')
    j.finish('u',one['id'],'completed',output='answer')
    assert j.submit('u','p','s','next','key-2')[1]


def test_events_are_persisted_and_owner_scoped(tmp_path):
    from backend.runs import RunJournal
    j = RunJournal(tmp_path/'r.db'); one,_ = j.submit('u','p','s','hello','key')
    event_id = j.event('u',one['id'],'delta',{'text':'hello'})
    events = j.events('u',one['id'])
    assert events[0]['data'] == {'text':'hello'}
    assert j.events('u',one['id'],after=event_id) == []
    with pytest.raises(KeyError):
        j.events('other',one['id'])
    j.finish('u',one['id'],'completed',output='hello')
    assert j.get('u',one['id'])['output'] == 'hello'


def test_restart_marks_unfinished_runs_unknown_without_replaying(tmp_path):
    from backend.runs import RunJournal
    j = RunJournal(tmp_path/'r.db'); one,_ = j.submit('u','p','s','hello','key')
    j.set_upstream('u',one['id'],'native-run-1')
    j.recover()
    assert j.get('u',one['id'])['status'] == 'unknown'
    assert j.get('u',one['id'])['upstream_id'] == 'native-run-1'
