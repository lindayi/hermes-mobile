"""Recovery uses temporary state and a fake transport; never a real dispatch."""
import asyncio

import pytest

from test_orchestration import BODY, USER, runtime_at, until


def existing_run(o, j):
    run, _ = j.submit(USER['id'], USER['profile'], BODY['session_id'], BODY['input'], BODY['idempotency_key'])
    j.set_upstream(USER['id'], run['id'], 'upstream')
    j.finish(USER['id'], run['id'], 'unknown')
    return run


@pytest.mark.asyncio
async def test_same_id_refresh_recovers_running_then_terminal_without_replay(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    import backend.orchestration as orchestration
    # Wall-clock scheduling under loaded integration checks is not the polling
    # contract. Hold the observation clock fixed while concurrent readers drain.
    clock = [100.0]
    monkeypatch.setattr(orchestration, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    o.recovery_interval = .1
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'running'
        await asyncio.gather(*(o.refresh(USER, run['id']) for _ in range(10)))
        assert len(g.requests) == 1
        g.status = {'run_id': 'upstream', 'status': 'completed', 'output': 'preserved final'}
        clock[0] += .12
        result = await o.refresh(USER, run['id'])
        assert (result['id'], result['input'], result['idempotency_key'], result['output']) == (
            run['id'], BODY['input'], BODY['idempotency_key'], 'preserved final')
        assert not g.starts and not g.stops
        assert g.requests == [('GET', '/v1/runs/upstream', {})] * 2
    finally:
        await o.close()



@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['foreign_run', 'foreign_session', 'foreign_profile', 'disabled', 'malformed', 'unavailable'])
async def test_observation_fails_closed(tmp_path, case):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    g.status = {'run_id': 'upstream', 'status': 'completed', 'output': 'must not trust'}
    if case == 'foreign_run':
        g.status['run_id'] = 'foreign'
    if case == 'foreign_session':
        g.status['session_id'] = 'foreign'
    if case == 'foreign_profile':
        g.status['profile'] = 'foreign'
    if case == 'disabled':
        g.execution_ready = False
    if case == 'malformed':
        g.status = []
    if case == 'unavailable':
        async def unavailable(*args, **kw):
            raise RuntimeError('secret')
        g.request = unavailable
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'unknown'
        if case == 'disabled':
            assert not g.requests
        assert not g.starts and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', ['running', 'completed', 'unavailable'])
async def test_terminal_race_is_monotonic(tmp_path, reply):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kw):
        entered.set()
        await release.wait()
        if reply == 'unavailable':
            raise RuntimeError('unavailable')
        return {'run_id': 'upstream', 'status': reply, 'output': 'stale'}
    g.request = delayed
    pending = asyncio.create_task(o.refresh(USER, run['id']))
    await entered.wait()
    j.finish(USER['id'], run['id'], 'completed', output='authoritative first')
    release.set()
    try:
        result = await pending
        assert (result['status'], result['output']) == ('completed', 'authoritative first')
        assert len([e for e in j.events(USER['id'], run['id']) if e['data'].get('status') == 'completed']) == 1
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_disconnect_keeps_observing_same_id_until_completion(tmp_path):
    o, j, g = runtime_at(tmp_path)
    o.recovery_interval = .03
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        await g.queue.put(None)
        await until(lambda: bool(g.requests))
        assert o.get(USER, run['id'])['status'] == 'running'
        g.status = {'run_id': 'upstream', 'status': 'completed', 'output': 'natural completion'}
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        assert len(g.starts) == 1 and not g.stops
        assert len(g.requests) == 2
        assert o.get(USER, run['id'])['output'] == 'natural completion'
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_failed_stop_intent_survives_restart_and_positive_running(tmp_path):
    from backend.orchestration import Orchestrator
    from backend.runs import RunJournal
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    async def ambiguous_stop(rid):
        g.stops.append(rid)
        raise RuntimeError('lost ACK')
    g.stop = ambiguous_stop
    await o.stop(USER, run['id'])
    await o.close()
    reopened = RunJournal(j.path)
    reopened.recover()
    recovered = Orchestrator(reopened, g, o.catalog)
    try:
        result = await recovered.refresh(USER, run['id'])
        assert result['status'] in ('unknown', 'stopping')
        assert g.stops == ['upstream'] and not g.starts
    finally:
        await recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['binding', 'upstream', 'close'])
