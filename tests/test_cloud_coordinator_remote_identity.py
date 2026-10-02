"""Remote numeric identity boundaries; task/session IDs remain opaque strings."""
from copy import deepcopy

import pytest

from deploy.cloud_coordinator import MAX_RECEIPT_POLLS, OWNER_ID, REPOSITORY_ID
from test_cloud_coordinator import BASE, Coordinator, FakeApi, StateStore, enrolled_record


KINDS = ["valid", "float", "string", "bool", "zero", "negative", "missing"]


def mutate_id(target, key, kind):
    value = target[key]
    if kind == "missing":
        target.pop(key)
    else:
        target[key] = {
            "valid": value, "float": float(value), "string": str(value),
            "bool": True, "zero": 0, "negative": -1,
        }[kind]


def setup(tmp_path, *, repair=True):
    api = FakeApi(unresolved=repair)
    store = StateStore(tmp_path / "state.json")
    store.enroll(enrolled_record())
    coordinator = Coordinator(api, store, clock=lambda: 1790856660)
    return api, store, coordinator


@pytest.mark.parametrize("state", ["failed", "timed_out", "cancelled"])
@pytest.mark.parametrize("field", ["creator", "owner", "repository", "pull_artifact"])
@pytest.mark.parametrize("kind", KINDS)
def test_terminal_task_requires_live_numeric_ownership(tmp_path, state, field, kind):
    api, store, coordinator = setup(tmp_path)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    task = api.tasks[fix["task_id"]]
    task["state"] = state
    task["artifacts"].append({
        "provider": "github", "type": "pull", "data": {"id": fix["pull_id"]},
    })
    if field == "pull_artifact":
        mutate_id(task["artifacts"][-1]["data"], "id", kind)
    elif kind == "missing":
        task.pop(field)
    else:
        mutate_id(task[field], "id", kind)

    if kind == "valid":
        coordinator.run(apply=True)
        assert store.action(fix["key"])["status"] == "completed"
        assert [e["reason"] for e in store.snapshot()["lifecycle_events"]] == ["task_failed"]
        assert api.fix_attempts == 2  # Authenticated terminal proof releases the lock.
        return

    # Reopen durable state each time; weak evidence never creates a blind retry.
    for poll in range(1, MAX_RECEIPT_POLLS + 2):
        coordinator = Coordinator(api, StateStore(store.path), clock=lambda: 1790856660)
        summary = coordinator.run(apply=True)["pull_requests"][0]
        stored = store.action(fix["key"])
        assert stored["status"] == ("sent" if poll < MAX_RECEIPT_POLLS else "uncertain")
        assert stored["receipt_waits"] == min(poll, MAX_RECEIPT_POLLS)
        assert stored["task_id"] == fix["task_id"]
        assert api.fix_attempts == 1
        assert not summary["repair_requested"] and "agent" in summary["reasons"]
        reasons = [e["reason"] for e in store.snapshot()["lifecycle_events"]]
        assert reasons == ([] if poll < MAX_RECEIPT_POLLS else ["execution_uncertain"])
    assert not api.graphql_writes


def mutate_pull(pull, field, kind):
    target, key = (pull[field.split("_")[0]]["repo"], "id") if "_repo" in field else (pull, field)
    if kind == "other":
        target[key] += 1
    else:
        mutate_id(target, key, kind)


@pytest.mark.parametrize("stage", [
    "dispatch_first", "dispatch_last", "handoff_first", "handoff_last", "merge_last",
])
@pytest.mark.parametrize("field", ["number", "id", "head_repo", "base_repo"])
@pytest.mark.parametrize("kind", ["valid", "float", "missing", "bool", "zero", "negative", "string", "other"])
def test_fresh_pull_fences_reject_weak_numeric_identity(tmp_path, monkeypatch, stage, field, kind):
    api, store, coordinator = setup(tmp_path, repair=stage != "merge_last")
    if stage.startswith("handoff"):
        coordinator.run(apply=True)
        fix = next(a for a in store.actions().values() if a["kind"] == "fix")
        api.complete_task(fix["task_id"], fix)
        plan = coordinator._build_plan(apply=True)  # Verify receipt; defer remote handoff.
        action = store.action(fix["key"])
        assert action["handoff_state"] == "pending"
        if stage == "handoff_first":
            api.pull["draft"] = True
    else:
        plan = coordinator._build_plan(apply=False)
        action = plan["pull_requests"][0]["merge_action" if stage == "merge_last" else "repair"]
    snapshot = plan["snapshots"][0]
    original_get = api.get
    reads = 0
    target_read = {"dispatch_first": 1, "dispatch_last": 2,
                   "handoff_first": 1, "handoff_last": 2, "merge_last": 3}[stage]

    def get(route):
        nonlocal reads
        value = original_get(route)
        if route.endswith("/pulls/16"):
            reads += 1
            if reads == target_read:
                value = deepcopy(value)
                mutate_pull(value, field, kind)
        return value

    monkeypatch.setattr(api, "get", get)
    writes, graphql = len(api.writes), len(api.graphql_writes)
    if stage.startswith("dispatch"):
        coordinator._dispatch_task(action)
    elif stage.startswith("handoff"):
        coordinator._advance_task_handoff(action["key"], action, snapshot)
    else:
        coordinator._enable_auto_merge(action, snapshot)
    assert reads >= target_read
    if kind == "valid":
        assert len(api.writes) > writes or len(api.graphql_writes) > graphql
    else:
        assert len(api.writes) == writes and len(api.graphql_writes) == graphql
        if stage.startswith("dispatch"):
            assert store.action(action["key"]) is None
            assert store.snapshot()["enrollments"]["16"]["attempts"] == 0
        elif stage.startswith("handoff"):
            assert store.action(action["key"])["handoff_waits"] == 1


