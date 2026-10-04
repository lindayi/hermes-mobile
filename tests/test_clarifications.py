import asyncio
import json
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from backend.clarifications import ClarificationJournal
from backend.hermes_client import IntegrationUnavailable
from backend.runs import RunConflict, RunJournal


OWNER = {'id': 'owner', 'profile': 'default', 'role': 'owner'}


@pytest.fixture
def authenticated_clarification_app(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from backend.app import Settings, create_app
    from backend.hermes_client import GatewayClient
    from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
    from test_native_catalog import create_native_db

    native = SimpleNamespace(mode='waiting', items=[], calls=[], answer_entered=None,
                             answer_release=None, get_entered=None, get_release=None,
                             snapshot=None, adapter=None, answer_response=None)

    async def handle(request):
        native.calls.append((request.method, request.url.path))
        assert request.headers['Authorization'] == 'Bearer ' + gateway.token
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'mobile_run_controls': {
                'version': 1, 'clarifications': native.mode != 'unsupported'}})
        if request.method == 'GET':
            assert request.url.path == '/v1/runs/native-run'
            if native.get_entered:
                native.get_entered.set()
                await native.get_release.wait()
            if native.mode == 'timeout':
                raise httpx.ReadTimeout('synthetic timeout', request=request)
            if native.mode in ('missing', 'unavailable'):
                return httpx.Response(404 if native.mode == 'missing' else 503)
            if native.snapshot is not None:
                return httpx.Response(200, json=native.snapshot)
            status = ('waiting_for_clarification'
                      if any(item['status'] == 'pending' for item in native.items) else 'running')
            return httpx.Response(200, json={'run_id': 'native-run', 'status': status,
                                            'clarifications': native.items})
        assert request.method == 'POST'
        if native.answer_response is not None:
            if native.answer_response == 'timeout':
                raise httpx.ReadTimeout('Synthetic lost acknowledgement', request=request)
            status, reply = native.answer_response
            return httpx.Response(status, json=reply)
        question_id = request.url.path.rsplit('/', 1)[-1]
        if native.adapter is not None:
            response = await native.adapter._handle_clarification_answer(SimpleNamespace(
                match_info={'run_id': 'native-run', 'question_id': question_id},
                body=json.loads(request.content)))
            return httpx.Response(response.status, json=json.loads(response.text))
        item = next(item for item in native.items if item['question_id'] == question_id)
        body = json.loads(request.content)
        item.update(status='answered', **body, updated_at=time.time())
        if native.answer_entered:
            native.answer_entered.set()
            await native.answer_release.wait()
        return httpx.Response(200, json={'object': 'hermes.run.clarification',
            'run_id': 'native-run', 'question_id': question_id,
            'status': 'answered', 'answer': body['answer']})

    home = tmp_path / 'synthetic-home'
    home.mkdir()
    create_native_db(home / 'state.db')
    gateway = GatewayClient('http://127.0.0.1:8642', 'synthetic-token',
                            execution_ready=True, transport=httpx.MockTransport(handle))
    app = create_app(Settings(state_dir=tmp_path / 'app-state', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP), gateway_client=gateway)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        owner = client.get(BASE + '/auth/me').json()['user']
        runtime = app.state.orchestrator
        runtime.recovery_interval = 3600
        client.portal.call(runtime._recovery_task.cancel)
        run, _ = app.state.journal.submit(owner['id'], 'default', 'wa-1', 'Original', 'original')
        app.state.journal.set_upstream(owner['id'], run['id'], 'native-run')
        app.state.journal.set_active_status(owner['id'], run['id'], 'waiting_for_clarification',
                                           upstream_id='native-run')
        yield app, client, owner, app.state.journal.get(owner['id'], run['id']), native


def test_actual_native_conflict_reconciles_first_answer_and_replacement(
        authenticated_clarification_app):
    from backend.native_run_controls import run_controls_adapter
    from test_native_run_controls import Base
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime = app.state.orchestrator
    question = pending(created_at=1, updated_at=1)
    runtime.clarifications.event(owner, run, question)
    accepted = {**question, 'status': 'answered', 'answer': 'Keep current',
                'other': False, 'updated_at': 2}
    replacement = pending('b' * 32, created_at=3, updated_at=3)
    native.items = [accepted, replacement]
    native.adapter = run_controls_adapter(Base)()
    native.adapter._state('native-run')['clarifications'] = {
        question['question_id']: accepted}
    path = BASE + '/runs/' + run['id'] + '/clarifications'
    response = client.post(path + '/' + question['question_id'] + '/answer',
                           json={'answer': 'Change it', 'other': False})
    assert response.status_code == 409, response.text
    state = client.get(path).json()
    assert state['status'] == 'waiting_for_clarification'
    assert [(item['status'], item['answer']) for item in state['items']] == [
        ('answered', 'Keep current'), ('pending', None)]
    before = app.state.journal.events(owner['id'], run['id'])
    assert client.get(path).json() == state
    assert app.state.journal.events(owner['id'], run['id']) == before
    assert sum(method == 'POST' for method, _ in native.calls) == 1
    assert not any(path == '/v1/runs' for _, path in native.calls)


@pytest.mark.parametrize('response', [
    'timeout', (503, {'error': {'code': 'clarification_conflict'}}),
    (409, {'error': {'code': 'clarification_conflict'}}),
    (409, {'object': 'hermes.run.clarification', 'run_id': 'foreign',
           'question_id': 'a' * 32, 'status': 'rejected',
           'error': {'code': 'clarification_conflict'}}),
])
def test_unbound_or_lost_native_rejection_retains_attempt_without_resend(
        authenticated_clarification_app, response):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime = app.state.orchestrator
    question = pending()
    runtime.clarifications.event(owner, run, question)
    native.items = [question]
    native.answer_response = response
    path = BASE + '/runs/' + run['id'] + '/clarifications'
    result = client.post(path + '/' + question['question_id'] + '/answer',
                         json={'answer': 'Change it', 'other': False})
    assert result.status_code == 200
    assert result.json()['status'] == 'unknown'
    assert result.json()['answer'] == 'Change it'
    native.items = [{**question, 'status': 'answered', 'answer': 'Keep current',
                     'other': False, 'updated_at': time.time() + 1}]
    state = client.get(path).json()
    assert state['items'][0]['status'] == 'unknown'
    assert state['items'][0]['answer'] == 'Change it'
    assert sum(method == 'POST' for method, _ in native.calls) == 1
    assert not any(path == '/v1/runs' for _, path in native.calls)


