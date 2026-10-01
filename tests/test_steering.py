"""Real router/auth/SQLite; only native HTTP is isolated via MockTransport."""
import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.hermes_client import GatewayClient
from test_auth import BASE, ORIGIN, BOOTSTRAP, enroll
from test_native_catalog import create_native_db

BODY = {'input': 'Use the smaller example', 'idempotency_key': 'steer-key'}
CAP = {'mobile_run_controls': {'version': 1, 'steering': True, 'live_commentary': True}}
ACK = {'object':'hermes.run.steer', 'run_id': 'native-run', 'idempotency_key': 'steer-key', 'steer_id': 'steer-key',
       'accepted': True, 'status': 'accepted_unconfirmed'}


@contextmanager
def environment(tmp_path, handler=None):
    calls = []
    async def upstream(request):
        calls.append(request)
        if handler:
            value = handler(request)
            if hasattr(value, '__await__'):
                value = await value
            if value is not None:
                return value
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json=CAP)
        if request.url.path.endswith('/steer'):
            return httpx.Response(200, json=ACK)
        return httpx.Response(200, json={'run_id': 'native-run', 'status': 'running'})
    gateway = GatewayClient('http://127.0.0.1:8642', 'test-token', True,
                            transport=httpx.MockTransport(upstream))
    home = tmp_path/'home'; home.mkdir(); create_native_db(home/'state.db')
    app = create_app(Settings(state_dir=tmp_path/'state', profiles={'default': home},
                              bootstrap_secret=BOOTSTRAP), gateway_client=gateway)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        enroll(client)
        user = client.get(BASE+'/auth/me').json()['user']
        run, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'original', 'start-key')
        app.state.journal.set_upstream(user['id'], run['id'], 'native-run')
        yield app, client, user, run['id'], calls


@pytest.mark.parametrize('commentary', [None, False, 1, 'true'])
def test_incomplete_commentary_capability_is_rejected(tmp_path, commentary):
    cap = {'mobile_run_controls': {'version': 1, 'steering': True}}
    if commentary is not None:
        cap['mobile_run_controls']['live_commentary'] = commentary
    def handler(request):
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json=cap)
    with environment(tmp_path, handler) as (_, client, _, rid, calls):
        assert client.get(BASE+f'/runs/{rid}/controls').json()['steering'] is False
        assert client.post(BASE+f'/runs/{rid}/steer', json=BODY).status_code == 503
        assert all(r.method == 'GET' for r in calls)


@pytest.mark.parametrize('native', [
    {'run_id': 'native-run', 'status': 'completed'},
    {'run_id': 'native-run', 'status': 'waiting_for_approval'},
    {'run_id': 'native-run', 'status': 'stopping'},
    {'run_id': 'foreign', 'status': 'running'}, {},
])
def test_first_controls_read_requires_current_owned_running_native(tmp_path, native):
    def handler(request):
        if request.url.path == '/v1/runs/native-run':
            return httpx.Response(200, json=native)
    with environment(tmp_path, handler) as (_, client, _, rid, calls):
        assert client.get(BASE+f'/runs/{rid}/controls').json()['steering'] is False
        assert any(r.url.path == '/v1/runs/native-run' for r in calls)
        assert all(r.method == 'GET' for r in calls)


@pytest.mark.parametrize('state', ['waiting_for_approval', 'stopping'])
def test_controls_native_await_preserves_local_fence(tmp_path, state):
    holder = {}
    def handler(request):
        if request.url.path == '/v1/runs/native-run':
            app, user, rid = holder['binding']
            app.state.journal.finish(user['id'], rid, state)
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        holder['binding'] = app, user, rid
        assert client.get(BASE+f'/runs/{rid}/controls').json()['steering'] is False
        assert app.state.journal.get(user['id'], rid)['status'] == state


@pytest.mark.parametrize('identity', [None, 'FOREIGN'])
def test_unbound_approval_sse_never_creates_intent(tmp_path, identity):
    event = {'request_id': 'approval-1', 'tool': 'terminal', 'command': 'private'}
    if identity is not None:
        event['run_id'] = identity
    def handler(request):
        if request.url.path == '/v1/runs':
            return httpx.Response(200, json={'run_id': 'native-run'})
        if request.url.path.endswith('/events'):
            text = 'event: approval.request\ndata: '+json.dumps(event)+'\n\n'
            text += 'event: run.completed\ndata: {"run_id":"native-run"}\n\n'
            return httpx.Response(200, text=text, headers={'content-type': 'text/event-stream'})
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        client.portal.call(app.state.orchestrator._stream, user, app.state.journal.get(user['id'], rid), [])
        with app.state.journal.connect() as db:
            assert db.execute('SELECT count(*) FROM orchestration_approvals').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0] == 0
        assert not any(e['name'] == 'approval' for e in app.state.journal.events(user['id'], rid))


