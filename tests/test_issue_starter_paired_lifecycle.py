"""Synthetic regression seams between SHA-bound enrollment and durable lifecycle."""
from copy import deepcopy
import hashlib
import json

import pytest

from deploy.cloud_coordinator import _authorized_result_heads
from test_cloud_coordinator import (
    BASE, HEAD, OWNER, Coordinator, FakeApi, StateStore, refresh_owner_review,
)

NOW = 1790856660
RESULT_HEAD = "c" * 40


def bound_worker(tmp_path, *, sensitive=False, authorize=False):
    api = FakeApi(unresolved=True, sensitive=sensitive, authorize=authorize)
    api.comments[0]["body"] = f"/hermes enroll {HEAD}"
    store = StateStore(tmp_path / "coordinator" / "state.json")
    worker = Coordinator(api, store, clock=lambda: NOW)
    worker.run(apply=True)
    action = next(a for a in store.actions().values() if a["kind"] == "fix")
    return api, store, worker, action


def test_dispatch_prompt_supplies_v2_bindings_without_child_task_discovery(tmp_path):
    api, store, worker, action = bound_worker(tmp_path)
    prompt = action["body"]
    assert "Hermes-Task-Receipt: v2\n" in prompt
    assert f"nonce={action['dispatch_nonce']}\n" in prompt
    assert "pr=16\n" in prompt
    assert f"start_head={HEAD}\n" in prompt
    assert f"base={BASE}\n" in prompt
    assert "COPILOT_AGENT_SESSION_ID" in prompt
    assert "missing" in prompt and "blocker" in prompt
    assert "Do not wait for CI" in prompt
    assert "parent controller" in prompt
    assert "task=<" not in prompt and "returned-task-id" not in prompt
    assert "result=ready|conflict_incompatible|policy_broken" in prompt


def finish(api, action, *, result="ready"):
    api.head_sha = RESULT_HEAD
    api.pull["head"]["sha"] = RESULT_HEAD
    api.complete_task(action["task_id"], action, result=result, head_sha=RESULT_HEAD)


def test_bound_proof_is_durable_before_compaction_and_survives_restart(tmp_path, monkeypatch):
    api, store, worker, action = bound_worker(tmp_path)
    finish(api, action)
    api.unresolved = False
    refresh_owner_review(api, RESULT_HEAD)
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
    api, store, worker, action = bound_worker(tmp_path, sensitive=True, authorize=True)
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] == HEAD
    assert enrollment["sensitive_authorization"]["head_sha"] == HEAD
    assert enrollment["targeted_review"]["head_sha"] == HEAD
    finish(api, action)
    api.unresolved = False
    result = worker.run(apply=True)["pull_requests"][0]
    assert "sensitive" in result["reasons"]
    assert not result["auto_merge_requested"]
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_authorization"] is None
    assert enrollment["targeted_review"] is None
    api.review_submitted_at = "2026-10-01T12:06:00Z"
    # Publish a new structured owner review for the result head, then select its
    # exact raw body digest in a fresh authorization command.
    api.authorize_sha = RESULT_HEAD
    refresh_owner_review(api, RESULT_HEAD, review_id=api.owner_review_id + 1)
    api.comments.append({
        "id": 10001,
        "body": (f"/hermes authorize-sensitive {RESULT_HEAD} review "
                 f"{api.owner_review_id} {api.owner_review_digest}"),
        "user": {"id": OWNER}, "created_at": "2026-10-01T12:11:00Z",
        "updated_at": "2026-10-01T12:11:00Z",
    })
    result = worker.run(apply=True)["pull_requests"][0]
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == RESULT_HEAD
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_authorization"]["head_sha"] == RESULT_HEAD
    assert enrollment["sensitive_authorization"]["review_id"] == api.owner_review_id
    assert enrollment["sensitive_authorization"]["body_sha256"] == api.owner_review_digest
    assert enrollment["targeted_review"]["head_sha"] == RESULT_HEAD
    assert enrollment["targeted_review"]["review_id"] == api.owner_review_id
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


