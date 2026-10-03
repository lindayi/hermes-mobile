"""Issue #39: bounded body-only Copilot review findings and strict review selection.

All review bodies are public synthetic fixtures shaped like actual
`ccr-overview-v2` reviews; no production data or model execution is used.
"""
import hashlib
import json

import pytest

from deploy.cloud_coordinator import (
    MAX_FINDINGS,
    StateStore,
    _latest_source_failure,
    copilot_review_valid,
    repair_request,
)
from deploy.review_evidence import (
    INDEPENDENT_REVIEW_SCHEMA,
    latest_reviews,
    parse_independent_review_body,
    sensitive_review_authorized,
)
from test_cloud_coordinator import (
    BASE, COPILOT_REVIEWER, HEAD, OWNER, FakeApi, _managed_cycle, enrolled_record, source_run,
)

PICTURE = (
    '<picture><source media="(prefers-color-scheme: dark)" '
    'srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/'
    'medium-v2-dark.svg"><img alt="Medium" src="https://github.githubassets.com/static/'
    'images/icons/copilot-code-review/medium-v2-light.svg"></picture>'
)
FOOTER = (
    "\n<hr>\n<p>💡 <a href=\"/lindayi/hermes-mobile/new/main?filename=.github/skills/"
    "code-review/SKILL.md\" class=\"Link--inTextBlock\">Add a <code>code-review</code> agent skill</a></p>\n"
)


def overview(status, summary, findings, *sections):
    return (
        "<!-- ccr-overview-v2 -->\n\n<h2>Copilot review overview</h2>\n\n"
        f"<h3>{status}</h3>\n\n<p>{summary}</p>\n\n<p><strong>Review effort:</strong> Balanced</p>\n"
        f"<p><strong>Findings:</strong> {findings}</p>\n\n" + "\n\n".join(sections) + FOOTER
    )


def section(title, inner, *, open_=False):
    tag = "<details open>" if open_ else "<details>"
    return f"{tag}\n<summary><strong>{title}</strong></summary>\n\n{inner}\n</details>"


