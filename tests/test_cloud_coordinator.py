"""Contracts of the simplified coordinator, using synthetic GitHub responses only."""
from copy import deepcopy

import pytest

from deploy.cloud_coordinator import (
    COPILOT_REVIEWER_ID, OWNER_ID, REPOSITORY, REPOSITORY_ID, ApiError, Coordinator,
    CoordinatorError, StateStore, copilot_review_valid, enrollment_from_comment, main,
    required_check_states,
)

HEAD = "a" * 40
MAIN = "b" * 40
REPO = f"repos/{REPOSITORY}"


def pull(**changes):
    value = {
        "number": 7, "id": 700, "state": "open", "merged": False, "draft": False,
        "mergeable": True, "mergeable_state": "clean", "title": "t", "body": "Closes #6",
        "head": {"sha": HEAD, "ref": "topic", "repo": {"id": REPOSITORY_ID}},
        "base": {"sha": MAIN, "ref": "main", "repo": {"id": REPOSITORY_ID}},
    }
    value.update(changes)
    return value


def comment(body="/hermes enroll", *, user=OWNER_ID, edited=False, id=1):
    return {"id": id, "body": body, "user": {"id": user},
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:01Z" if edited else "2026-10-01T00:00:00Z"}


def review(state="COMMENTED", commit=HEAD, *, user=COPILOT_REVIEWER_ID, at="2026-10-01T01:00:00Z",
           id=1):
    return {"id": id, "state": state, "commit_id": commit, "user": {"id": user},
            "submitted_at": at}


def check(name, conclusion="success", *, app=15368, head=HEAD, status="completed", id=1):
    return {"id": id, "name": name, "head_sha": head, "status": status,
            "conclusion": conclusion, "app": {"id": app}}


GREEN = [check("source-ci"), check("integration-tests"), check("issue-link")]


class FakeApi:
    def __init__(self, *, pr=None, comments=None, reviews=None, threads=None, checks=None,
                 tasks=None, task_error=False, merge_error=None):
        self.pr = pr or pull()
        self.comments = [comment()] if comments is None else comments
        self.reviews = [review()] if reviews is None else reviews
        self.threads = [] if threads is None else threads
        self.checks = GREEN if checks is None else checks
        self.tasks = tasks or []
        self.task_error = task_error
        self.merge_error = merge_error
        self.writes = []

    def get(self, route):
        if route == REPO:
            return {"id": REPOSITORY_ID}
        if route == "user":
            return {"id": OWNER_ID}
        if route == f"{REPO}/commits/main":
            return {"sha": MAIN}
        if route == f"{REPO}/pulls/7":
            return deepcopy(self.pr)
        raise AssertionError(route)

    def get_all(self, route, *, collection=None):
        if route.startswith(f"{REPO}/pulls?state=open"):
            return [deepcopy(self.pr)] if self.pr["state"] == "open" else []
        if route.startswith(f"{REPO}/issues/7/comments"):
            return self.comments
        if route.startswith(f"agents/repos/{REPOSITORY}/tasks"):
            return self.tasks
        if route.startswith(f"{REPO}/pulls/7/reviews"):
            return self.reviews
        if "/check-runs" in route:
            return self.checks
        if "/statuses" in route:
            return []
        raise AssertionError(route)

    def graphql(self, query, variables):
        return {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": self.threads, "pageInfo": {"hasNextPage": False}}}}}}

    def write(self, route, body, *, method="POST"):
        self.writes.append((method, route, body))
        if route.endswith("/tasks"):
            if self.task_error:
                raise ApiError("lost response")
            return {"id": f"task-{len(self.writes)}"}
        if route.endswith("/merge"):
            if self.merge_error:
                raise ApiError("rejected", status=self.merge_error)
            return {"merged": True}
        return {}


def run(tmp_path, api, *, apply=True):
    return Coordinator(api, StateStore(tmp_path / "state" / "state.json"),
                       clock=lambda: 1790900000).run(apply=apply)


def outcome(result):
    return result["pull_requests"][0]["outcome"]


def test_only_exact_owner_commands_enroll():
    assert enrollment_from_comment(pull(), comment())["head"] == HEAD
    assert enrollment_from_comment(pull(), comment(f"/hermes enroll {HEAD}"))
    starter = f"/hermes enroll {HEAD} issue 6 body-sha256 {'c' * 64}"
    assert enrollment_from_comment(pull(), comment(starter))
    assert enrollment_from_comment(pull(), comment(f"/hermes enroll {'c' * 40}")) is None
    assert enrollment_from_comment(pull(), comment(user=1)) is None
    assert enrollment_from_comment(pull(), comment(edited=True)) is None
    assert enrollment_from_comment(pull(), comment("/hermes enroll please")) is None
    assert enrollment_from_comment(pull(base={"ref": "dev", "repo": {"id": REPOSITORY_ID}}),
                                   comment()) is None


@pytest.mark.parametrize("reviews,valid", [
    ([review()], True),
    ([review("APPROVED")], True),
    ([review(commit="c" * 40)], False),
    ([review("CHANGES_REQUESTED")], False),
    ([review("DISMISSED")], False),
    ([review(user=198982749)], False),
    ([], False),
    ([review(), review("CHANGES_REQUESTED", at="2026-10-01T02:00:00Z", id=2)], False),
    ([review("CHANGES_REQUESTED"), review(at="2026-10-01T02:00:00Z", id=2)], True),
])
def test_latest_exact_head_copilot_review_gate(reviews, valid):
    assert copilot_review_valid(HEAD, reviews) is valid


def test_required_checks_bind_head_app_and_latest_run():
    assert set(required_check_states(HEAD, GREEN, []).values()) == {"success"}
    stale = [check("source-ci", head="c" * 40), *GREEN[1:]]
    assert required_check_states(HEAD, stale, [])["source-ci"] == "pending"
    forged = [check("issue-link", app=1), *GREEN[:2]]
    assert required_check_states(HEAD, forged, [])["issue-link"] == "pending"
    rerun = [check("source-ci", "failure", id=1), check("source-ci", id=2), *GREEN[1:]]
    assert required_check_states(HEAD, rerun, [])["source-ci"] == "success"
    status_only = [GREEN[0], GREEN[2]]
    statuses = [{"context": "integration-tests", "state": "success", "created_at": "x"}]
    assert required_check_states(HEAD, status_only, statuses)["integration-tests"] == "success"


def test_all_gates_merge_with_exact_head_once(tmp_path):
    api = FakeApi()
    assert outcome(run(tmp_path, api)) == "merged"
    assert api.writes == [("PUT", f"{REPO}/pulls/7/merge", {"sha": HEAD, "merge_method": "squash"})]


@pytest.mark.parametrize("change", [
    {"reviews": [review(commit="c" * 40)]},
    {"reviews": [review("CHANGES_REQUESTED")]},
    {"threads": [{"id": "t", "isResolved": False, "comments": {"nodes": [{"path": "x", "body": "y"}]}}]},
    {"checks": GREEN[:2]},
    {"checks": [check("source-ci", "failure"), *GREEN[1:]]},
    {"pr": pull(draft=True)},
    {"pr": pull(mergeable_state="blocked")},
])
def test_any_missing_gate_blocks_merge(tmp_path, change):
    api = FakeApi(**change)
    run(tmp_path, api)
    assert not any(route.endswith("/merge") for _, route, _ in api.writes)


def test_plan_mode_never_writes(tmp_path):
    api = FakeApi()
    assert outcome(run(tmp_path, api, apply=False)) == "merge_planned"
    assert api.writes == []
    assert not (tmp_path / "state" / "state.json").exists()


def test_review_is_requested_once_per_head(tmp_path):
    api = FakeApi(reviews=[])
    assert outcome(run(tmp_path, api)) == "review_requested"
    assert outcome(run(tmp_path, api)) == "awaiting_review"
    assert [route for _, route, _ in api.writes] == [f"{REPO}/pulls/7/requested_reviewers"]
    api.pr = pull(head={**pull()["head"], "sha": "d" * 40})
    run(tmp_path, api)
    assert len(api.writes) == 2


def test_findings_dispatch_one_fix_task_per_head(tmp_path):
    thread = {"id": "t", "isResolved": False,
              "comments": {"nodes": [{"path": "a.py", "body": "Bug, see https://x.test"}]}}
    api = FakeApi(threads=[thread])
    assert outcome(run(tmp_path, api)) == "dispatched"
    assert outcome(run(tmp_path, api)) == "waiting"
    tasks = [body for _, route, body in api.writes if route.endswith("/tasks")]
    assert len(tasks) == 1
    assert tasks[0]["head_ref"] == "topic" and tasks[0]["base_ref"] == "main"
    assert "untrusted" in tasks[0]["prompt"] and "https://x.test" not in tasks[0]["prompt"]


def test_active_agent_task_defers_all_actions(tmp_path):
    task = {"state": "in_progress", "artifacts": [{"data": {"head_ref": "topic"}}]}
    api = FakeApi(tasks=[task])
    assert outcome(run(tmp_path, api)) == "agent_active"
    assert api.writes == []


def test_ambiguous_dispatch_is_never_retried(tmp_path):
    api = FakeApi(pr=pull(mergeable=False, mergeable_state="dirty"), task_error=True)
    assert outcome(run(tmp_path, api)) == "uncertain"
    assert outcome(run(tmp_path, api)) == "blocked"
    assert len([1 for _, route, _ in api.writes if route.endswith("/tasks")]) == 1
    state = StateStore(tmp_path / "state" / "state.json").load()
    assert {e["reason"] for e in state["lifecycle_events"]} == {"execution_uncertain"}


def test_reservation_survives_crash_before_response(tmp_path):
    class Crash(FakeApi):
        def write(self, route, body, *, method="POST"):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, Crash(pr=pull(mergeable_state="behind")))
    api = FakeApi(pr=pull(mergeable_state="behind", head={**pull()["head"], "sha": "d" * 40}))
    # Even a new head cannot dispatch while an earlier dispatch is unreconciled.
    assert outcome(run(tmp_path, api)) == "blocked"
    assert api.writes == []


def test_retargeted_pull_is_never_acted_on(tmp_path):
    api = FakeApi()
    run(tmp_path, api, apply=False)
    api.reviews = []
    run(tmp_path, api)  # enroll; review requested
    api.reviews = [review()]
    api.pr = pull(base={"ref": "release", "sha": MAIN, "repo": {"id": REPOSITORY_ID}})
    assert outcome(run(tmp_path, api)) == "not_main_targeted"
    assert not any(route.endswith("/merge") for _, route, _ in api.writes)


def test_reenrollment_does_not_repeat_review_request(tmp_path):
    api = FakeApi(reviews=[])
    run(tmp_path, api)
    api.comments = [comment(), comment(id=2)]
    assert outcome(run(tmp_path, api)) == "awaiting_review"
    assert len(api.writes) == 1


def test_reconcile_budget_exhausts_and_blocks(tmp_path):
    api = FakeApi(pr=pull(mergeable_state="behind"))
    for index in range(3):
        api.pr = pull(mergeable_state="behind", head={**pull()["head"], "sha": f"{index}" * 40})
        assert outcome(run(tmp_path, api)) == "dispatched"
    api.pr = pull(mergeable_state="behind", head={**pull()["head"], "sha": "9" * 40})
    assert outcome(run(tmp_path, api)) == "exhausted"
    assert outcome(run(tmp_path, api)) == "blocked"


def test_merged_pull_records_event_and_retires(tmp_path):
    api = FakeApi()
    run(tmp_path, api, apply=True)  # enroll + merge
    api.pr = pull(state="closed", merged=True, merge_commit_sha="e" * 40,
                  merged_at="2026-10-01T02:00:00Z")
    result = run(tmp_path, api)
    assert outcome(result) == "merged" and result["enrolled"] == 0
    state = StateStore(tmp_path / "state" / "state.json").load()
    assert [e["reason"] for e in state["lifecycle_events"]] == ["merged"]
    assert run(tmp_path, api)["pull_requests"] == []


def test_merge_rejection_is_retried_next_cycle(tmp_path):
    api = FakeApi(merge_error=405)
    assert outcome(run(tmp_path, api)) == "merge_rejected"
    api.merge_error = None
    assert outcome(run(tmp_path, api)) == "merged"


def test_identity_and_cli_guards(tmp_path, capsys):
    class Stranger(FakeApi):
        def get(self, route):
            return {"id": 1} if route == "user" else super().get(route)

    with pytest.raises(CoordinatorError):
        run(tmp_path, Stranger())
    with pytest.raises(SystemExit):
        main(["--apply"])


def test_legacy_state_keeps_active_enrollments_without_budget(tmp_path):
    import json

    path = tmp_path / "state" / "state.json"
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps({"version": 1, "enrollments": {
        "7": {"issue": 7, "comment": 1, "head": HEAD, "active": True},
        "8": {"issue": 8, "comment": 2, "head": HEAD, "active": False}},
        "actions": {}, "lifecycle_events": []}))
    path.chmod(0o600)
    state = StateStore(path).load()
    assert list(state["enrollments"]) == ["7"]
    assert state["enrollments"]["7"]["fixes"] == 5