@pytest.mark.parametrize("stale_review_state", ["APPROVED", "COMMENTED", "CHANGES_REQUESTED"])
def test_actual_starter_to_lifecycle_consumer_fixer_review_checks_merge_and_replay(
        tmp_path, stale_review_state):
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
    emitted = next(c for c in producer.comments if c["body"].startswith(f"/hermes enroll {HEAD} issue "))
    assert any(route.endswith("/issues/41/comments") and body["body"] == emitted["body"]
               for route, body in producer.posts)

    api = FakeApi(unresolved=True)
    api.comments = [dict(emitted)]  # Actual in-checkout producer output, no fallback.
    api.pull.update(pull)
    api.pull["draft"] = False
    attach_closing_issue_api(api)
    api.review_sha = HEAD
    store = StateStore(tmp_path / "paired" / "state.json")

    def run():
        return Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]

    assert run()["repair_requested"]
    first = next(a for a in store.actions().values() if a["kind"] == "fix")
    finish(api, first)
    api.pending_required = True
    api.review_state = stale_review_state
    # A stale-head owner report is not evidence for the changed result head.
    waiting = run()
    assert not waiting["auto_merge_requested"] and api.fix_attempts == 1
    assert store.action(first["key"])["handoff_state"] == "waiting_review"
    assert not any(route.endswith("/requested_reviewers") for route, _ in api.writes)
    saved = store.action(first["key"])
    assert saved["receipt_session_completed_at"] == "2026-10-01T12:05:30Z"
    assert saved["receipt_completed_at"] == saved["receipt_session_completed_at"]
    durable = store.snapshot()["enrollments"]["16"]["receipt_proofs"][0]
    assert durable["receipt_session_completed_at"] == saved["receipt_session_completed_at"]
    # A new coordinator reads only durable evidence, not a now-absent task API row.
    api.tasks.clear()
    waiting = run()
    assert store.action(first["key"])["handoff_state"] == "waiting_review"
    assert not waiting["repair_requested"] and not waiting["auto_merge_requested"]
    assert api.fix_attempts == 1
    assert api.review_attempts == 1
    assert not any(route.endswith("/requested_reviewers") for route, _ in api.writes)
    api.unresolved = False
    api.review_state = "PENDING"
    review = next(
        action for action in StateStore(store.path).actions().values()
        if action.get("kind") == "review"
    )
    api.complete_review_task(
        review["task_id"],
        review,
        source_action=StateStore(store.path).action(first["key"]),
        verdict="changes_requested",
        findings=[{
            "path": "frontend/styles.css",
            "comment": "Publish a bounded follow-up fixer request before approval.",
        }],
        report="One bounded follow-up is required before approval.",
    )
    first_review = run()
    second_review = run()
    third_review = run()
    assert not first_review["review_valid"]
    assert not second_review["review_valid"]
    assert not third_review["review_valid"]
    api.review_sha = RESULT_HEAD
    second = next(
        a for a in store.actions().values()
        if (a.get("kind") == "fix"
            and a.get("task_id") != first["task_id"]
            and a.get("head") == RESULT_HEAD)
    )
    assert second["head"] == RESULT_HEAD
    assert store.action(first["key"]) is None
    assert store.snapshot()["enrollments"]["16"]["receipt_proofs"][0]["receipt_head"] == RESULT_HEAD
    durable = StateStore(store.path).snapshot()["enrollments"]["16"]["receipt_proofs"][0]
    assert durable["receipt_session_completed_at"] == "2026-10-01T12:05:30Z"
    final_head = "d" * 40
    api.complete_task(second["task_id"], second, head_sha=final_head)
    api.head_sha = final_head
    api.pull["head"]["sha"] = final_head
    # The second task advances the PR head after the earlier review findings.
    api.tasks[second["task_id"]]["sessions"][0]["completed_at"] = "2026-10-01T12:07:00Z"
    api.tasks[second["task_id"]]["updated_at"] = "2026-10-01T12:10:00Z"
    api.unresolved = False
    api.source_failure = False
    api.review_state = "PENDING"
    waiting = run()
    assert store.action(second["key"])["handoff_state"] == "waiting_review"
    assert not waiting["auto_merge_requested"] and api.fix_attempts == 2
    api.tasks.clear()
    assert not run()["auto_merge_requested"]
    final_review = next(
        action for action in StateStore(store.path).actions().values()
        if action.get("kind") == "review" and action.get("source_task_id") == second["task_id"]
    )
    api.complete_review_task(
        final_review["task_id"],
        final_review,
        source_action=StateStore(store.path).action(second["key"]),
        verdict="pass",
        findings=[],
        report="No further bounded follow-up required.",
    )
    assert not run()["auto_merge_requested"]
    assert not run()["auto_merge_requested"]  # Required checks remain pending.
    api.pending_required = False
    refresh_owner_review(api, final_head, submitted_at="2026-10-01T12:07:02Z")
    assert run()["auto_merge_requested"]
    assert api.graphql_writes[-1][1]["expectedHeadOid"] == final_head
    run()
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 2
    assert store.snapshot()["enrollments"]["16"]["authorized_head"] == HEAD
    api.head_sha = "e" * 40
    api.pull["head"]["sha"] = api.head_sha
    assert "unauthorized-continuation" in run()["reasons"]
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 2


