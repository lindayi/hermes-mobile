"""Synthetic regression seams between SHA-bound enrollment and durable lifecycle."""
from copy import deepcopy
import hashlib
import json
import re

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

    def inspect_before_compaction(issue, head, current_main_sha=None):
        enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
        initial, authorized, blocked = _authorized_result_heads(
            issue, enrollment, {}, api.comments, BASE,
        )
        assert initial == HEAD and RESULT_HEAD in authorized and not blocked
        observed.append(True)
        original_retire(issue, head, current_main_sha)

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
    producer.comments[0]["created_at"] = "2026-10-01T11:00:00Z"
    producer.comments[0]["updated_at"] = "2026-10-01T11:00:00Z"
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    producer.task_detail["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["completed_at"] = "2026-10-01T11:05:00Z"
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


def actual_starter_consumer(tmp_path, *, admit=True, missing_review=False,
                            completed_at=True, extra_artifact=False,
                            existing_closing=True, id_only_artifact=False,
                            session_id="session-1"):
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
    if not existing_closing:
        producer.closing_issues = []
    producer.comments[0]["created_at"] = "2026-10-01T11:00:00Z"
    producer.comments[0]["updated_at"] = "2026-10-01T11:00:00Z"
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    producer.task_detail["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["created_at"] = "2026-10-01T11:02:00Z"
    producer.task_detail["sessions"][0]["completed_at"] = "2026-10-01T11:05:00Z"
    producer.task_detail["sessions"][0]["id"] = session_id
    if id_only_artifact:
        producer.task_detail["artifacts"][1]["data"].pop("global_id")
    if not completed_at:
        producer.task_detail["sessions"][0].pop("completed_at")
    if extra_artifact:
        producer.task_detail["artifacts"].append({
            "provider": "copilot", "type": "log", "data": {},
        })
    assert make_coordinator(tmp_path, producer).run(apply=True)["handed_off"] == 1
    emitted = next(c for c in producer.comments if c["body"].startswith(f"/hermes enroll {HEAD} issue "))
    api = FakeApi(unresolved=True)
    api.comments = [dict(emitted)]
    api.start_comment_id = producer.comments[0]["id"]
    api.initial_source_issue = deepcopy(producer.current_issue)
    api.source_comments = deepcopy(producer.comments)
    api.source_timeline = deepcopy(producer.timeline)
    api.starter_state_path = make_coordinator(tmp_path, producer).store.path
    api.issue_edit_evidence = deepcopy(producer.edit_evidence)
    original_get, original_get_all = api.get, api.get_all
    original_graphql = api.graphql

    def get(route):
        if route == "repos/lindayi/hermes-mobile/issues/28":
            return api.initial_source_issue
        return original_get(route)

    def get_all(route, *, collection=None):
        if route.startswith("repos/lindayi/hermes-mobile/issues/28/comments?"):
            return api.source_comments
        if route.startswith("repos/lindayi/hermes-mobile/issues/28/timeline?"):
            return api.source_timeline
        values = original_get_all(route, collection=collection)
        if missing_review and "/check-runs?" in route:
            return [item for item in values if item.get("name") != "agent-review"]
        return values

    def graphql(query, variables):
        if "IssueEditEvidence" in query:
            return {
                "data": {"repository": {
                    "databaseId": 1399942965,
                    "nameWithOwner": "lindayi/hermes-mobile",
                    "issue": {
                        **api.issue_edit_evidence,
                    },
                }},
            }
        return original_graphql(query, variables)

    api.get, api.get_all = get, get_all
    api.graphql = graphql
    api.pull.update(pull)
    api.pull["draft"] = False
    api.tasks[producer.task_detail["id"]] = deepcopy(producer.task_detail)
    api.task_posts = 1
    api.pull_files = [
        {"filename": "tests/test_cloud_coordinator.py", "status": "modified", "sha": "f" * 40},
    ]
    api.blob_contents["f" * 40] = b"Synthetic coordinator test contents\n"
    if missing_review:
        api.owner_review_body = "no independent review has been published"
    attach_closing_issue_api(api)
    store = StateStore(tmp_path / "paired-main" / "state.json")
    if not admit:
        return api, store
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    action = next(a for a in store.actions().values() if a["kind"] == "fix")
    return api, store, action


def test_actual_starter_dispatches_first_review_without_a_fixer_or_failed_check(tmp_path):
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
    producer.comments[0]["created_at"] = "2026-10-01T11:00:00Z"
    producer.comments[0]["updated_at"] = "2026-10-01T11:00:00Z"
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    producer.task_detail["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["completed_at"] = "2026-10-01T11:05:00Z"
    assert make_coordinator(tmp_path, producer).run(apply=True)["handed_off"] == 1
    emitted = next(c for c in producer.comments if c["body"].startswith(f"/hermes enroll {HEAD} issue "))

    class SourceApi(FakeApi):
        def get(self, route):
            if route == "repos/lindayi/hermes-mobile/issues/28":
                return producer.current_issue
            return super().get(route)

        def get_all(self, route, *, collection=None):
            if route.startswith("repos/lindayi/hermes-mobile/issues/28/comments?"):
                return list(producer.comments)
            if route.startswith("repos/lindayi/hermes-mobile/issues/28/timeline?"):
                return list(producer.timeline)
            values = super().get_all(route, collection=collection)
            if "/check-runs?" in route:
                return [item for item in values if item.get("name") != "agent-review"]
            return values

        def graphql(self, query, variables):
            if "IssueEditEvidence" in query:
                return {
                    "data": {"repository": {
                        "databaseId": 1399942965,
                        "nameWithOwner": "lindayi/hermes-mobile",
                        "issue": {
                            "lastEditedAt": None,
                            "userContentEdits": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                        },
                    }},
                }
            return super().graphql(query, variables)

    api = SourceApi()
    api.comments = [dict(emitted)]
    api.pull.update(pull)
    api.pull["draft"] = False
    api.pull_files = [
        {"filename": "README.md", "status": "modified", "sha": "f" * 40},
    ]
    api.blob_contents["f" * 40] = b"Synthetic starter PR contents\n"
    api.owner_review_body = "no independent review has been published"
    api.tasks[producer.task_detail["id"]] = producer.task_detail
    api.task_posts = 1
    attach_closing_issue_api(api)
    path = tmp_path / "starter-first-review" / "state.json"
    clock = [1790942400]

    def run():
        return Coordinator(api, StateStore(path), clock=lambda: clock[0]).run(apply=True)

    first = run()
    second = run()
    third = run()
    assert first["pull_requests"][0]["required_checks_green"] is False
    assert second["pull_requests"][0]["required_checks_green"] is False
    actions = StateStore(path).actions()
    reviews = [item for item in actions.values() if item.get("kind") == "review"]
    assert len(reviews) == 1, (
        first["pull_requests"][0]["reasons"], second["pull_requests"][0]["reasons"],
        third["pull_requests"][0]["reasons"], api.review_attempts,
        StateStore(path).snapshot()["enrollments"]["16"],
    )
    assert reviews[0]["status"] == "sent"
    assert reviews[0]["source_task_id"] == producer.task_detail["id"]
    assert reviews[0]["task_id"] != reviews[0]["source_task_id"]
    assert not any(item.get("kind") == "fix" for item in actions.values())
    assert api.fix_attempts == 0 and api.review_attempts == 1
    assert first["pull_requests"][0]["auto_merge_eligible"] is False
    assert second["pull_requests"][0]["auto_merge_eligible"] is False
    assert third["pull_requests"][0]["auto_merge_eligible"] is False

    source = StateStore(path).snapshot()["enrollments"]["16"]["initial_source"]
    api.complete_review_task(
        reviews[0]["task_id"], reviews[0],
        source_action={
            "source_start_head": source["head_sha"],
            "source_session_id": source["session_id"],
            "source_comment_id": source["admission_comment_id"],
        },
        verdict="pass", findings=[], report="No findings in the complete source inventory.",
    )
    for _ in range(3):
        result = run()
    assert result["pull_requests"][0]["review_valid"] is True
    assert result["pull_requests"][0]["required_checks_green"] is True
    published = [
        item for item in api.owner_reviews
        + [api._current_owner_review_record()]
        if (item.get("commit_id") == HEAD and item.get("state") == "COMMENTED"
            and item.get("body", "").startswith(
                '{"schema":"hermes-independent-agent-review-v1",',
            ))
    ]
    assert len(published) == 1
    agent_statuses = [
        item for item in api.status_log.get(HEAD, [])
        if item.get("context") == "agent-review" and item.get("state") == "success"
    ]
    assert len(agent_statuses) == 1
    assert not any(
        item.get("kind") == "fix" for item in StateStore(path).actions().values()
    )
    assert api.review_attempts == 1 and api.fix_attempts == 0

    review_id = api.owner_review_id
    review_digest = hashlib.sha256(api.owner_review_body.encode("utf-8")).hexdigest()
    api.comments.append({
        "id": 11000, "user": {"id": OWNER},
        "body": f"/hermes authorize-sensitive {HEAD} review {review_id} {review_digest}",
        "created_at": "2026-10-02T12:01:00Z",
        "updated_at": "2026-10-02T12:01:00Z",
    })
    clock[0] = 1790942520
    authorized = run()["pull_requests"][0]
    assert authorized["auto_merge_eligible"] is True
    assert authorized["auto_merge_requested"] is True
    assert api.review_attempts == 1 and api.fix_attempts == 0


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
    assert merged["auto_merge_requested"], merged["reasons"]
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
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    emitted = api.comments[0]
    digest = hashlib.sha256(api.pull["body"].encode("utf-8")).hexdigest()
    assert emitted["body"] == (
        f"/hermes enroll {HEAD} issue 28 body-sha256 {digest} "
        f"source-task task-1 source-session session-1 "
        f"source-command {api.start_comment_id}"
    )
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
    assert enrollment["issue"] == 16
    assert enrollment["starter_admission"] == {
        "version": 3, "issue_number": 28, "head_sha": HEAD,
        "body_sha256": digest, "comment_id": emitted["id"],
        "comment_created_at": emitted["created_at"],
        "source_task_id": "task-1", "source_session_id": "session-1",
        "start_comment_id": api.start_comment_id,
    }


@pytest.mark.parametrize("change", [
    "body", "edge", "draft", "head", "comment_body", "comment_time",
    "comment_author", "comment_missing", "comment_duplicate", "incomplete",
])
@pytest.mark.parametrize("renewal", [False, True])
def test_starter_admission_final_precommit_aborts_entire_preparation(tmp_path, monkeypatch, change, renewal):
    from deploy.cloud_coordinator import ApiError, CoordinatorError

    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
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
    ("version", True), ("version", 4), ("issue_number", True),
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


@pytest.mark.parametrize("change", [
    "task_owner", "task_creator", "task_repository", "task_state", "session_id",
    "session_user", "session_branch", "branch_artifact", "command_task",
    "command_session", "command_edited", "issue_body_edited",
    "task_unknown", "task_created", "session_created", "session_completed",
    "session_owner", "session_repository", "session_task", "session_state",
    "pull_artifact", "duplicate_branch", "command_missing", "issue_title_edited",
    "incomplete_edits", "global_null", "global_mismatch",
])
def test_initial_starter_source_rejects_unbound_or_changed_evidence(tmp_path, change):
    positive, positive_store = actual_starter_consumer(
        tmp_path / "positive", admit=False, missing_review=True,
    )
    positive.unresolved = False
    for _ in range(3):
        Coordinator(positive, StateStore(positive_store.path),
                    clock=lambda: 1790942400).run(apply=True)
    assert positive.review_attempts == 1 and positive.fix_attempts == 0
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    task = next(iter(api.tasks.values()))
    if change == "task_owner":
        task["owner"] = {"id": 9}
    elif change == "task_creator":
        task["creator"] = {"id": 9}
    elif change == "task_repository":
        task["repository"] = {"id": 9}
    elif change == "task_state":
        task["state"] = "in_progress"
    elif change == "session_id":
        task["sessions"][0]["id"] = "other-session"
    elif change == "session_user":
        task["sessions"][0]["user"] = {"id": 9}
    elif change == "session_branch":
        task["sessions"][0]["head_ref"] = "other-branch"
    elif change == "branch_artifact":
        task["artifacts"][0]["data"]["head_ref"] = "other-branch"
    elif change == "command_task":
        api.comments[0]["body"] = api.comments[0]["body"].replace(
            "source-task task-1", "source-task untrusted-task",
        )
    elif change == "command_session":
        api.comments[0]["body"] = api.comments[0]["body"].replace(
            "source-session session-1", "source-session untrusted-session",
        )
    elif change == "command_edited":
        start = next(item for item in api.source_comments if item["body"] == "/hermes start")
        start["updated_at"] = "2026-10-01T20:01:00Z"
    elif change == "issue_body_edited":
        api.initial_source_issue["body"] += " changed"
        api.issue_edit_evidence = {
            "lastEditedAt": "2026-10-01T20:01:00Z",
            "userContentEdits": {
                "nodes": [{"editedAt": "2026-10-01T20:01:00Z"}],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
    elif change == "task_unknown":
        task["state"] = "unknown"
    elif change == "task_created":
        task["created_at"] = "2026-10-01T10:00:00Z"
    elif change == "session_created":
        task["sessions"][0]["created_at"] = "2026-10-01T10:00:00Z"
    elif change == "session_completed":
        task["sessions"][0]["completed_at"] = "2026-10-03T12:00:00Z"
    elif change == "session_owner":
        task["sessions"][0]["owner"] = {"id": 9}
    elif change == "session_repository":
        task["sessions"][0]["repository"] = {"id": 9}
    elif change == "session_task":
        task["sessions"][0]["task_id"] = "other"
    elif change == "session_state":
        task["sessions"][0]["state"] = "unknown"
    elif change == "pull_artifact":
        task["artifacts"][1]["data"]["id"] += 1
    elif change in {"global_null", "global_mismatch"}:
        task["artifacts"][1]["data"]["global_id"] = (
            None if change == "global_null" else "PR_other"
        )
    elif change == "duplicate_branch":
        task["artifacts"].append(deepcopy(task["artifacts"][0]))
    elif change == "command_missing":
        api.source_comments = [c for c in api.source_comments if c["body"] != "/hermes start"]
    elif change == "issue_title_edited":
        api.initial_source_issue["title"] += " changed"
        api.source_timeline.append({"event": "renamed", "created_at": "2026-10-01T12:00:00Z"})
    elif change == "incomplete_edits":
        api.issue_edit_evidence["userContentEdits"]["pageInfo"]["hasNextPage"] = True
    def run():
        return Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]

    first = run()
    second = run()
    if change == "task_state":
        assert "agent" in first["reasons"] and "agent" in second["reasons"]
        assert "starter-source-provenance" not in first["reasons"]
    else:
        assert "starter-source-provenance" in first["reasons"]
        assert "starter-source-provenance" in second["reasons"]
    assert not any(
        action.get("kind") in {"review", "fix"}
        for action in StateStore(store.path).actions().values()
    )
    outbox = StateStore(store.path).snapshot()["outbox"]
    assert sum(key.endswith(":starter-source-provenance") for key in outbox) == (
        0 if change == "task_state" else 1
    )
    assert api.review_attempts == 0 and api.fix_attempts == 0


def test_initial_starter_source_rejects_a_removed_closing_edge(tmp_path):
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    api.closing_issues = []
    result = Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=True)
    assert not result["pull_requests"]
    assert not any(
        action.get("kind") in {"review", "fix"}
        for action in StateStore(store.path).actions().values()
    )
    assert api.review_attempts == 0 and api.fix_attempts == 0


@pytest.mark.parametrize("change", [
    None, "errors", "data_null", "data_list", "issue_null", "connection_null",
    "node_not_object", "page_info_not_object", "null_with_nodes",
    "latest_missing", "edited_without_nodes", "changed_across_pages",
    "rename_payload_missing",
])
def test_initial_starter_source_requires_consistent_complete_edit_history(tmp_path, change):
    # Baseline: coherent two-page history and a title rename, all before `/hermes start`.
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    rename = {
        "event": "renamed", "created_at": "2026-10-01T10:15:00Z",
        "rename": {"from": "Earlier title", "to": api.initial_source_issue["title"]},
    }
    if change == "rename_payload_missing":
        rename.pop("rename")
    api.source_timeline.append(rename)
    older, latest = "2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z"
    original_graphql = api.graphql
    edit_reads = []

    def graphql(query, variables):
        if "StarterIssueEditEvidence" not in query:
            return original_graphql(query, variables)
        cursor = variables.get("after")
        edit_reads.append(cursor)
        assert cursor in {None, "page-two"}
        first_page = cursor is None
        nodes = [{"editedAt": older if first_page else latest}]
        issue = {
            "lastEditedAt": latest,
            "userContentEdits": {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": first_page,
                    "endCursor": "page-two" if first_page else None,
                },
            },
        }
        response = {"data": {"repository": {
            "databaseId": 1399942965,
            "nameWithOwner": "lindayi/hermes-mobile",
            "issue": issue,
        }}}
        if change == "errors":
            response["errors"] = [{"message": "Unavailable edit history"}]
        elif change == "data_null":
            response["data"] = None
        elif change == "data_list":
            response["data"] = []
        elif change == "issue_null":
            response["data"]["repository"]["issue"] = None
        elif change == "connection_null":
            issue["userContentEdits"] = None
        elif change == "node_not_object":
            nodes[0] = "not-an-edit-node"
        elif change == "page_info_not_object":
            issue["userContentEdits"]["pageInfo"] = []
        elif change == "null_with_nodes":
            issue["lastEditedAt"] = None
        elif change == "latest_missing" and not first_page:
            nodes[0] = {"editedAt": "2026-10-01T10:15:00Z"}
        elif change == "edited_without_nodes":
            nodes.clear()
        elif change == "changed_across_pages" and first_page:
            issue["lastEditedAt"] = older
        return response

    api.graphql = graphql
    writes, graphql_writes = list(api.writes), list(api.graphql_writes)

    def run():
        return Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]

    first = run()
    second = run()
    actions = StateStore(store.path).actions().values()
    outbox = StateStore(store.path).snapshot()["outbox"]
    assert edit_reads
    if change is None:
        assert "starter-source-provenance" not in first["reasons"]
        assert api.review_attempts == 1 and api.fix_attempts == 0
        assert sum(action.get("kind") == "review" for action in actions) == 1
        assert not any(key.endswith(":starter-source-provenance") for key in outbox)
        return
    assert "starter-source-provenance" in first["reasons"]
    assert "starter-source-provenance" in second["reasons"]
    assert not any(action.get("kind") in {"review", "fix"} for action in actions)
    assert sum(key.endswith(":starter-source-provenance") for key in outbox) == 1
    assert api.review_attempts == 0 and api.fix_attempts == 0
    # Only the deduplicated blocker outcome and its pending status are written.
    new_writes = api.writes[len(writes):]
    assert api.writes[:len(writes)] == writes and api.graphql_writes == graphql_writes
    comments = [body for route, body in new_writes if route.endswith("/issues/16/comments")]
    assert len(comments) == 1
    assert "No authenticated initial-source task provenance" in comments[0]["body"]
    assert all(
        route.endswith("/issues/16/comments") or "/statuses/" in route
        for route, _ in new_writes
    )


def test_legacy_starter_admission_does_not_adopt_matching_task_list_entries(tmp_path):
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    api.comments[0]["body"] = re.sub(
        r" source-task [^ ]+ source-session [^ ]+ source-command [0-9]+$",
        "", api.comments[0]["body"],
    )

    def run():
        return Coordinator(
            api, StateStore(store.path), clock=lambda: NOW,
        ).run(apply=True)["pull_requests"][0]

    first = run()
    second = run()
    assert "starter-source-provenance" in first["reasons"]
    assert "starter-source-provenance" in second["reasons"]
    assert not any(
        action.get("kind") in {"review", "fix"}
        for action in StateStore(store.path).actions().values()
    )
    assert api.review_attempts == 0 and api.fix_attempts == 0


@pytest.mark.parametrize("mode", [
    "legacy", "legacy_link_intent", "missing_completed_at", "extra_artifact", "already_reviewed",
    "id_only_artifact",
])
def test_initial_source_supported_handoffs_and_exact_head_review(tmp_path, mode):
    api, store = actual_starter_consumer(
        tmp_path, admit=False, missing_review=True,
        completed_at=mode != "missing_completed_at",
        extra_artifact=mode == "extra_artifact",
        existing_closing=mode != "legacy_link_intent",
        id_only_artifact=mode == "id_only_artifact",
    )
    api.unresolved = False
    task = next(iter(api.tasks.values()))
    if mode in {"legacy", "legacy_link_intent"}:
        if mode == "legacy_link_intent":
            saved = json.loads(api.starter_state_path.read_text())
            record = next(iter(saved["commands"].values()))
            assert record["link_intent"]["session_id"] == "session-1"
            record.pop("source_session_id")
            api.starter_state_path.write_text(json.dumps(saved))
        api.comments[0]["body"] = re.sub(
            r" source-task [^ ]+ source-session [^ ]+ source-command [0-9]+$",
            "", api.comments[0]["body"],
        )
        blocked = Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]
        assert "starter-source-provenance" in blocked["reasons"]
        legacy_enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
        assert legacy_enrollment["starter_admission"]["version"] == 1
        assert "initial_source" not in legacy_enrollment
        assert api.review_attempts == 0 and api.fix_attempts == 0
    elif mode == "already_reviewed":
        refresh_owner_review(api, HEAD)
    before = api.starter_state_path.read_bytes()
    for _ in range(4):
        result = Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
            starter_state_path=api.starter_state_path,
        ).run(apply=True)["pull_requests"][0]
    assert api.starter_state_path.read_bytes() == before
    assert api.fix_attempts == 0
    assert api.review_attempts == (0 if mode == "already_reviewed" else 1)
    assert "starter-source-provenance" not in result["reasons"]
    enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
    assert enrollment["attempts"] == 0 and not enrollment.get("receipt_proofs")
    assert enrollment["initial_source"]["task_id"] == task["id"]
    if mode in {"legacy", "legacy_link_intent"}:
        assert enrollment["starter_admission"] == legacy_enrollment["starter_admission"]
        assert enrollment["comment"] == legacy_enrollment["comment"]
        assert enrollment["authorized_head"] == legacy_enrollment["authorized_head"]
    if mode != "already_reviewed":
        review = next(a for a in store.actions().values() if a["kind"] == "review")
        source = enrollment["initial_source"]
        api.complete_review_task(
            review["task_id"], review,
            source_action={
                "source_start_head": source["head_sha"],
                "source_session_id": source["session_id"],
                "source_comment_id": source["admission_comment_id"],
            },
            verdict="pass", findings=[], report="Verified initial source accepted.",
        )
        for _ in range(4):
            result = Coordinator(
                api, StateStore(store.path), clock=lambda: 1790942400,
                starter_state_path=api.starter_state_path,
            ).run(apply=True)["pull_requests"][0]
        assert result["review_valid"] and result["required_checks_green"]
        assert result["auto_merge_eligible"] and api.review_attempts == 1
        assert sum(item.get("state") == "success"
                   for item in api.status_log.get(HEAD, [])
                   if item.get("context") == "agent-review") == 1
        assert api.starter_state_path.read_bytes() == before


