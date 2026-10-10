"""Synthetic acceptance regressions for the owner-authorized issue #92 gate."""
from copy import deepcopy

import pytest

from deploy import autonomy_policy
from deploy.cloud_coordinator import _valid_initial_source, copilot_review_valid
from test_autonomy_policy import SHA, _evidence
from test_cloud_coordinator import (
    BASE, HEAD, COPILOT_AGENT, COPILOT_REVIEWER, Coordinator, FakeApi, StateStore,
)


REQUIRED = [
    {"context": "source-ci", "app_id": 15368},
    {"context": "integration-tests", "app_id": None},
    {"context": "issue-link", "app_id": 15368},
]


class CopilotApi(FakeApi):
    def get(self, route):
        if route.endswith("/protection/required_status_checks"):
            return {"checks": deepcopy(REQUIRED), "strict": self.strict_protection}
        return super().get(route)

    def get_all(self, route, *, collection=None):
        values = super().get_all(route, collection=collection)
        if "/reviews?" in route:
            return [item for item in values if item["user"]["id"] == COPILOT_REVIEWER]
        if "/check-runs?" in route:
            return [item for item in values
                    if item.get("name") != "copilot-pull-request-reviewer"]
        return values


def copilot_evidence():
    evidence = _evidence()
    review = evidence["copilot_review"]
    review["reviews"] = [review["reviews"][0]]
    review.pop("selected_review")
    review["check_runs"] = [{
        "name": item["context"], "app": {"id": item["app_id"]},
        "head_sha": review["head_sha"], "status": "completed", "conclusion": "success",
    } for item in REQUIRED]
    review["statuses"] = []
    review["checks_complete"] = True
    evidence["copilot_review"] = review
    evidence["protection"]["required_checks"] = deepcopy(REQUIRED)
    return evidence


@pytest.mark.parametrize("state", ["COMMENTED", "APPROVED"])
def test_copilot_only_accepts_real_review_and_checks(tmp_path, state):
    api = CopilotApi()
    api.review_state = state
    result = Coordinator(api, StateStore(tmp_path / "state.json")).run(apply=True)
    assert result["pull_requests"][0]["auto_merge_eligible"]
    assert api.review_attempts == api.fix_attempts == 0
    assert not any("/statuses/" in route or route.endswith("/reviews")
                   for route, body in api.writes)
    assert autonomy_policy.validate_transition(copilot_evidence(), phase="post-cutover")["ready"]


@pytest.mark.parametrize("hazard", [
    "absent", "stale", "wrong_author", "rejecting", "unresolved", "failed_ci",
])
def test_three_ci_contexts_do_not_replace_copilot_review(tmp_path, hazard):
    class InvalidReviewApi(CopilotApi):
        def get_all(self, route, *, collection=None):
            values = super().get_all(route, collection=collection)
            if "/reviews?" in route:
                if hazard == "absent":
                    return []
                for review in values:
                    if hazard == "stale":
                        review["commit_id"] = BASE
                    elif hazard == "wrong_author":
                        review["user"] = {"id": COPILOT_AGENT}
                    elif hazard == "rejecting":
                        review["state"] = "CHANGES_REQUESTED"
            if "/check-runs?" in route and hazard == "failed_ci":
                values[0]["conclusion"] = "failure"
            return values

    api = InvalidReviewApi()
    if hazard == "unresolved":
        api.unresolved = True
    result = Coordinator(api, StateStore(tmp_path / "state.json")).run(apply=False)
    assert not result["pull_requests"][0]["auto_merge_eligible"]
    evidence = copilot_evidence()
    review = evidence["copilot_review"]
    if hazard == "absent":
        review["reviews"] = []
    elif hazard == "unresolved":
        review["threads"][0]["isResolved"] = False
    elif hazard == "failed_ci":
        review["check_runs"][0]["conclusion"] = "failure"
    else:
        review["reviews"][0].update({
            "stale": {"commit_id": BASE},
            "wrong_author": {"user": {"id": COPILOT_AGENT}},
            "rejecting": {"state": "CHANGES_REQUESTED"},
        }[hazard])
    assert not autonomy_policy.validate_transition(evidence, phase="post-cutover")["ready"]


