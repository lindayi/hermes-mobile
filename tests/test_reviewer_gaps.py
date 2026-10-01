import asyncio
import json
import time

import pytest

from backend.runs import RunConflict
from test_orchestration import USER, BODY, runtime_at, until
from test_run_tracking_recovery import existing_run


def placeholder(o, j):
    run = existing_run(o, j)
    j.finish(USER['id'], run['id'], 'waiting_for_approval')
    run = o.get(USER, run['id'])
    o._missing_approval(USER, run, 'action')
    return run


@pytest.mark.asyncio
async def test_placeholder_upgrade_keeps_identity_expiry_and_single_intent(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = placeholder(o, j)
    before = o.approvals(USER)['items'][0]
    expires = time.time() + 90
    event = {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'action',
             'tool': 'terminal', 'command': 'pwd', 'expires_at': expires}
    try:
        o._approval(USER, run, event)
        o._approval(USER, run, event)
        with j.connect() as db:
            row = dict(db.execute('SELECT * FROM orchestration_approvals').fetchone())
            intents = db.execute('SELECT approval_id,status FROM approval_notification_intents').fetchall()
        assert row['id'] == before['id']
        assert row['status'] == 'pending'
        assert row['expires_at'] == expires
        assert json.loads(row['action']) == {'tool': 'terminal', 'command': 'pwd'}
        assert [tuple(r) for r in intents] == [(row['id'], 'pending')]
        assert len([e for e in j.events(USER['id'], run['id']) if e['name'] == 'approval']) == 2
        assert not g.starts and not g.stops and not g.requests
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fence', [
    'completed', 'failed', 'cancelled', 'stopping', 'stop_intent', 'wrong_run',
    'wrong_user', 'wrong_profile', 'wrong_session', 'disabled', 'closed', 'ownership',
    'unknown_run', 'sending', 'unknown', 'resolved', 'resolved_external',
    'other_sending', 'other_unknown',
])
async def test_placeholder_upgrade_preserves_fences(tmp_path, fence):
    o, j, g = runtime_at(tmp_path)
    run = placeholder(o, j)
    user = dict(USER)
    event = {'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'action',
             'tool': 'terminal', 'command': 'pwd'}
    if fence in ('completed', 'failed', 'cancelled', 'stopping'):
        j.finish(USER['id'], run['id'], fence)
    elif fence == 'unknown_run':
        j.finish(USER['id'], run['id'], 'unknown')
    elif fence == 'stop_intent':
        with j.connect() as db:
            db.execute('INSERT INTO run_stop_intents VALUES(?)', (run['id'],))
    elif fence == 'wrong_run':
        event['run_id'] = 'foreign'
    elif fence == 'wrong_user':
        user['id'] = 'foreign'
    elif fence == 'wrong_profile':
        event['profile'] = 'foreign'
    elif fence == 'wrong_session':
        event['session_id'] = 'foreign'
    elif fence == 'disabled':
        g.execution_ready = False
    elif fence == 'closed':
        await o.close()
    elif fence == 'ownership':
        o.recovery_validator = lambda user: False
    elif fence.startswith('other_'):
        with j.connect() as db:
            db.execute('INSERT INTO orchestration_approvals VALUES(?,?,?,?,?,?)',
                       ('other', run['id'], 'other', '{}', fence.removeprefix('other_'), time.time()+90))
    else:
        with j.connect() as db:
            db.execute('UPDATE orchestration_approvals SET status=?', (fence,))
    with j.connect() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM orchestration_approvals')]
    status = o.get(USER, run['id'])['status']
    try:
        try:
            o._approval(user, run, event)
        except (KeyError, RunConflict):
            pass
        with j.connect() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM orchestration_approvals')] == before
            assert db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0] == 0
        assert o.get(USER, run['id'])['status'] == status
        assert not g.starts and not g.stops and not g.requests
    finally:
        await o.close()