def actual_starter_consumer(tmp_path, *, admit=True):
    from test_issue_starter import (
        FakeApi as StarterApi, completed_task, make_coordinator, pull_request, start_task,
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
    emitted = next(c for c in producer.comments if c["body"].startswith(f"/hermes enroll {HEAD} issue "))
    api = FakeApi(unresolved=True)
    api.comments = [dict(emitted)]
    api.pull.update(pull)
    api.pull["draft"] = False
    attach_closing_issue_api(api)
    store = StateStore(tmp_path / "paired-main" / "state.json")
    if not admit:
        return api, store
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    action = next(a for a in store.actions().values() if a["kind"] == "fix")
    return api, store, action


def finish_v2(api, action, *, head=RESULT_HEAD, result="ready"):
    api.head_sha = head
    api.pull["head"]["sha"] = head
    api.complete_task(action["task_id"], action, result=result, head_sha=head)
    comment = api.comments[-1]
    comment["body"] = comment["body"].replace(
        "Hermes-Task-Receipt: v1", "Hermes-Task-Receipt: v2",
    ).replace(f"task={action['task_id']}\n", "").replace(
        f"base={BASE}", f"base={action['main_sha']}",
    )


@pytest.mark.parametrize("observation", ["before_first_poll", "after_compaction"])
def test_actual_paired_v2_main_advance_keeps_authority_but_requires_neutral_repair(
        tmp_path, observation):
    api, store, first = actual_starter_consumer(tmp_path)
    finish_v2(api, first)
    api.unresolved = False
    refresh_owner_review(api, RESULT_HEAD)
    api.pending_required = True

    def run():
        return Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]

    if observation == "after_compaction":
        assert not run()["auto_merge_requested"]
        assert store.action(first["key"]) is None  # Real lifecycle compaction.
        api.tasks.clear()
    moved_main = "d" * 40
    api.advance_main = True  # Fake API returns M1 after initial dispatch reads.
    api.pull["base"]["sha"] = moved_main
    api.pull["mergeable_state"] = "behind"
    api.pending_required = False
    result = run()
    assert "unauthorized-continuation" not in result["reasons"]
    assert not result["auto_merge_requested"] and not api.graphql_writes
    assert result["repair_requested"]
    second = next(
        a for a in store.actions().values()
        if (a.get("kind") == "fix"
            and a.get("task_id") != first["task_id"]
            and a.get("task_type") == "neutral")
    )
    assert second["head"] == RESULT_HEAD and second["main_sha"] == moved_main
    assert second["task_type"] == "neutral"
    assert f"base={moved_main}\n" in second["body"]
    assert api.fix_attempts == 2
    assert api.review_attempts == 0
    assert store.action(first["key"]) is None
    proof = store.snapshot()["enrollments"]["16"]["receipt_proofs"][0]
    assert proof["receipt_base"] == BASE and proof["main_sha"] == BASE
    assert proof["receipt_version"] == "v2"
    api.tasks.pop(first["task_id"], None)
    assert not run()["auto_merge_requested"] and api.fix_attempts == 2
    # Neutral repair produces C against M1; only fresh review/checks permit merge.
    repaired_head = "e" * 40
    finish_v2(api, second, head=repaired_head)
    api.tasks[second["task_id"]]["sessions"][0]["completed_at"] = "2026-10-01T12:07:00Z"
    api.tasks[second["task_id"]]["updated_at"] = "2026-10-01T12:07:00Z"
    api.pull["mergeable_state"] = "clean"
    assert not run()["auto_merge_requested"]  # Previous review predates second task.
    api.review_submitted_at = "2026-10-01T12:07:01Z"
    api.pending_required = True
    assert not run()["auto_merge_requested"]
    api.pending_required = False
    api.strict_protection = False
    assert not run()["auto_merge_requested"]  # Never bypass strict current-main policy.
    api.strict_protection = True
    review = next(
        action for action in StateStore(store.path).actions().values()
        if action.get("kind") == "review"
    )
    api.complete_review_task(
        review["task_id"], review, source_action=StateStore(store.path).action(second["key"]),
    )
    published = run()
    assert not published["review_valid"]
    merged = run()
    assert merged["auto_merge_requested"]
    assert api.graphql_writes[-1][1]["expectedHeadOid"] == repaired_head
    run()
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 2
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 2
    api.head_sha = "f" * 40
    api.pull["head"]["sha"] = api.head_sha
    assert "unauthorized-continuation" in run()["reasons"]
    assert len(api.graphql_writes) == 1