def missed_item(title, location, explanation):
    return (f"<details>\n<summary>{PICTURE} {title}</summary>\n\n<code>{location}</code>\n\n"
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
    # Legacy structural fixtures are authored HTML, not a Markdown renderer.
    return {"id": review_id, "state": state, "body": body,
            "body_html": body.removeprefix("<!-- ccr-overview-v2 -->"),
            "commit_id": commit_id,
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


def test_latest_review_requires_present_unique_positive_ids():
    valid = copilot_review(NO_FINDINGS)
    assert latest_reviews([valid], COPILOT_REVIEWER) == [valid]

    malformed = dict(valid)
    malformed.pop("id")
    assert latest_reviews([malformed], COPILOT_REVIEWER) is None
    assert latest_reviews([valid, dict(valid)], COPILOT_REVIEWER) is None


def _independent_review_evidence():
    report = {
        "schema": INDEPENDENT_REVIEW_SCHEMA,
        "reviewed_head_sha": HEAD,
        "review_method": "independent-agent",
        "verdict": "pass",
        "evidence_sha256": "c" * 64,
    }
    body = json.dumps(report, separators=(",", ":"))
    review_id = 64001
    body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    review = {
        "id": review_id, "user": {"id": OWNER}, "commit_id": HEAD,
        "state": "COMMENTED", "submitted_at": SUBMITTED, "body": body,
    }
    authorization = {
        "actor_id": OWNER, "head_sha": HEAD, "state": "approved",
        "review_id": review_id, "body_sha256": body_sha256,
    }
    targeted = {
        "review_id": review_id, "reviewer_id": OWNER, "head_sha": HEAD,
        "state": "COMMENTED", "body_sha256": body_sha256,
        "evidence_sha256": report["evidence_sha256"],
    }
    return report, body, review, authorization, targeted


def test_owner_published_independent_review_requires_bounded_structured_positive_body():
    report, body, review, authorization, targeted = _independent_review_evidence()
    assert parse_independent_review_body(body, HEAD) == report
    assert sensitive_review_authorized(
        [review], HEAD, authorization, targeted, owner_id=OWNER,
    )

    for invalid_body in (
        "Independent review passed.",
        body.replace('"verdict":"pass"', '"verdict":"fail"'),
        body.replace(HEAD, BASE),
        body[:-1] + ',"unexpected":true}',
        '{"schema":"x","schema":"x"}',
        " " * 4097,
    ):
        invalid_review = review | {"body": invalid_body}
        assert not sensitive_review_authorized(
            [invalid_review], HEAD, authorization, targeted, owner_id=OWNER,
        )


def test_current_independent_review_is_bound_to_latest_exact_unedited_record():
    from deploy.review_evidence import current_independent_agent_review

    _, body, review, _, targeted = _independent_review_evidence()
    assert current_independent_agent_review(
        [review], HEAD, owner_id=OWNER, expected=targeted,
    ) == targeted
    assert current_independent_agent_review(
        [review], HEAD, owner_id=OWNER, expected=targeted, complete=False,
    ) is None
    assert current_independent_agent_review(
        [review], HEAD, owner_id=OWNER, expected=targeted | {"body_sha256": "d" * 64},
    ) is None
    assert current_independent_agent_review([], HEAD, owner_id=OWNER) is None

    edited_body = json.dumps({
        "schema": INDEPENDENT_REVIEW_SCHEMA,
        "reviewed_head_sha": HEAD,
        "review_method": "independent-agent",
        "verdict": "pass",
        "evidence_sha256": "d" * 64,
    }, separators=(",", ":"))
    assert current_independent_agent_review(
        [dict(review, body=edited_body)], HEAD, owner_id=OWNER, expected=targeted,
    ) is None
    assert current_independent_agent_review(
        [dict(review, commit_id="b" * 40)], HEAD, owner_id=OWNER,
    ) is None
    superseded = dict(
        review, id=review["id"] + 1, state="CHANGES_REQUESTED",
        submitted_at="2026-10-01T13:00:00Z",
    )
    assert current_independent_agent_review(
        [review, superseded], HEAD, owner_id=OWNER,
    ) is None
    assert current_independent_agent_review(
        [dict(review, updated_at="2026-10-01T12:31:00Z")],
        HEAD, owner_id=OWNER,
    ) is None
    assert body == review["body"]


@pytest.mark.parametrize("mutation", [
    lambda record: record.update(state="DISMISSED"),
    lambda record: record.update(commit_id=BASE),
    lambda record: record.update(user={"id": COPILOT_REVIEWER}),
    lambda record: record.update(dismissed=True),
    lambda record: record.update(dismissed_at=SUBMITTED),
    lambda record: record.update(submitted_at="2026-10-01T12:30:00"),
    lambda record: record.update(id=True),
])
def test_owner_published_independent_review_rejects_invalid_authenticated_record(mutation):
    _, _, review, authorization, targeted = _independent_review_evidence()
    mutation(review)
    assert not sensitive_review_authorized(
        [review], HEAD, authorization, targeted, owner_id=OWNER,
    )


def test_owner_published_independent_review_rejects_edited_replaced_or_conflicting_records():
    _, body, review, authorization, targeted = _independent_review_evidence()
    edited = review | {"body": body + " "}
    assert not sensitive_review_authorized(
        [edited], HEAD, authorization, targeted, owner_id=OWNER,
    )
    assert not sensitive_review_authorized(
        [], HEAD, authorization, targeted, owner_id=OWNER,
    )
    assert not sensitive_review_authorized(
        [review, dict(review, state="DISMISSED")],
        HEAD, authorization, targeted, owner_id=OWNER,
    )
    negative_later_review = dict(
        review, id=review["id"] + 1, state="CHANGES_REQUESTED",
        submitted_at="2026-10-01T13:30:00Z", body="no",
    )
    assert not sensitive_review_authorized(
        [review, negative_later_review], HEAD, authorization, targeted, owner_id=OWNER,
    )


def test_owner_authorization_must_bind_the_selected_review_and_exact_digest():
    _, _, review, authorization, targeted = _independent_review_evidence()
    for changed_authorization in (
        authorization | {"review_id": review["id"] + 1},
        authorization | {"body_sha256": "d" * 64},
        authorization | {"head_sha": BASE},
        authorization | {"actor_id": COPILOT_REVIEWER},
        authorization | {"legacy": True},
    ):
        assert not sensitive_review_authorized(
            [review], HEAD, changed_authorization, targeted, owner_id=OWNER,
        )
    assert not sensitive_review_authorized(
        [review], HEAD, authorization,
        targeted | {"evidence_sha256": "d" * 64}, owner_id=OWNER,
    )


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
    prose = "Required correction: reject non-integer receipt author IDs before accepting."
    api = ReviewApi([copilot_review(prose, state="CHANGES_REQUESTED")])
    result = _managed_cycle(api, tmp_path / "state.json")
    assert result["pull_requests"][0]["repair_requested"]
    _, evidence = _task_evidence(api)
    [finding] = evidence[0]["review_findings"]
    assert finding["kind"] == "changes-requested" and finding["comment"] == prose
    assert finding["review"] == REVIEW_ID and "thread" not in finding


@pytest.mark.parametrize("prose", [
    "Required correction: reject pending receipts before claiming an attempt.",
    "Required correction: reject receipts awaiting validation before claiming an attempt.",
    "Required correction: reject receipts not yet verified before claiming an attempt.",
])
def test_changes_requested_overview_domain_state_correction_consumes_one_attempt(tmp_path, prose):
    body = overview("🟡 Changes recommended", prose, "None")
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert result["pull_requests"][0]["repair_requested"]
    assert not result["pull_requests"][0]["review_valid"]
    prompts, evidence = _task_evidence(api)
    assert len(prompts) == 1 and api.fix_attempts == 1
    [finding] = evidence[0]["review_findings"]
    assert prose in finding["comment"]
    assert finding["kind"] == "changes-requested"
    assert finding["review"] == REVIEW_ID and finding["head"] == HEAD
    assert finding["submitted_at"] == SUBMITTED and "thread" not in finding
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 1


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


def test_looks_good_without_findings_never_consumes_budget(tmp_path):
    body = overview("🟢 Looks good", "The change preserves validation behavior.", "None")
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert api.fix_attempts == 0
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())


def test_changes_requested_overview_without_open_or_missed_items_forwards_summary():
    body = overview("🟡 Changes recommended", "Required change: reject stale receipts.",
                    "None", RESOLVED)
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(body, state="CHANGES_REQUESTED"),
    ])
    [finding] = _evidence(request)["review_findings"]
    assert finding["kind"] == "changes-requested"
    assert "Required change: reject stale receipts." in finding["comment"]
    assert "Synthetic resolved finding" not in finding["comment"]
    assert "code-review" not in finding["comment"]
    # Inline Open findings are carried by their review threads, not duplicated.
    assert repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(OPEN_ONLY, state="CHANGES_REQUESTED"),
    ]) is None


