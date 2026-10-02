"""Issue #39: bounded body-only Copilot review findings and strict review selection.

All review bodies are public synthetic fixtures shaped like actual
`ccr-overview-v2` reviews; no production data or model execution is used.
"""
import json

import pytest

from deploy.cloud_coordinator import (
    MAX_FINDINGS,
    StateStore,
    _latest_source_failure,
    copilot_review_valid,
    repair_request,
)
from test_cloud_coordinator import (
    BASE, COPILOT_REVIEWER, HEAD, OWNER, FakeApi, _managed_cycle, source_run,
)

PICTURE = (
    '<picture><source media="(prefers-color-scheme: dark)" '
    'srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/'
    'medium-v2-dark.svg"><img alt="Medium" src="https://github.githubassets.com/static/'
    'images/icons/copilot-code-review/medium-v2-light.svg"></picture>'
)
FOOTER = (
    "\n---\n\n💡 <a href=\"/lindayi/hermes-mobile/new/main?filename=.github/skills/"
    "code-review/SKILL.md\" class=\"Link--inTextBlock\">Add a `code-review` agent skill</a>\n"
)


def overview(status, summary, findings, *sections):
    return (
        "<!-- ccr-overview-v2 -->\n\n## Copilot review overview\n\n"
        f"### {status}\n\n{summary}\n\n**Review effort:** Balanced  \n"
        f"**Findings:** {findings}\n\n" + "\n\n".join(sections) + FOOTER
    )


def section(title, inner, *, open_=False):
    tag = "<details open>" if open_ else "<details>"
    return f"{tag}\n<summary><strong>{title}</strong></summary>\n\n{inner}\n</details>"


def missed_item(title, location, explanation):
    return (f"<details>\n<summary>{PICTURE} {title}</summary>\n\n`{location}`\n\n"
            f"{explanation}\n</details>")


RESOLVED = section(
    "Resolved since last review (1)",
    f"- {PICTURE} [Synthetic resolved finding](#discussion_r1001)",
)
OPEN = section(
    "Open (1)", f"- {PICTURE} [Synthetic inline finding](#discussion_r1002) · New",
    open_=True,
)
MISSED_EXPLANATION = (
    "Synthetic deferred defect: a transient state consumes the bounded budget. "
    "See https://example.invalid/private and ask @someone; token: synthetic-value-123."
)
MISSED = section(
    "Previously missed (1)",
    "In code that hasn't changed since last review\n\n" + missed_item(
        "Defer synthetic transient state", "deploy/example.py:12", MISSED_EXPLANATION,
    ),
)
BODY_ONLY = overview(
    "🔵 Needs a closer look", "Synthetic transient state can consume the budget.",
    "None", RESOLVED, MISSED,
)
NO_FINDINGS = overview(
    "🟢 Looks good", "No issues were found in the synthetic change.", "None",
)
PENDING_VALIDATION = overview(
    "🔵 Needs a closer look",
    "It changes synthetic coordination paths, while exact-head verification remains pending.",
    "None", RESOLVED,
)
RESOLVED_ONLY = overview(
    "🟢 Looks good", "Earlier synthetic findings were resolved.", "None", RESOLVED,
)
OPEN_ONLY = overview(
    "🟡 Changes recommended", "One synthetic inline finding remains.", f"1 {PICTURE}", OPEN,
)
REVIEW_ID = 5300000001
SUBMITTED = "2026-10-01T12:30:00Z"


def copilot_review(body, *, state="COMMENTED", review_id=REVIEW_ID, commit_id=HEAD,
                   submitted_at=SUBMITTED, user_id=COPILOT_REVIEWER):
    return {"id": review_id, "state": state, "body": body, "commit_id": commit_id,
            "submitted_at": submitted_at, "user": {"id": user_id},
            "author_association": "CONTRIBUTOR"}


class ReviewApi(FakeApi):
    """FakeApi with a synthetic complete review collection."""

    def __init__(self, reviews, **kwargs):
        super().__init__(**kwargs)
        self.reviews = reviews
        self.review_reads = 0

    def get_all(self, route, *, collection=None):
        if route.endswith("/pulls/16/reviews?per_page=100"):
            self.review_reads += 1
            return [dict(review) for review in self.reviews]
        return super().get_all(route, collection=collection)


