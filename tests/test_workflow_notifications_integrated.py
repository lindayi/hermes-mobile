"""PR32/current-main boundary regressions; isolated synthetic state only."""
from copy import deepcopy
import json
import os
import sqlite3

import pytest

from deploy import workflow_notifications as adapter
from test_workflow_notifications import (
    NOW, adapter_fixture, event, export, _write_deployed_proof, _assert_deployment_deferred,
)


def worker_proof(tmp_path, monkeypatch, *, risk='routine', bootstrap=False, duplicate=False):
    # Invoke the incoming worker, with its real intent validation/finish/save_state.
    # Existing synthetic fixture replaces ALL Git, API and controller execution.
    from test_pull_delivery import worker_v2, raw, SHA, BASE, NOW as WORKER_NOW
    root = tmp_path / 'worker'
    root.mkdir()
    worker, paths, _, api, effects, run, post = worker_v2(
        root, monkeypatch, risk, base=None if bootstrap else BASE,
        local=None if bootstrap else BASE,
        diff=raw(('M', 'backend/app.py' if risk == 'sensitive' else 'frontend/styles.css')))
    result = worker.poll_once(paths, get=api.__getitem__, post=post, run=run, now=WORKER_NOW)
    assert result['status'] == 'deployed', result
    assert len([effect for effect in effects if effect[0] == 'run']) == 1
    if duplicate:
        assert worker.poll_once(paths, get=api.__getitem__, post=post, run=run,
                                now=WORKER_NOW)['status'] == 'duplicate'
    ledger = json.loads((paths.state / 'state.json').read_text())
    deployed = event('deployed', 'controller_verified', pr_number=32,
                     head_sha='d' * 40, merge_sha=SHA)
    fixture = adapter_fixture(tmp_path / 'adapter', export(deployed))
    consumer_paths = fixture[0]
    _write_deployed_proof(consumer_paths, SHA)
    consumer_paths.delivery_state.write_text(json.dumps(ledger))
    os.utime(consumer_paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))
    return fixture, deployed, ledger


@pytest.mark.parametrize(('risk', 'bootstrap'), [('routine', False), ('sensitive', False), ('sensitive', True)])
@pytest.mark.parametrize('duplicate', [False, True])
def test_actual_worker_v2_terminal_is_consumed(tmp_path, monkeypatch, risk, bootstrap, duplicate):
    fixture, _, _ = worker_proof(tmp_path, monkeypatch, risk=risk, bootstrap=bootstrap, duplicate=duplicate)
    paths, _, _, inbox, _, _ = fixture
    assert adapter.process(paths, now=NOW) == {'status': 'plan', 'events': 1, 'writes': False}
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 1
    assert adapter.process(paths, apply=True, now=NOW)['inbox_items'] == 0
    with sqlite3.connect(inbox) as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (1,)