@pytest.mark.parametrize('status', [
    'waiting_for_approval', 'stopping', 'completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('recovered', [False, True])
def test_reopen_applies_bound_native_lifecycle_before_polling(
        authenticated_clarification_app, status, recovered):
    from backend.orchestration import Orchestrator
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    question = pending()
    runtime.clarifications.event(owner, run, question)
    if recovered:
        reopened = RunJournal(journal.path)
        reopened.recover()
        Orchestrator(reopened, runtime.gateway, runtime.catalog)
    native.snapshot = {
        'run_id': 'native-run', 'status': status, 'output': 'Synthetic terminal output',
        'clarifications': [{**question, 'status': (
            'cancelled' if status in ('stopping', 'cancelled') else 'expired'),
            'updated_at': time.time() + 1}],
        'pending_approvals': [{'run_id': 'native-run', 'request_id': 'action'}],
    }
    path = BASE + '/runs/' + run['id'] + '/clarifications'
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()['status'] == status
    if status in ('completed', 'failed', 'cancelled'):
        assert journal.get(owner['id'], run['id'])['output'] == 'Synthetic terminal output'
    events = journal.events(owner['id'], run['id'])
    assert client.get(path).json() == response.json()
    assert journal.events(owner['id'], run['id']) == events
    fresh = Orchestrator(RunJournal(journal.path), runtime.gateway, runtime.catalog)
    assert client.portal.call(fresh.clarifications_for_run, owner, run['id'])[
        'status'] == status
    assert journal.events(owner['id'], run['id']) == events
    assert all(method == 'GET' for method, _ in native.calls)


@pytest.mark.parametrize('fence', [
    'owner', 'run', 'session', 'profile', 'binding', 'stop', 'terminal'])
def test_reopen_lifecycle_snapshot_preserves_identity_and_progress_fences(
        authenticated_clarification_app, fence):
    import httpx
    from test_auth import BASE, ORIGIN

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    question = pending()
    runtime.clarifications.event(owner, run, question)
    native.snapshot = {
        'run_id': 'native-run', 'status': 'waiting_for_approval',
        'clarifications': [{**question, 'status': 'expired', 'updated_at': time.time() + 1}],
        'pending_approvals': [{'run_id': 'native-run', 'request_id': 'action'}],
    }

    async def race():
        native.get_entered, native.get_release = asyncio.Event(), asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                     cookies=dict(client.cookies), headers=dict(client.headers)) as api:
            request = asyncio.create_task(api.get(BASE + '/runs/' + run['id'] + '/clarifications'))
            await asyncio.wait_for(native.get_entered.wait(), 1)
            other, _ = journal.submit(owner['id'], 'default', 'parallel', 'Other', 'parallel')
            journal.set_upstream(owner['id'], other['id'], 'other-native')
            journal.finish(owner['id'], other['id'], 'completed', output='Independent progress')
            if fence == 'owner':
                runtime.recovery_validator = lambda user: False
            elif fence == 'run':
                native.snapshot['run_id'] = 'foreign'
            elif fence in ('session', 'profile'):
                native.snapshot[fence + '_id' if fence == 'session' else fence] = 'foreign'
            elif fence == 'binding':
                with closing(journal.connect()) as connection, connection:
                    connection.execute('UPDATE runs SET upstream_id=? WHERE id=?',
                                       ('replacement-native', run['id']))
            elif fence == 'stop':
                journal.finish(owner['id'], run['id'], 'stopping')
                runtime.clarifications.mark_pending_unknown(owner, run['id'])
            else:
                journal.finish(owner['id'], run['id'], 'completed', output='Newer output')
            before = journal.get(owner['id'], run['id'])
            native.get_release.set()
            response = await asyncio.wait_for(request, 1)
            assert response.status_code == 200
            assert journal.get(owner['id'], run['id']) == before
            assert runtime.approvals(owner)['items'] == []
            assert journal.get(owner['id'], other['id'])['output'] == 'Independent progress'

    client.portal.call(race)


def test_authenticated_native_loss_fences_run_and_stabilizes_replay(authenticated_clarification_app):
    from backend.orchestration import Orchestrator
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    old = pending('c' * 32, created_at=1, updated_at=2, status='answered',
                  answer='Keep current', other=False)
    unanswered = pending(created_at=3, updated_at=3)
    attempted = pending('b' * 32, created_at=4, updated_at=4)
    for item in (old, unanswered, attempted):
        runtime.clarifications.event(owner, run, item)
    runtime.clarifications.claim(owner, run, attempted['question_id'],
                                 {'answer': 'Change it', 'other': False})
    native.mode = 'missing'
    path = BASE + '/runs/' + run['id'] + '/clarifications'
    response = client.get(path)
    assert response.status_code == 200
    state = response.json()
    assert state['status'] == 'unknown'
    assert state['available'] is False
    assert [item['status'] for item in state['items']] == ['answered', 'unknown', 'unknown']
    assert state['items'][2]['answer'] == 'Change it'
    events = journal.events(owner['id'], run['id'])
    assert len([event for event in events if event['name'] == 'done']) == 1
    for _ in range(3):
        assert client.get(path).json() == state
        client.portal.call(runtime._reconcile, owner, run['id'])
        assert journal.events(owner['id'], run['id']) == events
    reopened = RunJournal(journal.path)
    client.portal.call(reopened.recover)
    fresh = Orchestrator(reopened, runtime.gateway, runtime.catalog)
    client.portal.call(fresh._reconcile, owner, run['id'])
    assert client.portal.call(fresh.clarifications_for_run, owner, run['id']) == state
    assert reopened.events(owner['id'], run['id']) == events
    with closing(reopened.connect()) as connection:
        replay = reopened._replay_events(connection, run['id'])
        assert [event['data']['status'] for event in replay] == ['answered', 'unknown', 'unknown']
        assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
    answer = client.post(path + '/' + unanswered['question_id'] + '/answer',
                         json={'answer': 'Keep current', 'other': False})
    assert answer.status_code == 409
    assert all(method == 'GET' for method, _ in native.calls)


@pytest.mark.parametrize('mode', ['timeout', 'unavailable', 'unsupported'])
def test_authenticated_uncertainty_does_not_fence_live_waiter(authenticated_clarification_app, mode):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    app.state.orchestrator.clarifications.event(owner, run, pending())
    before = app.state.journal.events(owner['id'], run['id'])
    native.mode = mode
    state = client.get(BASE + '/runs/' + run['id'] + '/clarifications').json()
    assert state['available'] is False
    assert state['status'] == 'waiting_for_clarification'
    assert state['items'][0]['status'] == 'pending'
    assert app.state.journal.events(owner['id'], run['id']) == before


def test_pre_dispatch_clarification_unavailable_is_explicitly_not_sent(
        authenticated_clarification_app):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    question = pending()
    app.state.orchestrator.clarifications.event(owner, run, question)
    native.mode = 'unsupported'

    response = client.post(
        BASE + '/runs/' + run['id'] + '/clarifications/' +
        question['question_id'] + '/answer',
        json={'answer': 'Keep current', 'other': False})

    assert response.status_code == 503
    assert response.json()['code'] == 'clarification_not_sent'
    assert app.state.orchestrator.clarifications.list(owner, run['id'])[0]['status'] == 'pending'
    assert all(method == 'GET' for method, _ in native.calls)


def test_conflicting_answer_preserves_first_native_receipt(authenticated_clarification_app):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    question = pending()
    native.items = [question]
    app.state.orchestrator.clarifications.event(owner, run, question)
    path = (BASE + '/runs/' + run['id'] + '/clarifications/' +
            question['question_id'] + '/answer')

    accepted = client.post(path, json={'answer': 'Keep current', 'other': False})
    conflict = client.post(path, json={'answer': 'Change it', 'other': False})
    state = client.get(BASE + '/runs/' + run['id'] + '/clarifications').json()

    assert accepted.status_code == 200
    assert conflict.status_code == 409
    assert state['items'][0]['status'] == 'answered'
    assert state['items'][0]['answer'] == 'Keep current'
    assert len([call for call in native.calls if call[0] == 'POST']) == 1


@pytest.mark.parametrize('late_status', ['answered', 'expired'])
@pytest.mark.parametrize('current_status', ['pending', 'sending', 'unknown'])
def test_older_terminal_observations_preserve_current_waiter(
        authenticated_clarification_app, late_status, current_status):
    from backend.orchestration import Orchestrator
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    old = pending(created_at=1, updated_at=1)
    current = pending('b' * 32, created_at=2, updated_at=2)
    native.items = [current, old]  # Native history need not be chronological.
    for item in native.items:
        runtime.clarifications.event(owner, run, item)
    body = {'answer': 'Change it', 'other': False}
    if current_status != 'pending':
        runtime.clarifications.claim(owner, run, current['question_id'], body)
        if current_status == 'unknown':
            runtime.clarifications.finish(owner, run['id'], current['question_id'], 'unknown')
    terminal = {**old, 'status': late_status, 'updated_at': 3}
    if late_status == 'answered':
        terminal.update(answer='Keep current', other=False)
    native.items = [current, terminal]
    for item in (terminal, current, terminal, old):
        client.portal.call(runtime._observe_clarification_event, owner, run['id'], item)
        assert journal.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'
    records = runtime.clarifications.list(owner, run['id'])
    assert records[0]['status'] == late_status
    assert records[1]['status'] == current_status
    if current_status == 'pending':
        reopened = RunJournal(journal.path)
        fresh = Orchestrator(reopened, runtime.gateway, runtime.catalog)
        client.portal.call(fresh._reconcile, owner, run['id'])
        assert reopened.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'
        client.portal.call(runtime._reconcile, owner, run['id'])
        path = BASE + '/runs/' + run['id'] + '/clarifications/' + current['question_id'] + '/answer'
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'answered'
        assert journal.get(owner['id'], run['id'])['status'] == 'running'
        assert client.post(path, json=body).json()['status'] == 'answered'
        assert client.post(path, json={'answer': 'Keep current', 'other': False}).status_code == 409
        client.portal.call(runtime._observe_clarification_event, owner, run['id'], terminal)
        assert journal.get(owner['id'], run['id'])['status'] == 'running'
        assert native.calls.count(('POST', '/v1/runs/native-run/clarifications/' +
                                   current['question_id'])) == 1
    assert not any(path == '/v1/runs' for _, path in native.calls)


@pytest.mark.parametrize('progress', ['stopping', 'unknown', 'completed', 'failed', 'cancelled'])
def test_authenticated_loss_preserves_bound_progress_during_get(
        authenticated_clarification_app, progress):
    import httpx
    from test_auth import BASE, ORIGIN

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    runtime.clarifications.event(owner, run, pending())
    native.mode = 'missing'

    async def race():
        native.get_entered, native.get_release = asyncio.Event(), asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                     cookies=dict(client.cookies), headers=dict(client.headers)) as api:
            authenticated = await api.get(BASE + '/auth/me')
            assert authenticated.status_code == 200, authenticated.text
            request = asyncio.create_task(api.get(BASE + '/runs/' + run['id'] + '/clarifications'))
            await asyncio.wait_for(native.get_entered.wait(), 1)
            if progress == 'unknown':
                journal.finish(owner['id'], run['id'], 'stopping')
            journal.finish(owner['id'], run['id'], progress)
            if progress in ('stopping', 'unknown'):
                runtime.clarifications.mark_pending_unknown(owner, run['id'])
            events = journal.events(owner['id'], run['id'])
            native.get_release.set()
            response = await asyncio.wait_for(request, 1)
            assert response.status_code == 200
            assert response.json()['status'] == progress
            assert journal.events(owner['id'], run['id']) == events
            await runtime._reconcile(owner, run['id'])
            assert journal.get(owner['id'], run['id'])['status'] == progress
            assert journal.events(owner['id'], run['id']) == events

    client.portal.call(race)
    assert all(method == 'GET' for method, _ in native.calls)