@pytest.mark.parametrize("author", [5164171, 198982749])
@pytest.mark.parametrize("state", ["COMMENTED", "APPROVED"])
def test_owner_and_bot_authors_share_copilot_review_gate(author, state):
    from deploy.cloud_coordinator import independent_review_valid
    evidence = copilot_evidence()
    review = evidence["copilot_review"]
    review["pull_author_id"] = author
    review["reviews"][0].update({
        "state": state,
        "body": "",
        "body_html": "",
    })
    assert independent_review_valid(
        review["head_sha"], review["reviews"], review["threads"],
        pull_author_id=author,
    )
    blockers = set()
    autonomy_policy._check_review(evidence, evidence["main"]["sha"], "post-cutover", blockers)
    assert not blockers


@pytest.mark.parametrize("state", ["COMMENTED", "APPROVED"])
def test_copilot_reviewer_cannot_author_the_pull_request(state):
    from deploy.cloud_coordinator import independent_review_valid

    evidence = copilot_evidence()
    review = evidence["copilot_review"]
    review["pull_author_id"] = COPILOT_REVIEWER
    review["reviews"][0].update({"state": state, "body": "", "body_html": ""})

    assert not independent_review_valid(
        review["head_sha"], review["reviews"], review["threads"],
        pull_author_id=COPILOT_REVIEWER,
    )
    blockers = set()
    autonomy_policy._check_review(
        evidence, evidence["main"]["sha"], "post-cutover", blockers,
    )
    assert "copilot-review" in blockers


@pytest.mark.parametrize("body,body_html", [
    (
        "<!-- ccr-overview-v2 -->\n\n## Copilot review overview\n\n"
        "### 🔵 Needs a closer look\n\nNo bugs found.\n\n**Findings:** None\n\n"
        "<details><summary><strong>Previously missed (1)</strong></summary>\n\n"
        "<details><summary>Finding</summary>\n\n"
        "Required correction: reject stale evidence.\n</details></details>",
        "<h2>Copilot review overview</h2><h3>🔵 Needs a closer look</h3>"
        "<p>No bugs found.</p><p><strong>Findings:</strong> None</p>"
        "<details><summary><strong>Previously missed (1)</strong></summary>"
        "<details><summary>Finding</summary><p>"
        "Required correction: reject stale evidence.</p></details></details>",
    ),
    (
        "<!-- ccr-overview-v2 -->\n\nIncomplete rendered inventory",
        "<details",
    ),
])
def test_read_only_validator_rejects_actionable_or_ambiguous_comment(
        body, body_html):
    from deploy.cloud_coordinator import independent_review_valid

    evidence = copilot_evidence()
    review = evidence["copilot_review"]
    review["reviews"][0].update({
        "state": "COMMENTED", "body": body, "body_html": body_html,
    })

    result = autonomy_policy.validate_transition(evidence, phase="post-cutover")

    assert not result["ready"]
    assert "copilot-review" in result["blockers"]
    assert not independent_review_valid(
        review["head_sha"], review["reviews"], review["threads"],
        pull_author_id=review["pull_author_id"],
    )


@pytest.mark.parametrize("change", [
    {"state": "PENDING"}, {"state": "DISMISSED"}, {"state": "CHANGES_REQUESTED"},
    {"commit_id": BASE}, {"dismissed": True}, {"dismissed_at": "2026-10-01T22:00:00Z"},
    {"user": {"id": COPILOT_AGENT}}, {"submitted_at": None},
])
def test_copilot_only_rejects_invalid_review(change):
    review = {
        "id": 1, "user": {"id": COPILOT_REVIEWER}, "commit_id": HEAD,
        "state": "COMMENTED", "submitted_at": "2026-10-01T21:00:00Z",
    }
    assert not copilot_review_valid(HEAD, [review | change], [])
    evidence = copilot_evidence()
    evidence["copilot_review"]["reviews"][0].update(change)
    assert not autonomy_policy.validate_transition(evidence, phase="post-cutover")["ready"]


@pytest.mark.parametrize("hazard", [
    "reviews_complete", "threads_complete", "checks_complete",
    "unresolved", "comments_partial", "missing_check", "failed_check", "wrong_app", "stale_check",
])
def test_copilot_only_validator_fails_closed(hazard):
    evidence = copilot_evidence()
    review = evidence["copilot_review"]
    if hazard.endswith("_complete"):
        review[hazard] = False
    elif hazard == "unresolved":
        review["threads"][0]["isResolved"] = False
    elif hazard == "comments_partial":
        review["threads"][0]["comments_complete"] = False
    elif hazard == "missing_check":
        review["check_runs"].pop()
    else:
        field, value = {
            "failed_check": ("conclusion", "failure"),
            "wrong_app": ("app", {"id": 9}),
            "stale_check": ("head_sha", SHA),
        }[hazard]
        review["check_runs"][-1][field] = value
    assert not autonomy_policy.validate_transition(evidence, phase="post-cutover")["ready"]


