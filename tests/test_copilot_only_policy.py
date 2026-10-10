"""Synthetic acceptance regressions for the owner-authorized issue #92 gate."""
from copy import deepcopy

import pytest

from deploy import autonomy_policy
from deploy.cloud_coordinator import copilot_review_valid
from test_autonomy_policy import SHA, _evidence
from test_cloud_coordinator import (
    BASE, HEAD, COPILOT_AGENT, COPILOT_REVIEWER, Coordinator, FakeApi, StateStore,
)


REQUIRED = [
    {"context": "source-ci", "app_id": 15368},
    {"context": "integration-tests", "app_id": None},
    {"context": "issue-link", "app_id": 15368},
    {"context": "copilot-pull-request-reviewer", "app_id": 15368},
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


@pytest.mark.parametrize("lost_response", [False, True])
def test_copilot_request_is_real_and_never_duplicated(tmp_path, lost_response):
    class RequestApi(CopilotApi):
        def get_all(self, route, *, collection=None):
            if "/reviews?" in route:
                return []
            return super().get_all(route, collection=collection)

        def write(self, route, body, **kwargs):
            if route.endswith("/requested_reviewers"):
                self.writes.append((route, body))
                assert body == {"reviewers": ["copilot-pull-request-reviewer[bot]"]}
                if lost_response:
                    from deploy.cloud_coordinator import ApiError
                    raise ApiError("Synthetic lost response")
                return self.pull | {"requested_reviewers": [{"id": COPILOT_REVIEWER}]}
            return super().write(route, body, **kwargs)

    api = RequestApi()
    store = StateStore(tmp_path / "state.json")
    for _ in range(3):
        result = Coordinator(api, store).run(apply=True)
        assert not result["pull_requests"][0]["auto_merge_eligible"]
    assert len([route for route, _ in api.writes if route.endswith("/requested_reviewers")]) == 1
    assert api.review_attempts == api.fix_attempts == 0
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 0
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