@pytest.mark.parametrize('late_status', ['answered', 'expired'])
def test_authenticated_answer_serializes_late_history_and_other_session_progress(
        authenticated_clarification_app, late_status):
    import httpx
    from test_auth import BASE, ORIGIN

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    old = pending(created_at=1, updated_at=1, status='expired')
    current = pending('b' * 32, created_at=2, updated_at=2)
    next_question = pending('c' * 32, created_at=3, updated_at=3)
    native.items = [old, current]
    for item in native.items:
        runtime.clarifications.event(owner, run, item)
    other, _ = journal.submit(owner['id'], 'default', 'cli-1', 'Unrelated', 'unrelated')
    journal.set_upstream(owner['id'], other['id'], 'other-native')
    journal.set_active_status(owner['id'], other['id'], 'waiting_for_clarification',
                              upstream_id='other-native')
    other = journal.get(owner['id'], other['id'])
    unrelated = pending('d' * 32, run_id='other-native')
    runtime.clarifications.event(owner, other, unrelated)

    async def race():
        native.answer_entered, native.answer_release = asyncio.Event(), asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                     cookies=dict(client.cookies), headers=dict(client.headers)) as api:
            authenticated = await api.get(BASE + '/auth/me')
            assert authenticated.status_code == 200, authenticated.text
            path = BASE + '/runs/' + run['id'] + '/clarifications/'
            body = {'answer': 'Change it', 'other': False}
            answer = asyncio.create_task(api.post(path + current['question_id'] + '/answer', json=body))
            await asyncio.wait_for(native.answer_entered.wait(), 1)
            newer_event = asyncio.create_task(runtime._observe_clarification_event(
                owner, run['id'], next_question))
            late = {**old, 'status': late_status, 'updated_at': 4}
            if late_status == 'answered':
                late.update(answer='Keep current', other=False)
            late_event = asyncio.create_task(runtime._observe_clarification_event(owner, run['id'], late))
            await asyncio.sleep(0)
            assert not newer_event.done() and not late_event.done()
            unrelated.update(status='answered', answer='Keep current', other=False,
                             updated_at=unrelated['updated_at'] + 1)
            await asyncio.wait_for(runtime._observe_clarification_event(owner, other['id'], unrelated), 1)
            assert journal.get(owner['id'], other['id'])['status'] == 'running'
            native.answer_release.set()
            response = await asyncio.wait_for(answer, 1)
            assert response.status_code == 200
            await asyncio.wait_for(asyncio.gather(newer_event, late_event), 1)
            assert journal.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'
            current_saved = runtime.clarifications.list(owner, run['id'])[1]
            assert current_saved['status'] == 'answered' and current_saved['answer'] == 'Change it'
            native.items.append(next_question)
            assert (await api.post(path + next_question['question_id'] + '/answer', json=body)).status_code == 200
            assert journal.get(owner['id'], run['id'])['status'] == 'running'

    client.portal.call(race)
    assert len([call for call in native.calls if call[0] == 'POST']) == 2
    assert not any(path == '/v1/runs' for _, path in native.calls)


def test_replayed_older_pending_history_does_not_reopen_resolved_current_question(
        authenticated_clarification_app):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    current = pending('b' * 32, created_at=2, updated_at=2, status='answered',
                      answer='Change it', other=False)
    client.portal.call(runtime._observe_clarification_event, owner, run['id'], current)
    assert journal.get(owner['id'], run['id'])['status'] == 'running'
    old = pending(created_at=1, updated_at=1)
    for item in (old, current, old):
        client.portal.call(runtime._observe_clarification_event, owner, run['id'], item)
        assert journal.get(owner['id'], run['id'])['status'] == 'running'
    native.items = [current, old]
    # A newer live waiter remains answerable even with unresolved historical evidence.
    newest = pending('c' * 32, created_at=3, updated_at=3)
    native.items.append(newest)
    client.portal.call(runtime._observe_clarification_event, owner, run['id'], newest)
    path = BASE + '/runs/' + run['id'] + '/clarifications/' + newest['question_id'] + '/answer'
    response = client.post(path, json={'answer': 'Keep current', 'other': False})
    assert response.status_code == 200, response.text
    assert journal.get(owner['id'], run['id'])['status'] == 'running'
    assert runtime.clarifications.list(owner, run['id'])[0]['status'] == 'pending'


@pytest.mark.parametrize('binding', [
    {'run_id': 'foreign-native'}, {'profile': 'foreign-profile'}, {'session_id': 'cli-1'},
])
def test_late_clarification_event_requires_owned_native_binding(
        authenticated_clarification_app, binding):
    app, client, owner, run, native = authenticated_clarification_app
    before = app.state.journal.events(owner['id'], run['id'])
    event = pending(status='expired', **binding)
    client.portal.call(app.state.orchestrator._observe_clarification_event, owner, run['id'], event)
    assert app.state.journal.events(owner['id'], run['id']) == before
    assert app.state.journal.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'


@pytest.mark.parametrize('current_status', ['sending', 'unknown'])
@pytest.mark.parametrize('observe', ['api', 'reconcile'])
def test_running_history_snapshot_cannot_resume_unresolved_current_question(
        authenticated_clarification_app, current_status, observe):
    from test_auth import BASE

    app, client, owner, run, native = authenticated_clarification_app
    runtime, journal = app.state.orchestrator, app.state.journal
    old = pending(created_at=1, updated_at=2, status='expired')
    current = pending('b' * 32, created_at=3, updated_at=3)
    for item in (current, old):
        runtime.clarifications.event(owner, run, item)
    runtime.clarifications.claim(owner, run, current['question_id'],
                                 {'answer': 'Change it', 'other': False})
    if current_status == 'unknown':
        runtime.clarifications.finish(owner, run['id'], current['question_id'], 'unknown')
    native.items = [old]
    if observe == 'api':
        state = client.get(BASE + '/runs/' + run['id'] + '/clarifications').json()
        assert state['status'] == 'waiting_for_clarification'
    else:
        client.portal.call(runtime._reconcile, owner, run['id'])
    assert journal.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'
    assert runtime.clarifications.list(owner, run['id'])[1]['status'] == current_status
    assert all(method == 'GET' for method, _ in native.calls)