@pytest.mark.parametrize("publication", ["sent", "uncertain", "lost_response"])
@pytest.mark.parametrize("legacy_identity", [False, True])
def test_first_review_anchor_survives_main_advance_before_dispatch(
        tmp_path, monkeypatch, publication, legacy_identity):
    from deploy import cloud_coordinator

    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    attempts = []
    write = api.write
    anchor_request = cloud_coordinator.review_anchor_request

    def legacy_anchor(snapshot, source_action, **kwargs):
        request = anchor_request(snapshot, source_action, **kwargs)
        material = (
            f"{snapshot['issue']}:{snapshot['head']}:"
            f"{source_action['source_task_id']}:{source_action['source_comment_id']}"
        )
        marker = (
            cloud_coordinator.REVIEW_ANCHOR_MARKER_PREFIX
            + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        request["body"] = request["body"].replace(request["marker"], marker)
        request["marker"] = marker
        request["prefix"] = cloud_coordinator.REVIEW_ANCHOR_PREFIX + marker
        request["key"] = f"review-anchor:{snapshot['issue']}:{snapshot['head']}"
        return request

    def publish(route, body):
        if "Reserved independent-review anchor" in body.get("body", ""):
            attempts.append(body["body"])
            if publication != "sent" and len(attempts) == 1:
                if publication == "lost_response":
                    write(route, body)
                raise cloud_coordinator.CoordinatorError("response lost")
        return write(route, body)

    def run():
        return Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]

    monkeypatch.setattr(api, "write", publish)
    if legacy_identity:
        monkeypatch.setattr(cloud_coordinator, "review_anchor_request", legacy_anchor)
    baseline = run()
    monkeypatch.setattr(cloud_coordinator, "review_anchor_request", anchor_request)
    old_key, old_anchor = next(
        (key, entry) for key, entry in StateStore(store.path).snapshot()["outbox"].items()
        if entry.get("kind") == "review-anchor"
    )
    source = StateStore(store.path).snapshot()["enrollments"]["16"]["initial_source"]
    assert old_anchor["status"] == ("sent" if publication == "sent" else "uncertain")
    assert old_anchor["main_sha"] == BASE
    assert not baseline["review_valid"] and not baseline["auto_merge_eligible"]
    assert api.review_attempts == api.fix_attempts == 0

    new_main = "e" * 40
    api.current_main_sha = new_main
    run()
    anchors = [
        (key, entry) for key, entry in StateStore(store.path).snapshot()["outbox"].items()
        if entry.get("kind") == "review-anchor"
    ]
    assert len(anchors) == 2
    new_key, new_anchor = next(item for item in anchors if item[0] != old_key)
    assert new_anchor["main_sha"] == new_main
    assert new_anchor["marker"] != old_anchor["marker"]
    assert new_anchor["status"] == "sent"
    assert f"against base `{new_main}`" in new_anchor["body"]
    retained_anchor = StateStore(store.path).snapshot()["outbox"][old_key]
    if publication == "lost_response":
        assert retained_anchor["status"] == "sent" and retained_anchor["comment_id"] > 0
        assert all(retained_anchor[field] == value for field, value in old_anchor.items()
                   if field != "status")
    else:
        assert retained_anchor == old_anchor
    blocked = run()
    assert "scope" in blocked["reasons"] and not blocked["auto_merge_eligible"]
    assert api.review_attempts == api.fix_attempts == 0
    api.pull["base"]["sha"] = new_main
    for _ in range(3):
        result = run()
    review = next(
        item for item in StateStore(store.path).actions().values()
        if item.get("kind") == "review"
    )
    assert review["status"] == "sent" and review["main_sha"] == new_main
    assert review["anchor_comment_id"] == new_anchor["comment_id"]
    assert review["source_task_id"] == source["task_id"]
    assert review["task_id"] != source["task_id"]
    assert api.review_attempts == 1 and api.fix_attempts == 0
    assert len(attempts) == 2
    assert not result["review_valid"] and not result["auto_merge_eligible"]

    api.complete_review_task(
        review["task_id"], review,
        source_action={
            "source_start_head": source["head_sha"],
            "source_session_id": source["session_id"],
            "source_comment_id": source["admission_comment_id"],
        },
    )
    for _ in range(3):
        result = run()
    assert result["review_valid"] and result["required_checks_green"]
    assert result["auto_merge_eligible"]
    assert api.review_attempts == 1 and api.fix_attempts == 0
    assert sum(
        route.endswith("/pulls/16/reviews") for route, _ in api.writes
    ) == 1
    assert sum(
        item.get("context") == "agent-review" and item.get("state") == "success"
        for item in api.status_log.get(HEAD, [])
    ) == 1
    enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
    assert enrollment["initial_source"] == source
    assert enrollment["attempts"] == 0 and not enrollment.get("receipt_proofs")
    assert StateStore(store.path).snapshot()["outbox"][old_key] == retained_anchor