@pytest.mark.parametrize("heading", [
    '<summary class="finding"><strong>Open (1)</strong></summary>',
    '<summary><strong>Open (1)</strong></summary >',
    '<summary class="finding"><strong>Open (1)</strong></summary >',
])
def test_attributed_open_does_not_duplicate_resolved_inline_evidence(tmp_path, heading):
    body = OPEN_ONLY.replace('<summary><strong>Open (1)</strong></summary>', heading)
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert api.fix_attempts == 0
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("close", ["</details >", "</DETAILS\t>"])
def test_structural_resolved_close_never_leaks_into_current_summary(close):
    body = overview("🟡 Changes recommended", "Required correction: reject stale receipts.",
                    "None", RESOLVED.replace("</details>", close))
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(body, state="CHANGES_REQUESTED"),
    ])
    [finding] = _evidence(request)["review_findings"]
    assert "Required correction: reject stale receipts." in finding["comment"]
    assert "Synthetic resolved finding" not in finding["comment"]


@pytest.mark.parametrize("prose", [
    "Exact-head verification remains pending. No code issues were found.",
    "The exact-head verification remains pending.",
    "Exact-head verification is still pending.",
])
def test_structural_validation_sentences_never_dispatch(tmp_path, prose):
    body = overview("🔵 Needs a closer look", prose, "None", RESOLVED)
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert not result["pull_requests"][0]["review_valid"]
    assert api.fix_attempts == 0
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


