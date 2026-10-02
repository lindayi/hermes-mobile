"""Synthetic regression seams between SHA-bound enrollment and durable lifecycle."""
import pytest

from deploy.cloud_coordinator import _authorized_result_heads
from test_cloud_coordinator import (
    BASE, HEAD, OWNER, Coordinator, FakeApi, StateStore,
)

NOW = 1790856660
RESULT_HEAD = "c" * 40


def bound_worker(tmp_path, *, sensitive=False):
    api = FakeApi(unresolved=True, sensitive=sensitive)
    api.comments[0]["body"] = f"/hermes enroll {HEAD}"
    store = StateStore(tmp_path / "coordinator" / "state.json")
    worker = Coordinator(api, store, clock=lambda: NOW)
    worker.run(apply=True)
    action = next(a for a in store.actions().values() if a["kind"] == "fix")
    return api, store, worker, action


def finish(api, action, *, result="ready"):
    api.head_sha = RESULT_HEAD
    api.pull["head"]["sha"] = RESULT_HEAD
    api.complete_task(action["task_id"], action, result=result, head_sha=RESULT_HEAD)


def test_bound_proof_is_durable_before_compaction_and_survives_restart(tmp_path, monkeypatch):
    api, store, worker, action = bound_worker(tmp_path)
    finish(api, action)
    api.unresolved = False
    original_retire = store.retire
    observed = []

    def inspect_before_compaction(issue, head):
        enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
        initial, authorized, blocked = _authorized_result_heads(
            issue, enrollment, {}, api.comments, BASE,
        )
        assert initial == HEAD and RESULT_HEAD in authorized and not blocked
        observed.append(True)
        original_retire(issue, head)

    monkeypatch.setattr(store, "retire", inspect_before_compaction)
    worker.run(apply=True)
    assert observed
    monkeypatch.setattr(store, "retire", original_retire)
    assert store.action(action["key"]) is None  # Actual lifecycle compaction, not disabled.
    restarted = Coordinator(api, StateStore(store.path), clock=lambda: NOW)
    result = restarted.run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" not in result["reasons"]
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 1
    assert api.fix_attempts == 1
    api.comments[-1]["updated_at"] = "2026-10-01T12:05:01Z"
    result = restarted.run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" in result["reasons"]


@pytest.mark.parametrize("pending", [True, False])
def test_new_owner_sha_command_renews_active_enrollment_without_reset(tmp_path, pending):
    api, store, worker, action = bound_worker(tmp_path, sensitive=True)
    store.authorize_sensitive(16, HEAD)
    if not pending:
        api.tasks[action["task_id"]]["state"] = "failed"
        api.unresolved = False
        worker.run(apply=True)
    api.head_sha = RESULT_HEAD
    api.pull["head"]["sha"] = RESULT_HEAD
    blocked = worker.run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" in blocked["reasons"]
    api.comments.append({
        "id": 10001, "body": f"/hermes enroll {RESULT_HEAD}",
        "user": {"id": OWNER}, "created_at": "2026-10-01T12:11:00Z",
        "updated_at": "2026-10-01T12:11:00Z",
    })
    result = worker.run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" not in result["reasons"]
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["authorized_head"] == HEAD
    assert enrollment["comment"] == 10001
    assert enrollment["attempts"] == 1
    assert enrollment["sensitive_sha"] is None
    assert api.fix_attempts == 1
    if pending:
        assert store.action(action["key"])["task_id"] == action["task_id"]
        assert store.action(action["key"])["status"] == "sent"
    before = store.snapshot()["enrollments"]["16"]
    worker.run(apply=True)
    assert store.snapshot()["enrollments"]["16"] == before


@pytest.mark.parametrize("result_code", ["policy_broken", "conflict_incompatible"])
def test_changed_head_blocker_keeps_meaningful_lifecycle_classification(tmp_path, result_code):
    api, store, worker, action = bound_worker(tmp_path)
    finish(api, action, result=result_code)
    result = worker.run(apply=True)["pull_requests"][0]
    assert "task-result-blocked" in result["reasons"]
    assert "unauthorized-continuation" not in result["reasons"]
    assert not result["auto_merge_eligible"] and not result["repair_requested"]
    assert any(e["reason"] == result_code and e["head_sha"] == RESULT_HEAD
               for e in store.snapshot()["lifecycle_events"])
    assert any(route.endswith("/comments") and "owner attention" in body["body"]
               for route, body in api.writes)
    assert api.fix_attempts == 1


@pytest.mark.parametrize("updated", [None, "invalid", "2026-10-01T19:59:59Z", "2026-10-01T20:00:01Z"])
@pytest.mark.parametrize("phase", ["collect", "preflight"])
def test_starter_rejects_edited_or_unverifiable_owner_commands(tmp_path, updated, phase):
    from deploy.issue_starter import CoordinatorError
    from test_issue_starter import FakeApi as StarterApi, make_coordinator

    api = StarterApi()
    worker = make_coordinator(tmp_path, api)
    candidate = worker._collect(worker.store.snapshot())[0]
    if updated is None:
        api.comments[0].pop("updated_at")
    else:
        api.comments[0]["updated_at"] = updated
    if phase == "collect":
        assert worker.run(apply=True)["dispatched"] == 0
    else:
        with pytest.raises(CoordinatorError, match="command evidence"):
            worker._fresh_issue(candidate)
    assert not any(route.endswith("/tasks") for route, _ in api.posts)