@pytest.mark.parametrize("change", [
    "base_repo", "head_repo", "base_ref", "base_sha", "head_sha", "unknown_mergeability",
])
def test_v2_moving_main_does_not_waive_live_reconciliation_fences(tmp_path, change):
    api, store, first = actual_starter_consumer(tmp_path)
    finish_v2(api, first)
    api.unresolved = False
    api.review_submitted_at = "2026-10-01T12:06:00Z"
    api.advance_main = True
    api.pull["base"]["sha"] = "d" * 40
    api.pull["mergeable_state"] = "behind"
    if change.endswith("repo"):
        api.pull[change.split("_")[0]]["repo"] = {"id": 42}
    elif change == "base_ref":
        api.pull["base"]["ref"] = "other"
    elif change == "base_sha":
        api.pull["base"]["sha"] = "f" * 40
    elif change == "head_sha":
        api.head_sha = api.pull["head"]["sha"] = "f" * 40
    else:
        api.pull["mergeable"] = None
        api.pull["mergeable_state"] = "unknown"
    worker = Coordinator(api, StateStore(store.path), clock=lambda: NOW)
    if change in {"base_repo", "head_repo", "base_ref"}:
        from deploy.cloud_coordinator import CoordinatorError
        with pytest.raises(CoordinatorError, match="identity did not match enrollment"):
            worker.run(apply=True)
    else:
        result = worker.run(apply=True)["pull_requests"][0]
        assert not result["repair_requested"] and not result["auto_merge_requested"]
    assert api.fix_attempts == 1 and not api.graphql_writes
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 1


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("field", [
    "receipt_base", "receipt_session_id", "receipt_task_id", "receipt_result",
    "receipt_version", "receipt_start_head", "receipt_head", "main_sha",
])
def test_compacted_receipt_metadata_must_match_canonical_body(tmp_path, version, field):
    api, store, worker, action = bound_worker(tmp_path)
    (finish_v2 if version == "v2" else finish)(api, action)
    api.unresolved = False
    refresh_owner_review(api, RESULT_HEAD)
    api.pending_required = True
    worker.run(apply=True)
    assert store.action(action["key"]) is None
    enrollment = store.snapshot()["enrollments"]["16"]
    proof = enrollment["receipt_proofs"][0]
    assert RESULT_HEAD in _authorized_result_heads(16, enrollment, {}, api.comments, BASE)[1]
    if field == "main_sha" and version == "v1":
        # Historical v1 projection did not save dispatch main. No invention needed.
        proof.pop("main_sha", None)
        proof.pop("receipt_version", None)
        assert RESULT_HEAD in _authorized_result_heads(16, enrollment, {}, api.comments, "d" * 40)[1]
        return
    if field == "receipt_result":
        proof[field] = "policy_broken"
    elif field == "receipt_version":
        proof[field] = "v1" if version == "v2" else "v2"
    else:
        proof[field] = "d" * 40
    if field == "receipt_task_id":
        proof["task_id"] = proof[field]
        # v2 task binding comes only from the trusted persisted API proof; it is
        # intentionally absent from the child body. Test its internal equality.
        if version == "v2":
            proof["task_id"] = "original-task"
    if field == "receipt_start_head":
        proof["head"] = proof[field]
        enrollment["owner_authorized_head"] = proof[field]
    initial, authorized, blocked = _authorized_result_heads(
        16, enrollment, {}, api.comments, "d" * 40,
    )
    assert initial == HEAD
    assert RESULT_HEAD not in authorized and not blocked
    assert proof["receipt_head"] not in authorized


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("change", [
    "float_author", "float_id", "string_author", "string_id", "bool_id",
    "duplicate", "mixed_version", "copied_nonce", "edited",
])
def test_restart_revalidates_unique_unchanged_strict_numeric_receipt(tmp_path, version, change):
    api, store, worker, action = bound_worker(tmp_path)
    (finish_v2 if version == "v2" else finish)(api, action)
    api.unresolved = False
    refresh_owner_review(api, RESULT_HEAD)
    api.pending_required = True
    worker.run(apply=True)
    assert store.action(action["key"]) is None
    api.tasks.clear()
    receipt = api.comments[-1]
    if change.endswith("author"):
        convert = float if change == "float_author" else str
        receipt["user"] = {"id": convert(receipt["user"]["id"])}
    elif change.endswith("id"):
        convert = {"float_id": float, "string_id": str, "bool_id": bool}[change]
        receipt["id"] = convert(receipt["id"])
    elif change == "edited":
        receipt["updated_at"] = "2026-10-01T12:05:01Z"
    else:
        duplicate = dict(receipt, id=10002)
        if change == "mixed_version":
            if version == "v2":
                duplicate["body"] = duplicate["body"].replace("v2\n", "v1\n").replace(
                    "session=", f"task={action['task_id']}\nsession=",
                )
            else:
                duplicate["body"] = duplicate["body"].replace("v1\n", "v2\n").replace(
                    f"task={action['task_id']}\n", "",
                )
        elif change == "copied_nonce":
            duplicate["body"] = "quoted:\n" + duplicate["body"]
        api.comments.append(duplicate)
    result = Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]
    assert "unauthorized-continuation" in result["reasons"]
    assert not result["repair_requested"] and not result["auto_merge_requested"]
    assert not api.graphql_writes and api.fix_attempts == 1


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("change", [
    "task_owner", "task_creator", "task_repository", "session_id", "session_task",
    "session_nonce", "missing_pull", "receipt_author", "receipt_edited", "receipt_body",
])
def test_bound_consumer_rejects_unverified_task_session_and_receipt_identity(tmp_path, change, version):
    api, store, worker, action = bound_worker(tmp_path)
    (finish_v2 if version == "v2" else finish)(api, action)
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


