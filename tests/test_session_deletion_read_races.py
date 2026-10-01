"""Already-open readers must not replay content past a deletion fence."""
from types import SimpleNamespace
import asyncio
import pytest
from backend.runs import RunJournal, RunConflict
from backend.orchestration import Orchestrator
from test_session_deletion import deletion_app
from test_auth import BASE


@pytest.mark.asyncio
async def test_open_event_stream_stops_before_next_buffered_event(deletion_app, monkeypatch):
    app, client, user, _, _ = deletion_app
    journal = app.state.journal
    run, _ = journal.submit(user['id'],'default','cli-1','input','key')
    journal.event(user['id'],run['id'],'delta',{'text':'first'})
    journal.event(user['id'],run['id'],'delta',{'text':'second'})
    journal.finish(user['id'],run['id'],'completed')
    monkeypatch.setattr(app.state.auth,'is_session_active',lambda *args:True)
    endpoint = next(route.endpoint for route in app.routes if getattr(route,'path',None)==BASE+'/runs/{rid}/events')
    async def disconnected(): return False
    response = await endpoint(run['id'],SimpleNamespace(is_disconnected=disconnected),after=0,user=dict(user,session_id='auth-fixture'))
    stream = response.body_iterator
    assert 'first' in await anext(stream)
    journal.claim_deletion(user['id'],'default','cli-1')
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_controls_rechecks_fence_after_native_read(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    entered, release = asyncio.Event(), asyncio.Event()
    class Gateway:
        def require_execution(self): pass
        async def request(self,*args):
            entered.set(); await release.wait()
            return {'run_id':'upstream','status':'completed'}
    runtime = Orchestrator(journal,Gateway(),SimpleNamespace(profiles={'default':tmp_path}))
    user = dict(id='owner',profile='default')
    run, _ = journal.submit('owner','default','chat','input','key')
    journal.set_upstream('owner',run['id'],'upstream')
    attempt, _ = runtime.steering.claim(user,journal.get('owner',run['id']),dict(input='guide',idempotency_key='steer'))
    runtime.steering.finish(attempt['id'],'not_delivered')
    journal.finish('owner',run['id'],'completed')
    task = asyncio.create_task(runtime.controls(user,run['id']))
    await entered.wait()
    journal.claim_deletion('owner','default','chat')
    release.set()
    with pytest.raises(RunConflict): await task