@pytest.mark.parametrize("field", ["number", "id", "head_repo", "base_repo"])
@pytest.mark.parametrize("kind", KINDS)
def test_review_request_response_requires_numeric_pull_identity(tmp_path, monkeypatch, field, kind):
    api, store, coordinator = setup(tmp_path)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    plan = coordinator._build_plan(apply=True)
    snapshot = plan["snapshots"][0]
    original_write = api.write

    def write(route, body):
        response = deepcopy(original_write(route, body))
        if route.endswith("/requested_reviewers"):
            mutate_pull(response, field, kind)
        return response

    monkeypatch.setattr(api, "write", write)
    coordinator._advance_task_handoff(fix["key"], store.action(fix["key"]), snapshot)
    stored = store.action(fix["key"])
    assert stored["review_request_state"] == ("sent" if kind == "valid" else "uncertain")
    assert stored["handoff_state"] == ("waiting_review" if kind == "valid" else "review_request_uncertain")
    api.requested_reviewers = []  # No later independent proof: never blindly re-POST.
    writes = list(api.writes)
    coordinator = Coordinator(api, StateStore(store.path), clock=lambda: 1790856660)
    coordinator._advance_task_handoff(fix["key"], store.action(fix["key"]), snapshot)
    assert api.writes == writes
    assert api.fix_attempts == 1


@pytest.mark.parametrize("field", ["head_sha", "head_ref", "base_sha", "base_ref"])
@pytest.mark.parametrize("change", ["valid", "changed", "missing"])
def test_review_response_is_bound_to_handoff_revision(tmp_path, monkeypatch, field, change):
    api, store, coordinator = setup(tmp_path)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    plan = coordinator._build_plan(apply=True)
    snapshot = plan["snapshots"][0]
    original_write = api.write

    def write(route, body):
        response = deepcopy(original_write(route, body))
        if route.endswith("/requested_reviewers"):
            side, key = field.split("_")
            if change == "missing":
                response[side].pop(key)
            elif change == "changed":
                response[side][key] = "f" * 40 if key == "sha" else "other-branch"
        return response

    monkeypatch.setattr(api, "write", write)
    coordinator._advance_task_handoff(fix["key"], store.action(fix["key"]), snapshot)
    stored = store.action(fix["key"])
    assert stored["review_request_state"] == ("sent" if change == "valid" else "uncertain")
    assert stored["handoff_state"] == ("waiting_review" if change == "valid" else "review_request_uncertain")
    api.requested_reviewers = []
    writes = list(api.writes)
    coordinator = Coordinator(api, StateStore(store.path), clock=lambda: 1790856660)
    coordinator._advance_task_handoff(fix["key"], store.action(fix["key"]), snapshot)
    assert api.writes == writes
    assert api.fix_attempts == 1


@pytest.mark.parametrize("stage", ["enrollment", "snapshot", "authorization"])
@pytest.mark.parametrize("field", ["number", "id", "head_repo", "base_repo"])
@pytest.mark.parametrize("kind", KINDS)
def test_pull_collection_requires_numeric_identity(tmp_path, stage, field, kind):
    from deploy.cloud_coordinator import CoordinatorError, enrollment_from_comment

    api, store, coordinator = setup(tmp_path)
    mutate_pull(api.pull, field, kind)
    if stage == "enrollment":
        result = enrollment_from_comment(api.issue, api.pull, api.comments[0])
        assert bool(result) is (kind == "valid")
    elif stage == "snapshot":
        if kind == "valid":
            assert coordinator._snapshot_pull(16, enrolled_record(), BASE)["scoped"]
        else:
            with pytest.raises(CoordinatorError, match="identity"):
                coordinator._snapshot_pull(16, enrolled_record(), BASE)
    else:
        api.comments = [{"id": 124, "user": {"id": OWNER_ID},
                         "body": f"/hermes authorize-sensitive {api.head_sha}"}]
        _, commands, _, candidates = coordinator._scan_enrollments(store.snapshot())
        assert bool(candidates["16"].get("sensitive_sha")) is (kind == "valid")
        assert bool(commands[0][1].get("validated")) is (kind == "valid")


@pytest.mark.parametrize("route,expected", [("user", OWNER_ID), ("repos/lindayi/hermes-mobile", REPOSITORY_ID)])
@pytest.mark.parametrize("kind", KINDS)
def test_cycle_identity_requires_positive_integer(tmp_path, monkeypatch, route, expected, kind):
    from deploy.cloud_coordinator import CoordinatorError

    api, store, coordinator = setup(tmp_path, repair=False)
    value = {"id": expected}
    mutate_id(value, "id", kind)
    original_get = api.get
    monkeypatch.setattr(api, "get", lambda path: value if path == route else original_get(path))
    if kind == "valid":
        coordinator._identity()
    else:
        with pytest.raises(CoordinatorError):
            coordinator._identity()
    assert not api.writes and not api.graphql_writes