@pytest.mark.parametrize(('risk', 'bootstrap'), [
    ('routine', False), ('sensitive', False), ('sensitive', True),
])
def test_cli_lifecycle_export_uses_real_v2_ledger_and_reaches_owner_inbox(
        tmp_path, monkeypatch, capsys, risk, bootstrap):
    from datetime import datetime, timezone

    from deploy.cloud_coordinator import StateStore, main
    from deploy.workflow_lifecycle_sources import LifecycleSourcePaths
    from test_pull_delivery import BASE, NOW as WORKER_NOW, SHA, raw, worker_v2
    from test_workflow_notifications import NOW, _write_deployed_proof

    worker_root = tmp_path / 'worker'
    worker_root.mkdir(mode=0o700)
    producer, worker_paths, _, api, _, run, post = worker_v2(
        worker_root, monkeypatch, risk,
        base=None if bootstrap else BASE,
        local=None if bootstrap else BASE,
        diff=raw(('M', 'backend/app.py' if risk == 'sensitive' else 'frontend/styles.css')),
    )
    result = producer.poll_once(
        worker_paths, get=api.__getitem__, post=post, run=run, now=WORKER_NOW,
    )
    assert result['status'] == 'deployed', result
    assert producer.poll_once(
        worker_paths, get=api.__getitem__, post=post, run=run, now=WORKER_NOW,
    )['status'] == 'duplicate'
    ledger = json.loads((worker_paths.state / 'state.json').read_text())

    fixture = adapter_fixture(tmp_path / 'adapter', export(event()))
    consumer_paths, state_dir, _, inbox, export_path, notifications = fixture
    _write_deployed_proof(consumer_paths, SHA)
    consumer_paths.delivery_state.write_text(json.dumps(ledger))
    consumer_paths.delivery_state.chmod(0o600)
    os.utime(consumer_paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))

    class ClosedPullApi:
        issue = {'number': 16, 'pull_request': {'url': 'pull/16'}}
        comment = {
            'id': 123, 'user': {'id': 5164171}, 'body': '/hermes enroll',
            'updated_at': '2026-10-01T11:00:00Z',
        }
        pull = {
            'number': 16, 'id': 160000016, 'node_id': 'PR_node_16',
            'state': 'closed', 'merged': True, 'merge_commit_sha': SHA,
            'merged_at': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'head': {'sha': 'a' * 40, 'ref': 'topic', 'repo': {'id': 1399942965}},
            'base': {'sha': BASE, 'ref': 'main', 'repo': {'id': 1399942965}},
        }

        def get(self, route):
            if route == 'repos/lindayi/hermes-mobile':
                return {'id': 1399942965}
            if route == 'user':
                return {'id': 5164171}
            if route == 'repos/lindayi/hermes-mobile/commits/main':
                return {'sha': BASE}
            if route == 'repos/lindayi/hermes-mobile/pulls/16':
                return self.pull
            raise AssertionError(f'Unexpected API read: {route}')

        def get_all(self, route, *, collection=None):
            if route.startswith('repos/lindayi/hermes-mobile/issues?'):
                return [self.issue]
            if route.startswith('repos/lindayi/hermes-mobile/issues/16/comments?'):
                return [self.comment]
            raise AssertionError(f'Unexpected API list: {route}')

    coordinator_state = tmp_path / 'coordinator' / 'state.json'
    seed = StateStore(coordinator_state)
    seed.enroll({
        'issue': 16, 'comment': 123, 'head': 'a' * 40, 'base': BASE,
        'pull_id': 160000016, 'pull_node_id': 'PR_node_16',
        'repository_id': 1399942965, 'last_open_seen': True,
    })
    source_paths = LifecycleSourcePaths(
        notifications=consumer_paths,
        starter_state=tmp_path / 'starter-state.json',
    )
    assert main(
        ['--once', '--apply', '--state', str(coordinator_state)],
        api_factory=ClosedPullApi,
        store_factory=StateStore,
        lifecycle_source_paths_factory=lambda: source_paths,
    ) == 0
    capsys.readouterr()

    unchanged_export = export_path.read_bytes()
    payload = json.loads(unchanged_export)
    assert {item['reason'] for item in payload['events']} >= {'merged', 'controller_verified'}
    now = datetime.now(timezone.utc)
    assert adapter.process(consumer_paths, now=now)['writes'] is False
    assert adapter.process(consumer_paths, apply=True, now=now)['inbox_items'] == 2
    assert adapter.process(consumer_paths, apply=True, now=now)['inbox_items'] == 0
    assert export_path.read_bytes() == unchanged_export
    with sqlite3.connect(inbox) as db:
        recipients = db.execute('SELECT DISTINCT user_id FROM inbox').fetchall()
        assert recipients == [('owner-user',)]
        assert db.execute('SELECT count(*) FROM inbox').fetchone() == (2,)
    assert notifications.list_inbox('member-user') == []
    assert state_dir == consumer_paths.config.parent / 'app-state'


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2.0}, {'version': 1}, {'version': 3},
    {'risk': []}, {'risk': 'other'}, {'risk': None},
    {'base_sha': None}, {'base_sha': True}, {'base_sha': 'B' * 40}, {'base_sha': 'b' * 39},
    {'unknown': 'field'}, {'remove': 'version'}, {'remove': 'risk'}, {'remove': 'base_sha'},
])
def test_worker_v2_terminal_requires_exact_typed_metadata(tmp_path, monkeypatch, change):
    fixture, deployed, ledger = worker_proof(tmp_path, monkeypatch)
    paths, state_dir, _, inbox, _, _ = fixture
    record = next(iter(ledger['records'].values()))
    if 'remove' in change:
        record.pop(change['remove'])
    else:
        record.update(change)
    ledger['last'] = deepcopy(record)
    paths.delivery_state.write_text(json.dumps(ledger))
    os.utime(paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))
    _assert_deployment_deferred(paths, state_dir, inbox, deployed)