@pytest.mark.parametrize("response", ["queued", "unknown"])
@pytest.mark.parametrize("head_change", [False, True])
def test_first_review_main_advance_after_dispatch_never_replays_creation(
        tmp_path, monkeypatch, response, head_change):
    from deploy.cloud_coordinator import CoordinatorError

    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    write = api.write

    def create(route, body):
        if route == "agents/repos/lindayi/hermes-mobile/tasks" and response == "unknown":
            write(route, body)
            raise CoordinatorError("response lost")
        return write(route, body)

    def run():
        return Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]

    monkeypatch.setattr(api, "write", create)
    for _ in range(3):
        run()
    old_review = next(
        item for item in StateStore(store.path).actions().values()
        if item.get("kind") == "review"
    )
    assert old_review["status"] == ("sent" if response == "queued" else "uncertain")
    assert api.review_attempts == 1
    source = StateStore(store.path).snapshot()["enrollments"]["16"]["initial_source"]
    api.current_main_sha = "e" * 40
    api.pull["base"]["sha"] = api.current_main_sha
    if head_change:
        api.head_sha = RESULT_HEAD
        api.pull["head"]["sha"] = RESULT_HEAD
    for _ in range(4):
        result = run()
    if head_change and response == "queued":
        assert hashlib.sha256(old_review["key"].encode()).hexdigest()[:32] in (
            StateStore(store.path).snapshot()["retired"]["16"]["actions"]
        )
    else:
        assert StateStore(store.path).action(old_review["key"]) == old_review
    assert api.review_attempts == 1 and api.fix_attempts == 0
    assert not result["review_valid"] and not result["auto_merge_eligible"]
    assert ("unauthorized-continuation" if head_change else "agent") in result["reasons"]
    assert StateStore(store.path).snapshot()["enrollments"]["16"]["initial_source"] == source
    if response == "queued":
        api.complete_review_task(
            old_review["task_id"], old_review,
            source_action={
                "source_start_head": source["head_sha"],
                "source_session_id": source["session_id"],
                "source_comment_id": source["admission_comment_id"],
            },
        )
        for _ in range(3):
            result = run()
        assert result["review_valid"] is (not head_change)
        assert result["auto_merge_eligible"] is (not head_change)
        assert api.review_attempts == 1 and api.fix_attempts == 0
        if not head_change:
            retained = StateStore(store.path).action(old_review["key"])
            assert retained["main_sha"] == BASE
            assert retained["dispatch_nonce"] == old_review["dispatch_nonce"]
            assert retained["anchor_comment_id"] == old_review["anchor_comment_id"]
        assert sum(
            item.get("context") == "agent-review" and item.get("state") == "success"
            for item in api.status_log.get(HEAD, [])
        ) == (0 if head_change else 1)


