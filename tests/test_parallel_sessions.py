"""Parallel admission regressions: real SQLite, isolated native transport only."""
import asyncio
import sqlite3

import pytest

from backend.orchestration import Orchestrator
from backend.runs import RunConflict, RunJournal
from test_orchestration import USER, catalog_at, complete_context, until
from test_chat_snapshot import chat
from test_auth import BASE


LEGACY_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS orchestration_one_active ON runs((1)) WHERE status IN ('queued','running','stopping','waiting_for_approval','unknown')"


class ParallelGateway:
    def __init__(self):
        self.starts, self.queues, self.stops = [], {}, []

    def require_execution(self):
        pass

    async def start(self, session_id, text, history=None):
        rid = 'native-' + session_id
        self.starts.append(session_id)
        self.queues[rid] = asyncio.Queue()
        return {'run_id': rid}

    async def events(self, rid):
        while True:
            yield await self.queues[rid].get()

    async def stop(self, rid):
        self.stops.append(rid)


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_distinct_sessions_run_concurrently_after_reopen(tmp_path, legacy):
    catalog = catalog_at(tmp_path)
    with sqlite3.connect(catalog.profiles['default'] / 'state.db') as c:
        c.executemany('INSERT INTO sessions VALUES (?)', [('second',), ('third',), ('fourth',), ('fifth',)])
    journal = RunJournal(tmp_path / 'runs.db')
    historical, _ = journal.submit('owner', 'default', 'old', 'old input', 'old-key',
                                    history_anchor=anchor('old', 'old'))
    journal.finish('owner', historical['id'], 'completed', output='old output')
    events = journal.events('owner', historical['id'])
    if legacy:
        with journal.connect() as c:
            c.execute(LEGACY_INDEX)
    journal = RunJournal(journal.path)
    gateway = ParallelGateway()
    runtime = Orchestrator(journal, gateway, catalog, history_loader=complete_context)
    try:
        runs = [await runtime.submit(USER, dict(session_id=sid, input='hello', idempotency_key=sid))
                for sid in ('native', 'second', 'third', 'fourth')]
        await until(lambda: len(gateway.starts) == 4)
        assert all(runtime.get(USER, r['id'])['status'] == 'running' for r in runs)
        with pytest.raises(RunConflict, match='capacity'):
            await runtime.submit(USER, dict(session_id='fifth', input='hello', idempotency_key='fifth'))
        await gateway.queues['native-second'].put(dict(event='message.delta', run_id='native-second', delta='second only'))
        await gateway.queues['native-second'].put(dict(event='run.completed', run_id='native-second', output='second done'))
        await until(lambda: runtime.get(USER, runs[1]['id'])['status'] == 'completed')
        assert runtime.get(USER, runs[0]['id'])['status'] == 'running'
        assert not any(e['name'] == 'delta' for e in journal.events('owner', runs[0]['id']))
        await runtime.stop(USER, runs[0]['id'])
        assert gateway.stops == ['native-native']
        assert runtime.get(USER, runs[2]['id'])['status'] == 'running'
        reopened = RunJournal(journal.path)
        observer = Orchestrator(reopened, gateway, catalog)
        await observer.close()
        assert reopened.get('owner', historical['id'])['output'] == 'old output'
        assert reopened.events('owner', historical['id']) == events
    finally:
        await runtime.close()



def anchor(session, canonical):
    return lambda: dict(session_id=session, canonical_session_id=canonical, message_id=0)


