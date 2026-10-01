"""Native-proved completed delegate targets must share the mobile fence."""
from contextlib import closing
import pytest
from backend.runs import RunJournal, RunConflict
from test_auth import BASE
from test_session_deletion import deletion_app


def test_confirmed_native_delegate_targets_receive_tombstones(deletion_app):
    app, client, user, calls, behavior = deletion_app
    run, _ = app.state.journal.submit(user['id'],'default','delegate-alias','input','key', history_anchor=lambda:
        dict(session_id='delegate-alias',canonical_session_id='delegate',message_id=0))
    app.state.journal.finish(user['id'],run['id'],'completed')
    behavior['deleted_ids'] = ['cli-1','delegate']
    assert client.request('DELETE',BASE+'/sessions/cli-1',json={'confirm':True}).status_code == 200
    for sid in ['delegate','delegate-alias']:
        with pytest.raises(RunConflict): app.state.journal.require_session(user['id'],'default',sid)
    with pytest.raises(RunConflict): app.state.journal.get(user['id'],run['id'])
    assert app.state.journal.submit(user['id'],'default','unrelated','input','new-key')[1]


def test_inflight_delete_fences_admission_until_native_target_set_is_known(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    journal.claim_deletion('owner','default','root')
    with pytest.raises(RunConflict):
        journal.submit('owner','default','possibly-delegate','input','key')
    assert journal.submit('owner','other','unrelated','input','other-key')[1]


def test_delete_refuses_unresolved_admission_when_native_delegate_scope_unknown(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    journal.submit('owner','default','possibly-delegate','input','key')
    with pytest.raises(RunConflict): journal.claim_deletion('owner','default','root')


@pytest.mark.parametrize('targets', [[], ['other'], ['cli-1','../bad'], ['cli-1',None], 'cli-1'])
def test_malformed_native_target_receipt_is_never_success(deletion_app,targets):
    app, client, user, calls, behavior = deletion_app
    behavior['deleted_ids'] = targets
    assert client.request('DELETE',BASE+'/sessions/cli-1',json={'confirm':True}).status_code == 503