def _task_evidence(api):
    prompts = [body["prompt"] for route, body in api.writes
               if route == "agents/repos/lindayi/hermes-mobile/tasks"]
    return prompts, [
        json.loads(prompt.split("Untrusted evidence: `", 1)[1].split("`\n\n<!--", 1)[0])
        for prompt in prompts
    ]


def _evidence(request):
    return json.loads(request["body"].split("Untrusted evidence: `", 1)[1].split("`\n\n<!--", 1)[0])


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
def test_body_only_previously_missed_finding_dispatches_bounded_fixer(tmp_path, state):
    api = ReviewApi([copilot_review(BODY_ONLY, state=state)])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    plan = result["pull_requests"][0]
    assert plan["repair_requested"]
    assert not plan["review_valid"]  # Body evidence is never approval.
    assert not api.graphql_writes
    prompts, evidence = _task_evidence(api)
    assert len(prompts) == 1 and api.fix_attempts == 1
    assert evidence[0]["failed_source_checks"] == []
    [finding] = evidence[0]["review_findings"]
    assert "thread" not in finding  # Never fabricate a thread ID.
    assert finding["review"] == REVIEW_ID
    assert finding["head"] == HEAD and finding["submitted_at"] == SUBMITTED
    assert finding["kind"] == "previously-missed"
    assert "Defer synthetic transient state" in finding["comment"]
    assert "deploy/example.py:12" in finding["comment"]
    assert "transient state consumes the bounded budget" in finding["comment"]
    assert len(finding["comment"]) <= 1000
    for unsafe in ("example.invalid", "githubassets", "<picture", "@someone",
                   "synthetic-value-123", "Synthetic resolved finding"):
        assert unsafe not in prompts[0]
    assert "untrusted" in prompts[0].lower()
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 1


def test_changes_requested_prose_without_inline_findings_is_forwarded(tmp_path):
    prose = "Synthetic request: reject non-integer receipt author IDs before accepting."
    api = ReviewApi([copilot_review(prose, state="CHANGES_REQUESTED")])
    result = _managed_cycle(api, tmp_path / "state.json")
    assert result["pull_requests"][0]["repair_requested"]
    _, evidence = _task_evidence(api)
    [finding] = evidence[0]["review_findings"]
    assert finding["kind"] == "changes-requested" and finding["comment"] == prose
    assert finding["review"] == REVIEW_ID and "thread" not in finding


@pytest.mark.parametrize("body", [NO_FINDINGS, PENDING_VALIDATION, RESOLVED_ONLY],
                         ids=["no-findings", "pending-validation", "resolved-only"])
def test_non_actionable_changes_requested_overviews_never_consume_budget(tmp_path, body):
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert api.fix_attempts == 0
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())


def test_changes_requested_overview_without_open_or_missed_items_forwards_summary():
    body = overview("🟡 Changes recommended", "Synthetic summary of a required change.",
                    "None", RESOLVED)
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(body, state="CHANGES_REQUESTED"),
    ])
    [finding] = _evidence(request)["review_findings"]
    assert finding["kind"] == "changes-requested"
    assert "Synthetic summary of a required change." in finding["comment"]
    assert "Synthetic resolved finding" not in finding["comment"]
    assert "code-review" not in finding["comment"]
    # Inline Open findings are carried by their review threads, not duplicated.
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(OPEN_ONLY, state="CHANGES_REQUESTED"),
    ]) is None


def test_open_zero_overview_is_not_repair_evidence():
    open_zero = section("Open (0)", "")
    body = overview("🟡 Changes recommended", "Synthetic summary.", "None", open_zero)
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(body, state="CHANGES_REQUESTED"),
    ]) is None