def test_owned_running_steer_is_durable_acceptance_not_delivery(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, calls):
        response = client.post(BASE+f'/runs/{rid}/steer', json=BODY)
        assert response.status_code == 200, response.text
        attempt = response.json()
        assert attempt['status'] == 'accepted_unconfirmed'
        assert attempt['run_id'] == rid and attempt['input'] == BODY['input']
        assert attempt['steer_id'] == 'steer-key'
        controls = client.get(BASE+f'/runs/{rid}/controls').json()
        assert controls['steering'] is True and controls['attempts'] == [attempt]
        assert app.state.journal.get(user['id'], rid)['status'] == 'running'
        posts = [r for r in calls if r.method == 'POST']
        assert len(posts) == 1 and json.loads(posts[0].content) == BODY
        assert posts[0].url.path == '/v1/runs/native-run/steer'
        assert any(e['name'] == 'steering' for e in app.state.journal.events(user['id'], rid))


def test_duplicate_attempt_returns_saved_result_and_conflicts_changed_binding(tmp_path):
    with environment(tmp_path) as (app, client, user, rid, calls):
        first = client.post(BASE+f'/runs/{rid}/steer', json=BODY).json()
        app.state.journal.finish(user['id'], rid, 'completed')
        before = len(calls)
        again = client.post(BASE+f'/runs/{rid}/steer', json=BODY)
        assert again.status_code == 200 and again.json() == first
        assert len(calls) == before
        changed = client.post(BASE+f'/runs/{rid}/steer', json=dict(BODY, input='different'))
        assert changed.status_code == 409
        another, _ = app.state.journal.submit(user['id'], 'default', 'wa-1', 'next', 'next-key')
        app.state.journal.set_upstream(user['id'], another['id'], 'different-native-run')
        changed = client.post(BASE+f'/runs/{another["id"]}/steer', json=BODY)
        assert changed.status_code == 409
        assert len(calls) == before


def test_concurrent_duplicates_claim_once_before_native_io(tmp_path):
    async def handler(request):
        if request.url.path.endswith('/steer'):
            await asyncio.sleep(.03)
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        async def race():
            runtime = app.state.orchestrator
            return await asyncio.gather(*(runtime.steer(user, rid, BODY) for _ in range(6)))
        results = client.portal.call(race)
        assert len({r['id'] for r in results}) == 1
        assert len([r for r in calls if r.method == 'POST']) == 1


@pytest.mark.parametrize('state', ['queued', 'waiting_for_approval', 'stopping', 'unknown', 'completed', 'failed', 'cancelled', 'missing-upstream'])
def test_ineligible_runs_reject_before_any_native_io(tmp_path, state):
    with environment(tmp_path) as (app, client, user, rid, calls):
        with app.state.journal.connect() as db:
            if state == 'missing-upstream':
                db.execute('UPDATE runs SET upstream_id=NULL WHERE id=?', (rid,))
            else:
                db.execute('UPDATE runs SET status=? WHERE id=?', (state, rid))
        response = client.post(BASE+f'/runs/{rid}/steer', json=BODY)
        assert response.status_code == 409, response.text
        assert client.get(BASE+f'/runs/{rid}/controls').json() == {'steering': False, 'attempts': []}
        assert not calls


@pytest.mark.parametrize('body', [dict(BODY, profile='other'), dict(BODY, run_id='native'), dict(BODY, input=' '), dict(BODY, input=1), dict(BODY, input='x'*32001), dict(BODY, idempotency_key=''), dict(BODY, idempotency_key=' '*2), dict(BODY, idempotency_key='x'*129), [], {}])
def test_strict_steer_input_is_rejected_before_native_io(tmp_path, body):
    with environment(tmp_path) as (_, client, _, rid, calls):
        response = client.post(BASE+f'/runs/{rid}/steer', json=body)
        assert response.status_code == 422
        assert not calls


