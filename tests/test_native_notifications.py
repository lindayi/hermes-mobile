"""Synthetic-only durable notification tests: never import live SDK registries."""
import importlib
import json
import sqlite3

import pytest


def module():
    return importlib.import_module('backend.native_notifications')


def event(id='deleg_one', **changes):
    return dict(type='async_delegation', delegation_id=id, session_key='run_native',
                origin_session_id='session_a', summary='finished', **changes)


def test_exact_full_result_survives_reopen_and_restored_duplicate(tmp_path):
    m = module()
    box = m.NotificationOutbox(tmp_path / 'outbox.sqlite')
    result = {'summary': 'full result', 'private': {'all': [1, 2, 3]}}
    key = box.capture(event(), result, route='owned')
    assert key == 'async:deleg_one'
    assert box.capture(event(restored=True), result, route='owned') == key
    reopened = m.NotificationOutbox(tmp_path / 'outbox.sqlite')
    saved = reopened.record(key)
    assert saved['event'] == event()
    assert saved['result'] == result
    assert saved['state'] == 'pending'
    assert reopened.status()['pending'] == 1
    assert (tmp_path / 'outbox.sqlite').stat().st_mode & 0o077 == 0


def test_lease_cas_conflicts_and_lost_ack_restart(tmp_path):
    m = module()
    now = [100.0]
    box = m.NotificationOutbox(tmp_path / 'outbox.sqlite', clock=lambda: now[0])
    box.capture(event('conflicting'), {'full': True}, route='owned')
    with pytest.raises(m.NotificationConflict):
        box.capture({**event('conflicting'), 'summary': 'changed'}, {'full': True}, route='owned')
    with pytest.raises(m.NotificationConflict):
        box.capture(event('conflicting'), {'full': False}, route='owned')
    with pytest.raises(m.NotificationConflict):
        box.capture(event('conflicting'), {'full': True}, route='foreign')
    assert box.claim(20) == []
    box.capture(event(), {'full': True}, route='owned')
    first = box.claim(20)[0]
    other = m.NotificationOutbox(box.path, clock=lambda: now[0])
    assert other.claim(20) == []
    now[0] += 61
    second = other.claim(20)[0]
    with pytest.raises(m.NotificationConflict):
        box.ack(**{k: first[k] for k in ('event_id', 'payload_sha256', 'lease_token')}, receipt_id='receipt')
    args = {k: second[k] for k in ('event_id', 'payload_sha256', 'lease_token')}
    with pytest.raises(m.NotificationConflict):
        box.ack(**{**args, 'payload_sha256': '0'*64}, receipt_id='receipt')
    expected = {'status': 'delivered', 'event_id': 'async:deleg_one', 'receipt_id': 'receipt'}
    assert box.ack(**args, receipt_id='receipt') == expected
    assert other.ack(**args, receipt_id='receipt') == expected
    with pytest.raises(m.NotificationConflict):
        other.ack(**args, receipt_id='different')
    assert box.status()['delivered'] == 1
    assert box.claim(20) == []


def test_ack_fails_closed_if_conflict_arrives_after_lease(tmp_path):
    m = module()
    box = m.NotificationOutbox(tmp_path/'box.sqlite')
    box.capture(event(), {'full': 1}, route='owned')
    lease = box.claim()[0]
    with pytest.raises(m.NotificationConflict):
        box.capture({**event(), 'summary': 'conflicting payload'}, {'full': 2}, route='owned')
    assert box.status()['conflicts'] == 1
    with pytest.raises(m.NotificationConflict):
        box.ack(**{k: lease[k] for k in ('event_id', 'payload_sha256', 'lease_token')},
                receipt_id='synthetic-receipt')
    assert box.record('async:deleg_one')['state'] == 'pending'
    assert box.record('async:deleg_one')['receipt_id'] is None
    assert box.claim() == []


def test_ordinary_identity_quarantine_and_explicit_recovery_import(tmp_path):
    m = module()
    box = m.NotificationOutbox(tmp_path / 'outbox.sqlite')
    proc = {'type': 'completion', 'session_id': 'proc1', 'started_at': 12.5, 'session_key': 'run_native'}
    assert box.capture(proc, route='owned') == 'process:proc1:12.5'
    pattern = {'type': 'pattern', 'session_id': 'proc1', 'output': 'same'}
    key = box.capture(pattern, route='owned', migration_id='archive-1')
    assert key == 'migration:archive-1'
    assert box.capture(pattern, route='owned', migration_id='archive-1') == key
    unknown = box.capture(pattern, route='quarantined')
    assert box.record(unknown)['state'] == 'quarantined'
    records = [{'event': event(), 'result': {'exact': True}, 'provenance': 'verified-backup:sha256'}]
    assert box.import_records(records, classify=lambda e: 'owned') == ['async:deleg_one']
    assert box.import_records(records, classify=lambda e: 'owned') == ['async:deleg_one']
    assert box.record('async:deleg_one')['historical'] == 1
    assert box.record('async:deleg_one')['result'] == {'exact': True}
    assert len(box.claim()) == 3


def routing_fixture(tmp_path):
    home, state = tmp_path / 'home', tmp_path / 'bridge'
    home.mkdir(); state.mkdir()
    with sqlite3.connect(state / 'auth.sqlite') as db:
        db.execute('CREATE TABLE users(id,role,profile,status)')
        db.execute("INSERT INTO users VALUES('owner','owner','default','ready')")
    with sqlite3.connect(state / 'runs.sqlite') as db:
        db.execute('CREATE TABLE runs(id,user_id,profile,session_id,upstream_id)')
        db.execute("INSERT INTO runs VALUES('run','owner','default','session_a','run_native')")
        db.execute('CREATE TABLE session_deletions(user_id,profile,session_id)')
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('''CREATE TABLE sessions(id,parent_session_id,source,profile_name,
            model_config,session_key,origin_json,chat_id,chat_type,thread_id,end_reason)''')
        db.execute("INSERT INTO sessions VALUES('session_a',NULL,'api_server','default','{}',NULL,NULL,NULL,NULL,NULL,NULL)")
    return home, state


@pytest.fixture
def observe_route_queries(monkeypatch):
    """Instrument real resolver SQLite cursors, not the callback outbox reader."""
    from collections import Counter
    from contextlib import contextmanager
    from pathlib import Path

    observed = {'opened': Counter(), 'rows': Counter(), 'sessions': []}
    original = module().readonly

    class BoundedCursor:
        def __init__(self, cursor, kind):
            self.cursor, self.kind = cursor, kind

        def fetchall(self):
            pytest.fail(f'{self.kind} route query must not fetchall fanout')

        def fetchone(self):
            row = self.cursor.fetchone()
            if row is not None:
                observed['rows'][self.kind] += 1
            return row

        def __iter__(self):
            return self

        def __next__(self):
            row = self.fetchone()
            if row is None:
                raise StopIteration
            return row

    class ObservedDatabase:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, values=()):
            assert self.db.in_transaction
            cursor = self.db.execute(sql, values)
            normalized = ' '.join(sql.split())
            if 'FROM runs' in normalized and 'upstream_id IN' in normalized:
                return BoundedCursor(cursor, 'runs')
            if 'FROM sessions WHERE parent_session_id=' in normalized:
                return BoundedCursor(cursor, 'children')
            if 'FROM sessions WHERE id=' in normalized:
                observed['sessions'].append(values[0])
            return cursor

    @contextmanager
    def readonly(path):
        observed['opened'][Path(path).name] += 1
        with original(path) as db:
            yield ObservedDatabase(db)

    monkeypatch.setattr(module(), 'readonly', readonly)
    return observed


