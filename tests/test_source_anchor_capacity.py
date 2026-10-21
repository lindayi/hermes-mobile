from test_cloud_coordinator import (
    FakeApi, Coordinator, StateStore, enrolled_record, REPAIR_LIMIT,
)


def _case(tmp_path):
    api = FakeApi(unresolved=True)
    store = StateStore(tmp_path / 'state.json')
    store.enroll(enrolled_record())
    coordinator = Coordinator(api, store, clock=lambda: 1790856660)
    return api, store, coordinator


def _exhaust(store):
    store._mutate(lambda data: data['enrollments']['16'].__setitem__(
        'source_anchor_reservations', REPAIR_LIMIT))


def test_exhausted_source_anchor_capacity_reports_budget_without_phantom_repair(tmp_path):
    api, store, coordinator = _case(tmp_path)
    _exhaust(store)
    result = coordinator.run(apply=True)['pull_requests'][0]
    assert not result['repair_requested']
    assert 'budget' in result['reasons']
    assert api.task_posts == 0
    assert store.snapshot()['enrollments']['16']['attempts'] == 0
    assert result['outcomes'] > 0
    writes = list(api.writes)
    repeated = coordinator.run(apply=True)['pull_requests'][0]
    assert not repeated['repair_requested']
    assert api.writes == writes
    assert api.task_posts == 0


def test_capacity_change_before_dispatch_does_not_report_a_started_repair(tmp_path, monkeypatch):
    api, store, coordinator = _case(tmp_path)
    original = coordinator._dispatch_task
    def exhaust_then_dispatch(action):
        _exhaust(store)
        return original(action)
    monkeypatch.setattr(coordinator, '_dispatch_task', exhaust_then_dispatch)
    result = coordinator.run(apply=True)['pull_requests'][0]
    assert not result['repair_requested']
    assert 'source-anchor-budget' in result['reasons']
    assert api.task_posts == 0
    assert store.snapshot()['enrollments']['16']['attempts'] == 0


def test_matching_reserved_source_anchor_can_finish_at_capacity(tmp_path):
    api, store, coordinator = _case(tmp_path)
    plan = coordinator._build_plan(apply=False)
    action = plan['pull_requests'][0]['repair'] | {'receipt_anchor_role': 'continuation-source'}
    anchor = coordinator._ensure_receipt_anchor(action, source=True)
    assert anchor['status'] == 'sent'
    _exhaust(store)
    writes = len(api.writes)
    result = coordinator.run(apply=True)['pull_requests'][0]
    assert result['repair_requested']
    assert api.task_posts == 1
    assert sum(route.endswith('/comments') for route, _ in api.writes[writes:]) == 0
    assert store.snapshot()['enrollments']['16']['source_anchor_reservations'] == REPAIR_LIMIT