@pytest.mark.asyncio
async def test_real_bound_sse_can_fill_snapshot_placeholder(tmp_path):
    o, j, g = runtime_at(tmp_path)
    async def capable():
        g.action_approvals_ready = True
    g.require_action_approvals = capable
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                    'pending_approvals': [{'run_id': 'upstream', 'request_id': 'action'}]}
        assert (await o.refresh(USER, run['id']))['status'] == 'waiting_for_approval'
        assert o.approvals(USER)['items'][0]['executable'] is False
        await g.queue.put({'event': 'approval.request', 'run_id': 'upstream',
                          'request_id': 'action', 'tool': 'terminal', 'command': 'pwd'})
        await asyncio.sleep(.03)
        with j.connect() as db:
            row = dict(db.execute('SELECT * FROM orchestration_approvals').fetchone())
        assert row['status'] == 'pending', {'approval': row, 'run': o.get(USER, run['id'])}
        assert o.approvals(USER)['items'][0]['executable'] is True
        assert len(g.starts) == 1 and not g.stops
    finally:
        await o.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('details', [
    pytest.param({}, id='identity-only'),
    pytest.param({'reason': 'Needs approval', 'title': 'Review action'}, id='reason-title-only'),
    pytest.param({'description': 'dangerous command', 'pattern_key': 'fixture',
                  'pattern_keys': ['fixture'], 'allow_permanent': True,
                  'allow_session': True}, id='native-metadata-only'),
    pytest.param({'command': ''}, id='empty'),
    pytest.param({'command': ' \t\n'}, id='blank'),
    pytest.param({'command': None}, id='null'),
    pytest.param({'command': 123}, id='number'),
    pytest.param({'command': True}, id='boolean'),
    pytest.param({'command': ['pwd']}, id='list'),
    pytest.param({'command': {'command': 'pwd'}}, id='object'),
])
@pytest.mark.parametrize('supplied_expiry', [False, True], ids=['absent-expiry', 'valid-expiry'])
@pytest.mark.parametrize('decision', ['once', 'deny'])
async def test_stream_missing_action_cannot_upgrade_or_dispatch(tmp_path, details, supplied_expiry, decision):
    o, j, g = runtime_at(tmp_path)
    decisions = []
    async def capable():
        g.action_approvals_ready = True
    async def approve(*args):
        decisions.append(args)
    g.require_action_approvals, g.approve = capable, approve
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                    'pending_approvals': [{'run_id': 'upstream', 'request_id': 'action'}]}
        assert (await o.refresh(USER, run['id']))['status'] == 'waiting_for_approval'
        before = o.approvals(USER)['items'][0]
        with j.connect() as db:
            stored_before = dict(db.execute('SELECT * FROM orchestration_approvals').fetchone())
        requests_before = list(g.requests)
        event = dict(details, event='approval.request', run_id='upstream', request_id='action')
        if supplied_expiry:
            event['expires_at'] = time.time() + 90
        await g.queue.put(event)
        await g.queue.put(event)
        # A following public delta is a deterministic barrier through the real stream loop.
        await g.queue.put({'event': 'message.delta', 'delta': 'ingestion barrier'})
        await until(lambda: any(e['name'] == 'delta' and e['data']['text'] == 'ingestion barrier'
                                for e in j.events(USER['id'], run['id'])))
        with j.connect() as db:
            stored_after = dict(db.execute('SELECT * FROM orchestration_approvals').fetchone())
            intents = db.execute('SELECT count(*) FROM approval_notification_intents').fetchone()[0]
        view = o.approvals(USER)['items'][0]
        rejected = False
        try:
            await o.decide(USER, before['id'], decision)
        except RunConflict:
            rejected = True
        assert rejected, {'approval': stored_after, 'view': view,
                          'notification_intents': intents, 'dispatched': decisions}
        assert stored_after == stored_before
        assert view == before and view['executable'] is False and view['choices'] == []
        assert view['expires_at'] is None and stored_after['expires_at'] == 0
        assert intents == 0 and decisions == []
        assert len([e for e in j.events(USER['id'], run['id']) if e['name'] == 'approval']) == 1
        assert o.get(USER, run['id'])['status'] == 'waiting_for_approval'
        assert len(g.starts) == 1 and not g.stops and g.requests == requests_before
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('expiry_seconds', [None, 90, 600], ids=['absent-expiry', 'valid-expiry', 'capped-expiry'])
async def test_native_payload_without_tool_can_upgrade_and_dispatch(tmp_path, expiry_seconds):
    o, j, g = runtime_at(tmp_path)
    decisions = []
    async def capable():
        g.action_approvals_ready = True
    async def approve(*args):
        decisions.append(args)
    g.require_action_approvals, g.approve = capable, approve
    try:
        run = await o.submit(USER, BODY)
        await g.started.wait()
        g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                    'pending_approvals': [{'run_id': 'upstream', 'request_id': 'action'}]}
        await o.refresh(USER, run['id'])
        before = o.approvals(USER)['items'][0]
        # tools/approval.py approval_data copied by api_server._approval_notify:
        # command is a redacted display string; neither tool nor arguments is required.
        action = {'command': 'curl -H "Authorization: [REDACTED]" https://example.test',
                  'description': 'Review command', 'pattern_key': 'fixture',
                  'pattern_keys': ['fixture'], 'allow_permanent': True, 'allow_session': True}
        started = time.time()
        event = dict(action, event='approval.request', run_id='upstream', request_id='action',
                     timestamp=started, choices=['once', 'deny', 'session', 'always'])
        if expiry_seconds is not None:
            event['expires_at'] = started + expiry_seconds
        # Invalid details must not poison a subsequent genuinely detailed request.
        await g.queue.put({'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'action'})
        await g.queue.put(event)
        await g.queue.put(event)
        await g.queue.put({'event': 'message.delta', 'delta': 'native payload barrier'})
        await until(lambda: any(e['name'] == 'delta' and e['data']['text'] == 'native payload barrier'
                                for e in j.events(USER['id'], run['id'])))
        with j.connect() as db:
            row = dict(db.execute('SELECT * FROM orchestration_approvals').fetchone())
            intents = db.execute('SELECT approval_id,status FROM approval_notification_intents').fetchall()
        assert row['id'] == before['id'] and row['status'] == 'pending'
        assert json.loads(row['action']) == action
        if expiry_seconds == 90:
            assert row['expires_at'] == event['expires_at']
        else:
            assert started + 300 <= row['expires_at'] <= time.time() + 300
        assert [tuple(r) for r in intents] == [(before['id'], 'pending')]
        assert len([e for e in j.events(USER['id'], run['id']) if e['name'] == 'approval']) == 2
        assert o.approvals(USER)['items'][0]['executable'] is True
        assert decisions == []
        await o.decide(USER, before['id'], 'once')
        assert decisions == [('upstream', 'action', 'once')]
        assert o.get(USER, run['id'])['status'] == 'running'
        assert len(g.starts) == 1 and not g.stops
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_deciding_known_action_does_not_hide_other_missing_action(tmp_path):
    o, j, g = runtime_at(tmp_path)
    async def capable():
        g.action_approvals_ready = True
    decisions = []
    async def approve(*args):
        decisions.append(args)
    g.require_action_approvals, g.approve = capable, approve
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream',
                          'request_id': 'known', 'tool': 'terminal', 'command': 'pwd'})
    g.status = {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': key}
                                      for key in ('known', 'missing')]}
    try:
        await o.refresh(USER, run['id'])
        actions = o.approvals(USER)['items']
        assert {a['request_id'] for a in actions} == {'known', 'missing'}
        await o.decide(USER, next(a['id'] for a in actions if a['request_id'] == 'known'), 'once')
        assert decisions == [('upstream', 'known', 'once')]
        assert o.get(USER, run['id'])['status'] == 'waiting_for_approval'
        assert [a['request_id'] for a in o.approvals(USER)['items']] == ['missing']
    finally:
        await o.close()

@pytest.mark.asyncio
async def test_legacy_notification_observer_rechecks_execution_after_await(tmp_path):
    o, j, g = runtime_at(tmp_path)
    run = existing_run(o, j)
    j.set_upstream(USER['id'], run['id'], 'upstream')
    o._approval(USER, run, {'event': 'approval.request', 'run_id': 'upstream',
                          'request_id': 'known', 'tool': 'terminal', 'command': 'pwd'})
    j.recover()
    o.approval_validator = lambda *args: None
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'run_id': 'upstream', 'status': 'waiting_for_approval',
                'pending_approvals': [{'run_id': 'upstream', 'request_id': 'known'}]}
    g.request = delayed
    task = asyncio.create_task(o.reconcile_approval_notifications())
    await entered.wait()
    g.execution_ready = False
    release.set()
    try:
        await task
        assert o.get(USER, run['id'])['status'] == 'unknown'
    finally:
        await o.close()
