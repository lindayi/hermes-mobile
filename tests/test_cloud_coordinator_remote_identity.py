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
    "dispatch_first", "dispatch_last", "handoff_first", "merge_last",
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
        assert action["handoff_state"] == "waiting_review"
        if stage == "handoff_first":
            api.pull["draft"] = True
    else:
        plan = coordinator._build_plan(apply=False)
        action = plan["pull_requests"][0]["merge_action" if stage == "merge_last" else "repair"]
    snapshot = plan["snapshots"][0]
    original_get = api.get
    reads = 0
    target_read = {"dispatch_first": 1, "dispatch_last": 2,
                   "handoff_first": 1, "merge_last": 3}[stage]

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


def test_task_handoff_never_requests_or_waits_for_copilot(tmp_path, monkeypatch):
    api, store, coordinator = setup(tmp_path)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    original_get, original_write = api.get, api.write

    def get(route):
        assert not route.endswith("/requested_reviewers")
        return original_get(route)

    def write(route, body):
        assert not route.endswith("/requested_reviewers")
        return original_write(route, body)

    monkeypatch.setattr(api, "get", get)
    monkeypatch.setattr(api, "write", write)
    coordinator.run(apply=True)
    assert store.action(fix["key"])["handoff_state"] == "waiting_review"
    assert api.fix_attempts == 1
    assert not any(route.endswith("/requested_reviewers") for route, _ in api.writes)


@pytest.mark.parametrize("field,invalid", [
    ("user", 999999),
    ("owner", 999999),
    ("repository", 123),
])
def test_review_report_requires_expected_reviewer_session_principal_ids(
        tmp_path, field, invalid):
    api = FakeApi(source_failure=True)
    api.owner_reviews = []
    api.owner_review_body = "not a structured independent review"
    store = StateStore(tmp_path / "state.json")
    coordinator = Coordinator(api, store, clock=lambda: 1790856660)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    api.source_failure = False
    coordinator.run(apply=True)
    Coordinator(api, StateStore(store.path), clock=lambda: 1790856660).run(apply=True)
    review = next(a for a in store.actions().values() if a["kind"] == "review")
    api.complete_review_task(review["task_id"], review, source_action=store.action(fix["key"]))
    api.tasks[review["task_id"]]["sessions"][0][field]["id"] = invalid

    summary = Coordinator(api, StateStore(store.path), clock=lambda: 1790856660).run(
        apply=True,
    )["pull_requests"][0]

    assert summary["review_valid"] is False
    assert StateStore(store.path).action(fix["key"])["handoff_state"] == "waiting_review"
    rejected = StateStore(store.path).action(review["key"])
    assert rejected["status"] == "completed"
    assert rejected["report_retry_allowed"] is False
    assert rejected["report_error"]


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
        api.authorize_sha_review = True
        api.comments = [{"id": 124, "user": {"id": OWNER_ID},
                         "body": (f"/hermes authorize-sensitive {api.head_sha} review "
                                  f"{api.owner_review_id} {api.owner_review_digest}")}]
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