@pytest.mark.parametrize('gate,expected', [('csrf',403), ('origin',403), ('anonymous',401), ('revoked',401), ('owner',404), ('profile',404), ('pending',409)])
def test_auth_ownership_and_profile_gates_precede_native_io(tmp_path, gate, expected):
    with environment(tmp_path) as (app, client, user, rid, calls):
        if gate == 'csrf':
            client.headers.pop('X-CSRF-Token')
        elif gate == 'origin':
            client.headers['Origin'] = 'https://evil.example'
        elif gate == 'anonymous':
            client.cookies.clear()
        elif gate == 'revoked':
            with app.state.auth.store.transaction() as db:
                db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        elif gate in ('owner','profile'):
            with app.state.journal.connect() as db:
                field = 'user_id' if gate == 'owner' else 'profile'
                db.execute('UPDATE runs SET '+field+'=? WHERE id=?', ('other',rid))
        elif gate == 'pending':
            with app.state.auth.store.transaction() as db:
                db.execute("UPDATE users SET status='pending' WHERE id=?", (user['id'],))
        assert client.post(BASE+f'/runs/{rid}/steer', json=BODY).status_code == expected
        if gate not in ('csrf','origin'):
            assert client.get(BASE+f'/runs/{rid}/controls').status_code == expected
        assert not calls


@pytest.mark.parametrize('cap', [{}, {'features': {'run_steer': True}}, {'mobile_run_controls': {'version': True, 'steering': True}}, {'mobile_run_controls': {'version': 1, 'steering': False}}])
def test_old_capabilities_never_enable_dispatch(tmp_path, cap):
    def handler(request):
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200,json=cap)
    with environment(tmp_path, handler) as (_, client, _, rid, calls):
        assert client.get(BASE+f'/runs/{rid}/controls').json()['steering'] is False
        assert client.post(BASE+f'/runs/{rid}/steer', json=BODY).status_code == 503
        assert all(r.method == 'GET' for r in calls)

@pytest.mark.parametrize('failure', ['timeout', '500', '404', 'wrong-run', 'wrong-key', 'no-steer-id', 'no-status', 'false-accepted', 'array'])
def test_ambiguous_ack_is_durable_unknown_never_resubmitted(tmp_path, failure):
    def handler(request):
        if request.url.path.endswith('/steer'):
            if failure == 'timeout':
                raise httpx.ReadTimeout('private details')
            if failure in ('500','404'):
                return httpx.Response(int(failure), json={'error': 'private details'})
            ack = dict(ACK)
            if failure == 'wrong-run': ack['run_id'] = 'elsewhere'
            if failure == 'wrong-key': ack['idempotency_key'] = 'elsewhere'
            if failure == 'no-steer-id': ack.pop('steer_id')
            if failure == 'no-status': ack.pop('status')
            if failure == 'false-accepted': ack['accepted'] = 1
            return httpx.Response(200,json=[] if failure == 'array' else ack)
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        response = client.post(BASE+f'/runs/{rid}/steer', json=BODY)
        assert response.status_code == 200, response.text
        attempt = response.json()
        assert attempt['status'] == 'unknown' and attempt['input'] == BODY['input']
        assert 'private details' not in response.text
        assert client.post(BASE+f'/runs/{rid}/steer', json=BODY).json() == attempt
        assert len([r for r in calls if r.method == 'POST']) == 1
        assert app.state.journal.get(user['id'],rid)['status'] == 'running'


def test_restart_recovers_claim_unknown_even_if_native_now_404(tmp_path):
    from backend.orchestration import Orchestrator
    from backend.runs import RunJournal
    def handler(request):
        if request.url.path != '/v1/capabilities':
            return httpx.Response(404)
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        runtime = app.state.orchestrator
        claimed, fresh = runtime.steering.claim(user, runtime.get(user,rid), BODY)
        assert fresh and claimed['status'] == 'sending'
        reopened = Orchestrator(RunJournal(app.state.journal.path), runtime.gateway, runtime.catalog)
        reopened.steering.recover()
        retry = client.portal.call(reopened.steer, user, rid, BODY)
        assert retry['status'] == 'unknown' and retry['id'] == claimed['id']
        controls = client.portal.call(reopened.controls, user, rid)
        assert controls['attempts'][0]['status'] == 'unknown'
        assert all(r.method == 'GET' for r in calls)