@pytest.mark.parametrize("command", ["/hermes enroll", f"/hermes enroll {HEAD}"])
def test_manual_enrollment_without_source_has_one_explicit_blocker(tmp_path, command):
    api = FakeApi()
    api.owner_review_body = "no independent review"
    api.comments[0]["body"] = command
    path = tmp_path / "manual-no-source" / "state.json"
    for _ in range(3):
        result = Coordinator(
            api, StateStore(path), clock=lambda: NOW,
        ).run(apply=True)["pull_requests"][0]
        assert "starter-source-provenance" in result["reasons"]
    assert api.review_attempts == 0 and api.fix_attempts == 0
    assert sum(key.endswith(":starter-source-provenance")
               for key in StateStore(path).snapshot()["outbox"]) == 1


@pytest.mark.parametrize("authenticated", [True, False])
def test_manual_missing_source_skips_only_authenticated_active_cloud_workflow(tmp_path, authenticated):
    from deploy.cloud_coordinator import (
        COPILOT_AGENT_ID, COPILOT_WORKFLOW_ID, COPILOT_WORKFLOW_PATH, REPOSITORY_ID,
    )

    api = FakeApi()
    api.owner_review_body = "no independent review"
    api.workflow_runs = [{
        "id": 789, "name": "Running cloud work", "workflow_id": COPILOT_WORKFLOW_ID,
        "path": COPILOT_WORKFLOW_PATH, "head_sha": "c" * 40,
        "head_branch": "topic", "event": "dynamic", "status": "in_progress",
        "actor": {"id": COPILOT_AGENT_ID if authenticated else 9},
        "repository": {"id": REPOSITORY_ID},
        "head_repository": {"id": REPOSITORY_ID},
    }]
    path = tmp_path / "active-workflow" / "state.json"
    for _ in range(3):
        result = Coordinator(api, StateStore(path), clock=lambda: NOW).run(apply=True)["pull_requests"][0]
        assert ("starter-source-provenance" in result["reasons"]) is not authenticated
    assert api.review_attempts == 0 and api.fix_attempts == 0
    assert sum(key.endswith(":starter-source-provenance")
               for key in StateStore(path).snapshot()["outbox"]) == (0 if authenticated else 1)


