"""Public synthetic full-media review pairs, authored without a test renderer."""
import json
from types import SimpleNamespace

import pytest

from deploy.cloud_coordinator import ApiError, GhApi, StateStore, copilot_review_valid, repair_request
from deploy.review_evidence import OVERVIEW_MARKER, parse_body, review_body_disposition
from test_cloud_coordinator import HEAD, _managed_cycle
from test_review_evidence import ReviewApi, copilot_review, _evidence


def review_pair(raw, rendered, *, state="CHANGES_REQUESTED"):
    return copilot_review(raw, state=state) | {"body_html": rendered}


RAW_ESCAPED = (
    "<!-- ccr-overview-v2 -->\n\n## Copilot review overview\n\n"
    "### 🔵 Needs a closer look\n\nNo bugs found.\n\n**Findings:** None\n\n"
    "<details><summary><strong>Previously missed (1)</strong></summary>\n\n"
    "<details><summary>Decode literal context</summary>\n\n`example.py:1`\n\n"
    "Required correction: decode &lt;details&gt; only after validation.\n"
    "</details></details>"
)
HTML_ESCAPED = (
    "<h2>Copilot review overview</h2><h3>🔵 Needs a closer look</h3>"
    "<p>No bugs found.</p><p><strong>Findings:</strong> None</p>"
    "<details><summary><strong>Previously missed (1)</strong></summary>"
    "<details><summary>Decode literal context</summary><p><code>example.py:1</code></p>"
    "<p>Required correction: decode &lt;details&gt; only after validation.</p>"
    "</details></details>"
)


def test_review5392186494_entity_text_is_not_markup():
    review = review_pair(RAW_ESCAPED, HTML_ESCAPED, state="COMMENTED")
    result = review_body_disposition([review], HEAD, reviewer_id=review["user"]["id"])
    assert not result["ambiguous"]
    [finding] = result["findings"]
    assert "Required correction: decode <details> only after validation." in finding["text"]
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    assert _evidence(request)["review_findings"][0]["review"] == review["id"]


@pytest.mark.parametrize("raw, rendered", [
    ("No **bugs** were found.", "<p>No <strong>bugs</strong> were found.</p>"),
    ("No bugs were found in `deploy/example.py`.",
     "<p>No bugs were found in <code>deploy/example.py</code>.</p>"),
    ("No bugs found. The change should be merged after CI.",
     "<p>No bugs found. The change should be merged after CI.</p>"),
    ("The parser has a bug.", "<p>The parser has a bug.</p>"),
    ("The parser should reject receipts.", "<p>The parser should reject receipts.</p>"),
])
def test_unproven_prose_does_not_dispatch(raw, rendered):
    review = review_pair(OVERVIEW_MARKER + "\n\n## Copilot review overview\n\n"
                        "### 🟢 Looks good\n\n" + raw + "\n\n**Findings:** None", (
        "<h2>Copilot review overview</h2><h3>🟢 Looks good</h3>" + rendered +
        "<p><strong>Findings:</strong> None</p>"))
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None


@pytest.mark.parametrize("rendered", [None, [], {}, "", " ", "<details", "<p>unclosed",
                                      "<p>" + "x" * 65536 + "</p>"])
def test_missing_or_invalid_rendered_never_falls_back_to_raw(rendered):
    review = review_pair(RAW_ESCAPED, rendered)
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None


def test_absent_rendered_never_falls_back_to_raw():
    review = copilot_review(RAW_ESCAPED)
    review.pop("body_html")
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None


@pytest.mark.parametrize("rendered", [None, {}, "", "<details", "<p>" + "x" * 65536 + "</p>"])
def test_adapter_missing_rendered_blocks_otherwise_actionable_raw(rendered):
    record = review_pair(
        OVERVIEW_MARKER + "\n\nRequired correction: reject pending receipts.", rendered)
    api = GhApi(run=lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps([record]), stderr=""))
    reviews = api.get_all("repos/lindayi/hermes-mobile/pulls/16/reviews?per_page=100")
    assert repair_request(HEAD, 0, [], [], reviews=reviews) is None
    assert not copilot_review_valid(HEAD, reviews, [])


@pytest.mark.parametrize("output", ['[{}]\n[', '[{}]\n{"incomplete":true}', 'not json'])
def test_adapter_incomplete_review_pages_fail_closed(output):
    api = GhApi(run=lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=output, stderr=""))
    with pytest.raises(ApiError):
        api.get_all("repos/lindayi/hermes-mobile/pulls/16/reviews?per_page=100")


@pytest.mark.parametrize("raw", [
    " " + OVERVIEW_MARKER + "\n", "\n" + OVERVIEW_MARKER + "\n",
    "> " + OVERVIEW_MARKER + "\n", "`" + OVERVIEW_MARKER + "`\n",
    OVERVIEW_MARKER + " trailing\n", "prose\n" + OVERVIEW_MARKER + "\n",
])
def test_only_canonical_leading_raw_marker_identifies_overview(raw):
    review = review_pair(raw + RAW_ESCAPED, HTML_ESCAPED, state="COMMENTED")
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None