def test_canonical_alias_cannot_admit_a_second_run(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    first, _ = journal.submit('owner', 'default', 'old', 'hello', 'one',
                              history_anchor=anchor('old', 'canonical'))
    with pytest.raises(RunConflict, match='active or unresolved'):
        journal.submit('owner', 'default', 'canonical', 'again', 'two',
                       history_anchor=anchor('canonical', 'canonical'))
    with journal.connect() as c:
        assert [r['id'] for r in c.execute('SELECT id FROM runs')] == [first['id']]


def test_profile_capacity_is_four_including_unresolved_work(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    runs = [journal.submit('owner', 'default', f's{i}', 'hello', f'k{i}')[0]
            for i in range(4)]
    journal.finish('owner', runs[0]['id'], 'unknown')
    assert journal.submit('owner', 'default', 's0', 'hello', 'k0')[0]['id'] == runs[0]['id']
    with pytest.raises(RunConflict, match='capacity'):
        journal.submit('other-owner', 'default', 's4', 'hello', 'k4')
    other, _ = journal.submit('member', 'member', 's0', 'hello', 'k0')
    assert other['profile'] == 'member'
    journal.finish('owner', runs[1]['id'], 'completed')
    assert journal.submit('owner', 'default', 's4', 'hello', 'k4')[1] is True


@pytest.mark.parametrize('status', ['queued', 'running', 'stopping', 'waiting_for_approval', 'unknown', 'unrecognized'])
@pytest.mark.parametrize('requested,canonical', [('canonical', 'canonical'), ('new-alias', 'canonical'), ('old', 'rotated')])
def test_alias_fence_is_profile_scoped_not_user_scoped(tmp_path, status, requested, canonical):
    journal = RunJournal(tmp_path / 'runs.db')
    first, _ = journal.submit('owner', 'default', 'old', 'one', 'one', history_anchor=anchor('old', 'canonical'))
    with journal.connect() as c:
        c.execute('UPDATE runs SET status=? WHERE id=?', (status, first['id']))
    with pytest.raises(RunConflict, match='active or unresolved'):
        journal.submit('another-owner', 'default', requested, 'two', 'two', history_anchor=anchor(requested, canonical))
    assert journal.submit('member', 'other-profile', requested, 'two', 'two', history_anchor=anchor(requested, canonical))[1]
    assert journal.submit('owner', 'default', 'independent', 'two', 'independent')[1]


def test_historical_alias_chain_fences_a_rotated_session(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    for old, new in [('original', 'middle'), ('middle', 'current')]:
        run, _ = journal.submit('owner', 'default', old, 'old', old, history_anchor=anchor(old, new))
        journal.finish('owner', run['id'], 'completed')
    run, _ = journal.submit('owner', 'default', 'current', 'current', 'current')
    journal.finish('owner', run['id'], 'unknown')
    with pytest.raises(RunConflict):
        journal.submit('owner', 'default', 'original', 'again', 'again')
    journal.finish('owner', run['id'], 'completed')
    assert journal.submit('owner', 'default', 'original', 'again', 'again')[1]


@pytest.mark.parametrize('same_canonical', [False, True])
def test_independent_connections_serialize_racing_admissions(tmp_path, same_canonical):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    journal = RunJournal(tmp_path / 'runs.db')
    journals = [RunJournal(journal.path) for _ in range(8)]
    ready = Barrier(len(journals))

    def submit(index):
        sid = f'alias-{index}'
        ready.wait(timeout=10)
        try:
            return journals[index].submit('owner', 'default', sid, 'hello', sid,
                history_anchor=anchor(sid, 'canonical' if same_canonical else sid))[0]
        except RunConflict:
            return None

    with ThreadPoolExecutor(max_workers=len(journals)) as pool:
        results = list(pool.map(submit, range(len(journals))))
    accepted = [run for run in results if run is not None]
    assert len(accepted) == (1 if same_canonical else 4)
    with journal.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == len(accepted)
        assert c.execute('SELECT COUNT(*) FROM run_history_anchors').fetchone()[0] == len(accepted)


def test_parallel_recovery_keeps_original_identities_and_deployment_fence(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    originals = [journal.submit('owner', 'default', sid, sid, sid)[0] for sid in ('one', 'two')]
    for run in originals:
        journal.set_upstream('owner', run['id'], 'native-' + run['session_id'])
    journal = RunJournal(journal.path)
    journal.recover()
    for run in originals:
        retried, created = journal.submit('owner', 'default', run['session_id'], run['input'], run['idempotency_key'])
        assert not created and retried['id'] == run['id'] and retried['status'] == 'unknown'
        assert retried['upstream_id'] == 'native-' + run['session_id']
        with pytest.raises(RunConflict):
            journal.submit('owner', 'default', run['session_id'], 'new', 'new-' + run['session_id'])
    journal.set_deployment_gate('deploy-owner')
    with pytest.raises(RunConflict, match='deployment'):
        journal.submit('owner', 'default', 'third', 'new', 'third')
    journal.clear_deployment_gate('wrong-owner')
    with pytest.raises(RunConflict, match='deployment'):
        journal.submit('owner', 'other-profile', 'third', 'new', 'third')
    journal.clear_deployment_gate('deploy-owner')
    assert journal.submit('owner', 'default', 'third', 'new', 'third')[1]


def test_parallel_api_preserves_canonical_conflicts_idempotency_and_capacity(chat):
    app, client, user, native_path = chat
    with sqlite3.connect(native_path) as c:
        c.executemany('INSERT INTO sessions(id) VALUES (?)', [('alias',), ('third',), ('fourth',), ('fifth',)])
    runtime = app.state.orchestrator
    gateway = runtime.gateway = ParallelGateway()
    runtime.history_loader = lambda profile, sid: dict(complete_context(profile, sid),
        canonical_session_id='wa-1' if sid == 'alias' else sid)
    bodies = [dict(session_id=sid, input=sid, idempotency_key=sid)
              for sid in ('wa-1', 'cli-1', 'third', 'fourth')]
    first = client.post(BASE + '/runs', json=bodies[0])
    assert first.status_code == 200, first.text
    retry = client.post(BASE + '/runs', json=bodies[0])
    assert retry.status_code == 200 and retry.json()['id'] == first.json()['id']
    alias = client.post(BASE + '/runs', json=dict(session_id='alias', input='alias', idempotency_key='alias'))
    assert alias.status_code == 409 and 'active or unresolved' in alias.json()['detail']
    for body in bodies[1:]:
        response = client.post(BASE + '/runs', json=body)
        assert response.status_code == 200, response.text
    fifth = client.post(BASE + '/runs', json=dict(session_id='fifth', input='five', idempotency_key='fifth'))
    assert fifth.status_code == 409 and 'capacity' in fifth.json()['detail']
    assert set(gateway.starts) == {'wa-1', 'cli-1', 'third', 'fourth'}
    client.cookies.clear()
    assert client.post(BASE + '/runs', json=bodies[0]).status_code == 401


@pytest.mark.asyncio
async def test_parallel_approvals_with_same_request_id_stay_run_bound(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    gateway = ParallelGateway()
    gateway.execution_ready = gateway.action_approvals_ready = True
    decisions = []

    async def capable():
        pass

    async def approve(rid, request, decision):
        decisions.append((rid, request, decision))

    gateway.require_action_approvals, gateway.approve = capable, approve
    runtime = Orchestrator(journal, gateway, catalog_at(tmp_path))
    runs = [journal.submit('owner', 'default', sid, sid, sid)[0] for sid in ('one', 'two')]
    try:
        for run in runs:
            journal.set_upstream('owner', run['id'], 'native-' + run['session_id'])
            runtime._approval(USER, run, dict(event='approval.request', run_id='native-' + run['session_id'],
                                             request_id='same-request', command=run['session_id']))
        items = runtime.approvals(USER)['items']
        assert len(items) == 2 and len({a['id'] for a in items}) == 2
        result = await runtime.decide(USER, items[0]['id'], 'once')
        assert result['run_id'] == runs[0]['id']
        assert decisions == [('native-one', 'same-request', 'once')]
        assert runtime.get(USER, runs[0]['id'])['status'] == 'running'
        assert runtime.get(USER, runs[1]['id'])['status'] == 'waiting_for_approval'
        assert [a['id'] for a in runtime.approvals(USER)['items']] == [items[1]['id']]
        with pytest.raises(KeyError):
            await runtime.decide(dict(USER, id='foreign'), items[1]['id'], 'once')
        assert len(decisions) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_legacy_index_migration_waits_for_runtime_startup_and_rollback_needs_drain(tmp_path):
    journal = RunJournal(tmp_path / 'runs.db')
    runs = [journal.submit('owner', 'default', sid, sid, sid)[0] for sid in ('one', 'two')]
    # Exact CREATE from the baseline Orchestrator: rollback fails closed if
    # multiple active/unknown rows survive. Never clear them merely to roll back.
    with journal.connect() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(LEGACY_INDEX)
    for run in runs:
        journal.finish('owner', run['id'], 'completed')
    with journal.connect() as c:
        c.execute(LEGACY_INDEX)
    journal = RunJournal(journal.path)  # Deployment controller, before gating.
    with journal.connect() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='orchestration_one_active'").fetchone()
    journal.set_deployment_gate('release')
    runtime = Orchestrator(journal, ParallelGateway(), catalog_at(tmp_path))
    try:
        with journal.connect() as c:
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='orchestration_one_active'").fetchone()
        with pytest.raises(RunConflict, match='deployment'):
            journal.submit('owner', 'default', 'three', 'three', 'three')
        journal.clear_deployment_gate('release')
        assert journal.submit('owner', 'default', 'three', 'three', 'three')[1]
        assert journal.submit('owner', 'default', 'four', 'four', 'four')[1]
    finally:
        await runtime.close()