@pytest.mark.parametrize("active_source_work", [False, True])
def test_copilot_request_requires_source_provenance_and_idle_source(
        tmp_path, active_source_work, monkeypatch):
    class RequestApi(CopilotApi):
        def get_all(self, route, *, collection=None):
            if "/reviews?" in route:
                return []
            return super().get_all(route, collection=collection)

    api = RequestApi()
    store = StateStore(tmp_path / "state.json")
    if active_source_work:
        api.workflow_runs = [{
            "id": 781, "head_branch": "topic", "workflow_id": 372426410,
            "path": "dynamic/copilot-swe-agent/copilot", "event": "dynamic",
            "actor": {"id": COPILOT_AGENT}, "repository": {"id": 1399942965},
            "head_repository": {"id": 1399942965}, "status": "in_progress",
        }]
    enrollment = {
        "issue": 16, "comment": 123, "head": HEAD, "base": BASE,
        "pull_id": api.pull["id"], "pull_node_id": api.pull["node_id"],
        "repository_id": 1399942965,
    }
    if active_source_work:
        enrollment.update({
            "authorized_head": HEAD,
            "starter_admission": {
                "version": 1, "issue_number": 16, "head_sha": HEAD,
                "body_sha256": "c" * 64, "comment_id": 123,
                "comment_created_at": "2026-10-01T11:00:00Z",
            },
        })
    store.enroll(enrollment)
    coordinator = Coordinator(api, store)
    if active_source_work:
        source = {
            "version": 1, "issue_number": 16, "start_comment_id": 100,
            "start_comment_created_at": "2026-10-01T11:00:00Z",
            "task_id": "starter-task", "session_id": "starter-session",
            "task_created_at": "2026-10-01T11:01:00Z",
            "session_created_at": "2026-10-01T11:02:00Z",
            "session_completed_at": "2026-10-01T11:05:00Z",
            "head_sha": HEAD, "head_ref": "topic", "pull_id": api.pull["id"],
            "pull_node_id": api.pull["node_id"], "repository_id": 1399942965,
            "pull_body_sha256": "c" * 64, "issue_body_sha256": "d" * 64,
            "admission_comment_id": 123,
            "admission_comment_created_at": "2026-10-01T11:00:00Z",
        }
        assert _valid_initial_source(source)
        snapshot_pull = coordinator._snapshot_pull

        def source_bound_snapshot(*args, **kwargs):
            return snapshot_pull(*args, **kwargs) | {"initial_source": source}

        monkeypatch.setattr(coordinator, "_snapshot_pull", source_bound_snapshot)
        coordinator._reconcile_actions = lambda *_args, **_kwargs: False
    coordinator.run(apply=True)
    assert not any(route.endswith("/requested_reviewers") for route, _ in api.writes)
    assert api.review_attempts == api.fix_attempts == 0
    assert not any("/statuses/" in route or route.endswith("/reviews") for route, _ in api.writes)


@pytest.mark.parametrize("status", ["sending", "uncertain", "sent", "completed"])
def test_retired_review_reservation_is_not_replayed_or_repurposed(tmp_path, status):
    api = CopilotApi()
    store = StateStore(tmp_path / "state.json")
    store.enroll({
        "issue": 16, "comment": 123, "head": HEAD, "base": BASE,
        "pull_id": api.pull["id"], "pull_node_id": api.pull["node_id"],
        "repository_id": 1399942965,
    })
    action = {"kind": "review", "issue": 16, "head": HEAD,
              "main_sha": BASE, "key": "historical-review"}
    store.claim_action(action["key"], action)
    store.update_action(action["key"], status)
    before = store.action(action["key"])
    result = Coordinator(api, store).run(apply=True)
    assert result["pull_requests"][0]["auto_merge_eligible"] is (status == "completed")
    assert api.review_attempts == api.fix_attempts == 0
    retained = store.action(action["key"])
    if status != "completed":
        assert retained == before
    assert not any("/statuses/" in route or route.endswith("/reviews") for route, _ in api.writes)