@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_rendered_quotation_never_confers_disclosure_or_summary_authority(tag):
    rendered = (
        "<h2>Copilot review overview</h2><h3>🟢 Looks good</h3>"
        "<p>No bugs found.</p><" + tag + ">"
        "<details><summary>Previously missed (1)</summary>"
        "<p>Required correction: reject receipts.</p></details></" + tag + ">"
    )
    review = review_pair(OVERVIEW_MARKER + "\n\nSynthetic quoted example.", rendered)
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None


@pytest.mark.parametrize("entity", [
    "&lt;details&gt;", "&#60;details&#62;", "&#x3c;details&#x3e;",
])
def test_decoded_entities_never_create_new_elements(entity):
    rendered = HTML_ESCAPED.replace("&lt;details&gt;", entity)
    result = parse_body(RAW_ESCAPED, "COMMENTED", body_html=rendered)
    assert not result["ambiguous"]
    assert "decode <details> only after validation." in result["findings"][0][1]


def test_decoded_escaped_section_and_marker_are_text_not_authority():
    rendered = (
        "<p>&lt;!-- ccr-overview-v2 --&gt;</p>"
        "<p>&lt;details&gt;&lt;summary&gt;Previously missed (1)&lt;/summary&gt;"
        "Required correction: quoted example.&lt;/details&gt;</p>"
    )
    assert repair_request(HEAD, 0, [], [], reviews=[
        review_pair("Synthetic example.", rendered, state="COMMENTED")]) is None


@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_genuine_summary_retains_literal_quotation_without_status_filtering(tag):
    rendered = (
        "<p>Required correction: preserve this literal example.</p><" + tag + ">"
        "Validation is pending.\n---\n&lt;details&gt;\n&lt;!-- ccr-overview-v2 --&gt;"
        "</" + tag + ">"
    )
    result = parse_body("Required correction: preserve this literal example.", "CHANGES_REQUESTED",
                        body_html=rendered)
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested"
    assert "Validation is pending.\n---\n<details>\n<!-- ccr-overview-v2 -->" in text


def test_known_active_evidence_survives_independently_ambiguous_and_history_sections():
    rendered = (
        HTML_ESCAPED + "<details><summary>Unknown scope</summary>NEVER FORWARD UNKNOWN</details>"
        "<details><summary>History (1)</summary>"
        "<details><summary>Previously missed (1)</summary>NEVER FORWARD HISTORY</details></details>"
    )
    result = parse_body(RAW_ESCAPED, "COMMENTED", body_html=rendered)
    assert result["ambiguous"]
    [(_, text)] = result["findings"]
    assert "Decode literal context" in text
    assert "NEVER FORWARD" not in text


@pytest.mark.parametrize("label, value", [("Findings:", "None"), ("Review effort:", "Balanced")])
@pytest.mark.parametrize("break_", ["<br>", "\n", " "])
def test_overview_framing_cannot_swallow_explicit_correction(label, value, break_):
    raw = (OVERVIEW_MARKER + "\n\n## Copilot review overview\n\n"
           f"**{label}** {value}  \nRequired correction: reject pending receipts.")
    rendered = ("<h2>Copilot review overview</h2>"
                f"<p><strong>{label}</strong> {value}{break_}"
                "Required correction: reject pending receipts.</p>")
    review = review_pair(raw, rendered)
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    assert request is not None
    [finding] = _evidence(request)["review_findings"]
    assert "Required correction: reject pending receipts." in finding["comment"]


@pytest.mark.parametrize("literal", ["details", "summary"])
def test_body_plaintext_literal_survives_final_request_with_redaction_and_caps(literal):
    rendered = HTML_ESCAPED.replace("&lt;details&gt;", f"&lt;{literal}&gt;").replace(
        "only after validation.",
        "only after validation. https://example.invalid/private token: synthetic-value-123 "
        "@someone " + "x" * 1200)
    review = review_pair(RAW_ESCAPED, rendered, state="COMMENTED")
    threads = [{"id": "thread-literal", "isResolved": False, "comments": [{
        "body": "decode <details> and <summary> https://example.invalid/private "
                "token: synthetic-value-123 @someone " + "x" * 1200}]}]
    request = repair_request(HEAD, 0, threads, [], reviews=[review])
    thread, body = _evidence(request)["review_findings"]
    assert f"decode <{literal}> only after validation." in body["comment"]
    assert f"decode <{literal}> only after validation." in request["body"]
    assert "<details>" not in thread["comment"] and "<summary>" not in thread["comment"]
    for finding in (thread, body):
        assert len(finding["comment"]) == 1000
        assert "[link removed]" in finding["comment"]
        assert "[credential redacted]" in finding["comment"]
        assert "＠someone" in finding["comment"]
    for unsafe in ("example.invalid", "synthetic-value-123", "@someone"):
        assert unsafe not in request["body"]