def test_equal_question_creation_times_remain_conservative(authenticated_clarification_app):
    app, client, owner, run, native = authenticated_clarification_app
    runtime = app.state.orchestrator
    resolved = pending(created_at=1, updated_at=2, status='expired')
    unresolved = pending('b' * 32, created_at=1, updated_at=1)
    runtime.clarifications.event(owner, run, unresolved)
    client.portal.call(runtime._observe_clarification_event, owner, run['id'], resolved)
    assert app.state.journal.get(owner['id'], run['id'])['status'] == 'waiting_for_clarification'


def pending(question_id='a' * 32, **overrides):
    now = time.time()
    return {
        'question_id': question_id, 'run_id': 'native-run',
        'question': 'Choose a plan?', 'choices': ['Keep current', 'Change it'],
        'multi_select': False, 'status': 'pending', 'answer': None, 'other': None,
        'created_at': now, 'updated_at': now, **overrides,
    }


def run_state(tmp_path):
    journal = RunJournal(tmp_path / 'runs.sqlite')
    run, _ = journal.submit('owner', 'default', 'session', 'Original', 'original')
    journal.set_upstream('owner', run['id'], 'native-run')
    journal.set_active_status('owner', run['id'], 'waiting_for_clarification',
                              upstream_id='native-run')
    return journal, journal.get('owner', run['id'])