@pytest.mark.parametrize('fanout', [2, 9])
@pytest.mark.parametrize('duplicate_owner', ['owner', 'member'])
def test_bounded_route_rejects_run_fanout_before_lineage(
        tmp_path, observe_route_queries, fanout, duplicate_owner):
    from deploy.native_notification_release import _resolve_routes

    home, state = routing_fixture(tmp_path)
    with sqlite3.connect(state / 'runs.sqlite') as db:
        db.executemany('INSERT INTO runs VALUES(?,?,?,?,?)', [
            (f'duplicate_{index}', duplicate_owner, 'default', f'unwalked_{index}',
             'run_native') for index in range(fanout - 1)])
        db.execute("INSERT INTO runs VALUES('unique','owner','default','session_a','unique')")
        # A different profile must not make the default-profile key ambiguous.
        db.execute("INSERT INTO runs VALUES('other','owner','other','unwalked','unique')")
        db.execute("INSERT INTO runs VALUES('member','member','default','unwalked','member')")
    events = [{**event(), 'session_key': key}
              for key in ('run_native', 'unique', 'member', 'missing')]
    assert _resolve_routes(events, home, state) == ('owner', [
        ('quarantined', None), ('owned', 'session_a'),
        ('quarantined', None), ('quarantined', None)])
    observed = observe_route_queries
    assert observed['rows']['runs'] <= len(events)
    # Ambiguous and nonowner keys must be rejected before any lineage walk.
    assert observed['sessions'] == ['session_a']
    assert observed['opened'] == {'auth.sqlite': 1, 'runs.sqlite': 1, 'state.db': 1}