def test_empty_previously_missed_zero_remains_no_evidence():
    body = overview("🔵 Needs a closer look", "Synthetic.", "None",
                    section("Previously missed (0)", ""))
    assert repair_request(HEAD, 0, [], [], pull_number=16,
                          reviews=[copilot_review(body)]) is None


def test_open_zero_does_not_suppress_actionable_changes_requested_summary():
    prose = "Required correction: reject stale receipts."
    body = overview("🟡 Changes recommended", prose, "None", section("Open (0)", ""))
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[
        copilot_review(body, state="CHANGES_REQUESTED"),
    ])
    assert request is not None
    [finding] = _evidence(request)["review_findings"]
    assert prose in finding["comment"]
    assert finding["kind"] == "changes-requested"


@pytest.mark.parametrize("body", [NO_FINDINGS, PENDING_VALIDATION, RESOLVED_ONLY])
def test_open_zero_nonactionable_overview_is_not_repair_evidence(tmp_path, body):
    body = body.replace(FOOTER, section("Open (0)", "") + FOOTER)
    api = ReviewApi([copilot_review(body, state="CHANGES_REQUESTED")])
    path = tmp_path / "state.json"
    result = _managed_cycle(api, path)
    assert not result["pull_requests"][0]["repair_requested"]
    assert api.fix_attempts == 0
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())


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
    copilot_review(BODY_ONLY, state=[]),
    copilot_review(BODY_ONLY, state={}),
    copilot_review("", state="CHANGES_REQUESTED"),
], ids=[
    "no-findings", "pending-validation", "resolved-only", "open-only", "plain-comment",
    "stale-head", "foreign-owner", "foreign-agent", "approved", "pending", "dismissed",
    "list-state", "object-state", "empty-changes-requested",
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


@pytest.mark.parametrize("heading", [
    '<summary class="finding"><strong>Previously missed (1)</strong></summary>',
    '<summary><strong>Previously missed (unknown)</strong></summary>',
    '<summary><strong>Previously missed (0)</strong></summary>',
])
def test_previously_missed_heading_attributes_or_unproven_count_preserves_finding(heading):
    missed = "<details>" + heading + missed_item(
        "Reject synthetic stale receipts", "deploy/example.py:7", "Preserve required correction.",
    ) + "</details>"
    body = overview("🔵 Needs a closer look", "Synthetic.", "None", missed)
    request = repair_request(HEAD, 0, [], [], pull_number=16, reviews=[copilot_review(body)])
    assert request is not None
    [finding] = _evidence(request)["review_findings"]
    assert finding["kind"] == "previously-missed"
    assert "Reject synthetic stale receipts" in finding["comment"]
    assert "Preserve required correction." in finding["comment"]
    assert len(finding["comment"]) <= 1000 and "thread" not in finding
    if "unknown" in heading:
        assert "Previously missed (unknown)" in finding["comment"]


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
    store.enroll(enrolled_record())
    store.record_event("123")
    data = store.snapshot()
    data["enrollments"]["16"]["attempts"] = 3
    store._save(data)
    result = _managed_cycle(api, path)
    assert api.fix_attempts == 0
    assert "budget" in result["pull_requests"][0]["reasons"]
