"""Tombstone visibility and delayed admissions, all disposable state."""
import asyncio
from contextlib import closing
from types import SimpleNamespace
import pytest
from backend.runs import RunJournal, RunConflict
from backend.orchestration import Orchestrator
from test_auth import BASE
from test_session_deletion import deletion_app


def test_recreated_native_row_cannot_surface_in_pages_search_or_direct_routes(deletion_app):
    app, client, user, calls, _ = deletion_app
    run, _ = app.state.journal.submit(user['id'], 'default', 'cli-1', 'input', 'old-key')
    app.state.journal.finish(user['id'], run['id'], 'completed')
    assert client.request('DELETE', BASE+'/sessions/cli-1', json={'confirm': True}).status_code == 200
    # Transport deliberately left native row intact, equivalent to recreation.
    for params in ({}, {'kind':'all'}, {'q':'CLI'}, {'limit':1}, {'limit':1,'offset':1}):
        page = client.get(BASE+'/sessions', params=params).json()
        assert 'cli-1' not in [r['id'] for r in page['items']]
        assert page['total'] == (0 if params.get('q') else 1)
        if params.get('offset'): assert page['items'] == []
    before = len(calls)
    for path in ['/sessions/cli-1/messages', '/sessions/cli-1/telemetry', '/sessions/cli-1/model-options',
                 '/runs/'+run['id'], '/runs/'+run['id']+'/controls', '/runs/'+run['id']+'/events']:
        assert client.get(BASE+path).status_code == 409, path
    for suffix, body in [('/stop',{}),('/steer',{'input':'guide','idempotency_key':'steer'})]:
        assert client.post(BASE+'/runs/'+run['id']+suffix, json=body).status_code == 409
    assert client.patch(BASE+'/sessions/cli-1', json={'title':'Resurrect'}).status_code == 409
    for key in ('old-key', 'new-key'):
        assert client.post(BASE+'/runs', json={'session_id':'cli-1','input':'input','idempotency_key':key}).status_code == 409
    assert len(calls) == before


@pytest.mark.asyncio
async def test_history_await_cannot_cross_deletion_admission(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    entered, release = asyncio.Event(), asyncio.Event()
    class Gateway:
        def require_execution(self): pass
    async def history(profile, sid):
        entered.set(); await release.wait()
        return dict(complete=True, profile=profile, session_id=sid, canonical_session_id='canonical', history=[])
    catalog = SimpleNamespace(profiles={'default':tmp_path}, history_anchor=lambda profile,sid,canonical:
        dict(session_id=sid,canonical_session_id=canonical,message_id=0))
    runtime = Orchestrator(journal, Gateway(), catalog, history_loader=history)
    task = asyncio.create_task(runtime.submit({'id':'owner','profile':'default'},
        {'session_id':'alias','input':'input','idempotency_key':'key'}))
    await entered.wait()
    journal.claim_deletion('owner', 'default', 'canonical')
    release.set()
    with pytest.raises(RunConflict): await task
    assert not runtime._tasks
    with closing(journal.connect()) as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_tombstone_is_checked_before_loading_native_history(tmp_path):
    journal = RunJournal(tmp_path/'runs.sqlite')
    journal.claim_deletion('owner','default','chat')
    called = []
    class Gateway:
        def require_execution(self): pass
    async def history(profile, sid):
        called.append(sid)
        return dict(complete=True, profile=profile, session_id=sid, history=[])
    catalog = SimpleNamespace(profiles={'default':tmp_path}, history_anchor=lambda *args: {})
    runtime = Orchestrator(journal, Gateway(), catalog, history_loader=history)
    with pytest.raises(RunConflict):
        await runtime.submit({'id':'owner','profile':'default'}, {'session_id':'chat','input':'input','idempotency_key':'key'})
    assert not called


@pytest.mark.parametrize('kind', ['task', 'dispatch', 'control', 'steering', 'approval', 'deploy'])
def test_terminal_run_does_not_prove_workers_or_controls_are_quiet(deletion_app, kind):
    app, client, user, calls, _ = deletion_app
    journal, runtime = app.state.journal, app.state.orchestrator
    run, _ = journal.submit(user['id'],'default','alias','input','key', history_anchor=lambda:
        dict(session_id='alias',canonical_session_id='cli-1',message_id=0))
    journal.finish(user['id'],run['id'],'completed')
    if kind == 'task':
        class Task:
            def done(self): return False
        task = Task(); runtime._tasks[task] = (user,run)
    if kind == 'dispatch': runtime._dispatching.add(run['id'])
    if kind == 'control':
        lock = asyncio.Lock(); lock._locked = True
        runtime._control_locks[run['id']] = lock
    with closing(journal.connect()) as c, c:
        if kind == 'steering':
            c.execute('INSERT INTO steering_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('attempt',user['id'],'default',run['id'],'upstream','steer-key','guide','unknown',None,1,1))
        if kind == 'approval':
            c.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                ('approval',run['id'],'request','{}','sending',9999999999))
        if kind == 'deploy': c.execute("INSERT INTO deployment_gate VALUES(1,'fixture')")
    try:
        response = client.request('DELETE', BASE+'/sessions/cli-1',json={'confirm':True})
        assert response.status_code == 409, response.text
        assert not any(method == 'DELETE' for method, _ in calls)
    finally:
        runtime._tasks.clear(); runtime._dispatching.clear(); runtime._control_locks.clear()


def test_submit_and_delete_compete_on_same_sqlite_writer_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    for i in range(12):
        journal = RunJournal(tmp_path/f'runs-{i}.sqlite')
        barrier = Barrier(2)
        def submit():
            barrier.wait()
            try: journal.submit('owner','default',str(i),'input',str(i)); return 'submit'
            except RunConflict: return 'refused'
        def delete():
            barrier.wait()
            try: journal.claim_deletion('owner','default',str(i)); return 'delete'
            except RunConflict: return 'refused'
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(submit), pool.submit(delete)
            assert sorted([a.result(),b.result()]) in [['refused','submit'], ['delete','refused']]
