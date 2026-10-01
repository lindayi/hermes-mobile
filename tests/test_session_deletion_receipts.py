"""Read-only reconciliation of durable native receipts, no mutation retry."""
from contextlib import closing
import pytest
from backend.runs import RunJournal
from test_auth import BASE
from test_session_deletion import deletion_app


def test_durable_refusal_releases_claim_and_preserves_audit(deletion_app):
    app, client, user, calls, behavior = deletion_app
    behavior['reply'] = 'refused'
    response = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm':True})
    assert response.status_code == 409, response.text
    assert 'refused' in response.json()['detail']
    assert 'cli-1' in [row['id'] for row in client.get(BASE+'/sessions').json()['items']]
    assert client.get(BASE+'/sessions/cli-1/messages').status_code == 200
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM session_deletions').fetchone()[0] == 0
        operation = dict(c.execute('SELECT * FROM session_deletion_operations').fetchone())
        assert operation['state'] == 'refused'
        assert 'input' not in operation
    assert any(path == '/api/mobile/session-deletions/'+operation['operation_id'] for _, path in calls)
    # A NEW user-confirmed attempt is permitted after proved no mutation.
    behavior['reply'] = 'success'
    assert client.request('DELETE', BASE+'/sessions/cli-1',json={'confirm':True}).status_code == 200
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM session_deletion_operations').fetchone()[0] == 2


def test_lost_success_reconciles_receipt_without_second_delete(deletion_app):
    app, client, user, calls, behavior = deletion_app
    behavior['reply'] = 'committed-lost'
    response = client.request('DELETE',BASE+'/sessions/cli-1',json={'confirm':True})
    assert response.status_code == 200, response.text
    assert response.json() == {'id':'cli-1','deleted':True}
    assert len([1 for method, _ in calls if method == 'DELETE']) == 1


@pytest.mark.parametrize('outcome', ['deleted', 'refused', 'unknown', 'mismatched'])
def test_persisted_unknown_can_only_be_reconciled_by_bound_receipt(deletion_app, outcome):
    app, client, user, calls, behavior = deletion_app
    behavior['reply'] = 'lost'
    assert client.request('DELETE',BASE+'/sessions/cli-1',json={'confirm':True}).status_code == 503
    # Re-open on-disk journal, as after a bridge restart.
    journal = RunJournal(app.state.journal.path)
    with closing(journal.connect()) as c:
        operation = dict(c.execute('SELECT * FROM session_deletions').fetchone())
    behavior['durable_receipt'] = dict(id='cli-1',operation_id=operation['operation_id'],
        status=outcome,deleted=outcome=='deleted')
    if outcome == 'mismatched':
        behavior['durable_receipt'].update(status='refused',operation_id='wrong')
    response = client.get(BASE+'/sessions/cli-1/deletion')
    assert response.status_code == {'deleted':200,'refused':409,'unknown':503,'mismatched':503}[outcome]
    assert len([1 for method, _ in calls if method == 'DELETE']) == 1
    with closing(journal.connect()) as c:
        blocked = c.execute('SELECT count(*) FROM session_deletions').fetchone()[0]
    assert blocked == (0 if outcome == 'refused' else 1)