@pytest.mark.parametrize('eligible_count', [0, 1, 2, 9])
def test_bounded_route_child_ambiguity_counts_only_eligible_rows(
        tmp_path, observe_route_queries, eligible_count):
    from deploy.native_notification_release import _resolve_routes

    home, state = routing_fixture(tmp_path)
    with sqlite3.connect(home / 'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='session_a'")
        # These precede the real continuation: LIMIT on raw children would lose
        # the unique eligible route or falsely accept a later ambiguous family.
        children = [('tool', 'tool', '{}'), ('subagent', 'subagent', '{}'),
                    ('branch', 'api_server', '{"_branched_from":"session_a"}'),
                    ('delegate', 'api_server', '{"_delegate_from":"session_a"}')]
        children.extend((f'child_{index}', 'api_server', '{}')
                        for index in range(eligible_count))
        db.executemany('''INSERT INTO sessions VALUES(
            ?,'session_a',?,'default',?,NULL,NULL,NULL,NULL,NULL,NULL)''', children)
    expected = ('owned', 'child_0') if eligible_count == 1 else ('quarantined', None)
    assert _resolve_routes([event()], home, state) == ('owner', [expected])
    observed = observe_route_queries
    assert observed['rows']['children'] == min(eligible_count, 2)
    assert observed['sessions'] == (['session_a', 'child_0'] if eligible_count == 1
                                    else ['session_a'])
    assert observed['opened'] == {'auth.sqlite': 1, 'runs.sqlite': 1, 'state.db': 1}


def test_positive_route_lineage_foreign_and_deletion(tmp_path):
    home, state = routing_fixture(tmp_path)
    classify = module().OwnerRoute(home, state)
    assert classify(event()) == 'owned'
    assert classify({**event(), 'session_key': 'agent:whatsapp:chat'}) == 'foreign'
    assert classify({**event(), 'origin_session_id': 'missing'}) == 'quarantined'
    assert classify({**event(), 'session_key': 'unknown'}) == 'quarantined'
    with sqlite3.connect(home / 'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='session_a'")
        db.execute("INSERT INTO sessions VALUES('session_b','session_a','api_server','default','{}',NULL,NULL,NULL,NULL,NULL,NULL)")
        db.execute("INSERT INTO sessions VALUES('branch','session_a','api_server','default','{\"_branched_from\":\"session_a\"}',NULL,NULL,NULL,NULL,NULL,NULL)")
    assert classify({**event(), 'origin_session_id': 'session_b', 'parent_session_id': 'session_a'}) == 'owned'
    assert classify({**event(), 'origin_session_id': 'branch'}) == 'quarantined'
    with sqlite3.connect(state / 'runs.sqlite') as db:
        db.execute("INSERT INTO session_deletions VALUES('owner','default','session_a')")
    assert classify(event()) == 'quarantined'


def test_batched_route_resolution_preserves_family_and_negative_evidence(tmp_path):
    from deploy.native_notification_release import _resolve_routes

    home, state = routing_fixture(tmp_path)
    with sqlite3.connect(home / 'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='session_a'")
        db.execute("INSERT INTO sessions VALUES('session_b','session_a','api_server','default','{}',NULL,NULL,NULL,NULL,NULL,NULL)")
        db.execute("INSERT INTO sessions VALUES('branch','session_a','api_server','default','{\"_branched_from\":\"session_a\"}',NULL,NULL,NULL,NULL,NULL,NULL)")
    owner, routes = _resolve_routes([
        event(),
        {**event('deleg_family'), 'origin_session_id': 'session_b',
         'parent_session_id': 'session_a'},
        {**event('deleg_branch'), 'origin_session_id': 'branch'},
        {**event('deleg_wrong_session'), 'origin_session_id': 'missing'},
        {**event('deleg_foreign'), 'session_key': 'telegram:chat'},
        event('deleg_api_alias', platform='api'),
    ], home, state)

    assert owner == 'owner'
    assert routes == [
        ('owned', 'session_b'),
        ('owned', 'session_b'),
        ('quarantined', None),
        ('quarantined', None),
        ('foreign', None),
        ('owned', 'session_b'),
    ]
    with sqlite3.connect(state / 'runs.sqlite') as db:
        db.execute("INSERT INTO session_deletions VALUES('owner','default','session_a')")
    assert _resolve_routes([event()], home, state)[1] == [('quarantined', None)]


@pytest.mark.parametrize('field', ['chat_id', 'chat_type', 'thread_id', 'user_id'])
def test_explicit_foreign_route_is_retained_without_source_claim(tmp_path, field):
    from backend.background_delivery import BackgroundDeliveryService
    sdk, registry, box, capture = capture_fixture(tmp_path)
    assert capture.classify(event()) == 'owned'  # all positive ownership proof valid
    evt = event(**{field: 'foreign-route'})
    with pytest.raises(PermissionError, match='Foreign route'):
        BackgroundDeliveryService._route(None, None, evt, None)
    try:
        sdk._persist_completion(evt, {'exact': 'foreign result'})
        registry.completion_queue.put(evt)
        assert sdk.calls == []
        assert capture.classify(evt) == 'foreign'
        assert capture.source_row(evt)['delivery_state'] == 'pending'
        saved = box.record('async:deleg_one')
        assert saved['state'] == 'foreign' and saved['source_state'] == 'foreign-retained'
        assert saved['result'] == {'exact': 'foreign result'}
        assert capture.claim() == []
    finally:
        capture.close()


def test_api_platform_alias_matches_bridge_with_positive_ownership_proof(tmp_path):
    from contextlib import closing
    from test_background_delivery import Fixture
    bridge_path = tmp_path / 'bridge-proof'
    bridge_path.mkdir()
    bridge = Fixture(bridge_path)
    with closing(bridge.journal.connect()) as db:
        assert bridge.service()._route(bridge.user,
            {**bridge.items[0]['event'], 'platform': 'api'}, db) == 'chat'
    sdk, registry, box, capture = capture_fixture(tmp_path)
    evt = event(platform='api', scope_id='')
    try:
        assert capture.classify(evt) == 'owned'
        sdk._persist_completion(evt, {'exact': 'owned result'})
        registry.completion_queue.put(evt)
        assert len(capture.claim()) == 1
        assert capture.source_row(evt)['delivery_state'] == 'delivered'
        assert box.record('async:deleg_one')['state'] == 'pending'
        assert capture.classify({**evt, 'origin_session_id': 'unproved'}) == 'quarantined'
        assert capture.classify({**evt, 'session_key': 'unproved'}) == 'quarantined'
    finally:
        capture.close()


@pytest.mark.parametrize('has_continuation', [False, True])
def test_subagent_child_is_not_a_compression_continuation(tmp_path, has_continuation):
    home, state = routing_fixture(tmp_path)
    with sqlite3.connect(home/'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id='session_a'")
        db.execute("INSERT INTO sessions VALUES('child','session_a','subagent','default','{}',NULL,NULL,NULL,NULL,NULL,NULL)")
        if has_continuation:
            db.execute("INSERT INTO sessions VALUES('session_b','session_a','api_server','default','{}',NULL,NULL,NULL,NULL,NULL,NULL)")
    classify = module().OwnerRoute(home, state)
    assert classify({**event(), 'origin_session_id': 'child'}) == 'quarantined'
    assert classify(event()) == ('owned' if has_continuation else 'quarantined')
    if has_continuation:
        assert classify({**event(), 'origin_session_id': 'session_b'}) == 'owned'
    with sqlite3.connect(state/'runs.sqlite') as db:
        db.execute("UPDATE runs SET session_id='child'")
    assert classify({**event(), 'origin_session_id': 'child'}) == 'quarantined'


class SyntheticSDK:
    """The real SQLite source CAS shape, injectable without global SDK imports."""
    def __init__(self, home):
        self.home = home
        self.calls = []
        with sqlite3.connect(self._db_path()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS async_delegations(delegation_id PRIMARY KEY,
                event_json,result_json,delivery_state,delivery_claim)''')

    def _db_path(self):
        return self.home / 'state.db'

    def _persist_completion(self, evt, result):
        with sqlite3.connect(self._db_path()) as db:
            db.execute("INSERT OR REPLACE INTO async_delegations VALUES(?,?,?,'pending',NULL)",
                       (evt['delegation_id'], json.dumps(evt), json.dumps(result)))

    def claim_completion_delivery(self, id, token):
        self.calls.append(('claim', id))
        with sqlite3.connect(self._db_path()) as db:
            return db.execute("UPDATE async_delegations SET delivery_claim=? WHERE delegation_id=? AND delivery_state='pending' AND delivery_claim IS NULL", (token,id)).rowcount == 1

    def complete_completion_delivery(self, id, token):
        self.calls.append(('complete', id))
        with sqlite3.connect(self._db_path()) as db:
            return db.execute("UPDATE async_delegations SET delivery_state='delivered',delivery_claim=NULL WHERE delegation_id=? AND delivery_state='pending' AND delivery_claim=?", (id,token)).rowcount == 1


def capture_fixture(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    registry = SimpleNamespace(completion_queue=queue.Queue())
    box = m.NotificationOutbox(state/'outbox.sqlite')
    capture = m.NotificationCapture(box, home, m.OwnerRoute(home, state))
    capture.install(sdk, registry)
    return sdk, registry, box, capture


def test_inflight_queue_put_is_not_lost_during_close(tmp_path):
    import threading
    sdk, registry, box, capture = capture_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    matches = capture._matches_home
    def pause_before_worker_registration():
        entered.set()
        assert release.wait(5)
        return matches()
    capture._matches_home = pause_before_worker_registration
    evt = dict(type='completion', session_id='process-race', started_at=1,
               session_key='run_native', task_id='session_a', output='exact tail')
    errors = []
    def producer():
        try:
            registry.completion_queue.put(evt)
        except BaseException as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=producer)
    thread.start()
    try:
        assert entered.wait(5)
        capture.close()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    saved = box.record('process:process-race:1')
    assert not errors and saved, {'errors': errors, 'outbox_saved': saved is not None,
                                  'queue_size': registry.completion_queue.qsize()}
    assert saved['event'] == evt and saved['state'] == 'pending'
    assert saved['receipt_id'] is None and sdk.calls == []
    assert capture.evidence()['shutdown_publications'] == 1
    assert capture.evidence()['active_workers'] == 0


@pytest.mark.parametrize('foreign', [False, True])
def test_closed_publisher_retains_exact_payload_before_sdk(tmp_path, foreign):
    sdk, registry, box, capture = capture_fixture(tmp_path)
    evt = {**event(), 'session_key': 'agent:whatsapp:chat'} if foreign else event()
    result = {'full': ['exact', {'private': True}]}
    original = capture.original_publish
    def verify_before_sdk(evt, result):
        assert box.record('async:deleg_one')['result'] == result
        assert capture.source_row(evt) is None
        assert capture.evidence()['active_workers'] == 1
        assert capture.evidence()['shutdown_publications'] == 1
        return original(evt, result)
    capture.original_publish = verify_before_sdk
    late_publish, late_queue = sdk._persist_completion, registry.completion_queue
    capture.close()
    late_publish(evt, result)
    late_queue.put(evt)
    saved = box.record('async:deleg_one')
    assert saved['event'] == evt and saved['result'] == result
    assert saved['state'] == ('foreign' if foreign else 'pending')
    assert saved['receipt_id'] is None and box.status()['delivered'] == 0
    assert capture.evidence()['active_workers'] == 0
    assert capture.evidence()['shutdown_publications'] == 2
    if foreign:
        assert sdk.calls == [] and capture.source_row(evt)['delivery_state'] == 'pending'
        assert box.claim() == []
    with pytest.raises(RuntimeError, match='closing'):
        capture.claim()


def test_closed_capture_rejects_reinstall_before_mutating_hooks(tmp_path):
    sdk, registry, box, capture = capture_fixture(tmp_path)
    capture.close()
    publish, queue = sdk._persist_completion, registry.completion_queue
    with pytest.raises(RuntimeError, match='closed.*fresh'):
        capture.install(sdk, registry)
    assert sdk._persist_completion == publish and registry.completion_queue is queue
    assert not capture.installed
    fresh = module().NotificationCapture(box, capture.home, capture.classify)
    fresh.install(sdk, registry)
    try:
        sdk._persist_completion(event(), {'full': 1})
        registry.completion_queue.put(event())
        assert len(fresh.claim()) == 1
    finally:
        fresh.close()


@pytest.mark.parametrize('late_publications', [0, 1, 2])
def test_lifecycle_fence_survives_capture_gc_after_durable_ack(tmp_path, late_publications):
    import gc
    import os
    import queue
    import threading
    import weakref
    from types import SimpleNamespace
    from backend.native_maintenance import maintenance_snapshot
    from deploy.native_readiness import require_native_readiness

    def readiness(capture):
        adapter = SimpleNamespace(_maintenance_lock=threading.RLock(), _pending_agent_requests=0,
            _inflight_agent_runs=0, _active_run_tasks={}, _run_statuses={}, _active_run_agents={},
            _shutdown_interruptible_agents={}, _stopping_run_ids=set(), _maintenance_workers=0,
            _maintenance_uncertain=False, _maintenance_delegations=SimpleNamespace(count=lambda: 0),
            notification_evidence=capture.evidence)
        registry = SimpleNamespace(_lock=threading.Lock(), _running={}, completion_queue=queue.Queue())
        delegations = SimpleNamespace(_records_lock=threading.Lock(), _records={})
        evidence = maintenance_snapshot(adapter, registry, delegations, capture.home/'state.db')
        ready = require_native_readiness({'pid': os.getpid(), 'native_maintenance': evidence},
            expected_pid=os.getpid(), expected_start_ticks=evidence['start_ticks'], notification_version=1)
        return (evidence['notifications']['shutdown_publications'],
                evidence['work']['notification_lifecycle_uncertain'], ready)

    m = module()
    sdk, registry, box, old = capture_fixture(tmp_path)
    pid = os.getpid()
    retained = registry.completion_queue
    old.close()
    for index in range(late_publications):
        retained.put(dict(type='completion', session_id='gc-fence-' + str(index), started_at=1,
            session_key='run_native', task_id='session_a', output='exact retained tail'))
    for lease in box.claim():
        box.ack(**{k: lease[k] for k in ('event_id', 'payload_sha256', 'lease_token')},
                receipt_id='durable-receipt')
    assert box.status()['pending'] == 0
    assert box.status()['delivered'] == late_publications
    expected = (late_publications, int(bool(late_publications)), not late_publications)
    assert readiness(old) == expected
    home, classify, path = old.home, old.classify, box.path
    old_ref, queue_ref, publish_ref = weakref.ref(old), weakref.ref(retained), weakref.ref(old.publish)
    lifetime_ref = weakref.ref(old._lifetime)
    del retained, old, box
    gc.collect()
    # Keeping the safety counters must not pin captures, hooks or their payloads.
    assert old_ref() is None and queue_ref() is None and publish_ref() is None
    lifetime_collected = lifetime_ref() is None
    fresh = m.NotificationCapture(m.NotificationOutbox(path), home, classify)
    fresh.install(sdk, registry)
    try:
        assert os.getpid() == pid
        assert fresh.evidence()['active_workers'] == 0
        assert fresh.evidence()['pending'] == 0
        assert fresh.evidence()['delivered'] == late_publications
        after = readiness(fresh)
        assert after == expected, {'before': expected, 'after': after,
                                   'same_pid': os.getpid() == pid,
                                   'prior_lifetime_collected': lifetime_collected}
    finally:
        fresh.close()


@pytest.mark.parametrize('hook', ['queue', 'publisher'])
def test_fresh_capture_counts_and_drains_retained_old_hook(tmp_path, monkeypatch, hook):
    import threading
    m = module()
    sdk, registry, box, old = capture_fixture(tmp_path)
    late_queue, late_publish = registry.completion_queue, sdk._persist_completion
    old.close()
    # Reopening storage and replacing even the registry must not reset liveness.
    import queue
    from types import SimpleNamespace
    fresh = m.NotificationCapture(m.NotificationOutbox(box.path), old.home, old.classify)
    fresh.install(sdk, SimpleNamespace(completion_queue=queue.Queue()))
    entered, release, draining = threading.Event(), threading.Event(), threading.Event()
    closed = threading.Event()
    errors = []
    classify = old.classify
    def paused(evt):
        entered.set()
        assert release.wait(5)
        return classify(evt)
    old.classify = paused
    evt = dict(type='completion', session_id='late', started_at=1,
               session_key='run_native', task_id='session_a', output='exact tail')
    result = {'full': ['exact', {'private': True}]}
    original = old.original_publish
    def verify_durable(evt, payload):
        assert box.record('async:deleg_one')['result'] == payload
        assert old.source_row(evt) is None
        return original(evt, payload)
    old.original_publish = verify_durable
    def produce():
        try:
            if hook == 'queue':
                late_queue.put(evt)
            else:
                late_publish(event(), result)
        except BaseException as exc:
            errors.append(exc)
    def close():
        try:
            fresh.close()
            closed.set()
        except BaseException as exc:
            errors.append(exc)
    wait = fresh.condition.wait
    def observed_wait(*args, **kwargs):
        draining.set()
        return wait(*args, **kwargs)
    monkeypatch.setattr(fresh.condition, 'wait', observed_wait)
    producer, closer = threading.Thread(target=produce), threading.Thread(target=close)
    producer.start()
    try:
        assert entered.wait(5)
        evidence = fresh.evidence()
        assert evidence['pending'] == 0
        assert evidence['active_workers'] == 1
        assert evidence['shutdown_publications'] == 1
        closer.start()
        assert draining.wait(5)
        assert not closed.is_set()
    finally:
        release.set()
        producer.join(5)
        if closer.ident is not None:
            closer.join(5)
        fresh.close()
    assert not producer.is_alive() and not closer.is_alive() and not errors
    saved = box.record('process:late:1' if hook == 'queue' else 'async:deleg_one')
    assert saved['event'] == (evt if hook == 'queue' else event())
    assert saved['result'] == (None if hook == 'queue' else result)
    assert fresh.evidence()['active_workers'] == 0
    assert fresh.evidence()['pending'] == 1
    assert fresh.evidence()['shutdown_publications'] == 1


def test_publisher_prune_barrier_and_queue_before_admission(tmp_path):
    import queue
    import threading
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    persisted, release = threading.Event(), threading.Event()
    original = sdk._persist_completion
    def pause(evt, result):
        original(evt, result)
        persisted.set()
        assert release.wait(5)
    sdk._persist_completion = pause
    reg = SimpleNamespace(completion_queue=queue.Queue())
    box = m.NotificationOutbox(state / 'outbox.sqlite')
    owner = m.NotificationCapture(box, home, m.OwnerRoute(home, state))
    owner.install(sdk, reg)
    result = {'full': ['not in summary', {'secret': 'retained privately'}]}
    errors = []
    def publish():
        try:
            sdk._persist_completion(event(), result)
            reg.completion_queue.put(event())
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=publish)
    thread.start()
    assert persisted.wait(5)
    assert box.record('async:deleg_one')['result'] == result
    assert owner.evidence()['active_workers'] == 1
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('DELETE FROM async_delegations')
    release.set(); thread.join(5)
    assert not thread.is_alive() and not errors
    assert box.record('async:deleg_one')['source_state'] == 'missing'
    assert sdk.calls == []  # missing-row SDK compatibility is NOT acceptance
    proc = {'type':'completion','session_id':'proc1','started_at':1,'session_key':'run_native',
            'task_id':'session_a','command':'true','exit_code':0,'completion_reason':'exited',
            'termination_source':None,'output':'exact process tail'}
    reg.completion_queue.put(proc)
    assert box.record('process:proc1:1')['event'] == proc
    assert reg.completion_queue.empty()
    foreign = {**event('foreign'), 'session_key': 'agent:whatsapp:chat'}
    sdk._persist_completion(foreign, {'full':True})
    reg.completion_queue.put(foreign)
    assert reg.completion_queue.empty()  # only this listener's exact retained projection retired
    assert box.record('async:foreign')['route'] == 'foreign'
    assert box.record('async:foreign')['result'] == {'full':True}
    with sqlite3.connect(home/'state.db') as db:
        assert db.execute("SELECT delivery_state FROM async_delegations WHERE delegation_id='foreign'").fetchone()[0] == 'pending'
    assert owner.evidence()['foreign_retained'] == 1
    assert sdk.calls == []
    owner.close()
    assert sdk._persist_completion is pause


def test_prune_after_source_read_preserves_truthful_missing_state(tmp_path):
    sdk, registry, box, capture = capture_fixture(tmp_path)
    sdk._persist_completion(event(), {'full': 1})
    def missing_compat_claim(id, token):
        with sqlite3.connect(sdk._db_path()) as db:
            db.execute('DELETE FROM async_delegations WHERE delegation_id=?', (id,))
        return True  # installed SDK's missing-row compatibility, NOT acceptance
    sdk.claim_completion_delivery = missing_compat_claim
    try:
        registry.completion_queue.put(event())
        assert box.record('async:deleg_one')['source_state'] == 'missing'
        assert len(capture.claim()) == 1
        assert registry.completion_queue.empty()
        saved = box.record('async:deleg_one')
        assert saved['source_state'] == 'missing' and saved['result'] == {'full': 1}
        assert saved['state'] == 'pending' and saved['receipt_id'] is None
    finally:
        capture.close()


def test_source_transfer_survives_restart_without_web_receipt(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    sdk._persist_completion(event(), {'exact': 'full'})
    reg = SimpleNamespace(completion_queue=queue.Queue())
    reg.completion_queue.put(event(restored=True))
    box = m.NotificationOutbox(state / 'outbox.sqlite')
    owner = m.NotificationCapture(box, home, m.OwnerRoute(home,state))
    owner.install(sdk, reg)
    assert box.record('async:deleg_one')['result'] == {'exact': 'full'}
    assert box.record('async:deleg_one')['source_state'] == 'accepted'
    assert box.status()['pending'] == 1 and box.status()['delivered'] == 0
    assert reg.completion_queue.empty()
    assert sdk.calls == [('claim','deleg_one'), ('complete','deleg_one')]
    owner.close()
    again = m.NotificationCapture(m.NotificationOutbox(box.path), home, m.OwnerRoute(home,state))
    again.install(sdk, reg)
    assert again.outbox.status()['pending'] == 1
    assert again.outbox.record('async:deleg_one')['source_state'] == 'accepted'
    again.close()


def test_owner_claim_revalidates_deletion_and_retries_quarantine(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    owner = m.NotificationCapture(m.NotificationOutbox(state/'outbox.sqlite'), home, m.OwnerRoute(home,state))
    owner.install(sdk, SimpleNamespace(completion_queue=queue.Queue()))
    with sqlite3.connect(state/'runs.sqlite') as db:
        db.execute('DELETE FROM runs')
    sdk._persist_completion(event(), {'full':True})
    owner.registry.completion_queue.put(event())
    assert owner.claim() == []
    assert owner.evidence()['quarantined'] == 1
    with sqlite3.connect(state/'runs.sqlite') as db:
        db.execute("INSERT INTO runs VALUES('run','owner','default','session_a','run_native')")
    assert len(owner.claim()) == 1
    assert owner.registry.completion_queue.empty()  # no stale queue adoption after transfer
    sdk._persist_completion(event('deleg_two'), {'full':2})
    owner.registry.completion_queue.put(event('deleg_two'))
    with sqlite3.connect(state/'runs.sqlite') as db:
        db.execute("INSERT INTO session_deletions VALUES('owner','default','session_a')")
    assert owner.claim() == []
    assert owner.evidence()['pending'] == 2
    owner.close()


@pytest.mark.asyncio
async def test_adapter_reconnect_requires_fresh_instance_before_sdk_access(tmp_path, monkeypatch):
    import queue
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    sdk = SyntheticSDK(home)
    registry = SimpleNamespace(completion_queue=queue.Queue())
    lookups = []
    class Base:
        async def connect(self):
            return True
        async def disconnect(self):
            pass
    cls = m.notification_adapter(Base, home, state_dir=state)
    def get_sdk(self):
        lookups.append('sdk')
        return sdk
    monkeypatch.setattr(cls, '_notification_sdk', get_sdk)
    monkeypatch.setattr(cls, '_notification_registry', lambda self: registry)
    adapter = cls()
    assert await adapter.connect()
    sdk._persist_completion(event(), {'full': 1})
    registry.completion_queue.put(event())
    await adapter.disconnect()
    await adapter.disconnect()  # teardown stays idempotent
    publish, original_queue = sdk._persist_completion, registry.completion_queue
    with pytest.raises(RuntimeError, match='closed.*fresh'):
        await adapter.connect()
    assert lookups == ['sdk']  # reject before imports, archival or hook mutation
    assert sdk._persist_completion == publish and registry.completion_queue is original_queue
    assert not adapter._notification_capture.installed
    fresh = cls()
    try:
        assert await fresh.connect()
        assert len(fresh._notification_capture.claim()) == 1
        sdk._persist_completion(event('after-reconnect'), {'full': 2})
        registry.completion_queue.put(event('after-reconnect'))
        assert fresh.notification_evidence()['pending'] == 2
        assert len(fresh._notification_capture.claim()) == 1
    finally:
        await fresh.disconnect()
    assert not fresh._notification_capture.installed


@pytest.mark.asyncio
async def test_authenticated_owner_http_factory_and_capabilities(tmp_path, monkeypatch):
    import queue
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    m = module()
    home, state = routing_fixture(tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    sdk = SyntheticSDK(home)
    registry = SimpleNamespace(completion_queue=queue.Queue())
    sdk._persist_completion(event(), {'exact':'retained-before-registry'})
    class Base:
        async def connect(self):
            return True
        async def disconnect(self):
            self.disconnected = True
        def _check_auth(self, request):
            if request.headers.get('Authorization') != 'Bearer test-secret':
                return web.json_response({'error':'unauthorized'},status=401)
        async def _handle_capabilities(self, request):
            return web.json_response({'features':{'old_feature':True}})
        def _http_route_table(self):
            return [('GET','/v1/capabilities',self._handle_capabilities)]
    cls = m.notification_adapter(Base, home, state_dir=state)
    monkeypatch.setattr(cls, '_notification_sdk', lambda self: sdk)
    monkeypatch.setattr(cls, '_notification_registry', lambda self: registry)
    adapter = cls()
    assert await adapter.connect()
    assert adapter.notification_evidence()['pending'] == 1
    app = web.Application()
    for method,path,handler in adapter._http_route_table():
        app.router.add_route(method,path,handler)
        app.router.add_route(method,'/p/{profile}'+path,handler)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/v1/mobile/notifications/status')).status == 401
        headers = {'Authorization':'Bearer test-secret'}
        assert (await client.post('/p/other/v1/mobile/notifications/claim',json={'limit':20},headers=headers)).status == 403
        caps = await (await client.get('/v1/capabilities',headers=headers)).json()
        assert caps['features']['old_feature'] is True
        assert caps['mobile_notifications'] == {'version':1,'delivery':'durable-inbox','automatic_model_wake':False}
        for body in ({'limit':0}, {'limit':True}, {'limit':20,'profile':'other'}):
            assert (await client.post('/v1/mobile/notifications/claim',json=body,headers=headers)).status == 400
        data = await (await client.post('/v1/mobile/notifications/claim',json={'limit':20},headers=headers)).json()
        assert len(data['items']) == 1
        item = data['items'][0]
        assert item['event'] == event() and item['historical'] is True
        ack = {k:item[k] for k in ('event_id','payload_sha256','lease_token')}
        ack['receipt_id'] = 'durable-web-receipt'
        for _ in range(2):
            response = await client.post('/v1/mobile/notifications/ack',json=ack,headers=headers)
            assert response.status == 200
        status = await (await client.get('/v1/mobile/notifications/status',headers=headers)).json()
        assert status['delivered'] == 1 and status['pending'] == 0
        assert 'event' not in json.dumps(status) and 'receipt' not in json.dumps(status)
    await adapter.disconnect()
    assert adapter.disconnected and not adapter._notification_capture.installed


def test_actual_native_py311_isolated_startup_retention_and_http(tmp_path):
    import os
    import subprocess
    import textwrap
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    home = tmp_path/'isolated-home'
    home.mkdir()
    script = textwrap.dedent('''
        import asyncio,json,os,sqlite3,sys,time
        from pathlib import Path
        sys.path.insert(0, '/usr/local/lib/hermes-agent')
        sys.path.insert(0, sys.argv[1])
        from hermes_state import SessionDB
        home=Path(os.environ['HERMES_HOME']); state=home/'bridge'; state.mkdir()
        db=SessionDB(home/'state.db')
        db.create_session('session_a','api_server')
        with sqlite3.connect(state/'auth.sqlite') as c:
            c.execute('CREATE TABLE users(id,role,profile,status)')
            c.execute("INSERT INTO users VALUES('owner','owner','default','ready')")
        with sqlite3.connect(state/'runs.sqlite') as c:
            c.execute('CREATE TABLE runs(id,user_id,profile,session_id,upstream_id)')
            c.execute("INSERT INTO runs VALUES('run','owner','default','session_a','run_native')")
            c.execute('CREATE TABLE session_deletions(user_id,profile,session_id)')
        from tools import async_delegation as sdk
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        from backend.native_notifications import notification_adapter
        from aiohttp import ClientSession
        token='synthetic-only-test-key-'+'a7f40b93'*6
        cls=notification_adapter(APIServerAdapter,home,state_dir=state)
        adapter=cls(PlatformConfig(enabled=True,extra={'host':'127.0.0.1','port':0,'key':token}))
        def forbidden(*a,**k): raise AssertionError('No model execution allowed')
        adapter._create_agent=forbidden
        async def main():
            assert await adapter.connect()
            from tools.process_registry import process_registry
            for i in range(65):
                evt={'type':'async_delegation','delegation_id':'d'+str(i),'session_key':'run_native',
                     'origin_session_id':'session_a','summary':'result '+str(i),'status':'completed','completed_at':time.time()}
                sdk._persist_dispatch({**evt,'dispatched_at':time.time()})
                sdk._persist_completion(evt,{'full':{'i':i,'children':['one','two']}})
                process_registry.completion_queue.put(evt)
            sdk._prune_durable_records()
            assert adapter.notification_evidence()['pending']==65
            with sqlite3.connect(home/'state.db') as c:
                assert c.execute('SELECT COUNT(*) FROM async_delegations').fetchone()[0] <= 50
            assert adapter._notification_outbox.record('async:d0')['result']=={'full':{'i':0,'children':['one','two']}}
            from tools.process_registry import ProcessSession
            process=ProcessSession(id='proc_fixture',command='synthetic; never executed',task_id='session_a',
                session_key='run_native',started_at=17.5,notify_on_complete=True,output_buffer='exact SDK tail',exit_code=0,exited=True)
            process_registry._running[process.id]=process
            process_registry._move_to_finished(process)
            saved=adapter._notification_outbox.record('process:proc_fixture:17.5')
            assert saved['event']['task_id']=='session_a' and 'origin_session_id' not in saved['event']
            assert saved['event']['output']=='exact SDK tail' and saved['state']=='pending'
            assert process_registry.completion_queue.empty()
            port=adapter._site._server.sockets[0].getsockname()[1]
            async with ClientSession() as client:
                base='http://127.0.0.1:'+str(port)
                async with client.get(base+'/v1/mobile/notifications/status') as r:
                    assert r.status==401
                async with client.post(base+'/v1/mobile/notifications/claim',headers={'Authorization':'Bearer '+token},json={'limit':50}) as r:
                    assert r.status==200,await r.text()
                    data=await r.json(); assert len(data['items'])==50
            await adapter.disconnect()
            assert not adapter._notification_capture.installed
            print('native-py311: real SDK retention 65-><=50; 65 exact outbox results; authenticated HTTP 50 leases; clean shutdown')
        asyncio.run(main())
        db.close()
    ''')
    env = {'HOME':str(home),'HERMES_HOME':str(home),'PATH':'/usr/bin:/bin',
           'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
    result = subprocess.run(['/usr/local/lib/hermes-agent/venv/bin/python','-c',script,str(root)],
                            env=env,text=True,capture_output=True,timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '65 exact outbox results' in result.stdout


def test_scope_route_never_claimed_and_conflicting_evidence_is_retained(tmp_path):
    m = module()
    home, state = routing_fixture(tmp_path)
    assert m.OwnerRoute(home,state)({**event(), 'scope_id':'foreign-scope'}) == 'foreign'
    box = m.NotificationOutbox(state/'outbox.sqlite')
    box.capture(event(), {'full':1}, route='owned')
    conflict = {**event(), 'summary':'different exact result'}
    with pytest.raises(m.NotificationConflict):
        box.capture(conflict, {'full':2}, route='owned')
    assert box.status()['conflicts'] == 1
    with sqlite3.connect(box.path) as db:
        row = db.execute('SELECT event_json,result_json FROM notification_conflicts').fetchone()
    assert json.loads(row[0]) == conflict and json.loads(row[1]) == {'full':2}
    assert box.record('async:deleg_one')['result'] == {'full':1}


def test_single_hook_owner_wrong_home_falls_through_and_safe_uninstall(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home, state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    registry = SimpleNamespace(completion_queue=queue.Queue())
    first = m.NotificationCapture(m.NotificationOutbox(state/'outbox.sqlite'),home,m.OwnerRoute(home,state))
    second = m.NotificationCapture(m.NotificationOutbox(state/'other.sqlite'),home,m.OwnerRoute(home,state))
    first.install(sdk,registry)
    with pytest.raises(RuntimeError, match='already owned'):
        second.install(sdk,registry)
    foreign_home = tmp_path/'other-home'; foreign_home.mkdir()
    SyntheticSDK(foreign_home)
    sdk.home = foreign_home
    sdk._persist_completion(event(), {'foreign':True})
    registry.completion_queue.put(event())
    assert first.outbox.status()['pending'] == 0
    assert registry.completion_queue.get_nowait() == event()
    replacement = lambda *a: None
    sdk._persist_completion = replacement
    first.close()
    assert sdk._persist_completion is replacement


@pytest.mark.asyncio
async def test_cancelled_http_worker_and_shutdown_waiters_retain_real_work(tmp_path):
    import asyncio
    import threading
    m = module()
    home,state = routing_fixture(tmp_path)
    class Base:
        async def disconnect(self):
            self.closed = True
    adapter = m.notification_adapter(Base,home,state_dir=state)()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    def job():
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return 'real result'
    waiter = asyncio.create_task(adapter._notification_job(job))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert adapter.notification_evidence()['active_workers'] == 1
    shutdown = asyncio.create_task(adapter.disconnect())
    await asyncio.sleep(0)  # yield to teardown, never a timing-based worker wait
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert adapter.notification_evidence()['active_workers'] == 1
    assert not getattr(adapter,'closed',False)
    release.set()
    await adapter.disconnect()
    assert adapter.closed
    assert adapter.notification_evidence()['active_workers'] == 0


@pytest.mark.asyncio
async def test_cancelled_executor_waiter_does_not_finish_running_worker(tmp_path, monkeypatch):
    import asyncio
    import threading
    m = module()
    home, state = routing_fixture(tmp_path)
    class Base:
        async def disconnect(self):
            self.closed = True
    adapter = m.notification_adapter(Base, home, state_dir=state)()
    loop = asyncio.get_running_loop()
    submitted = []
    execute = loop.run_in_executor
    def record(*args):
        future = execute(*args)
        submitted.append(future)
        return future
    monkeypatch.setattr(loop, 'run_in_executor', record)
    entered, release = asyncio.Event(), threading.Event()
    def job():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        assert not getattr(adapter, 'closed', False)
        return 'worker exited'
    waiter = asyncio.create_task(adapter._notification_job(job))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        submitted[0].cancel()  # cancel the actual executor awaiter, not just HTTP
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)  # settle done callbacks, not a wall-clock wait
        assert adapter.notification_evidence()['active_workers'] == 1
        shutdown = asyncio.create_task(adapter.disconnect())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not getattr(adapter, 'closed', False)
        assert not shutdown.done()
        assert adapter.notification_evidence()['active_workers'] == 1
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        await asyncio.wait_for(adapter.disconnect(), 5)
    assert adapter.closed and adapter.notification_evidence()['active_workers'] == 0


@pytest.mark.parametrize('operation', ['claim', 'ack'])
@pytest.mark.parametrize('dequeued', [False, True], ids=['queued', 'worker-started'])
def test_rejected_executor_submission_never_revives_mutation(tmp_path, monkeypatch, operation, dequeued):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    m = module()
    home, state = routing_fixture(tmp_path)
    entered = threading.Event()
    calls = []
    class ObservedExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            def observed():
                entered.set()
                return fn(*args, **kwargs)
            return super().submit(observed)
    class Base:
        async def disconnect(self):
            self.closed = True
    async def scenario():
        adapter = m.notification_adapter(Base, home, state_dir=state)()
        box = adapter._notification_outbox
        box.capture(event(), {'full': 'retained'}, route='owned')
        if operation == 'ack':
            item = box.claim()[0]
            kwargs = {k: item[k] for k in ('event_id', 'payload_sha256', 'lease_token')}
        before = box.record('async:deleg_one')
        def mutation():
            calls.append((adapter._notification_closing, getattr(adapter, 'closed', False)))
            if operation == 'ack':
                return box.ack(**kwargs, receipt_id='rejected-receipt')
            return box.claim()
        loop = asyncio.get_running_loop()
        pool = ObservedExecutor(max_workers=1)
        loop.set_default_executor(pool)
        adjust = pool._adjust_thread_count
        def reject_after_enqueue():
            if dequeued:
                adjust()
                assert entered.wait(5)
            raise RuntimeError('synthetic thread creation failure after enqueue')
        monkeypatch.setattr(pool, '_adjust_thread_count', reject_after_enqueue)
        with pytest.raises(RuntimeError, match='after enqueue'):
            await adapter._notification_job(mutation)
        # Teardown uses a separate healthy executor, leaving the rejected item
        # in the original pool until AFTER the adapter/base have closed.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        try:
            await asyncio.wait_for(adapter.disconnect(), 5)
            assert adapter.closed
            assert adapter.notification_evidence()['active_workers'] == 0
            monkeypatch.setattr(pool, '_adjust_thread_count', adjust)
            assert pool.submit(lambda: 'revived').result(5) == 'revived'
            assert entered.is_set()
            assert calls == [], 'executor-rejected mutation ran despite failed admission'
            assert box.record('async:deleg_one') == before
        finally:
            monkeypatch.setattr(pool, '_adjust_thread_count', adjust)
            pool.shutdown(wait=True)
    asyncio.run(scenario())


def test_outbox_owner_home_binding_and_offline_historical_dropped(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home,state = routing_fixture(tmp_path)
    box = m.NotificationOutbox(state/'outbox.sqlite')
    capture = m.NotificationCapture(box,home,m.OwnerRoute(home,state))
    wrong = tmp_path/'wrong'; wrong.mkdir()
    with pytest.raises(ValueError, match='home binding'):
        m.NotificationCapture(m.NotificationOutbox(box.path),wrong,lambda e:'owned')
    sdk = SyntheticSDK(home)
    sdk._persist_completion(event(), {'full':'historical'})
    with sqlite3.connect(home/'state.db') as db:
        db.execute("UPDATE async_delegations SET delivery_state='dropped'")
    box.import_records([{'event':event(),'result':{'full':'historical'},'provenance':'verified:old'}], classify=capture.classify)
    capture.install(sdk,SimpleNamespace(completion_queue=queue.Queue()))
    now = [100.0]
    box.clock = lambda: now[0]
    for _ in range(12):
        items = capture.claim()
        assert len(items) == 1 and items[0]['historical']
        now[0] += 3 * 24 * 3600
    assert sdk.calls == []
    with sqlite3.connect(home/'state.db') as db:
        assert db.execute('SELECT delivery_state FROM async_delegations').fetchone()[0] == 'dropped'
    assert box.status()['pending'] == 1 and box.status()['delivered'] == 0
    capture.close()


def test_watch_migration_envelopes_are_distinct_and_quarantine_not_duplicated(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home,state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    reg = SimpleNamespace(completion_queue=queue.Queue())
    owner = m.NotificationCapture(m.NotificationOutbox(state/'outbox.sqlite'),home,m.OwnerRoute(home,state))
    owner.install(sdk,reg)
    watch = {'type':'watch_match','session_id':'proc','session_key':'run_native','task_id':'session_a',
             'command':'watch','pattern':'done','output':'done','suppressed':0,
             'platform':'api_server','chat_id':None,'user_id':None,'user_name':None,
             'thread_id':None,'message_id':None}
    reg.completion_queue.put(dict(watch)); reg.completion_queue.put(dict(watch))
    assert reg.completion_queue.empty()
    items = owner.claim()
    assert len(items) == 2 and len({item['event_id'] for item in items}) == 2
    assert all(item['event_id'].startswith('migration:') and item['event'] == watch for item in items)
    # Explicit chat routing is foreign even when it names the owned session.
    reg.completion_queue.put({**watch, 'chat_id': 'session_a'})
    assert owner.evidence()['foreign_retained'] == 1
    assert owner.claim() == [] and sdk.calls == []
    unknown = {**watch,'task_id':'unknown'}
    reg.completion_queue.put(unknown)
    for _ in range(3):
        assert owner.claim() == []
    assert owner.evidence()['quarantined'] == 1
    owner.close()


def test_foreign_startup_projection_retired_with_source_unchanged_and_restart_proof(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home,state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    foreign = {**event('foreign'), 'session_key':'agent:whatsapp:private-chat'}
    result = {'summary':'foreign result','private':{'exact':[1,2,3]}}
    sdk._persist_completion(foreign,result)
    with sqlite3.connect(home/'state.db') as db:
        before = db.execute('SELECT * FROM async_delegations').fetchall()
    dedicated = SimpleNamespace(completion_queue=queue.Queue())
    unrelated_gateway_queue = queue.Queue()
    dedicated.completion_queue.put({**foreign,'restored':True})
    unrelated_gateway_queue.put(foreign)
    box = m.NotificationOutbox(state/'outbox.sqlite')
    capture = m.NotificationCapture(box,home,m.OwnerRoute(home,state))
    capture.install(sdk,dedicated)
    assert dedicated.completion_queue.empty()
    assert unrelated_gateway_queue.get_nowait() == foreign
    assert capture.claim() == [] and sdk.calls == []
    with sqlite3.connect(home/'state.db') as db:
        assert db.execute('SELECT * FROM async_delegations').fetchall() == before
        db.execute('DELETE FROM async_delegations')
    capture.close()
    reopened = m.NotificationOutbox(box.path)
    assert reopened.record('async:foreign')['event'] == foreign
    assert reopened.record('async:foreign')['result'] == result
    assert reopened.status()['foreign_retained'] == 1
    assert reopened.status()['pending'] == 0
    assert reopened.claim() == []


def test_parallel_sqlite_consumers_have_one_lease_and_exact_batch_retained(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    m = module()
    one = m.NotificationOutbox(tmp_path/'outbox.sqlite')
    two = m.NotificationOutbox(one.path)
    batch = event(is_batch=True,results=[{'summary':'child one'},{'summary':'child two'}])
    one.capture(batch,{'children':[{'full':1},{'full':2}]},route='owned')
    barrier = threading.Barrier(2)
    def claim(box):
        barrier.wait(5)
        return box.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(claim,one), pool.submit(claim,two)
        results = first.result(5) + second.result(5)
    assert len(results) == 1 and results[0]['event'] == batch
    assert one.record('async:deleg_one')['result'] == {'children':[{'full':1},{'full':2}]}


def test_failed_install_restores_hooks_and_preserves_conflict(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home,state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    original = sdk._persist_completion
    reg = SimpleNamespace(completion_queue=queue.Queue())
    original_queue = reg.completion_queue
    changed = {**event(), 'summary':'conflicting restart payload'}
    sdk._persist_completion(changed, {'full':2})
    reg.completion_queue.put(changed)
    box = m.NotificationOutbox(state/'outbox.sqlite')
    box.capture(event(), {'full':1}, route='owned')
    owner = m.NotificationCapture(box,home,m.OwnerRoute(home,state))
    with pytest.raises(m.NotificationConflict):
        owner.install(sdk,reg)
    assert not owner.installed
    assert sdk._persist_completion == original and reg.completion_queue is original_queue
    assert box.status()['conflicts'] == 1


def test_unsupported_or_missing_producer_identity_is_durable_quarantine(tmp_path):
    import queue
    from types import SimpleNamespace
    m = module()
    home,state = routing_fixture(tmp_path)
    sdk = SyntheticSDK(home)
    reg = SimpleNamespace(completion_queue=queue.Queue())
    owner = m.NotificationCapture(m.NotificationOutbox(state/'outbox.sqlite'),home,m.OwnerRoute(home,state))
    owner.install(sdk,reg)
    malformed = {k:v for k,v in event().items() if k != 'delegation_id'}
    unsupported = {**event(), 'type':'unknown-native-kind'}
    reg.completion_queue.put(malformed)
    reg.completion_queue.put(unsupported)
    assert owner.claim() == []
    assert owner.evidence()['quarantined'] == 2
    assert reg.completion_queue.qsize() == 2
    assert sdk.calls == []
    owner.close()


def test_ambiguous_upstream_identity_is_not_owned(tmp_path):
    m = module()
    home,state = routing_fixture(tmp_path)
    classify = m.OwnerRoute(home,state)
    assert classify(event()) == 'owned'
    with sqlite3.connect(state/'runs.sqlite') as db:
        db.execute("INSERT INTO runs VALUES('other','other-user','default','session_a','run_native')")
    assert classify(event()) == 'quarantined'


@pytest.mark.parametrize('source', ['whatsapp', 'cli'])
def test_owned_web_run_may_resume_default_home_whatsapp_conversation(tmp_path, source):
    m = module()
    home,state = routing_fixture(tmp_path)
    with sqlite3.connect(home/'state.db') as db:
        db.execute("UPDATE sessions SET source=?,session_key='agent:whatsapp:owner',chat_id='owner-chat',origin_json='{}'", (source,))
    classify = m.OwnerRoute(home,state)
    assert classify(event()) == 'owned'
    assert classify({**event(), 'session_key':'agent:whatsapp:owner'}) == 'foreign'
    with sqlite3.connect(home/'state.db') as db:
        db.execute("UPDATE sessions SET profile_name='other-profile'")
    assert classify(event()) == 'quarantined'