@pytest.mark.parametrize("location", ["explicit", "xdg", "home", "empty_xdg"])
@pytest.mark.parametrize("ledger", ["valid", "permissions", "directory", "symlink", "oversized", "missing"])
def test_legacy_starter_bridge_is_supported_by_read_only_cli(tmp_path, capsys, monkeypatch, location, ledger):
    from deploy.cloud_coordinator import main

    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    api.comments[0]["body"] = re.sub(
        r" source-task [^ ]+ source-session [^ ]+ source-command [0-9]+$",
        "", api.comments[0]["body"],
    )
    Coordinator(api, store, clock=lambda: 1790942400).run(apply=True)
    args = ["--once", "--state", str(store.path)]
    if location == "explicit":
        args += ["--starter-state", str(api.starter_state_path)]
    else:
        monkeypatch.setenv("HOME", str(tmp_path))
        if location == "xdg":
            monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
            root = tmp_path / "xdg"
        else:
            monkeypatch.delenv("XDG_STATE_HOME", raising=False)
            if location == "empty_xdg":
                monkeypatch.setenv("XDG_STATE_HOME", "")
            root = tmp_path / ".local" / "state"
        default_path = root / "hermes-mobile-issue-starter" / "state.json"
        default_path.parent.mkdir(parents=True, mode=0o700)
        api.starter_state_path.rename(default_path)
        api.starter_state_path = default_path
    if ledger == "permissions":
        api.starter_state_path.chmod(0o644)
    elif ledger == "directory":
        api.starter_state_path.parent.chmod(0o755)
    elif ledger == "symlink":
        target = api.starter_state_path.with_name("target.json")
        api.starter_state_path.rename(target)
        api.starter_state_path.symlink_to(target)
    elif ledger == "oversized":
        from deploy.issue_starter import MAX_STATE_BYTES
        api.starter_state_path.write_bytes(b" " * (MAX_STATE_BYTES + 1))
    elif ledger == "missing":
        api.starter_state_path.unlink()
    saved_starter = api.starter_state_path.read_bytes() if api.starter_state_path.exists() else None
    saved_consumer = store.path.read_bytes()
    writes = deepcopy(api.writes)
    assert main(
        args,
        api_factory=lambda: api, lifecycle_source_paths_factory=lambda: None,
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert ("starter-source-provenance" in result["pull_requests"][0]["reasons"]) == (ledger != "valid")
    assert (api.starter_state_path.read_bytes() if api.starter_state_path.exists() else None) == saved_starter
    assert store.path.read_bytes() == saved_consumer
    assert api.writes == writes and api.review_attempts == 0


@pytest.mark.parametrize("kind", ["fix", "review"])
@pytest.mark.parametrize("status", ["completed", "failed", "sending", "uncertain", "sent"])
def test_source_less_enrollment_history_blocks_only_active_work(tmp_path, kind, status):
    api = FakeApi()
    api.owner_review_body = "no independent review"
    store = StateStore(tmp_path / "history" / "state.json")
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    assert store.claim_action("history", {
        "kind": kind, "issue": 16, "head": HEAD, "head_ref": "topic",
    })
    store.update_action("history", status, publication_state="done")
    for _ in range(3):
        result = Coordinator(api, StateStore(store.path), clock=lambda: NOW).run(apply=False)["pull_requests"][0]
        assert ("starter-source-provenance" in result["reasons"]) == (
            status in {"completed", "failed"}
        )
    assert api.review_attempts == 0 and api.fix_attempts == 0


@pytest.mark.parametrize("change", ["command", "body", "edge", "session", "issue"])
def test_saved_initial_source_revocation_before_dispatch_survives_restart(tmp_path, change):
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    Coordinator(api, store, clock=lambda: 1790942400).run(apply=True)
    assert store.snapshot()["enrollments"]["16"]["initial_source"]
    assert api.review_attempts == 0
    if change == "command":
        api.comments[0]["updated_at"] = "2026-10-01T21:01:00Z"
    elif change == "body":
        api.pull["body"] += " changed"
    elif change == "edge":
        api.closing_issues = []
    elif change == "session":
        next(iter(api.tasks.values()))["sessions"][0]["id"] = "other-session"
    else:
        api.initial_source_issue["title"] += " changed"
        api.source_timeline.append({"event": "renamed", "created_at": "2026-10-01T22:00:00Z"})
    for _ in range(3):
        result = Coordinator(
            api, StateStore(store.path), clock=lambda: 1790942400,
        ).run(apply=True)["pull_requests"][0]
        assert "starter-source-provenance" in result["reasons"]
        assert not result["review_valid"] and not result["auto_merge_eligible"]
    assert api.review_attempts == 0 and api.fix_attempts == 0
    assert sum(key.endswith(":starter-source-provenance")
               for key in StateStore(store.path).snapshot()["outbox"]) == 1


@pytest.mark.parametrize("change", [
    "task", "command", "accepted_digest", "head", "branch", "phase",
    "ambiguous", "permissions", "fetched_session", "session_binding", "session_binding_missing",
    "linked_session",
])
def test_legacy_saved_starter_bridge_rejects_single_binding_mutations(tmp_path, change):
    api, store = actual_starter_consumer(
        tmp_path, admit=False, missing_review=True, existing_closing=change != "linked_session",
    )
    api.unresolved = False
    api.comments[0]["body"] = re.sub(
        r" source-task [^ ]+ source-session [^ ]+ source-command [0-9]+$",
        "", api.comments[0]["body"],
    )
    # Prove assembled legacy recovery can dispatch with exactly these envelopes.
    positive_path = tmp_path / "positive-legacy" / "state.json"
    for _ in range(3):
        Coordinator(api, StateStore(positive_path), clock=lambda: 1790942400,
                    starter_state_path=api.starter_state_path).run(apply=True)
    assert api.review_attempts == 1 and api.fix_attempts == 0
    api.comments = [api.comments[0]]
    api.tasks = {"task-1": next(t for t in api.tasks.values() if t["id"] == "task-1")}
    api.review_attempts = 0
    saved = json.loads(api.starter_state_path.read_text())
    record = next(iter(saved["commands"].values()))
    fields = {
        "task": ("task_id", "unrelated-task"),
        "command": ("command_id", 9999),
        "accepted_digest": ("accepted_title_body_sha256", "e" * 64),
        "head": ("head_sha", RESULT_HEAD),
        "branch": ("branch", "unrelated"),
        "phase": ("phase", "task_created"),
        "session_binding": ("source_session_id", "different-saved-session"),
    }
    if change == "ambiguous":
        duplicate = deepcopy(record)
        duplicate["command_id"] = 9999
        saved["commands"]["28:9999"] = duplicate
    elif change == "fetched_session":
        api.tasks["task-1"]["sessions"][0]["id"] = "substituted-authenticated-session"
    elif change == "session_binding_missing":
        record.pop("source_session_id")
    elif change == "linked_session":
        record["link_intent"]["session_id"] = "substituted-link-session"
    elif change != "permissions":
        name, value = fields[change]
        record[name] = value
        if change == "command":
            saved["commands"] = {f"28:{value}": record}
    api.starter_state_path.write_text(json.dumps(saved))
    if change == "permissions":
        api.starter_state_path.chmod(0o644)
    for _ in range(3):
        result = Coordinator(api, StateStore(store.path), clock=lambda: 1790942400,
                             starter_state_path=api.starter_state_path).run(apply=True)["pull_requests"][0]
        assert "starter-source-provenance" in result["reasons"]
    assert api.review_attempts == 0 and api.fix_attempts == 0


def test_initial_starter_changes_requested_runs_real_fixer_receipt_and_delta_review(tmp_path):
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False
    api.task_posts = 1
    clock = [1790942400]

    def run():
        return Coordinator(api, StateStore(store.path),
                           clock=lambda: clock[0]).run(apply=True)["pull_requests"][0]

    for _ in range(3):
        run()
    initial_review = next(a for a in store.actions().values() if a["kind"] == "review")
    source = store.snapshot()["enrollments"]["16"]["initial_source"]
    api.complete_review_task(
        initial_review["task_id"], initial_review,
        source_action={
            "source_start_head": source["head_sha"],
            "source_session_id": source["session_id"],
            "source_comment_id": source["admission_comment_id"],
        },
        verdict="changes_requested",
        findings=[{"path": "tests/test_cloud_coordinator.py",
                   "comment": "Preserve exact source identity in the bounded follow-up."}],
        report="One bounded follow-up is required.",
    )
    for _ in range(3):
        result = run()
        assert not result["review_valid"] and not result["auto_merge_eligible"]
    fixer = next(a for a in store.actions().values() if a["kind"] == "fix")
    assert api.fix_attempts == 1 and api.review_attempts == 1
    api.complete_task(fixer["task_id"], fixer, head_sha=RESULT_HEAD)
    api.head_sha = RESULT_HEAD
    api.pull["head"]["sha"] = RESULT_HEAD
    for _ in range(3):
        run()
    validated_fixer = store.action(fixer["key"])
    assert validated_fixer["receipt_result"] == "ready"
    assert validated_fixer["receipt_session_id"]
    delta = next(a for a in store.actions().values()
                 if a["kind"] == "review" and a["source_task_id"] == fixer["task_id"])
    assert delta["task_id"] != fixer["task_id"] != initial_review["task_id"]
    api.complete_review_task(delta["task_id"], delta, source_action=validated_fixer,
                             verdict="pass", findings=[], report="Delta accepted.")
    for _ in range(4):
        result = run()
    assert result["review_valid"] and result["auto_merge_eligible"]
    assert api.fix_attempts == 1 and api.review_attempts == 2
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 1
    assert len(api.graphql_writes) == 1
    assert sum(item.get("state") == "success"
               for item in api.status_log.get(RESULT_HEAD, [])
               if item.get("context") == "agent-review") == 1


@pytest.mark.parametrize("change", ["body", "closing_edge", "body_and_closing_edge"])
def test_dispatched_initial_review_accepts_report_after_admission_only_binding_changes(
        tmp_path, change):
    api, store = actual_starter_consumer(tmp_path, admit=False, missing_review=True)
    api.unresolved = False

    def run():
        return Coordinator(api, StateStore(store.path),
                           clock=lambda: 1790942400).run(apply=True)["pull_requests"][0]

    for _ in range(3):
        run()
    review = next(a for a in store.actions().values() if a["kind"] == "review")
    source = deepcopy(store.snapshot()["enrollments"]["16"]["initial_source"])
    admission = deepcopy(store.snapshot()["enrollments"]["16"]["starter_admission"])
    assert review["status"] == "sent"
    assert api.review_attempts == 1 and api.fix_attempts == 0
    if change in {"body", "body_and_closing_edge"}:
        api.pull["body"] += "\n\nAuthenticated independent review results follow."
    if change in {"closing_edge", "body_and_closing_edge"}:
        api.closing_issues = []
    api.complete_review_task(
        review["task_id"], review,
        source_action={
            "source_start_head": source["head_sha"],
            "source_session_id": source["session_id"],
            "source_comment_id": source["admission_comment_id"],
        },
        verdict="pass", findings=[], report="The complete unchanged source head is accepted.",
    )
    for _ in range(5):
        result = run()
    assert result["review_valid"] and result["required_checks_green"]
    assert result["auto_merge_eligible"] and result["auto_merge_requested"]
    assert api.review_attempts == 1 and api.fix_attempts == 0
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["initial_source"] == source
    assert enrollment["starter_admission"] == admission
    assert enrollment["attempts"] == 0 and not enrollment.get("receipt_proofs")
    assert len(api.graphql_writes) == 1
    assert sum(item.get("state") == "success"
               for item in api.status_log.get(HEAD, [])
               if item.get("context") == "agent-review") == 1
    published = [
        item for item in api.owner_reviews + [api._current_owner_review_record()]
        if item.get("commit_id") == HEAD and item.get("state") == "COMMENTED"
        and item.get("body", "").startswith(
            '{"schema":"hermes-independent-agent-review-v1",',
        )
    ]
    assert len(published) == 1


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
    result = run()
    assert result["auto_merge_requested"], result["reasons"]
    assert len(api.graphql_writes) == 1 and api.fix_attempts == 1
    assert api.review_attempts == 0


@pytest.mark.parametrize("length", [128, 129, 256])
def test_starter_source_session_boundary_survives_paired_admission(tmp_path, length):
    session_id = "s" * length
    api, store = actual_starter_consumer(
        tmp_path, admit=False, missing_review=True, session_id=session_id,
    )
    api.unresolved = False
    for _ in range(3):
        Coordinator(api, StateStore(store.path), clock=lambda: 1791210000).run(apply=True)
    source = StateStore(store.path).snapshot()["enrollments"]["16"]["initial_source"]
    assert source["session_id"] == session_id
    review = next(
        action for action in StateStore(store.path).actions().values()
        if action["kind"] == "review"
    )
    api.complete_review_task(
        review["task_id"], review,
        source_action={
            "source_start_head": source["head_sha"],
            "source_session_id": source["session_id"],
            "source_comment_id": source["admission_comment_id"],
        },
        verdict="pass", findings=[], report="The complete source head is accepted.",
    )
    for _ in range(3):
        result = Coordinator(
            api, StateStore(store.path), clock=lambda: 1791210000,
        ).run(apply=True)["pull_requests"][0]
    assert result["review_valid"] and result["auto_merge_eligible"]
    assert api.review_attempts == 1 and api.fix_attempts == 0
    if length == 256:
        from deploy.cloud_coordinator import (
            _valid_initial_source, _valid_starter_admission, enrollment_from_comment,
        )

        enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
        invalid_admission = deepcopy(enrollment["starter_admission"])
        invalid_admission["source_session_id"] = "s" * 257
        invalid_source = deepcopy(source)
        invalid_source["session_id"] = "s" * 257
        assert not _valid_starter_admission(invalid_admission)
        assert not _valid_initial_source(invalid_source)
        invalid_comment = deepcopy(api.comments[0])
        invalid_comment["body"] = invalid_comment["body"].replace(
            f"source-session {'s' * 256}",
            f"source-session {'s' * 257}",
        )
        assert enrollment_from_comment(
            api.issue, api.pull, invalid_comment, api=api,
        ) is None


def test_upgraded_starter_reconciles_old_format_lost_response_and_consumer_replays_once(
        tmp_path):
    from test_issue_starter import (
        FakeApi as StarterApi, completed_task, make_coordinator, pull_request, start_task,
    )

    class Starter16Api(StarterApi):
        def get(self, route):
            return super().get(route.replace("/issues/16/comments", "/issues/41/comments"))

        def post(self, route, body):
            if (route.endswith("/issues/16/comments")
                    and body.get("body", "").startswith("/hermes enroll ")):
                legacy_body = body["body"].split(" source-task ", 1)[0]
                self.posts.append((route, body))
                self.comments.append({
                    "id": 9101, "body": legacy_body,
                    "created_at": "2026-10-01T21:00:00Z",
                    "updated_at": "2026-10-01T21:00:00Z",
                    "user": {"id": OWNER},
                })
                raise TimeoutError("accepted pre-upgrade response was lost")
            return super().post(route.replace("/issues/16/comments", "/issues/41/comments"), body)

    pull = pull_request(pull_id=160000016, node_id="PR_node_16", head_ref="topic")
    pull["number"] = 16
    producer = Starter16Api(pulls=[pull])
    producer.closing_issues = []
    producer.comments[0]["created_at"] = "2026-10-01T11:00:00Z"
    producer.comments[0]["updated_at"] = "2026-10-01T11:00:00Z"
    start_task(tmp_path, producer)
    producer.task_detail = completed_task(
        pull_id=pull["id"], node_id=pull["node_id"], head_ref="topic",
    )
    producer.task_detail["created_at"] = "2026-10-01T11:01:00Z"
    producer.task_detail["sessions"][0]["created_at"] = "2026-10-01T11:02:00Z"
    producer.task_detail["sessions"][0]["completed_at"] = "2026-10-01T11:05:00Z"

    first = make_coordinator(tmp_path, producer).run(apply=True)
    producer_path = make_coordinator(tmp_path, producer).store.path
    pending = json.loads(producer_path.read_text())["commands"]["28:9001"]
    assert first["blocked"] == 1
    assert pending["phase"] == "handoff_uncertain"
    assert pending["link_intent"]["session_id"] == "session-1"
    pending.pop("source_session_id")
    saved_pending = json.loads(producer_path.read_text())
    saved_pending["commands"]["28:9001"] = pending
    producer_path.write_text(json.dumps(saved_pending))

    recovered = make_coordinator(tmp_path, producer).run(apply=True)
    assert recovered["handed_off"] == 1
    saved = json.loads(producer_path.read_text())["commands"]["28:9001"]
    assert saved["phase"] == "handed_off" and saved["enrollment_state"] == "done"
    assert len([post for post in producer.posts
                if post[0].endswith("/issues/16/comments")]) == 1
    legacy_comment = next(item for item in producer.comments if item["id"] == 9101)

    api = FakeApi(unresolved=True)
    api.comments = [deepcopy(legacy_comment)]
    api.pull.update(pull)
    api.pull["draft"] = False
    api.initial_source_issue = deepcopy(producer.current_issue)
    api.source_comments = deepcopy(producer.comments)
    api.source_timeline = deepcopy(producer.timeline)
    api.starter_state_path = producer_path
    api.issue_edit_evidence = {
        "lastEditedAt": None, "nodes": [],
        "pageInfo": {"hasNextPage": False, "endCursor": None},
    }
    api.tasks[producer.task_detail["id"]] = deepcopy(producer.task_detail)
    api.task_posts = 1
    api.unresolved = False
    original_get, original_get_all = api.get, api.get_all

    def get(route):
        if route == "repos/lindayi/hermes-mobile/issues/28":
            return deepcopy(producer.current_issue)
        return original_get(route)

    def get_all(route, *, collection=None):
        if route.startswith("repos/lindayi/hermes-mobile/issues/28/comments?"):
            return deepcopy(producer.comments)
        if route.startswith("repos/lindayi/hermes-mobile/issues/28/timeline?"):
            return deepcopy(producer.timeline)
        return original_get_all(route, collection=collection)

    api.get, api.get_all = get, get_all
    api.owner_review_body = "No independent review has been published."
    api.pull_files = [
        {"filename": "README.md", "status": "modified", "sha": "f" * 40},
    ]
    api.blob_contents["f" * 40] = b"Synthetic starter source\n"
    attach_closing_issue_api(api)
    store = StateStore(tmp_path / "legacy-consumer" / "state.json")
    for _ in range(3):
        result = Coordinator(
            api, StateStore(store.path), clock=lambda: 1791210000,
            starter_state_path=producer_path,
        ).run(apply=True)["pull_requests"][0]
    enrollment = StateStore(store.path).snapshot()["enrollments"]["16"]
    worker = Coordinator(
        api, StateStore(store.path), clock=lambda: 1791210000,
        starter_state_path=producer_path,
    )
    source, source_error = worker._resolve_initial_source({
        "enrollment": enrollment, "pull": api.pull,
        "head": api.pull["head"]["sha"], "comments": api.comments,
    })
    assert "initial_source" in enrollment, result["reasons"]
    assert source_error is None and source == enrollment["initial_source"]
    source = enrollment["initial_source"]
    assert source["session_id"] == pending["link_intent"]["session_id"]
    assert api.review_attempts == 1 and api.fix_attempts == 0
    assert len([action for action in StateStore(store.path).actions().values()
                if action["kind"] == "review"]) == 1