@pytest.mark.parametrize("change", ["none", "body_and_edge", "edge_only"])
def test_published_starter_command_is_not_authority_for_changed_unlinked_pull(tmp_path, change):
    from test_issue_starter import (
        FakeApi as StarterApi, completed_task, make_coordinator, pull_request,
        start_task, closing_issue_response,
    )

    consumer = FakeApi(unresolved=True)
    store = StateStore(tmp_path / "consumer" / "state.json")
    observed = {}

    class Producer(StarterApi):
        def get(self, route):
            return super().get(route.replace("/issues/16/comments", "/issues/41/comments"))

        def post(self, route, body):
            result = super().post(route.replace("/issues/16/comments", "/issues/41/comments"), body)
            if route.endswith("/issues/16/comments") and body.get("body", "").startswith("/hermes enroll "):
                # Run downstream before the producer receives its own POST response.
                if change != "none":
                    self.closing_issues = []
                if change == "body_and_edge":
                    self.pulls[0]["body"] = "Changed without a closing reference."
                consumer.comments = [deepcopy(result)]
                consumer.pull.update(deepcopy(self.pulls[0]))
                consumer.pull["draft"] = False
                old_graphql = consumer.graphql
                def graphql(query, variables):
                    if "closingIssuesReferences" in query:
                        return closing_issue_response(consumer.pull, nodes=deepcopy(self.closing_issues))
                    return old_graphql(query, variables)
                consumer.graphql = graphql
                observed["consumer_result"] = Coordinator(
                    consumer, store, clock=lambda: 1790888460,
                ).run(apply=True)
                observed["enrollments"] = store.snapshot()["enrollments"]
            return result

    pull = pull_request(pull_id=160000016, node_id="PR_node_16", head_ref="topic")
    pull["number"] = 16
    producer = Producer(pulls=[pull])
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    result = make_coordinator(tmp_path, producer).run(apply=True)
    emitted = [c for c in producer.comments if c["body"].startswith("/hermes enroll ")]
    assert len(emitted) == 1
    if change == "none":
        assert result["handed_off"] == 1
        assert observed["enrollments"]["16"]["authorized_head"] == "a" * 40
        assert consumer.fix_attempts == 1
    else:
        assert result["handed_off"] == 0
        # Expected RED: downstream must not persist or act on this stale handoff.
        assert not observed["enrollments"], "Published handoff enrolled changed/unlinked same-SHA PR"
        assert consumer.fix_attempts == 0
        assert not consumer.writes and not consumer.graphql_writes