def test_body_plaintext_literal_edit_changes_final_request_and_marker():
    requests = [repair_request(HEAD, 0, [], [], reviews=[review_pair(
        RAW_ESCAPED, HTML_ESCAPED.replace("&lt;details&gt;", f"&lt;{literal}&gt;"),
        state="COMMENTED")]) for literal in ("details", "summary")]
    assert requests[0]["body"] != requests[1]["body"]
    assert requests[0]["marker"] != requests[1]["marker"]


@pytest.mark.parametrize("change", ["html-edit", "literal-edit", "missing", "malformed"])
def test_changed_rendered_evidence_before_dispatch_never_consumes_attempt(tmp_path, change):
    class Changed(ReviewApi):
        def get_all(self, route, *, collection=None):
            if route.endswith("/reviews?per_page=100") and self.review_reads:
                rendered = {
                    "html-edit": HTML_ESCAPED.replace("only after validation", "before dispatch"),
                    "literal-edit": HTML_ESCAPED.replace("&lt;details&gt;", "&lt;summary&gt;"),
                    "missing": None, "malformed": "<details",
                }[change]
                self.reviews = [review_pair(RAW_ESCAPED, rendered, state="COMMENTED")]
            return super().get_all(route, collection=collection)

    api = Changed([review_pair(RAW_ESCAPED, HTML_ESCAPED, state="COMMENTED")])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert api.review_reads >= 2
    assert not plan["repair_requested"] and api.fix_attempts == 0
    assert not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("count", ["1", "0", "unknown"])
@pytest.mark.parametrize("shape", ["empty", "intro-only", "resolved-only", "history-only", "intro-history"])
def test_empty_active_fallback_never_dispatches_or_hides_real_finding(tmp_path, count, shape):
    intro = "<p>In code that hasn't changed since last review</p>"
    history = ("<details><summary>History (1)</summary>"
               "<details><summary>Previously missed (1)</summary>"
               "<p>NEVER FORWARD obsolete correction.</p></details></details>")
    content = {
        "empty": "", "intro-only": intro, "history-only": history,
        "resolved-only": history.replace("History (1)", "Resolved since last review (1)"),
        "intro-history": intro + history,
    }[shape]
    section = f"<details><summary>Previously missed ({count})</summary>{content}</details>"
    rendered = ("<h2>Copilot review overview</h2><p>No bugs found.</p>"
                "<p><strong>Findings:</strong> None</p>" + section)
    review = review_pair(OVERVIEW_MARKER + "\n\nNo bugs found.", rendered, state="COMMENTED")
    result = parse_body(review["body"], review["state"], body_html=rendered)
    assert result["findings"] == []
    assert result["ambiguous"] or "no-findings" in result["classifications"]
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    assert not copilot_review_valid(HEAD, [review], [])
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    state = StateStore(path).snapshot()
    assert state["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in state["actions"].values())
    # Local uncertainty must not invent an extra finding or erase a genuine one.
    request = repair_request(HEAD, 0, [], [], reviews=[review | {
        "body_html": HTML_ESCAPED + section}])
    [finding] = _evidence(request)["review_findings"]
    assert "decode <details> only after validation." in finding["comment"]
    assert "Previously missed" not in finding["comment"]
    assert "NEVER FORWARD" not in finding["comment"]


@pytest.mark.parametrize("count", ["1", "0", "unknown"])
def test_populated_active_fallback_keeps_live_content_without_history(count):
    rendered = (
        f"<details><summary>Previously missed ({count})</summary>"
        "<p>In code that hasn't changed since last review</p>"
        "<details><summary>History (1)</summary>NEVER FORWARD obsolete correction.</details>"
        "<p>Required correction: retain this live fallback.</p></details>")
    review = review_pair(OVERVIEW_MARKER + "\n\nSynthetic active section.", rendered,
                         state="COMMENTED")
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    [finding] = _evidence(request)["review_findings"]
    assert f"Previously missed ({count})" in finding["comment"]
    assert "Required correction: retain this live fallback." in finding["comment"]
    assert "NEVER FORWARD" not in finding["comment"]
    assert not copilot_review_valid(HEAD, [review], [])


def test_adapter_requests_full_media_on_every_paginated_review_and_detail():
    calls = []
    first = review_pair(RAW_ESCAPED, HTML_ESCAPED, state="COMMENTED")
    second = first | {"id": first["id"] + 1, "submitted_at": "2026-10-01T13:00:00Z",
                      "body": OVERVIEW_MARKER + "\n\nNo bugs found.",
                      "body_html": "<p>No bugs found.</p>"}

    def run(args, **kwargs):
        calls.append(args)
        output = json.dumps([first]) + "\n" + json.dumps([second]) if "--paginate" in args else json.dumps(first)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    api = GhApi(run=run)
    records = api.get_all("repos/lindayi/hermes-mobile/pulls/16/reviews?per_page=100")
    assert records == [first, second]
    assert repair_request(HEAD, 0, [], [], reviews=records) is None
    assert api.get("repos/lindayi/hermes-mobile/pulls/16/reviews/5300000001") == first
    for args in calls:
        assert "-H" in args
        assert "Accept: application/vnd.github.full+json" in args