@pytest.mark.parametrize("review", [
    copilot_review(NO_FINDINGS),
    copilot_review(PENDING_VALIDATION),
    copilot_review(RESOLVED_ONLY),
    copilot_review(OPEN_ONLY),
    copilot_review("Synthetic commentary without a structured finding."),
    copilot_review(BODY_ONLY, commit_id=BASE),  # Stale head.
    copilot_review(BODY_ONLY, user_id=OWNER),  # Foreign author.
    copilot_review(BODY_ONLY, user_id=198982749),  # Coding agent, not reviewer.
    copilot_review(BODY_ONLY, state="APPROVED"),
    copilot_review(BODY_ONLY, state="PENDING", submitted_at=None),
    copilot_review(BODY_ONLY, state="DISMISSED"),
    copilot_review("", state="CHANGES_REQUESTED"),
], ids=[
    "no-findings", "pending-validation", "resolved-only", "open-only", "plain-comment",
    "stale-head", "foreign-owner", "foreign-agent", "approved", "pending", "dismissed",
    "empty-changes-requested",
])
def test_non_actionable_or_unauthenticated_reviews_never_consume_budget(tmp_path, review):
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert api.fix_attempts == 0
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())


def test_only_latest_exact_head_review_is_read():
    older = copilot_review(BODY_ONLY, review_id=REVIEW_ID - 1,
                           submitted_at="2026-10-01T12:00:00Z")
    newer = copilot_review(NO_FINDINGS)
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[older, newer]) is None
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[newer, older]) is None
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[older]) is not None
    # Tied latest reviews do not prove which body is current.
    tied = copilot_review(NO_FINDINGS, review_id=REVIEW_ID + 1)
    assert repair_request(HEAD, 0, [], [], pull_number=16,
                          reviews=[copilot_review(BODY_ONLY), tied]) is None


@pytest.mark.parametrize("malformed", [
    "not-a-record", None,
    {"user": None}, {"user": "copilot"}, {"user": {}},
    {"user": {"id": str(COPILOT_REVIEWER)}}, {"user": {"id": True}},
    {"user": {"id": 0}}, {"user": {"id": -1}}, {"user": {"id": 1.5}},
    {"id": True}, {"id": "5300000002"}, {"id": 0}, {"id": -5}, {"id": REVIEW_ID},
], ids=lambda value: repr(value)[:40])
def test_malformed_later_record_cannot_reuse_earlier_approval_or_findings(malformed):
    approved = copilot_review("", state="APPROVED", submitted_at="2026-10-01T12:00:00Z")
    later_fields = {"id": REVIEW_ID + 1, "state": "CHANGES_REQUESTED", "commit_id": HEAD,
                    "submitted_at": "2026-10-01T13:00:00Z", "body": "Synthetic later request.",
                    "user": {"id": COPILOT_REVIEWER}}
    later = later_fields | malformed if isinstance(malformed, dict) else malformed
    assert copilot_review_valid(HEAD, [approved], [])
    for reviews in ([approved, later], [later, approved]):
        assert not copilot_review_valid(HEAD, reviews, [])
        assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=reviews) is None


def test_findings_share_the_combined_budget_after_threads_and_source_failure():
    many = section("Previously missed (6)", "\n\n".join(
        missed_item(f"Synthetic missed {index}", f"deploy/example.py:{index}", "Explain.")
        for index in range(6)
    ))
    review = copilot_review(overview("🟡 Changes recommended", "Synthetic.", "None", many))
    threads = [{"id": "PRRT_synthetic", "isResolved": False,
                "comments": [{"body": f"Thread {index}"} for index in range(4)]}]
    failure = _latest_source_failure([source_run()], HEAD, "topic", 16)
    request = repair_request(HEAD, 0, threads, [], pull_number=16,
                             source_failure=failure, reviews=[review])
    evidence = _evidence(request)
    assert len(evidence["review_findings"]) + len(evidence["failed_source_checks"]) == MAX_FINDINGS
    assert evidence["failed_source_checks"] == [failure]
    assert [item.get("thread") for item in evidence["review_findings"][:4]] == ["PRRT_synthetic"] * 4
    assert [item["comment"].split("\n")[0].strip() for item in evidence["review_findings"][4:]] == [
        f"Synthetic missed {index}" for index in range(3)]
    # Without review records the existing request bytes are unchanged.
    assert (repair_request(HEAD, 0, threads, [], pull_number=16, source_failure=failure)
            == repair_request(HEAD, 0, threads, [], pull_number=16, source_failure=failure,
                              reviews=[]))