@pytest.mark.parametrize('hazard', ['latest', 'last', 'current', 'provenance', 'stale_ledger', 'stale_controller'])
def test_worker_v2_keeps_current_provenance_and_freshness_gates(tmp_path, monkeypatch, hazard):
    fixture, deployed, ledger = worker_proof(tmp_path, monkeypatch)
    paths, state_dir, _, inbox, _, _ = fixture
    if hazard == 'latest':
        ledger['latest_id'] += 1
    elif hazard == 'last':
        ledger['last'] = {'status': 'queued'}
    elif hazard == 'current':
        (paths.controller_state / 'current').unlink()
    elif hazard == 'provenance':
        (paths.controller_state / 'current' / 'git-provenance.json').write_text(json.dumps({'git_sha': 'f' * 40}))
    paths.delivery_state.write_text(json.dumps(ledger))
    os.utime(paths.delivery_state, (NOW.timestamp(), NOW.timestamp()))
    if hazard.startswith('stale_'):
        target = paths.delivery_state if hazard == 'stale_ledger' else paths.controller_state / 'status.json'
        old = NOW.timestamp() - adapter.MAX_EVENT_AGE - 1
        os.utime(target, (old, old))
    _assert_deployment_deferred(paths, state_dir, inbox, deployed)


@pytest.mark.parametrize('change', ['envelope_age', 'file_age', 'replacement', 'byte_change', 'late_invalid'])
@pytest.mark.parametrize('established', [False, True])
def test_export_revalidated_after_real_lock_wait(tmp_path, monkeypatch, change, established):
    import fcntl
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta
    from threading import Event

    approval = event('approval_required', 'sensitive_approval', pr_number=32,
                     head_sha='a' * 40, decision='approve_production')
    payload = export(event(), approval)
    paths, state_dir, _, inbox, event_path, _ = adapter_fixture(tmp_path, payload)
    state = state_dir / adapter.ADAPTER_STATE_NAME
    if established:
        adapter._initialize_state(state, 'owner-user')
    if change == 'envelope_age':
        payload['generated_at'] = (NOW - timedelta(seconds=adapter.MAX_EVENT_AGE - 1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for item in payload['events']:
            item['occurred_at'] = payload['generated_at']
        event_path.write_text(json.dumps(payload))
        os.utime(event_path, (NOW.timestamp(), NOW.timestamp()))
    elif change == 'file_age':
        old = NOW.timestamp() - adapter.MAX_EVENT_AGE + 1
        os.utime(event_path, (old, old))
    current = [NOW]

    class WallClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    monkeypatch.setattr(adapter, 'datetime', WallClock)
    before = {p: p.read_bytes() for p in (inbox, state) if p.exists()}
    waiting = Event()
    real_flock = fcntl.flock
    lock_fd = os.open(state_dir / adapter.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    real_flock(lock_fd, fcntl.LOCK_EX)

    def announce(fd, operation):
        waiting.set()
        return real_flock(fd, operation)

    monkeypatch.setattr(adapter.fcntl, 'flock', announce)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(adapter.process, paths, apply=True)
            try:
                assert waiting.wait(10)
                assert not future.done()
                current[0] += timedelta(seconds=2)
                if change == 'replacement':
                    event_path.write_text(json.dumps(export(event(event_id='replacement'))))
                elif change == 'byte_change':
                    event_path.write_bytes(event_path.read_bytes() + b'\n')
                elif change == 'late_invalid':
                    payload['events'][-1]['unknown'] = True
                    event_path.write_text(json.dumps(payload))
                if change not in ('file_age', 'envelope_age'):
                    os.utime(event_path, (current[0].timestamp(), current[0].timestamp()))
            finally:
                real_flock(lock_fd, fcntl.LOCK_UN)
            with pytest.raises(adapter.Blocked):
                future.result(timeout=10)
    finally:
        os.close(lock_fd)
    assert {p: p.read_bytes() for p in before} == before
    assert state.exists() == established