def test_waiter_answer_is_idempotent_owned_and_replayed_in_original_position(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    item = pending()
    view = bridge.event(OWNER, run, {**item, 'event': 'run.clarification'})
    assert view['status'] == 'pending'
    body = {'answer': 'Change it', 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh and duplicate['status'] == 'sending'
    bridge.finish(OWNER, run['id'], item['question_id'], 'answered')
    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh and duplicate['status'] == 'answered'
    replay = journal.events('owner', run['id'])
    with closing(journal.connect()) as connection:
        cards = [event for event in journal._replay_events(connection, run['id'])
                 if event['name'] == 'clarification']
    assert len(cards) == 1
    assert cards[0]['data']['status'] == 'answered'
    assert cards[0]['data']['answer'] == 'Change it'
    with pytest.raises(KeyError):
        bridge.list({'id': 'other-owner', 'profile': 'default'}, run['id'])


def test_unicode_multiselect_retry_after_native_answer_event_is_idempotent(tmp_path):
    import json

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    answer = ['Café with accented e', '東京 in Japanese']
    item = pending(choices=choices, multi_select=True)
    bridge.event(OWNER, run, item)
    body = {'answer': answer, 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert fresh and claimed['status'] == 'sending'

    native_answer = {
        **item, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': claimed['updated_at'] + 1,
    }
    observed = bridge.event(OWNER, run, native_answer)
    assert observed['status'] == 'answered'
    legacy_answer = json.dumps(answer)
    assert legacy_answer != bridge._encode_answer(answer)
    with closing(journal.connect()) as connection, connection:
        connection.execute('''UPDATE clarifications SET answer=?
            WHERE user_id=? AND profile=? AND run_id=? AND question_id=?''',
            (legacy_answer, OWNER['id'], OWNER['profile'], run['id'], item['question_id']))

    duplicate, fresh = bridge.claim(OWNER, run, item['question_id'], body)
    assert not fresh
    assert duplicate['status'] == 'answered'
    assert duplicate['answer'] == answer
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, item['question_id'],
                     {'answer': answer, 'other': True})


def test_completed_run_history_keeps_question_and_answer_at_the_event_position():
    from backend.native_catalog import _journal_turn

    run = {'id': 'local-run', 'created_at': 1, 'status': 'completed', 'error': None,
           'input': 'Original', 'output': 'Done'}
    clarification = {
        'question_id': 'b' * 32, 'run_id': 'local-run', 'session_id': 'session',
        'question': 'Choose a plan?', 'choices': ['Keep current', 'Change it'],
        'multi_select': False, 'status': 'answered', 'answer': 'Change it',
        'other': False, 'created_at': 2, 'updated_at': 4,
    }
    items = _journal_turn(run, [
        {'id': 1, 'name': 'commentary', 'data': {'text': 'Before'}, 'observed_at': 1.5},
        {'id': 2, 'name': 'clarification', 'data': clarification, 'observed_at': 2},
        {'id': 3, 'name': 'commentary', 'data': {'text': 'After'}, 'observed_at': 5},
    ])
    cards = [item for item in items if item.get('kind') == 'clarification']
    assert len(cards) == 1
    assert cards[0]['content'] == 'Choose a plan?'
    assert cards[0]['clarification_status'] == 'answered'
    assert cards[0]['clarification_answer'] == 'Change it'
    assert items.index(cards[0]) == 2


def test_stale_question_cannot_be_claimed_after_stop_intent(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    item = pending()
    bridge.event(OWNER, run, item)
    journal.finish('owner', run['id'], 'stopping')
    with pytest.raises(RunConflict, match='stale'):
        bridge.claim(OWNER, run, item['question_id'],
                     {'answer': 'Keep current', 'other': False})


@pytest.mark.parametrize(('record', 'answer'), [
    (pending(multi_select=True), {'answer': ['Keep current', 'Change it'], 'other': False}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Another'], 'other': True}),
    (pending(choices=None), {'answer': 'A free answer', 'other': False}),
])
def test_native_answer_shape_accepts_supported_choices(record, answer):
    assert ClarificationJournal.validate_answer(record, answer)


@pytest.mark.parametrize(('record', 'answer'), [
    (pending(), {'answer': 'Unlisted', 'other': False}),
    (pending(), {'answer': '', 'other': True}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Keep current'], 'other': False}),
    (pending(multi_select=True), {'answer': ['Keep current', 'Unlisted', 'Also unlisted'], 'other': True}),
    (pending(choices=None), {'answer': 'Free answer', 'other': True}),
])
def test_invalid_or_unrecognized_answers_are_rejected(record, answer):
    assert not ClarificationJournal.validate_answer(record, answer)


@pytest.mark.asyncio
async def test_owned_answer_resolves_the_waiting_native_request_without_new_run(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending(multi_select=True)
    native = SimpleNamespace(status='waiting_for_clarification', item=question,
                             answer_gate=asyncio.get_running_loop().create_future(),
                             answer_calls=[])

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': native.status,
                    'clarifications': [native.item]}

        async def answer_clarification(self, run_id, question_id, answer, other):
            assert run_id == 'native-run'
            assert question_id == question['question_id']
            native.answer_calls.append((answer, other))
            native.status = 'running'
            native.item = {**native.item, 'status': 'answered', 'answer': answer,
                           'other': other, 'updated_at': time.time()}
            native.answer_gate.set_result(answer)
            return {'object': 'hermes.run.clarification', 'run_id': run_id,
                    'question_id': question_id, 'status': 'answered', 'answer': answer}

    runtime = Orchestrator(journal, Gateway(), SimpleNamespace(profiles={'default': 'fixture'}))
    async def wait_for_native_answer():
        return await native.answer_gate

    native_waiter = asyncio.create_task(wait_for_native_answer())
    try:
        state = await runtime.clarifications_for_run(OWNER, run['id'])
        assert state['available'] is True
        assert state['run_id'] == run['id']
        assert state['status'] == 'waiting_for_clarification'
        assert state['items'][0]['status'] == 'pending'
        with pytest.raises(ValueError, match='Invalid clarification'):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': ['Keep current', 'Keep current'], 'other': True})
        assert native.answer_calls == []
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == 'pending'
        answer = ['Keep current', 'A new plan']
        answered = await runtime.answer_clarification(
            OWNER, run['id'], question['question_id'],
            {'answer': answer, 'other': True})
        assert answered['status'] == 'answered'
        assert await native_waiter == answer
        assert native.answer_calls == [(answer, True)]
        assert journal.get('owner', run['id'])['status'] == 'running'
        retry = await runtime.answer_clarification(
            OWNER, run['id'], question['question_id'],
            {'answer': answer, 'other': True})
        assert retry['status'] == 'answered'
        assert native.answer_calls == [(answer, True)]
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': ['Change it'], 'other': False})
        with pytest.raises(KeyError):
            await runtime.answer_clarification(
                {**OWNER, 'id': 'other-owner'}, run['id'], question['question_id'],
                {'answer': answer, 'other': True})
        with closing(journal.connect()) as connection:
            assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain_stop', [False, True])
@pytest.mark.parametrize('attempted_answer', [False, True])
@pytest.mark.parametrize('late_terminal,expected', [
    ('cancelled', 'cancelled'), ('completed', 'expired'), ('failed', 'expired'),
])
async def test_stop_fenced_observations_stabilize_across_reconcile_stream_and_reopen(
        tmp_path, uncertain_stop, attempted_answer, late_terminal, expected):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending()
    body = {'answer': 'Change it', 'other': False}

    class Gateway:
        def __init__(self):
            self.item = question
            self.status = 'waiting_for_clarification'
            self.answers = []
            self.stops = []
            self.requests = []

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            self.requests.append((method, path))
            return {'run_id': 'native-run', 'status': self.status,
                    'clarifications': [self.item]}

        async def stop(self, run_id):
            self.stops.append(run_id)
            if uncertain_stop:
                raise IntegrationUnavailable('Synthetic lost Stop acknowledgement')

        async def answer_clarification(self, *args):
            self.answers.append(args)
            raise IntegrationUnavailable('Synthetic lost answer acknowledgement')

        async def start(self, *args, **kwargs):
            raise AssertionError('No new run may be dispatched')

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    try:
        runtime.clarifications.event(OWNER, run, question)
        if attempted_answer:
            attempted = await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'], body)
            assert attempted['status'] == 'unknown'
        stopped = await runtime.stop(OWNER, run['id'])
        assert stopped['status'] == ('unknown' if uncertain_stop else 'stopping')
        saved = runtime.clarifications.list(OWNER, run['id'])[0]
        assert saved['status'] == 'unknown'

        def evidence():
            return (journal.get(OWNER['id'], run['id']),
                    runtime.clarifications.list(OWNER, run['id']),
                    journal.events(OWNER['id'], run['id']))

        baseline = evidence()
        assert sum(event['name'] == 'clarification' for event in baseline[2]) == (
            3 if attempted_answer else 2)
        assert sum(event['name'] == 'done' for event in baseline[2]) == int(uncertain_stop)
        for timestamp in (question['updated_at'], time.time() + 1, time.time() + 2):
            gateway.item = {**question, 'updated_at': timestamp}
            await runtime._reconcile(OWNER, run['id'])
            await runtime._observe_clarification_event(OWNER, run['id'], gateway.item)
            await runtime.clarifications_for_run(OWNER, run['id'])
            assert evidence() == baseline
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'], body)
        await runtime.close()

        journal = RunJournal(tmp_path / 'runs.sqlite')
        journal.recover()
        restarted = journal.get(OWNER['id'], run['id'])
        assert restarted['status'] == 'unknown'
        runtime = Orchestrator(
            journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
        baseline = evidence()
        for _ in range(3):
            await runtime._reconcile(OWNER, run['id'])
            await runtime._observe_clarification_event(OWNER, run['id'], gateway.item)
            await runtime.clarifications_for_run(OWNER, run['id'])
            assert evidence() == baseline
        with closing(journal.connect()) as connection:
            cards = [event for event in journal._replay_events(connection, run['id'])
                     if event['name'] == 'clarification']
        assert len(cards) == 1
        assert cards[0]['data']['status'] == 'unknown'

        if attempted_answer:
            gateway.item = {**question, 'status': 'answered', **body,
                            'updated_at': question['updated_at']}
            await runtime._reconcile(OWNER, run['id'])
            receipt = runtime.clarifications.list(OWNER, run['id'])[0]
            assert receipt['status'] == 'answered'
            assert receipt['answer'] == body['answer']
            assert receipt['other'] is False
            assert receipt['updated_at'] > saved['updated_at']
            assert journal.get(OWNER['id'], run['id'])['status'] == 'unknown'
            duplicate = await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'], body)
            assert duplicate['status'] == 'answered'
        gateway.status = late_terminal
        gateway.item = {**question, 'status': expected, 'updated_at': time.time() + 3}
        await runtime._reconcile(OWNER, run['id'])
        assert journal.get(OWNER['id'], run['id'])['status'] == late_terminal
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == (
            'answered' if attempted_answer else expected)
        terminal = evidence()
        assert sum(event['name'] == 'clarification' for event in terminal[2]) == (
            4 if attempted_answer else 3)
        assert sum(event['name'] == 'done' for event in terminal[2]) == int(uncertain_stop) + 1
        await runtime._reconcile(OWNER, run['id'])
        await runtime._observe_clarification_event(OWNER, run['id'], question)
        assert evidence() == terminal
        assert gateway.answers == (
            [('native-run', question['question_id'], body['answer'], False)]
            if attempted_answer else [])
        assert gateway.stops == ['native-run']
        assert all(method == 'GET' for method, _ in gateway.requests)
        assert not [event for event in terminal[2] if event['name'] == 'tool']
        with closing(journal.connect()) as connection:
            assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize('terminal,expected', [
    ('stopping', 'unknown'), ('unknown', 'unknown'),
    ('cancelled', 'cancelled'), ('completed', 'expired'), ('failed', 'expired'),
])
def test_fenced_pending_journal_observations_are_semantically_idempotent(
        tmp_path, terminal, expected):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    journal.finish(OWNER['id'], run['id'], 'stopping')
    journal.finish(OWNER['id'], run['id'], terminal)
    snapshot = {'run_id': 'native-run', 'clarifications': [question]}
    assert bridge.observe(OWNER, run, snapshot) == set()
    baseline = journal.events(OWNER['id'], run['id'])
    saved = bridge.list(OWNER, run['id'])[0]
    assert saved['status'] == expected
    for store in (bridge, ClarificationJournal(RunJournal(tmp_path / 'runs.sqlite'))):
        for timestamp in (question['updated_at'], time.time() + 1, time.time() + 2):
            item = {**question, 'updated_at': timestamp}
            assert store.observe(OWNER, run, {**snapshot, 'clarifications': [item]}) == set()
            assert store.event(OWNER, run, item) == saved
            assert journal.events(OWNER['id'], run['id']) == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain_stop', [False, True])
@pytest.mark.parametrize('stored_pending', [False, True])
async def test_stop_fences_delayed_pending_snapshot_and_event(
        tmp_path, uncertain_stop, stored_pending):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending()
    other_run, _ = journal.submit('owner', 'default', 'other-session', 'Other', 'other')
    journal.set_upstream('owner', other_run['id'], 'other-native')
    journal.set_active_status('owner', other_run['id'], 'waiting_for_clarification',
                              upstream_id='other-native')
    other_question = pending('b' * 32, run_id='other-native')

    class Gateway:
        def __init__(self):
            self.get_started = asyncio.Event()
            self.release_get = asyncio.Event()
            self.requests = []
            self.stops = []
            self.answers = []
            self.starts = []

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            self.requests.append((method, path))
            if path.endswith('other-native'):
                return {'run_id': 'other-native', 'status': 'waiting_for_clarification',
                        'clarifications': [other_question]}
            snapshot = {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                        'clarifications': [question]}
            self.get_started.set()
            await self.release_get.wait()
            return snapshot

        async def stop(self, run_id):
            self.stops.append(run_id)
            if uncertain_stop:
                raise IntegrationUnavailable('Synthetic Stop acknowledgement loss')

        async def answer_clarification(self, *args):
            self.answers.append(args)
            return {'status': 'answered'}

        async def start(self, *args, **kwargs):
            self.starts.append(args)
            raise AssertionError('No new run may be dispatched')

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    if stored_pending:
        runtime.clarifications.event(OWNER, run, question)
    snapshot = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
    try:
        await asyncio.wait_for(gateway.get_started.wait(), 1)
        stopped = await asyncio.wait_for(runtime.stop(OWNER, run['id']), 1)
        assert stopped['status'] == ('unknown' if uncertain_stop else 'stopping')
        with closing(journal.connect()) as connection:
            assert connection.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?',
                                      (run['id'],)).fetchone()
        if stored_pending:
            assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == 'unknown'
        other = await asyncio.wait_for(
            runtime.clarifications_for_run(OWNER, other_run['id']), 1)
        assert other['items'][0]['status'] == 'pending'
        with pytest.raises(KeyError):
            await runtime.clarifications_for_run(
                {**OWNER, 'id': 'foreign'}, run['id'])
        with pytest.raises(KeyError):
            await runtime.clarifications_for_run(
                {**OWNER, 'profile': 'foreign'}, run['id'])
        gateway.release_get.set()
        assembled = await asyncio.wait_for(snapshot, 1)
        assert assembled['status'] == stopped['status']
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': 'Change it', 'other': False})
        assert gateway.answers == []
        assert gateway.starts == []
        assert gateway.stops == ['native-run']
        assert assembled['items'][0]['status'] == 'unknown'
        assert assembled['items'][0]['answer'] is None

        await runtime._observe_clarification_event(
            OWNER, run['id'], {**question, 'updated_at': time.time() + 1})
        assert runtime.get(OWNER, run['id'])['status'] == stopped['status']
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == 'unknown'
        reopened = await runtime.clarifications_for_run(OWNER, run['id'])
        assert reopened['items'][0]['status'] == 'unknown'
        with closing(journal.connect()) as connection:
            cards = [event for event in journal._replay_events(connection, run['id'])
                     if event['name'] == 'clarification']
        assert len(cards) == 1
        assert cards[0]['data']['status'] == 'unknown'
        late_question = pending('c' * 32)
        await runtime._observe_clarification_event(OWNER, run['id'], late_question)
        assert [item['status'] for item in runtime.clarifications.list(OWNER, run['id'])] == [
            'unknown', 'unknown']
        assert runtime.get(OWNER, run['id'])['status'] == stopped['status']
        assert runtime.clarifications.list(OWNER, other_run['id'])[0]['status'] == 'pending'
        recovered = Orchestrator(
            journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
        try:
            restored = await recovered.clarifications_for_run(OWNER, run['id'])
            assert restored['status'] == stopped['status']
            assert [item['status'] for item in restored['items']] == ['unknown', 'unknown']
            other_restored = await recovered.clarifications_for_run(OWNER, other_run['id'])
            assert other_restored['items'][0]['status'] == 'pending'
        finally:
            await recovered.close()
        assert runtime.clarifications.list(OWNER, other_run['id'])[0]['status'] == 'pending'
        assert all(method == 'GET' for method, _ in gateway.requests)
        assert not [event for event in journal.events('owner', run['id'])
                    if event['name'] == 'tool']
    finally:
        gateway.release_get.set()
        await asyncio.gather(snapshot, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('ack_lost', [False, True])
async def test_stop_during_slow_answer_preserves_attempt_and_later_native_receipt(tmp_path, ack_lost):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending()
    body = {'answer': 'Change it', 'other': False}

    class Gateway:
        def __init__(self):
            self.answer_started = asyncio.Event()
            self.release_answer = asyncio.Event()
            self.stop_started = asyncio.Event()
            self.release_stop = asyncio.Event()
            self.answers = []

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def answer_clarification(self, *args):
            self.answers.append(args)
            self.answer_started.set()
            await self.release_answer.wait()
            if ack_lost:
                raise IntegrationUnavailable('Synthetic answer acknowledgement loss')
            return {'status': 'answered'}

        async def stop(self, run_id):
            assert run_id == 'native-run'
            self.stop_started.set()
            await self.release_stop.wait()
            raise IntegrationUnavailable('Synthetic Stop acknowledgement loss')

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    runtime.clarifications.event(OWNER, run, question)
    answer_task = asyncio.create_task(runtime.answer_clarification(
        OWNER, run['id'], question['question_id'], body))
    stop_task = None
    try:
        await asyncio.wait_for(gateway.answer_started.wait(), 1)
        stop_task = asyncio.create_task(runtime.stop(OWNER, run['id']))
        await asyncio.wait_for(gateway.stop_started.wait(), 1)
        assert runtime.get(OWNER, run['id'])['status'] == 'stopping'
        attempted = runtime.clarifications.list(OWNER, run['id'])[0]
        assert attempted['status'] == 'sending'
        assert attempted['answer'] == body['answer']
        assert attempted['other'] is False
        with closing(journal.connect()) as connection:
            assert connection.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?',
                                      (run['id'],)).fetchone()
        gateway.release_stop.set()
        assert (await asyncio.wait_for(stop_task, 1))['status'] == 'unknown'
        gateway.release_answer.set()
        receipt = await asyncio.wait_for(answer_task, 1)
        assert receipt['status'] == ('unknown' if ack_lost else 'answered')
        assert receipt['answer'] == body['answer']
        assert runtime.get(OWNER, run['id'])['status'] == 'unknown'
        await runtime._observe_clarification_event(OWNER, run['id'], question)
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == receipt['status']

        confirmed = {**question, 'status': 'answered', **body,
                     'updated_at': time.time() + 1}
        await runtime._observe_clarification_event(
            OWNER, run['id'], {**confirmed, 'run_id': 'foreign-native'})
        assert runtime.clarifications.list(OWNER, run['id'])[0]['status'] == receipt['status']
        await runtime._observe_clarification_event(OWNER, run['id'], confirmed)
        saved = runtime.clarifications.list(OWNER, run['id'])[0]
        assert saved['status'] == 'answered'
        assert saved['answer'] == body['answer']
        assert saved['updated_at'] > attempted['updated_at']
        await runtime._observe_clarification_event(
            OWNER, run['id'], {**confirmed, 'answer': 'Keep current',
                              'updated_at': confirmed['updated_at'] + 1})
        await runtime._observe_clarification_event(
            OWNER, run['id'], {**question, 'updated_at': confirmed['updated_at'] + 2})
        assert runtime.clarifications.list(OWNER, run['id'])[0] == saved
        duplicate = await runtime.answer_clarification(
            OWNER, run['id'], question['question_id'], body)
        assert duplicate['status'] == 'answered'
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': 'Keep current', 'other': False})
        assert gateway.answers == [('native-run', question['question_id'], 'Change it', False)]
        assert runtime.get(OWNER, run['id'])['status'] == 'unknown'
    finally:
        gateway.release_answer.set()
        gateway.release_stop.set()
        await asyncio.gather(answer_task, *([stop_task] if stop_task else []),
                             return_exceptions=True)
        await runtime.close()


@pytest.mark.parametrize('terminal,expected', [
    ('cancelled', 'cancelled'), ('completed', 'expired'), ('failed', 'expired'),
])
def test_delayed_pending_event_respects_known_terminal_mapping(tmp_path, terminal, expected):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    journal.finish(OWNER['id'], run['id'], terminal)
    saved = bridge.event(OWNER, run, question)
    assert saved['status'] == expected
    assert saved['answer'] is None
    assert journal.get(OWNER['id'], run['id'])['status'] == terminal
    assert bridge.list(OWNER, run['id'])[0]['status'] == expected


@pytest.mark.asyncio
async def test_stale_run_snapshot_preserves_question_published_by_native_stream(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending('b' * 32)

    class Gateway:
        def __init__(self):
            self.event_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.event_delivered = asyncio.Event()
            self.get_started = asyncio.Event()
            self.release_get = asyncio.Event()

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def start(self, *args, **kwargs):
            self.started.set()
            return {'run_id': 'native-run'}

        async def events(self, run_id):
            while True:
                event = await self.event_queue.get()
                self.event_delivered.set()
                yield event

        async def request(self, method, path, **kwargs):
            self.get_started.set()
            await self.release_get.wait()
            return {'run_id': 'native-run', 'status': 'running', 'clarifications': []}

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    stream = asyncio.create_task(runtime._stream(OWNER, run, []))
    try:
        await gateway.started.wait()
        journal.finish('owner', run['id'], 'unknown')
        snapshot = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
        await gateway.get_started.wait()
        await gateway.event_queue.put({'event': 'run.clarification', **question})
        await gateway.event_delivered.wait()
        await asyncio.sleep(0)
        gateway.release_get.set()

        await snapshot
        await asyncio.sleep(0.01)
        assert [(item['question_id'], item['status'])
                for item in runtime.clarifications.list(OWNER, run['id'])] == [
                    (question['question_id'], 'pending')]
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'

        saved = runtime.clarifications.list(OWNER, run['id'])
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        assert [(item['question_id'], item['status']) for item in saved] == [
            (question['question_id'], 'pending')]
        assert ClarificationJournal.validate_answer(
            saved[0], {'answer': 'Change it', 'other': False})
    finally:
        gateway.release_get.set()
        await gateway.event_queue.put({
            'event': 'run.completed', 'run_id': 'native-run', 'output': 'continued'})
        await asyncio.gather(stream, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_late_answer_ack_preserves_next_question_and_same_run_continuation(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    first = pending('c' * 32)
    next_question = pending('d' * 32, question='Choose the next step?')

    class Gateway:
        def __init__(self):
            self.event_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.event_delivered = asyncio.Event()
            self.answer_started = asyncio.Event()
            self.release_answer = asyncio.Event()
            self.starts = []
            self.answers = []

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def start(self, *args, **kwargs):
            self.starts.append(args)
            self.started.set()
            return {'run_id': 'native-run'}

        async def events(self, run_id):
            while True:
                event = await self.event_queue.get()
                self.event_delivered.set()
                yield event

        async def answer_clarification(self, run_id, question_id, answer, other):
            self.answers.append((run_id, question_id, answer, other))
            if question_id == first['question_id']:
                self.answer_started.set()
                await self.release_answer.wait()
            return {'status': 'answered'}

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    stream = asyncio.create_task(runtime._stream(OWNER, run, []))
    try:
        await gateway.started.wait()
        runtime.clarifications.event(OWNER, runtime.get(OWNER, run['id']), first)
        journal.set_active_status('owner', run['id'], 'waiting_for_clarification',
                                  upstream_id='native-run')
        answer_task = asyncio.create_task(runtime.answer_clarification(
            OWNER, run['id'], first['question_id'],
            {'answer': 'Change it', 'other': False}))
        await gateway.answer_started.wait()
        await gateway.event_queue.put({'event': 'run.clarification', **next_question})
        await gateway.event_delivered.wait()
        await asyncio.sleep(0)
        gateway.release_answer.set()
        answered = await answer_task

        await asyncio.sleep(0.01)
        assert [(item['question_id'], item['status'], item['answer'])
                for item in runtime.clarifications.list(OWNER, run['id'])] == [
                    (first['question_id'], 'answered', 'Change it'),
                    (next_question['question_id'], 'pending', None),
                ]
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'

        saved = runtime.clarifications.list(OWNER, run['id'])
        assert answered['status'] == 'answered'
        assert journal.get('owner', run['id'])['status'] == 'waiting_for_clarification'
        answerable = await runtime.answer_clarification(
            OWNER, run['id'], next_question['question_id'],
            {'answer': 'Keep current', 'other': False})
        assert answerable['status'] == 'answered'
        assert len(gateway.starts) == 1
        assert gateway.answers == [
            ('native-run', first['question_id'], 'Change it', False),
            ('native-run', next_question['question_id'], 'Keep current', False),
        ]
        retry = await runtime.answer_clarification(
            OWNER, run['id'], first['question_id'],
            {'answer': 'Change it', 'other': False})
        assert retry['status'] == 'answered'
        assert len(gateway.answers) == 2
        with pytest.raises(RunConflict):
            await runtime.answer_clarification(
                OWNER, run['id'], first['question_id'],
                {'answer': 'Keep current', 'other': False})
    finally:
        gateway.release_answer.set()
        await gateway.event_queue.put({
            'event': 'run.completed', 'run_id': 'native-run', 'output': 'continued'})
        await asyncio.gather(stream, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_stale_waiting_snapshot_cannot_reopen_a_terminal_run(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)
    question = pending('e' * 32)

    class Gateway:
        def __init__(self):
            self.event_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.event_delivered = asyncio.Event()
            self.get_started = asyncio.Event()
            self.release_get = asyncio.Event()

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def start(self, *args, **kwargs):
            self.started.set()
            return {'run_id': 'native-run'}

        async def events(self, run_id):
            while True:
                event = await self.event_queue.get()
                self.event_delivered.set()
                yield event

        async def request(self, method, path, **kwargs):
            self.get_started.set()
            await self.release_get.wait()
            return {
                'run_id': 'native-run', 'status': 'waiting_for_clarification',
                'clarifications': [question],
            }

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    stream = asyncio.create_task(runtime._stream(OWNER, run, []))
    try:
        await gateway.started.wait()
        runtime.clarifications.event(OWNER, runtime.get(OWNER, run['id']), question)
        journal.set_active_status('owner', run['id'], 'waiting_for_clarification',
                                  upstream_id='native-run')
        snapshot = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
        await gateway.get_started.wait()
        await gateway.event_queue.put({
            'event': 'run.completed', 'run_id': 'native-run', 'output': 'finished'})
        await gateway.event_delivered.wait()
        await asyncio.sleep(0)
        gateway.release_get.set()

        await snapshot
        await asyncio.wait_for(stream, 1)
        assert journal.get('owner', run['id'])['status'] == 'completed'
        with pytest.raises(RunConflict, match='stale'):
            await runtime.answer_clarification(
                OWNER, run['id'], question['question_id'],
                {'answer': 'Change it', 'other': False})
    finally:
        gateway.release_get.set()
        if not stream.done():
            await gateway.event_queue.put({
                'event': 'run.completed', 'run_id': 'native-run', 'output': 'finished'})
        await asyncio.gather(stream, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_overlapping_clarification_snapshots_are_serialized_per_owned_run(tmp_path):
    from backend.orchestration import Orchestrator

    journal, run = run_state(tmp_path)

    class Gateway:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.requests = 0
            self.active = 0
            self.max_active = 0

        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            self.requests += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.requests == 1:
                self.started.set()
                await self.release.wait()
            try:
                return {'run_id': 'native-run', 'status': 'running', 'clarifications': []}
            finally:
                self.active -= 1

    gateway = Gateway()
    runtime = Orchestrator(journal, gateway, SimpleNamespace(profiles={'default': 'fixture'}))
    first = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
    second = None
    try:
        await gateway.started.wait()
        second = asyncio.create_task(runtime.clarifications_for_run(OWNER, run['id']))
        await asyncio.sleep(0.01)
        assert gateway.requests == 1
        gateway.release.set()
        await asyncio.gather(first, second)
        assert gateway.requests == 2
        assert gateway.max_active == 1
        assert journal.get('owner', run['id'])['status'] == 'running'
    finally:
        gateway.release.set()
        await asyncio.gather(first, *(task for task in (second,) if task is not None),
                             return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_restores_only_verified_unanswered_waiters(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    bridge.recover()
    recovered = bridge.list(OWNER, run['id'])[0]
    assert recovered['status'] == 'unknown'
    assert recovered['updated_at'] >= question['updated_at']

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [question]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    restored = state['items'][0]
    assert state['available'] is True
    assert restored['status'] == 'pending'
    assert restored['answer'] is None
    assert restored['updated_at'] >= recovered['updated_at']
    replay = journal.events('owner', run['id'])
    assert replay[-1]['data']['status'] == 'pending'
    assert replay[-1]['data']['updated_at'] == restored['updated_at']


@pytest.mark.asyncio
async def test_rehydrate_does_not_keep_changed_waiter_payload_pending(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            changed = {**question, 'question': 'Changed question'}
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [changed]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is True
    assert state['items'][0]['status'] == 'unknown'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'unknown'


def test_recovery_transition_is_reflected_in_event_replay(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())
    bridge.recover()
    assert bridge.list(OWNER, run['id'])[0]['status'] == 'unknown'
    replay = journal.events('owner', run['id'])
    assert replay[-1]['data']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_transient_rehydrate_failure_preserves_live_pending_question(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            raise RuntimeError('synthetic transport unavailable')

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is False
    assert state['items'][0]['status'] == 'pending'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'pending'


@pytest.mark.asyncio
async def test_valid_native_snapshot_marks_missing_waiter_unknown(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    bridge.event(OWNER, run, pending())

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'running', 'clarifications': []}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['available'] is True
    assert state['items'][0]['status'] == 'unknown'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_uncertain_answer_is_not_restored_from_pending_native_snapshot(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)
    body = {'answer': 'Change it', 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]
    assert attempted['status'] == 'unknown'
    assert attempted['answer'] == 'Change it'

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'waiting_for_clarification',
                    'clarifications': [question]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    assert state['items'][0]['status'] == 'unknown'
    assert state['items'][0]['answer'] == 'Change it'


@pytest.mark.asyncio
async def test_native_confirmation_promotes_recovered_unicode_answer_monotonically(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    answer = ['Café with accented e', '東京 in Japanese']
    question = pending(choices=choices, multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    body = {'answer': answer, 'other': False}
    claimed, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert fresh and claimed['status'] == 'sending'
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]
    assert attempted['status'] == 'unknown'

    native_answer = {
        **question, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': 25,
    }

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            assert method == 'GET'
            assert path == '/v1/runs/native-run'
            return {'run_id': 'native-run', 'status': 'running',
                    'clarifications': [native_answer]}

    from backend.orchestration import Orchestrator
    runtime = Orchestrator(
        journal, Gateway(), SimpleNamespace(profiles={'default': 'fixture'}))
    try:
        state = await runtime.clarifications_for_run(OWNER, run['id'])
    finally:
        await runtime.close()
    confirmed = state['items'][0]
    assert state['available'] is True
    assert state['run_id'] == run['id']
    assert state['status'] == 'running'
    assert confirmed['status'] == 'answered'
    assert confirmed['answer'] == answer
    assert confirmed['other'] is False
    assert confirmed['updated_at'] >= attempted['updated_at']
    assert journal.get('owner', run['id'])['status'] == 'running'
    duplicate, fresh = bridge.claim(OWNER, run, question['question_id'], body)
    assert not fresh
    assert duplicate['status'] == 'answered'
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, question['question_id'],
                     {'answer': ['Café with accented e', 'Another answer'], 'other': False})
    with pytest.raises(RunConflict):
        bridge.claim(OWNER, run, question['question_id'],
                     {'answer': answer, 'other': True})


@pytest.mark.asyncio
@pytest.mark.parametrize(('answer', 'other'), [
    (['Café with accented e', 'A different answer'], False),
    (['Café with accented e', '東京 in Japanese'], True),
])
async def test_conflicting_native_answer_does_not_replace_recovered_attempt(
        tmp_path, answer, other):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    choices = ['Cafe with accented e', 'Tokyo in Japanese']
    attempted_answer = ['Café with accented e', '東京 in Japanese']
    question = pending(choices=choices, multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    bridge.claim(OWNER, run, question['question_id'],
                 {'answer': attempted_answer, 'other': False})
    bridge.recover()
    attempted = bridge.list(OWNER, run['id'])[0]

    class Gateway:
        def require_execution(self):
            return None

        async def require_clarifications(self):
            return None

        async def request(self, method, path, **kwargs):
            return {'run_id': 'native-run', 'status': 'running',
                    'clarifications': [{
                        **question, 'status': 'answered', 'answer': answer,
                        'other': other, 'updated_at': 25,
                    }]}

    state = await bridge.rehydrate(OWNER, run['id'], Gateway(), run)
    retained = state['items'][0]
    assert retained['status'] == 'unknown'
    assert retained['answer'] == attempted_answer
    assert retained['other'] is False
    assert retained['updated_at'] == attempted['updated_at']


def test_recovered_confirmation_cannot_cross_owner_profile_run_or_question(tmp_path):
    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    answer = ['Café with accented e', '東京 in Japanese']
    question = pending(
        choices=['Cafe with accented e', 'Tokyo in Japanese'],
        multi_select=True, created_at=20, updated_at=20)
    bridge.event(OWNER, run, question)
    bridge.claim(OWNER, run, question['question_id'], {'answer': answer, 'other': False})
    bridge.recover()
    confirmed = bridge._normalize({
        **question, 'status': 'answered', 'answer': answer, 'other': False,
        'updated_at': 25,
    }, 'native-run')

    assert bridge._save({**OWNER, 'profile': 'other'}, run, confirmed) is None
    assert bridge._save(OWNER, run, {**confirmed, 'upstream_id': 'different-upstream'}) is None
    assert bridge._save(
        OWNER, {**run, 'upstream_id': 'different-upstream'}, confirmed) is None
    assert bridge._save(OWNER, run, {**confirmed, 'question': 'Different question'}) is None
    with pytest.raises(KeyError):
        bridge._save({**OWNER, 'id': 'other-owner'}, run, confirmed)
    with pytest.raises(KeyError):
        bridge._save(OWNER, {**run, 'id': 'other-run'}, confirmed)

    retained = bridge.list(OWNER, run['id'])[0]
    assert retained['status'] == 'unknown'
    assert retained['answer'] == answer
    assert retained['other'] is False


@pytest.mark.asyncio
async def test_real_gateway_run_not_found_retires_waiters_durably_without_resubmission(tmp_path):
    import httpx
    from backend.hermes_client import GatewayClient

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    unanswered = pending('a' * 32)
    attempted = pending('b' * 32)
    bridge.event(OWNER, run, unanswered)
    bridge.event(OWNER, run, attempted)
    bridge.claim(OWNER, run, attempted['question_id'],
                 {'answer': 'Change it', 'other': False})
    calls = []

    async def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={
                'mobile_run_controls': {'version': 1, 'clarifications': True}})
        assert request.url.path == '/v1/runs/native-run'
        return httpx.Response(404, json={'detail': 'synthetic missing run'})

    gateway = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        state = await bridge.rehydrate(OWNER, run['id'], gateway, run)
    finally:
        await gateway.close()

    reopened = RunJournal(journal.path)
    recovered = ClarificationJournal(reopened).list(OWNER, run['id'])
    by_id = {item['question_id']: item for item in recovered}
    assert state['available'] is False
    assert by_id[unanswered['question_id']]['status'] == 'unknown'
    assert by_id[attempted['question_id']]['status'] == 'unknown'
    assert by_id[attempted['question_id']]['answer'] == 'Change it'
    with closing(reopened.connect()) as connection:
        replayed = [event for event in reopened._replay_events(connection, run['id'])
                    if event['name'] == 'clarification']
    assert {event['data']['status'] for event in replayed} == {'unknown'}
    assert calls == [
        ('GET', '/v1/capabilities'),
        ('GET', '/v1/runs/native-run'),
    ]
    with pytest.raises(RunConflict):
        ClarificationJournal(reopened).claim(
            OWNER, reopened.get('owner', run['id']), unanswered['question_id'],
            {'answer': 'Keep current', 'other': False})
    with pytest.raises(RunConflict):
        ClarificationJournal(reopened).claim(
            OWNER, reopened.get('owner', run['id']), attempted['question_id'],
            {'answer': 'Change it', 'other': False})


@pytest.mark.asyncio
async def test_real_gateway_transient_run_failure_keeps_waiter_pending(tmp_path):
    import httpx
    from backend.hermes_client import GatewayClient

    journal, run = run_state(tmp_path)
    bridge = ClarificationJournal(journal)
    question = pending()
    bridge.event(OWNER, run, question)

    async def handle(request):
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={
                'mobile_run_controls': {'version': 1, 'clarifications': True}})
        return httpx.Response(503, json={'detail': 'synthetic unavailable'})

    gateway = GatewayClient(
        'http://127.0.0.1:8642', 'synthetic-token', execution_ready=True,
        transport=httpx.MockTransport(handle))
    try:
        state = await bridge.rehydrate(OWNER, run['id'], gateway, run)
    finally:
        await gateway.close()
    assert state['available'] is False
    assert state['items'][0]['status'] == 'pending'
    assert journal.events('owner', run['id'])[-1]['data']['status'] == 'pending'