def test_unproven_previously_missed_section_is_forwarded_whole_not_dropped():
    mismatched = section("Previously missed (2)", missed_item(
        "Synthetic only parsed item", "deploy/example.py:3", "Synthetic detail.",
    ))
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[copilot_review(
        overview("🔵 Needs a closer look", "Synthetic.", "None", mismatched),
    )])
    [finding] = _evidence(request)["review_findings"]
    assert "Previously missed (2)" in finding["comment"]
    assert "Synthetic only parsed item" in finding["comment"]


def test_nested_details_preserve_actionable_tail_of_previously_missed_item():
    nested = section(
        "Previously missed (1)",
        "<details><summary>Reject stale receipt</summary>\n\n"
        "Validate receipt author before use.\n\n"
        "<details><summary>Reproduction</summary> Synthetic reproduction.</details>\n\n"
        "Required correction: reject the stale receipt before claiming an attempt."
        "</details>",
    )
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(overview("🔵 Needs a closer look", "Synthetic.", "None", nested)),
    ])
    [finding] = _evidence(request)["review_findings"]
    assert "Validate receipt author before use." in finding["comment"]
    assert "Synthetic reproduction." in finding["comment"]
    assert "Required correction: reject the stale receipt before claiming an attempt." in finding["comment"]


@pytest.mark.parametrize("first_item", [
    "<details>Unstructured item without a summary.</details>",
    '<details><summary class="finding">Classed summary</summary>First finding.</details>',
], ids=["missing-summary", "summary-attributes"])
def test_unproven_item_structure_preserves_complete_previously_missed_section(first_item):
    missed = section(
        "Previously missed (2)",
        first_item + "\n\n" + missed_item(
            "Later synthetic finding", "deploy/example.py:20", "Preserve this later finding.",
        ),
    )
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(overview("🔵 Needs a closer look", "Synthetic.", "None", missed)),
    ])
    findings = _evidence(request)["review_findings"]
    if "class=" in first_item:
        assert len(findings) == 2
        assert "Classed summary" in findings[0]["comment"]
    else:
        assert len(findings) == 1
        assert "Previously missed (2)" in findings[0]["comment"]
    assert any("Later synthetic finding" in finding["comment"] for finding in findings)
    assert any("Preserve this later finding." in finding["comment"] for finding in findings)


@pytest.mark.parametrize("change", ["edited", "new-review-no-findings", "new-head-review"])
def test_review_change_before_dispatch_suppresses_stale_action(tmp_path, change):
    class ChangesBeforeDispatch(ReviewApi):
        def get_all(self, route, *, collection=None):
            if route.endswith("/pulls/16/reviews?per_page=100") and self.review_reads >= 1:
                if change == "edited":
                    self.reviews = [copilot_review(BODY_ONLY.replace(
                        "Synthetic deferred defect", "Different synthetic defect"))]
                elif change == "new-review-no-findings":
                    self.reviews = [copilot_review(BODY_ONLY), copilot_review(
                        NO_FINDINGS, review_id=REVIEW_ID + 1,
                        submitted_at="2026-10-01T13:00:00Z")]
                else:
                    self.reviews = [copilot_review(BODY_ONLY), copilot_review(
                        BODY_ONLY, review_id=REVIEW_ID + 1, commit_id="c" * 40,
                        submitted_at="2026-10-01T13:00:00Z")]
            return super().get_all(route, collection=collection)

    api = ChangesBeforeDispatch([copilot_review(BODY_ONLY)])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert api.review_reads >= 2  # Fresh body evidence is re-read before claiming.
    assert api.fix_attempts == 0
    assert not result["pull_requests"][0]["repair_requested"]
    assert not api.graphql_writes
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())


def test_exhausted_budget_reports_body_only_findings_without_dispatch(tmp_path):
    api = ReviewApi([copilot_review(BODY_ONLY)])
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.enroll({"issue": 16, "comment": 123, "head": HEAD, "base": BASE})
    store.record_event("123")
    data = store.snapshot()
    data["enrollments"]["16"]["attempts"] = 3
    store._save(data)
    result = _managed_cycle(api, path)
    assert api.fix_attempts == 0
    assert "budget" in result["pull_requests"][0]["reasons"]