def test_bound_result_head_clears_sensitive_authorization(tmp_path):
    api, store, worker, action = bound_worker(tmp_path, sensitive=True)
    store.authorize_sensitive(16, HEAD)
    finish(api, action)
    api.unresolved = False
    result = worker.run(apply=True)["pull_requests"][0]
    assert "sensitive" in result["reasons"]
    assert not result["auto_merge_requested"]
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    api.comments.append({
        "id": 10001, "body": f"/hermes authorize-sensitive {RESULT_HEAD}",
        "user": {"id": OWNER}, "created_at": "2026-10-01T12:11:00Z",
        "updated_at": "2026-10-01T12:11:00Z",
    })
    result = worker.run(apply=True)["pull_requests"][0]
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == RESULT_HEAD
    assert result["auto_merge_requested"]


@pytest.mark.parametrize("draft", [False, True])
def test_deferred_handoff_rechecks_unchanged_bound_receipt_after_scan_commit(tmp_path, monkeypatch, draft):
    api, store, worker, action = bound_worker(tmp_path)
    finish(api, action)
    api.review_sha = HEAD
    api.pull["draft"] = draft
    receipt = next(c for c in api.comments if c["body"].startswith("Hermes-Task-Receipt:"))
    original_commit = store.commit_scan

    def edit_after_commit(*args, **kwargs):
        original_commit(*args, **kwargs)
        receipt["updated_at"] = "2026-10-01T12:05:01Z"

    monkeypatch.setattr(store, "commit_scan", edit_after_commit)
    before_writes = len(api.writes)
    worker.run(apply=True)
    assert not api.graphql_writes
    assert not any(route.endswith("/requested_reviewers")
                   for route, _ in api.writes[before_writes:])
    assert api.fix_attempts == 1


def test_actual_starter_to_lifecycle_consumer_fixer_review_checks_merge_and_replay(tmp_path):
    from test_issue_starter import (
        FakeApi as StarterApi, REPOSITORY, completed_task, make_coordinator,
        pull_request, start_task,
    )

    class Starter16Api(StarterApi):
        def get(self, route):
            return super().get(route.replace("/issues/16/comments", "/issues/41/comments"))

        def post(self, route, body):
            return super().post(route.replace("/issues/16/comments", "/issues/41/comments"), body)

    pull = pull_request(pull_id=160000016, node_id="PR_node_16", head_ref="topic")
    pull["number"] = 16
    producer = Starter16Api(pulls=[pull])
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    assert make_coordinator(tmp_path, producer).run(apply=True)["handed_off"] == 1
    emitted = next(c for c in producer.comments if c["body"] == f"/hermes enroll {HEAD}")
    assert any(route.endswith("/issues/41/comments") and body["body"] == emitted["body"]
               for route, body in producer.posts)

    api = FakeApi(unresolved=True)
    api.comments = [dict(emitted)]  # Actual in-checkout producer output, no fallback.
    api.pull.update(pull)
    api.pull["draft"] = False
    api.review_sha = HEAD
    store = StateStore(tmp_path / "paired" / "state.json")

    def run():
        return Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]

    assert run()["repair_requested"]
    first = next(a for a in store.actions().values() if a["kind"] == "fix")
    finish(api, first)
    api.pending_required = True
    waiting = run()
    assert not waiting["auto_merge_requested"] and api.fix_attempts == 1
    assert store.action(first["key"])["handoff_state"] == "waiting_review"
    assert sum(route.endswith("/requested_reviewers") for route, _ in api.writes) == 1
    api.review_sha = RESULT_HEAD
    api.review_state = "COMMENTED"
    assert run()["repair_requested"]
    second = next(a for a in store.actions().values() if a.get("task_id") == "task-2")
    assert second["head"] == RESULT_HEAD
    assert store.action(first["key"]) is None
    assert store.snapshot()["enrollments"]["16"]["receipt_proofs"][0]["receipt_head"] == RESULT_HEAD
    api.complete_task(second["task_id"], second)
    api.unresolved = False
    api.review_state = "APPROVED"
    assert not run()["auto_merge_requested"]  # Required checks remain pending.
    api.pending_required = False
    assert run()["auto_merge_requested"]
    assert api.graphql_writes[-1][1]["expectedHeadOid"] == RESULT_HEAD
    run()
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 2
    assert store.snapshot()["enrollments"]["16"]["authorized_head"] == HEAD
    api.head_sha = "d" * 40
    api.pull["head"]["sha"] = api.head_sha
    assert "unauthorized-continuation" in run()["reasons"]
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 2


@pytest.mark.parametrize("change", [
    "task_owner", "task_creator", "task_repository", "session_id", "session_task",
    "session_nonce", "missing_pull", "receipt_author", "receipt_edited", "receipt_body",
])
def test_bound_consumer_rejects_unverified_task_session_and_receipt_identity(tmp_path, change):
    api, store, worker, action = bound_worker(tmp_path)
    finish(api, action)
    task = api.tasks[action["task_id"]]
    receipt = next(c for c in api.comments if c["body"].startswith("Hermes-Task-Receipt:"))
    if change.startswith("task_"):
        task[change.removeprefix("task_")] = {"id": 99}
    elif change == "session_id":
        task["sessions"][0]["id"] = "other-session"
    elif change == "session_task":
        task["sessions"][0]["task_id"] = "other-task"
    elif change == "session_nonce":
        task["sessions"][0]["prompt"] = "another dispatch nonce"
    elif change == "missing_pull":
        task["artifacts"] = [a for a in task["artifacts"] if a["type"] != "pull"]
    elif change == "receipt_author":
        receipt["user"] = {"id": OWNER}
    elif change == "receipt_edited":
        receipt["updated_at"] = "2026-10-01T12:05:01Z"
    else:
        receipt["body"] += " copied"
    result = worker.run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" in result["reasons"]
    assert not result["repair_requested"] and not result["auto_merge_requested"]
    assert store.action(action["key"])["status"] != "completed"
    assert not store.snapshot()["enrollments"]["16"].get("receipt_proofs")
    assert api.fix_attempts == 1 and not api.graphql_writes