async def test_await_rechecks_execution_identity_and_lifecycle(tmp_path, change):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    entered, release = asyncio.Event(), asyncio.Event()
    valid = [True]
    o.recovery_validator = lambda user: valid[0]
    async def delayed(*args, **kw):
        entered.set()
        await release.wait()
        return {'run_id': 'upstream', 'status': 'completed', 'output': 'untrusted'}
    g.request = delayed
    task = asyncio.create_task(o.refresh(USER, run['id']))
    await entered.wait()
    if change == 'binding':
        valid[0] = False
    elif change == 'upstream':
        with j.connect() as db:
            db.execute("UPDATE runs SET upstream_id='different' WHERE id=?", (run['id'],))
    else:
        await o.close()
    release.set()
    try:
        assert (await task)['status'] == 'unknown'
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_identity_only_native_approval_has_no_fabricated_executable_grant(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'new-native-action'}]}
    decisions = []
    async def capable():
        g.action_approvals_ready = True
    async def approve(rid, aid, decision):
        decisions.append((rid, aid, decision))
    g.require_action_approvals, g.approve = capable, approve
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'waiting_for_approval'
        items = o.approvals(USER)['items']
        assert len(items) == 1 and items[0]['executable'] is False
        assert items[0]['expires_at'] is None
        assert 'details' in items[0]['error'].lower()
        assert items[0]['request_id'] == 'new-native-action'
        assert not decisions and not g.starts and not g.stops
        from backend.runs import RunConflict
        with pytest.raises(RunConflict):
            await o.decide(USER, items[0]['id'], 'deny')
        assert not decisions
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_snapshot_resolves_only_absent_pending_actions_before_restoring_waiting(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    for aid in ('current', 'vanished'):
        o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': aid})
    j.recover()
    g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'current'}]}
    try:
        await o.refresh(USER, run['id'])
        assert [a['request_id'] for a in o.approvals(USER)['items']] == ['current']
        with j.connect() as db:
            assert db.execute("SELECT status FROM orchestration_approvals WHERE request_id='vanished'").fetchone()[0] == 'resolved_external'
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('event', [
    {'event': 'run.completed', 'output': 'untrusted'},
    {'event': 'run.completed', 'run_id': 'foreign', 'output': 'untrusted'},
    {'event': 'run.completed', 'run_id': 'upstream', 'session_id': 'foreign', 'output': 'untrusted'},
])
async def test_unbound_terminal_stream_event_cannot_release_admission(tmp_path, event):
    o, j, g = runtime_at(tmp_path)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        await g.queue.put(event)
        await asyncio.sleep(.03)
        assert o.get(USER, run['id'])['status'] != 'completed'
        assert o.get(USER, run['id'])['output'] is None
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('snapshot_session,expected', [('canonical', 'completed'), ('native', 'unknown')])
async def test_canonical_anchor_not_ui_alias_binds_snapshot(tmp_path, snapshot_session, expected):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    with j.connect() as db:
        db.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)', (run['id'], 'native', 'canonical', 42))
    g.status = {'run_id': 'upstream', 'session_id': snapshot_session, 'status': 'completed', 'output': 'original'}
    try:
        assert (await o.refresh(USER, run['id']))['status'] == expected
        assert not g.starts
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain', ['sending', 'unknown'])
@pytest.mark.parametrize('native', ['running', 'waiting_for_approval'])
async def test_positive_status_never_reopens_uncertain_approval(tmp_path, uncertain, native):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'uncertain'})
    with j.connect() as db:
        db.execute('UPDATE orchestration_approvals SET status=?', (uncertain,))
    j.finish(USER['id'], run['id'], 'unknown')
    g.status = {'run_id': 'upstream', 'status': native,
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'new'}]}
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'unknown'
        assert not o.approvals(USER)['items']
        with j.connect() as db:
            assert db.execute('SELECT status FROM orchestration_approvals').fetchone()[0] == uncertain
        assert not g.starts and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_observation_timeout_is_bounded_and_worker_close_only_cancels_observer(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    o.observation_timeout = .02
    o.recovery_interval = .02
    entered = asyncio.Event()
    async def stalled(*args, **kw):
        entered.set()
        await asyncio.Event().wait()
    g.request = stalled
    try:
        async with asyncio.timeout(.5):
            assert (await o.refresh(USER, run['id']))['status'] == 'unknown'
        o.start_recovery()
        await entered.wait()
        async with asyncio.timeout(.5):
            await o.close()
        assert not g.starts and not g.stops
        assert o._recovery_task is None
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_positive_native_stopping_is_retained(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    g.status = {'run_id': 'upstream', 'status': 'stopping'}
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'stopping'
        assert not g.starts and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_authoritative_empty_pending_snapshot_restores_running_without_reopening_decision(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'resolved-natively'})
    g.status = {'run_id': 'upstream', 'status': 'running', 'pending_approvals': []}
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'running'
        assert not o.approvals(USER)['items']
        with j.connect() as db:
            assert db.execute('SELECT status FROM orchestration_approvals').fetchone()[0] == 'resolved_external'
        assert not g.starts and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_legacy_notification_recovery_cannot_bypass_session_binding(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'known'})
    j.recover()
    o.approval_validator = lambda *args: None
    g.status = {'run_id': 'upstream', 'session_id': 'foreign', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'known'}]}
    try:
        await o.reconcile_approval_notifications()
        assert o.get(USER, run['id'])['status'] == 'unknown'
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fence', ['stop', 'approval_unknown'])
async def test_notification_recovery_preserves_uncertainty_fences(tmp_path, fence):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'known'})
    if fence == 'stop':
        j.finish(USER['id'], run['id'], 'stopping')
    else:
        with j.connect() as db:
            db.execute("INSERT INTO orchestration_approvals VALUES('uncertain',?,'other','{}','unknown',0)", (run['id'],))
    j.recover()
    o.approval_validator = lambda *args: None
    g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'known'}]}
    try:
        await o.reconcile_approval_notifications()
        assert o.get(USER, run['id'])['status'] == 'unknown'
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [[{'run_id': 'foreign', 'request_id': 'bad'}], 'malformed'])
async def test_running_snapshot_with_contradictory_pending_actions_fails_closed(tmp_path, pending):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    g.status = {'run_id': 'upstream', 'status': 'running', 'pending_approvals': pending}
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'unknown'
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_status,error', [('stopping', None), ('unknown', 'Stop outcome is unresolved')])
async def test_upgrade_preserves_legacy_stop_evidence(tmp_path, legacy_status, error):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    with j.connect() as db:
        db.execute('UPDATE runs SET status=?,error=? WHERE id=?', (legacy_status, error, run['id']))
    j.recover()
    try:
        assert (await o.refresh(USER, run['id']))['status'] == 'unknown'
        assert not g.starts and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('expiry', [0, 'malformed'])
async def test_snapshot_never_restores_expired_or_malformed_native_approval(tmp_path, expiry):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'known'})
    j.recover()
    g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'known', 'expires_at': expiry}]}
    try:
        await o.refresh(USER, run['id'])
        assert not o.approvals(USER)['items']
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_stale_snapshot_cannot_resolve_new_sibling_stream_approval(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'first'})
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kw):
        entered.set()
        await release.wait()
        return {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'first'}]}
    g.request = delayed
    task = asyncio.create_task(o.refresh(USER, run['id']))
    await entered.wait()
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'newer'})
    release.set()
    try:
        await task
        assert {a['request_id'] for a in o.approvals(USER)['items']} == {'first', 'newer'}
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_progress_resets_inactivity_budget(tmp_path):
    o, j, g = runtime_at(tmp_path, run_timeout=.06)
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        for n in range(6):
            await g.queue.put({'event': 'message.delta', 'delta': str(n)})
            await asyncio.sleep(.02)
        await g.queue.put({'event': 'run.completed', 'run_id': 'upstream', 'output': 'original result'})
        await until(lambda: o.get(USER, run['id'])['status'] == 'completed')
        assert not g.requests
        assert len(g.starts) == 1
    finally:
        await o.close()
