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
    api.review_submitted_at = "2026-10-01T12:06:00Z"
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
    api.review_submitted_at = "2026-10-01T12:06:00Z"
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
    api.review_sha = RESULT_HEAD
    api.review_state = stale_review_state
    # Same result head is insufficient: this review predates task completion.
    waiting = run()
    assert not waiting["auto_merge_requested"] and api.fix_attempts == 1
    assert store.action(first["key"])["handoff_state"] == "waiting_review"
    assert sum(route.endswith("/requested_reviewers") for route, _ in api.writes) == 1
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
    assert sum(route.endswith("/requested_reviewers") for route, _ in api.writes) == 1
    api.review_sha = RESULT_HEAD
    api.review_state = "COMMENTED"
    api.review_submitted_at = "2026-10-01T08:05:31-04:00"
    assert run()["repair_requested"]
    second = next(a for a in store.actions().values() if a.get("task_id") == "task-2")
    assert second["head"] == RESULT_HEAD
    assert store.action(first["key"]) is None
    assert store.snapshot()["enrollments"]["16"]["receipt_proofs"][0]["receipt_head"] == RESULT_HEAD
    durable = StateStore(store.path).snapshot()["enrollments"]["16"]["receipt_proofs"][0]
    assert durable["receipt_session_completed_at"] == "2026-10-01T12:05:30Z"
    api.complete_task(second["task_id"], second)
    # The second task keeps the same head but completes after the earlier review.
    api.tasks[second["task_id"]]["sessions"][0]["completed_at"] = "2026-10-01T12:07:00Z"
    api.tasks[second["task_id"]]["updated_at"] = "2026-10-01T12:10:00Z"
    api.unresolved = False
    api.review_state = "APPROVED"
    waiting = run()
    assert store.action(second["key"])["handoff_state"] == "waiting_review"
    assert not waiting["auto_merge_requested"] and api.fix_attempts == 2
    api.tasks.clear()
    assert not run()["auto_merge_requested"]
    api.review_submitted_at = "2026-10-01T12:07:01Z"
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


def actual_starter_consumer(tmp_path):
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
    emitted = next(c for c in producer.comments if c["body"] == f"/hermes enroll {HEAD}")
    api = FakeApi(unresolved=True)
    api.comments = [dict(emitted)]
    api.pull.update(pull)
    api.pull["draft"] = False
    store = StateStore(tmp_path / "paired-main" / "state.json")
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
    api.review_submitted_at = "2026-10-01T12:06:00Z"
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
    second = next(a for a in store.actions().values() if a.get("task_id") == "task-2")
    assert second["head"] == RESULT_HEAD and second["main_sha"] == moved_main
    assert second["task_type"] == "neutral"
    assert f"base={moved_main}\n" in second["body"]
    assert api.fix_attempts == 2
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
    assert run()["auto_merge_requested"]
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
    api.pending_required = True
    api.review_submitted_at = "2026-10-01T12:06:00Z"
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
    api.pending_required = True
    api.review_submitted_at = "2026-10-01T12:06:00Z"
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