def attach_closing_issue_api(api):
    from test_issue_starter import closing_issue_response, issue_reference

    api.closing_issues = [issue_reference()]
    original = api.graphql

    def graphql(query, variables):
        if "closingIssuesReferences" in query:
            return closing_issue_response(api.pull, nodes=deepcopy(api.closing_issues))
        return original(query, variables)

    api.graphql = graphql


def test_actual_starter_admission_persists_compact_provenance(tmp_path):
    api, store = actual_starter_consumer(tmp_path, admit=False)
    emitted = api.comments[0]
    digest = hashlib.sha256(api.pull["body"].encode("utf-8")).hexdigest()
    assert emitted["body"] == f"/hermes enroll {HEAD} issue 28 body-sha256 {digest}"
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
    assert enrollment["issue"] == 16
    assert enrollment["starter_admission"] == {
        "version": 1, "issue_number": 28, "head_sha": HEAD,
        "body_sha256": digest, "comment_id": emitted["id"],
        "comment_created_at": emitted["created_at"],
    }


@pytest.mark.parametrize("change", [
    "body", "edge", "draft", "head", "comment_body", "comment_time",
    "comment_author", "comment_missing", "comment_duplicate", "incomplete",
])
@pytest.mark.parametrize("renewal", [False, True])
def test_starter_admission_final_precommit_aborts_entire_preparation(tmp_path, monkeypatch, change, renewal):
    from deploy.cloud_coordinator import ApiError, CoordinatorError

    api, store = actual_starter_consumer(tmp_path, admit=False)
    if renewal:
        Coordinator(api, store, clock=lambda: NOW).run(apply=True)
        command = deepcopy(api.comments[0])
        command["id"] += 100
        api.comments.append(command)
    store.record_event(7)
    before = store.path.read_bytes()
    baseline = store.snapshot()
    writes, graphql_writes = deepcopy(api.writes), deepcopy(api.graphql_writes)
    exported = []
    monkeypatch.setattr(store, "write_lifecycle_export", lambda **kw: exported.append(kw))
    worker = Coordinator(api, store, clock=lambda: NOW)
    original = worker._build_plan

    def changed_after_plan(*, apply):
        plan = original(apply=apply)
        assert plan["commands"] and plan["starter_admissions"]
        if not renewal:
            assert plan["pull_requests"][0]["repair"]
        # Other prepared scan work must roll back too, not just the enrollment.
        store.record_event(8)
        if change == "body":
            api.pull["body"] += " changed after planning"
        elif change == "edge":
            api.closing_issues = []
        elif change == "draft":
            api.pull["draft"] = True
        elif change == "head":
            api.pull["head"]["sha"] = RESULT_HEAD
        elif change == "comment_body":
            api.comments[-1]["body"] += " "
        elif change == "comment_time":
            api.comments[-1]["created_at"] = api.comments[-1]["updated_at"] = "2026-10-01T20:01:00Z"
        elif change == "comment_author":
            api.comments[-1]["user"]["id"] = float(OWNER)
        elif change == "comment_missing":
            api.comments.clear()
        elif change == "comment_duplicate":
            api.comments.append(deepcopy(api.comments[-1]))
        else:
            def unavailable(*args):
                raise ApiError("incomplete canonical evidence")
            api.graphql = unavailable
        return plan

    monkeypatch.setattr(worker, "_build_plan", changed_after_plan)
    with pytest.raises(CoordinatorError):
        worker.run(apply=True)
    assert store.path.read_bytes() == before
    assert StateStore(store.path).snapshot() == store.snapshot()
    assert store.snapshot() == baseline
    assert not exported and api.writes == writes and api.graphql_writes == graphql_writes


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("issue_number", True),
    ("issue_number", 0), ("issue_number", 2147483648), ("issue_number", "28"),
    ("head_sha", "A" * 40), ("body_sha256", "f" * 63),
    ("comment_id", 0), ("comment_id", 9100.0),
    ("comment_created_at", "2026-10-01"), ("unexpected", "unbounded"),
    (None, None), (None, {}),
])
def test_starter_admission_provenance_rejects_malformed_durable_state(tmp_path, field, value):
    from deploy.cloud_coordinator import CoordinatorError

    api, store, _ = actual_starter_consumer(tmp_path)
    state = store.snapshot()
    enrollment = state["enrollments"]["16"]
    if field is None:
        enrollment["starter_admission"] = value
    else:
        enrollment["starter_admission"][field] = value
    store.path.write_text(json.dumps(state))
    with pytest.raises(CoordinatorError, match="Starter admission"):
        StateStore(store.path).snapshot()


