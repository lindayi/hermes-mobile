import asyncio
import pytest
from backend.hermes_client import IntegrationUnavailable
from backend.runs import RunConflict
from test_action_approval import pending_approval
from test_orchestration import USER, until


@pytest.mark.asyncio
async def test_failed_decision_is_durably_unknown_and_never_retried(tmp_path):
    o, j, g, run, item = await pending_approval(tmp_path, fail=True)
    try:
        with pytest.raises(IntegrationUnavailable, match='outcome is unresolved'):
            await o.decide(USER, item['id'], 'once')
        assert o.get(USER, run['id'])['status'] == 'unknown'
        with j.connect() as c:
            assert c.execute('SELECT status FROM orchestration_approvals WHERE id=?', (item['id'],)).fetchone()[0] == 'unknown'
        with pytest.raises(RunConflict):
            await o.decide(USER, item['id'], 'once')
        assert len(g.decisions) == 1
        assert 'unresolved' in o.get(USER, run['id'])['error']
        await g.queue.put({'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'next', 'command': 'next action'})
        await asyncio.sleep(0.02)
        assert o.get(USER, run['id'])['status'] == 'unknown'
        assert o.approvals(USER)['items'] == []
        assert [e for e in j.events(USER['id'], run['id']) if e['name'] == 'approval'][-1]['data']['executable'] is False
    finally:
        await o.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('event', [
    {'run_id': 'upstream', 'request_id': 'stale'},
    {'run_id': 'foreign', 'request_id': 'action'},
])
async def test_mismatched_approval_event_cannot_resolve_current_action(tmp_path, event):
    o, j, g, run, item = await pending_approval(tmp_path)
    try:
        await g.queue.put(dict(event, event='approval.responded', choice='once', resolved=1))
        await asyncio.sleep(0.02)
        assert o.approvals(USER)['items'][0]['id'] == item['id']
        assert o.get(USER, run['id'])['status'] == 'waiting_for_approval'
        assert not g.decisions
    finally:
        await o.close()


@pytest.mark.asyncio
async def test_exact_external_reply_resolves_only_its_action(tmp_path):
    o, j, g, run, item = await pending_approval(tmp_path)
    try:
        await g.queue.put({'event': 'approval.request', 'run_id': 'upstream', 'request_id': 'second', 'command': 'second action'})
        await until(lambda: len(o.approvals(USER)['items']) == 2)
        await g.queue.put({'event': 'approval.responded', 'run_id': 'upstream', 'request_id': 'action', 'choice': 'once', 'resolved': 1})
        await asyncio.sleep(0.02)
        assert [i['request_id'] for i in o.approvals(USER)['items']] == ['second']
        with pytest.raises(RunConflict):
            await o.decide(USER, item['id'], 'once')
    finally:
        await o.close()
