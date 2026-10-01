"""Stored history deletion: disposable journal/native transports only."""
from contextlib import closing
import pytest
from backend.runs import RunJournal, RunConflict


def test_deletion_claim_serializes_admission_and_retains_retry_identity(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    assert hasattr(journal, 'claim_deletion'), 'durable deletion admission is missing'
    run, _ = journal.submit('owner', 'default', 'chat', 'private input', 'old-key')
    journal.finish('owner', run['id'], 'completed', output='private output')
    claim = journal.claim_deletion('owner', 'default', 'chat')
    assert claim['state'] == 'prepared'
    assert len(claim['operation_id']) == 32
    journal = RunJournal(journal.path)
    for sid, key in [('chat', 'fresh-key'), ('chat', 'old-key'), ('new-chat', 'old-key')]:
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', sid, 'private input', key)
    with pytest.raises(RunConflict):
        journal.claim_deletion('owner', 'default', 'chat')
    # Identity remains durable. Other principals/profiles are not hidden or purged.
    with closing(journal.connect()) as c:
        assert c.execute('SELECT idempotency_key FROM runs').fetchone()[0] == 'old-key'
    assert journal.submit('member', 'default', 'chat', 'own input', 'own-key')[1]
    assert journal.submit('owner', 'other', 'chat', 'own input', 'other-key')[1]


@pytest.mark.parametrize('status', ['queued', 'running', 'stopping', 'waiting_for_approval', 'unknown', 'new-unknown-state'])
@pytest.mark.parametrize('target', ['alias', 'anchor', 'canonical'])
def test_claim_refuses_any_active_or_unknown_alias(tmp_path, status, target):
    journal = RunJournal(tmp_path/'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'alias', 'input', 'key', history_anchor=lambda:
        dict(session_id='anchor', canonical_session_id='canonical', message_id=0))
    with closing(journal.connect()) as c, c:
        c.execute('UPDATE runs SET status=? WHERE id=?', (status, run['id']))
    with pytest.raises(RunConflict, match='active|unresolved'):
        journal.claim_deletion('owner', 'default', target)
    assert journal.get('owner', run['id'])['status'] == status


def test_completed_aliases_are_fenced_and_canonical_admission_is_rechecked(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'alias', 'input', 'key', history_anchor=lambda:
        dict(session_id='alias', canonical_session_id='canonical', message_id=0))
    journal.finish('owner', run['id'], 'completed')
    journal.claim_deletion('owner', 'default', 'canonical')
    with pytest.raises(RunConflict):
        journal.submit('owner', 'default', 'alias', 'input', 'new-key')
    with pytest.raises(RunConflict):
        journal.submit('owner', 'default', 'unseen-alias', 'input', 'new-key', history_anchor=lambda:
            dict(session_id='unseen-alias', canonical_session_id='canonical', message_id=0))
    with closing(journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 1


@pytest.mark.parametrize('operation', ['get', 'events', 'event', 'finish', 'set_upstream', 'snapshot_state', 'latest'])
def test_tombstone_blocks_reads_and_late_callbacks(tmp_path, operation):
    journal = RunJournal(tmp_path/'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'chat', 'input', 'key')
    journal.finish('owner', run['id'], 'completed')
    journal.claim_deletion('owner', 'default', 'chat')
    calls = {
        'get': lambda: journal.get('owner', run['id']),
        'events': lambda: journal.events('owner', run['id']),
        'event': lambda: journal.event('owner', run['id'], 'delta', {'text': 'late'}),
        'finish': lambda: journal.finish('owner', run['id'], 'completed', output='late'),
        'set_upstream': lambda: journal.set_upstream('owner', run['id'], 'late'),
        'snapshot_state': lambda: journal.snapshot_state('owner', 'default', 'chat'),
        'latest': lambda: journal.latest('owner', 'default', 'chat'),
    }
    with pytest.raises(RunConflict):
        calls[operation]()
    with closing(journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        assert c.execute('SELECT output FROM runs').fetchone()[0] is None


@pytest.fixture
def deletion_app(tmp_path, monkeypatch):
    import asyncio
    import json
    import httpx
    import backend.app as app_module
    from backend import model_controls
    from backend.hermes_client import GatewayClient
    from fastapi.testclient import TestClient
    from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
    from test_native_catalog import create_native_db
    home = tmp_path/'native'; home.mkdir(); create_native_db(home/'state.db')
    monkeypatch.setattr(app_module, 'MODEL_OWNER_HOME', home)
    monkeypatch.setattr(model_controls, 'standalone_owner_verified', lambda: True)
    calls, behavior = [], {'version': 1, 'reply': 'success'}
    def transport(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'features': {'mobile_session_delete_version': behavior['version']}})
        if request.url.path.startswith('/api/mobile/session-deletions/'):
            receipt = behavior.get('durable_receipt')
            return httpx.Response(200 if receipt else 404, json=receipt or {})
        if request.method == 'DELETE':
            body = json.loads(request.content)
            assert body['confirm'] is True and set(body) == {'confirm', 'operation_id'}
            reply = dict(id=request.url.path.rsplit('/', 1)[-1], deleted=True, operation_id=body['operation_id'])
            if 'deleted_ids' in behavior:
                reply['deleted_ids'] = behavior['deleted_ids']
            if behavior['reply'] == 'refused':
                behavior['durable_receipt'] = dict(reply, deleted=False, status='refused')
                return httpx.Response(409, json=behavior['durable_receipt'])
            if behavior['reply'] == 'committed-lost':
                behavior['durable_receipt'] = dict(reply, status='deleted')
                raise httpx.ReadError('lost fixture response after commit')
            if behavior['reply'] == 'false': reply['deleted'] = False
            if behavior['reply'] == 'mismatch': reply['id'] = 'someone-else'
            if behavior['reply'] == 'receipt': reply['operation_id'] = 'wrong'
            if behavior['reply'] == 'lost': raise httpx.ReadError('lost fixture response')
            if behavior['reply'] == '404': return httpx.Response(404, json={})
            return httpx.Response(200, json=reply)
        raise AssertionError('Unexpected native call: ' + str(request.url))
    gateway = GatewayClient('http://127.0.0.1:18642', 'fixture-token', True, transport=httpx.MockTransport(transport))
    app = app_module.create_app(app_module.Settings(state_dir=tmp_path/'app', profiles={'default':home}, bootstrap_secret=BOOTSTRAP), gateway_client=gateway)
    # Route tests need constructor state, not lifespan recovery/notification workers.
    # Enrollment can otherwise enable an unrelated background capability probe.
    client = TestClient(app, base_url=ORIGIN)
    try:
        client.headers['Origin'] = ORIGIN
        _, result = enroll(client)
        yield app, client, result.json()['user'], calls, behavior
    finally:
        client.close()
        asyncio.run(gateway.close())


def test_confirmed_delete_route_returns_exact_receipt_and_persists_tombstone(deletion_app):
    from test_auth import BASE
    app, client, user, calls, _ = deletion_app
    response = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'id': 'cli-1', 'deleted': True}
    with closing(app.state.journal.connect()) as c:
        row = c.execute('SELECT * FROM session_deletions WHERE user_id=?', (user['id'],)).fetchone()
        assert row['state'] == 'deleted'
    assert [path for method, path in calls if method == 'DELETE'] == ['/api/mobile/sessions/cli-1']


@pytest.mark.parametrize('reply', ['false', 'mismatch', 'receipt', 'lost', '404'])
def test_unconfirmed_native_reply_stays_unknown_never_retries(deletion_app, reply):
    from test_auth import BASE
    app, client, user, calls, behavior = deletion_app
    behavior['reply'] = reply
    response = client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True})
    assert response.status_code == 503
    assert 'unresolved' in response.json()['detail']
    with closing(app.state.journal.connect()) as c:
        assert c.execute('SELECT state FROM session_deletions').fetchone()[0] == 'native_unknown'
    behavior['reply'] = 'success'
    assert client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True}).status_code == 409
    assert len([1 for method, _ in calls if method == 'DELETE']) == 1