def test_admitted_starter_body_report_and_v2_receipt_survive_restart_and_manual_renewal(tmp_path):
    api, store, action = actual_starter_consumer(tmp_path)
    original = store.snapshot()["enrollments"]["16"]["starter_admission"]
    api.pull["body"] += "\n\nFocused checks and independent review report."
    api.closing_issues = []  # The starter proof is not lifetime PR authority.
    finish_v2(api, action)
    api.unresolved = False
    refresh_owner_review(api, RESULT_HEAD)
    api.pending_required = True

    def run():
        return Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]

    assert not run()["auto_merge_requested"]
    assert store.action(action["key"]) is None
    api.tasks.clear()
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["starter_admission"] == original
    assert enrollment["authorized_head"] == HEAD and enrollment["attempts"] == 1
    proofs = enrollment["receipt_proofs"]
    api.comments.append({
        "id": 10001, "body": f"/hermes enroll {RESULT_HEAD}",
        "user": {"id": OWNER}, "created_at": "2026-10-01T20:11:00Z",
        "updated_at": "2026-10-01T20:11:00Z",
    })
    assert not run()["auto_merge_requested"]
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["starter_admission"] == original
    assert enrollment["receipt_proofs"] == proofs
    assert enrollment["authorized_head"] == HEAD and enrollment["attempts"] == 1
    assert enrollment["owner_authorized_head"] == RESULT_HEAD
    api.pending_required = False
    refresh_owner_review(api, RESULT_HEAD, submitted_at="2026-10-01T12:07:00Z")
    assert run()["auto_merge_requested"]
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 1
    assert api.review_attempts == 0