def test_cancellation_after_dispatch_persists_unknown(tmp_path):
    async def scenario(app, user, rid, entered, release):
        task = asyncio.create_task(app.state.orchestrator.steer(user,rid,BODY))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.state.orchestrator.steering.attempts(user,rid)[0]['status'] == 'unknown'
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        if request.url.path.endswith('/steer'):
            entered.set()
            await release.wait()
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        client.portal.call(scenario,app,user,rid,entered,release)
        assert len([r for r in calls if r.method == 'POST']) == 1

@pytest.mark.parametrize('state', ['stopping','waiting_for_approval','completed'])
def test_lifecycle_change_during_capability_lookup_fences_claim(tmp_path, state):
    holder = {}
    def handler(request):
        if request.url.path == '/v1/capabilities':
            app,user,rid = holder['args']
            app.state.journal.finish(user['id'],rid,state)
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        holder['args'] = app,user,rid
        assert client.post(BASE+f'/runs/{rid}/steer',json=BODY).status_code == 409
        assert all(r.method == 'GET' for r in calls)


def test_stop_serializes_with_inflight_steer_and_later_steer_rejects(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        if request.url.path.endswith('/steer'):
            entered.set()
            await release.wait()
    with environment(tmp_path, handler) as (app, client, user, rid, calls):
        async def scenario():
            runtime = app.state.orchestrator
            steering = asyncio.create_task(runtime.steer(user,rid,BODY))
            await entered.wait()
            stopping = asyncio.create_task(runtime.stop(user,rid))
            await asyncio.sleep(.01)
            try:
                assert not any(r.url.path.endswith('/stop') for r in calls)
            finally:
                release.set()
                await steering
                await stopping
        client.portal.call(scenario)
        assert client.post(BASE+f'/runs/{rid}/steer',json=dict(BODY,idempotency_key='new')).status_code == 409
        assert [r.url.path.rsplit('/',1)[-1] for r in calls if r.method == 'POST'] == ['steer','stop']

@pytest.mark.parametrize('code', [200,409])
def test_native_correlated_not_delivered_is_definitive(tmp_path, code):
    def handler(request):
        if request.url.path.endswith('/steer'):
            return httpx.Response(code, json={'object':'hermes.run.steer', 'run_id': 'native-run', 'steer_id': BODY['idempotency_key'],
                                             'accepted': False, 'status': 'not_delivered'})
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        response=client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        assert response.status_code == 200
        assert response.json()['status'] == 'not_delivered'


def test_native_key_is_steer_id_and_must_match(tmp_path):
    def handler(request):
        if request.url.path.endswith('/steer'):
            return httpx.Response(200,json={'run_id':'native-run','steer_id':'different-key',
                                           'status':'accepted_unconfirmed','accepted':True})
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        assert client.post(BASE+f'/runs/{rid}/steer',json=BODY).json()['status'] == 'unknown'


def test_native_acceptance_without_redundant_key_echo(tmp_path):
    def handler(request):
        if request.url.path.endswith('/steer'):
            return httpx.Response(200,json={k:v for k,v in ACK.items() if k != 'idempotency_key'})
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        assert client.post(BASE+f'/runs/{rid}/steer',json=BODY).json()['status'] == 'accepted_unconfirmed'

@pytest.mark.parametrize('status', ['completed','failed','cancelled'])
@pytest.mark.parametrize('source', ['poll','sse'])
def test_every_terminal_path_preserves_steering_evidence(tmp_path, status, source):
    receipt = {'run_id':'native-run','steer_id':'steer-key','status':'unknown','accepted':True}
    terminal = {'run_id':'native-run','status':status,'output':'separate outcome',
                'pending_steer':BODY['input'], 'steer_receipts':[receipt]}
    def handler(request):
        if request.url.path == '/v1/runs':
            return httpx.Response(200,json={'run_id':'native-run'})
        if request.url.path.endswith('/events'):
            return httpx.Response(200,text='event: run.'+status+'\ndata: '+json.dumps(terminal)+'\n\n')
        if request.url.path == '/v1/runs/native-run':
            return httpx.Response(200,json=terminal)
    with environment(tmp_path,handler) as (app,client,user,rid,calls):
        client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        runtime=app.state.orchestrator
        if source == 'poll':
            client.portal.call(runtime._reconcile,user,rid)
        else:
            client.portal.call(runtime._stream,user,runtime.get(user,rid),[])
        assert runtime.get(user,rid)['status'] == status
        controls=client.get(BASE+f'/runs/{rid}/controls').json()
        assert controls['pending_steer'] == BODY['input']
        assert controls['steer_receipts'] == [receipt]
        assert controls['attempts'][0]['status'] == 'unknown'
        events=app.state.journal.events(user['id'],rid)
        assert events[-1]['name'] == 'done'
        assert any(e['name']=='steering' and 'pending_steer' in e['data'] for e in events)


def test_controls_refreshes_correlated_receipts_and_ignores_native_404(tmp_path):
    state = {'code':200,'receipt': {'run_id':'native-run','steer_id':'steer-key',
                                  'status':'not_delivered','accepted':False}}
    def handler(request):
        if request.url.path == '/v1/runs/native-run':
            return httpx.Response(state['code'],json={'run_id':'native-run', 'status':'running',
                                                     'steer_receipts':[state['receipt']]})
    with environment(tmp_path,handler) as (app,client,user,rid,calls):
        client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        controls=client.get(BASE+f'/runs/{rid}/controls').json()
        assert controls['attempts'][0]['status'] == 'not_delivered'
        state['code']=404
        assert client.get(BASE+f'/runs/{rid}/controls').json()['attempts'] == controls['attempts']
        assert len([r for r in calls if r.method=='POST']) == 1


def test_uncorrelated_receipt_never_changes_attempt_or_exposes_unowned_text(tmp_path):
    def handler(request):
        if request.url.path == '/v1/runs/native-run':
            return httpx.Response(200,json={'run_id':'another-run', 'pending_steer':'private',
                'steer_receipts':[{'run_id':'another-run','steer_id':'steer-key','status':'not_delivered','accepted':False}]})
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        controls=client.get(BASE+f'/runs/{rid}/controls')
        assert controls.json()['attempts'][0]['status'] == 'accepted_unconfirmed'
        assert 'private' not in controls.text


def test_late_ack_cannot_overwrite_terminal_receipt(tmp_path):
    holder={}
    def handler(request):
        if request.url.path.endswith('/steer'):
            app,user,rid=holder['args']
            runtime=app.state.orchestrator
            runtime.steering.observe(user,runtime.get(user,rid),{
                'run_id':'native-run','pending_steer':BODY['input'],
                'steer_receipts':[dict(ACK,status='unknown')]})
            app.state.journal.finish(user['id'],rid,'completed')
    with environment(tmp_path,handler) as (app,client,user,rid,_):
        holder['args']=app,user,rid
        response=client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        assert response.json()['status'] == 'unknown'
        assert app.state.orchestrator.get(user,rid)['status'] == 'completed'


def test_correlated_unknown_ack_retains_receipt_identity(tmp_path):
    def handler(request):
        if request.url.path.endswith('/steer'):
            return httpx.Response(200,json=dict(ACK,status='unknown',accepted=False))
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        response=client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        assert response.json()['status'] == 'unknown'
        assert response.json()['steer_id'] == 'steer-key'

@pytest.mark.parametrize('object_value', [None,'hermes.run'])
def test_ack_requires_exact_native_protocol_object(tmp_path,object_value):
    def handler(request):
        if request.url.path.endswith('/steer'):
            ack=dict(ACK)
            if object_value is None: ack.pop('object')
            else: ack['object']=object_value
            return httpx.Response(200,json=ack)
    with environment(tmp_path,handler) as (_,client,_,rid,_):
        assert client.post(BASE+f'/runs/{rid}/steer',json=BODY).json()['status']=='unknown'


def test_repeat_recovery_does_not_republish_unchanged_unknown_attempt(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        runtime=app.state.orchestrator
        runtime.steering.claim(user,runtime.get(user,rid),BODY)
        runtime.steering.recover()
        before=app.state.journal.events(user['id'],rid)
        runtime.steering.recover()
        assert app.state.journal.events(user['id'],rid)==before


def test_stale_poll_cannot_undo_terminal_unknown_receipt(tmp_path):
    with environment(tmp_path) as (app,client,user,rid,_):
        client.post(BASE+f'/runs/{rid}/steer',json=BODY)
        runtime=app.state.orchestrator
        runtime.steering.observe(user,runtime.get(user,rid),{'run_id':'native-run',
            'steer_receipts':[dict(ACK,status='unknown')]})
        runtime.steering.observe(user,runtime.get(user,rid),{'run_id':'native-run',
            'steer_receipts':[ACK]})
        assert runtime.steering.attempts(user,rid)[0]['status']=='unknown'
