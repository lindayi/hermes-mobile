"""Issue #39 structural classes; public synthetic review text only."""
import pytest

from deploy import review_evidence as evidence
from deploy.cloud_coordinator import repair_request, copilot_review_valid, StateStore
from test_cloud_coordinator import HEAD, _managed_cycle
from test_review_evidence import (
    BODY_ONLY, FOOTER, MISSED, OPEN, RESOLVED, ReviewApi, copilot_review,
    missed_item, overview, section, _evidence,
)


def parse(body, state="CHANGES_REQUESTED"):
    return evidence.parse_body(body, state, body_html=body.removeprefix(evidence.OVERVIEW_MARKER))


@pytest.mark.parametrize("prose", [
    "Required correction: reject pending receipts.",
    "The implementation fails to reject pending receipts.",
])
def test_positive_open_is_not_duplicated_as_body_summary(prose):
    body = overview("🟡 Changes recommended", prose, "1", OPEN)
    result = parse(body)
    assert "inline" in result["classifications"]
    assert result["findings"] == []
    assert repair_request(HEAD, 0, [], [], reviews=[copilot_review(
        body, state="CHANGES_REQUESTED")]) is None


@pytest.mark.parametrize("historical", ["Resolved since last review (1)", "Review history (1)"])
@pytest.mark.parametrize("position", ["outer", "inner", "deep"])
def test_historical_ancestors_never_forward_nested_active_looking_text(historical, position):
    obsolete = section(historical, section("Previously missed (1)", missed_item(
        "NEVER FORWARD HISTORICAL", "old.py:1", "Required correction: obsolete work.")))
    if position == "outer":
        body = overview("🔵 Needs a closer look", "No code issues were found.", "None", obsolete, MISSED)
    else:
        if position == "deep":
            obsolete = section("Reproduction", "Safe reproduction. " + obsolete)
        active = section("Previously missed (1)", section(
            "Reject pending receipts", "Current defect before. " + obsolete + " Current defect after."))
        body = overview("🔵 Needs a closer look", "No code issues were found.", "None", active)
    result = parse(body)
    assert not result["ambiguous"]
    assert result["findings"]
    forwarded = str(result["findings"])
    assert "NEVER FORWARD HISTORICAL" not in forwarded and "obsolete work" not in forwarded
    if position != "outer":
        assert "Current defect before" in forwarded and "Current defect after" in forwarded


@pytest.mark.parametrize("markup", [
    '<SUMMARY title="a > b"><strong class="x"><span>Previously</span> missed (1)</strong></SUMMARY >',
    '<summary><em>Previously missed</em> <strong>(1)</strong></summary\n>',
    '<summary data-x="</details>"><strong>Previously missed (1)</strong></summary>',
])
def test_nested_inline_markup_and_quoted_attributes_are_structural(markup):
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    '<DETAILS data-x=">">' + markup + missed_item(
                        "Reject pending receipts", "example.py:1", "Keep domain finding.") + '</DETAILS >')
    result = parse(body)
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed" and "Keep domain finding." in text
    assert "</details>" not in text


@pytest.mark.parametrize("quoted, literal", [
    ('<code>&lt;/details&gt; and &lt;/details &gt;</code>', '</details> and </details >'),
    ('<code>&lt;details data-x="`"&gt;</code>', '<details data-x="`">'),
    ('<pre><code>&lt;details&gt;&lt;summary&gt;Resolved&lt;/summary&gt;literal snippet\n</code></pre>',
     '<details><summary>Resolved</summary>literal snippet'),
    ('<code>&lt;!-- ccr-overview-v2 --&gt;</code>', '<!-- ccr-overview-v2 -->'),
])
def test_rendered_code_is_literal_not_disclosure_structure(quoted, literal):
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Required correction: handle " + quoted)))
    result = parse(body)
    assert not result["ambiguous"], result
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed" and literal in text


@pytest.mark.parametrize("quoted, literal", [
    ("<pre><code>&lt;/details&gt;&lt;details&gt;&lt;summary&gt;History&lt;/summary&gt;literal snippet\n</code></pre>",
     "</details><details><summary>History</summary>literal snippet"),
    ("<pre lang=\"html\"><code>&lt;details&gt;literal snippet&lt;/details&gt;</code></pre>",
     "<details>literal snippet</details>"),
])
def test_rendered_fences_preserve_literal_disclosures(quoted, literal):
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Required correction:\n" + quoted + "\nCurrent tail.")),
                    RESOLVED)
    result = parse(body)
    assert not result["ambiguous"], result
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed" and literal in text
    assert "Synthetic resolved finding" not in text
    request = repair_request(HEAD, 0, [], [], reviews=[copilot_review(body)])
    assert len(_evidence(request)["review_findings"]) == 1


@pytest.mark.parametrize("literal", [
    "```", "~~~", "~~~~", "````", "```` trailing", "~~~~ trailing",
    "    ````", "    ~~~~", "\t````", "\t~~~~",
])
def test_rendered_code_fence_characters_are_only_text(literal):
    quoted = ("<pre><code>" + literal + "\n"
              "&lt;/details&gt;&lt;summary&gt;literal after false closer&lt;/summary&gt;\n</code></pre>")
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Required correction:\n" + quoted)))
    result = parse(body)
    assert not result["ambiguous"], result
    assert literal in result["findings"][0][1]
    assert "</details><summary>literal after false closer</summary>" in result["findings"][0][1]


@pytest.mark.parametrize("quoted", [
    "<pre><code>unclosed", "<pre>unclosed", "<code>unclosed",
    "<pre><code>crossed</pre></code>",
])
def test_unbounded_rendered_code_fails_closed(quoted):
    body = overview("🟡 Changes recommended", "Required correction: fix parser.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Reproduction:\n" + quoted)))
    result = parse(body)
    assert result["ambiguous"] and result["findings"] == []


@pytest.mark.parametrize("quoted", [
    "<code>&lt;/details&gt;</code", "<code>&lt;/details&gt;</cod",
])
def test_unfinished_rendered_code_closer_is_not_recovered(quoted):
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Required correction: " + quoted)))
    result = parse(body)
    assert result["ambiguous"] and not result["findings"]


@pytest.mark.parametrize("indent", ["    ", "        ", "\t", " \t"])
def test_indented_disclosure_examples_never_become_active_findings(indent):
    quoted = "<pre><code>" + indent + "&lt;details&gt;Previously missed (1)&lt;/details&gt;</code></pre>"
    body = overview("🟢 Looks good", "No code issues were found.", "None", quoted)
    result = parse(body, "COMMENTED")
    assert result["findings"] == []
    review = copilot_review(body)
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    assert not copilot_review_valid(HEAD, [review], [])


@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_supported_disclosure_indentation_retains_active_findings(indent):
    disclosure = "\n".join(indent + line for line in MISSED.splitlines())
    body = overview("🟢 Looks good", "No code issues were found.", "None", disclosure)
    result = parse(body, "COMMENTED")
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed" and "Defer synthetic transient state" in text


@pytest.mark.parametrize("length", [1, 2, 3, 4])
def test_rendered_inline_backticks_do_not_change_structure(length):
    quoted = "<code>" + "`" * length + "&lt;/details&gt;" + "`" * length + "</code>"
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (1)", missed_item(
                        "Fix parser quoting", "parser.py:1", "Required correction:\n" + quoted)))
    result = parse(body)
    assert not result["ambiguous"], result
    assert "`" * length + "</details>" + "`" * length in result["findings"][0][1]


def test_backticks_in_attributes_do_not_open_markdown_code():
    body = overview("🟡 Changes recommended", "Required correction: reject pending receipts.", "None",
                    RESOLVED.replace('<details>', '<details title="`">').replace(
                        '</details>', '</details >'))
    result = parse(body)
    assert not result["ambiguous"] and result["findings"]
    assert "Synthetic resolved finding" not in str(result["findings"])


def test_balanced_deep_history_is_excluded_without_regex_round_limit():
    nested = "NEVER FORWARD HISTORICAL"
    for _ in range(20):
        nested = section("Supporting detail", nested)
    body = overview("🟡 Changes recommended", "Required correction: reject pending receipts.",
                    "None", section("History", nested))
    result = parse(body)
    assert not result["ambiguous"]
    assert result["findings"] and "NEVER FORWARD HISTORICAL" not in str(result["findings"])


@pytest.mark.parametrize("prose", [
    "The exact-head verification remains pending. Required correction: reject pending receipts.",
    "Required correction: reject receipts awaiting validation. Exact-head verification is still pending.",
    "No code issues were found. Required correction: exclude historical findings.",
    "Exact-head verification remains pending, but required correction: reject pending receipts before use.",
])
@pytest.mark.parametrize("status", ["🟡 Changes recommended", "🟢 Looks good"])
def test_status_and_friendly_headline_cannot_hide_real_changes_requested(prose, status):
    result = parse(overview(status, prose, "None", RESOLVED))
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested"
    if prose.startswith("The exact-head"):
        assert text == "Required correction: reject pending receipts."
    elif prose.startswith("Required correction:"):
        assert text == "Required correction: reject receipts awaiting validation."
    else:
        assert prose in text


@pytest.mark.parametrize("prose", [
    "Exact-head verification remains pending. No code issues were found.",
    "The exact-head verification remains pending.",
    "Exact-head verification is still pending.",
    "Validation is pending.",
])
def test_validation_only_is_an_explicit_disposition(prose):
    for body in (prose, overview("🔵 Needs a closer look", prose, "None")):
        result = parse(body)
        assert result["classifications"] == ["validation-only"]
        assert not result["ambiguous"] and result["findings"] == []


@pytest.mark.parametrize("prose", [
    "No bugs found.", "No bugs were found.", "No defects found.",
    "No code defects were found in the reviewed changes.",
    "No issues found.", "No problems were detected.",
    "No vulnerabilities were identified in this diff.", "No findings found!",
    "NO CODE BUGS WERE FOUND", "No bugs found. No defects were found.",
])
@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
def test_complete_negative_verdicts_are_not_affirmative_findings(prose, state):
    body = overview("🟢 Looks good", prose, "None")
    result = parse(body, state)
    assert result["classifications"] == ["no-findings"]
    assert not result["ambiguous"] and not result["findings"]
    assert repair_request(HEAD, 0, [], [], reviews=[copilot_review(body, state=state)]) is None
    assert not copilot_review_valid(HEAD, [copilot_review(body, state=state)], [])
    # Bare requested-changes remains authoritative except for explicit negatives.
    if state == "CHANGES_REQUESTED":
        assert parse(prose)["classifications"] == ["no-findings"]


@pytest.mark.parametrize("prose", [
    "No bugs found. Exact-head verification remains pending.",
    "The exact-head verification is still pending. No code defects were found.",
    "No vulnerabilities were identified. Validation is pending. No issues found.",
])
def test_complete_negative_verdicts_allow_validation_only(prose):
    for body in (prose, overview("🔵 Needs a closer look", prose, "None")):
        result = parse(body)
        assert result["classifications"] == ["validation-only"]
        assert not result["ambiguous"] and not result["findings"]


@pytest.mark.parametrize("prose", [
    "No bugs found. Receipt behavior remains unclear.",
    "No defects found. Exact-head verification remains pending. Receipt behavior remains unclear.",
])
def test_complete_negative_verdict_does_not_make_uncertain_prose_actionable(prose):
    result = parse(overview("🔵 Needs a closer look", prose, "None"))
    assert result["ambiguous"] and result["findings"] == []


@pytest.mark.parametrize("prose, correction", [
    ("No bugs found. Required correction: reject pending receipts.",
     "Required correction: reject pending receipts."),
    ("No defects found. Required correction: exclude history. Validation is pending.",
     "Required correction: exclude history."),
    ("No bugs were found in the diff, but required correction: reject pending receipts.",
     "required correction: reject pending receipts."),
    ("No issues were found. Required correction: reject boolean IDs.",
     "Required correction: reject boolean IDs."),
    ("Required correction: preserve validation failures. No vulnerabilities were found.",
     "Required correction: preserve validation failures."),
])
def test_complete_negative_verdict_never_swallows_separate_or_compound_correction(prose, correction):
    body = overview("🟢 Looks good", prose, "None", RESOLVED)
    result = parse(body)
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested" and correction in text
    assert "Validation is pending." not in text
    assert "Synthetic resolved finding" not in text


@pytest.mark.parametrize("disclosure", [OPEN, MISSED])
def test_complete_negative_verdict_preserves_independent_structural_findings(disclosure):
    body = overview("🟢 Looks good", "No bugs found. Required correction: reject pending receipts.",
                    "None", disclosure)
    result = parse(body)
    assert not result["ambiguous"]
    if disclosure == OPEN:
        assert result["findings"] == []
    else:
        [(kind, text)] = result["findings"]
        assert kind == "previously-missed" and "Defer synthetic transient state" in text


@pytest.mark.parametrize("prose", ["No bugs found.", "No defects were found. Validation is pending."])
def test_complete_negative_verdict_never_consumes_fixer_attempt(tmp_path, prose):
    review = copilot_review(overview("🟢 Looks good", prose, "None"), state="CHANGES_REQUESTED")
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("quoted", [
    "<code>Required correction: reject pending receipts.</code>",
    "<pre><code>Required correction: reject pending receipts.\n</code></pre>",
    "<blockquote><p>Required correction: reject pending receipts.</p></blockquote>",
])
def test_quoted_provenance_summary_never_dispatches(tmp_path, quoted):
    body = overview("🟢 Looks good", "No bugs found.\n\n" + quoted, "None")
    result = parse(body)
    assert result["findings"] == []
    review = copilot_review(body, state="CHANGES_REQUESTED")
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("label", [
    "<code>Previously missed (1)</code>", "Previously missed (1) <code>example</code>",
])
def test_quoted_provenance_section_label_is_not_authority(label):
    body = overview("🟢 Looks good", "No bugs found.", "None", section(label, MISSED))
    result = parse(body, "COMMENTED")
    assert result["ambiguous"] and result["findings"] == []
    # Unknown quotation scope must not erase an independently real finding.
    result = parse(body + MISSED, "COMMENTED")
    assert result["ambiguous"] and len(result["findings"]) == 1


@pytest.mark.parametrize("prose, literal", [
    ("Required correction: preserve <code>reject_pending</code> before dispatch.",
     "Required correction: preserve reject_pending before dispatch."),
    ("Required correction: preserve the identifier.\n<pre><code>reject_pending\n</code></pre>",
     "Required correction: preserve the identifier."),
])
def test_quoted_provenance_real_summary_retains_literal_context(prose, literal):
    result = parse(overview("🟢 Looks good", prose, "None"))
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested" and literal in text and "reject_pending" in text


@pytest.mark.parametrize("container", ["blockquote", "ul", "ol"])
def test_quoted_provenance_container_code_is_inert(tmp_path, container):
    quoted = ("<" + container + "><li><pre><code>"
              "&lt;details&gt;&lt;summary&gt;Previously missed (1)&lt;/summary&gt;"
              "Required correction: reject receipts.&lt;/details&gt;"
              "</code></pre></li></" + container + ">")
    body = overview("🟢 Looks good", "No bugs found.", "None", quoted)
    result = parse(body, "COMMENTED")
    assert result["findings"] == []
    api = ReviewApi([copilot_review(body)])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("prefix", ["    ", "\t", " \t", "> ", "- ", "1. ", "- Example: "])
def test_quoted_provenance_prefixed_marker_cannot_authorize_commented(tmp_path, prefix):
    body = prefix + evidence.OVERVIEW_MARKER + "\n\n" + MISSED
    result = parse(body, "COMMENTED")
    assert result["ambiguous"] and result["findings"] == []
    api = ReviewApi([copilot_review(body)])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_quoted_provenance_html_subtrees_are_inert(tag):
    quoted = f"<{tag}>" + evidence.OVERVIEW_MARKER + MISSED + f"</{tag}>"
    body = overview("🟢 Looks good", "No bugs found.", "None", quoted)
    assert parse(body)["findings"] == []
    assert parse(quoted + MISSED, "COMMENTED")["findings"] == []
    active = overview("🟢 Looks good", "No bugs found.", "None", section(
        "Previously missed (1)", missed_item("Fix quoting", "a.py:1", "Required correction: " + quoted)))
    [(kind, text)] = parse(active)["findings"]
    assert kind == "previously-missed" and "Defer synthetic transient state" in text


@pytest.mark.parametrize("quoted", [
    "<pre><code>Required correction: reject pending receipts.</code></pre>",
    "<blockquote><p>Required correction: reject pending receipts.</p></blockquote>",
    "<code>Required correction: reject pending receipts.</code>",
    "<pre>Required correction: reject pending receipts.</pre>",
    "<blockquote>Required correction: reject pending receipts.</blockquote>",
])
def test_quoted_provenance_sibling_summary_never_actionable(quoted):
    body = overview("🟢 Looks good", "No bugs found.\n\n" + quoted, "None")
    assert parse(body)["findings"] == []


@pytest.mark.parametrize("container", ["blockquote", "pre", "code"])
def test_quoted_provenance_container_disclosures_are_not_live(container):
    quoted = "<" + container + ">" + MISSED + "</" + container + ">"
    result = parse(overview("🟢 Looks good", "No bugs found.", "None", quoted), "COMMENTED")
    assert result["findings"] == []


@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_quoted_provenance_html_section_labels_are_not_live(tag):
    body = overview("🟢 Looks good", "No bugs found.", "None",
                    section(f"<{tag}>Previously missed (1)</{tag}>", MISSED))
    result = parse(body, "COMMENTED")
    assert result["ambiguous"] and result["findings"] == []


@pytest.mark.parametrize("quoted, literal", [
    ("<pre><code>Validation is pending.\n---\n**Findings:** None\n</code></pre>",
     "Validation is pending.\n---\n**Findings:** None"),
    ('<pre><span title="</pre>">literal context</span></pre>', "literal context"),
    ('<code><!-- </code> -->literal context</code>', "literal context"),
])
def test_quoted_provenance_context_survives_summary_filters(quoted, literal):
    correction = "Required correction: preserve this example.\n" + quoted
    body = overview("🟢 Looks good", "Validation is pending.\n" + correction, "None")
    result = parse(body)
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested" and literal in text
    assert not text.startswith("Validation is pending.")


def test_quoted_provenance_horizontal_rule_cannot_truncate_later_correction():
    quoted = "<pre><code>---\nNo bugs found.\n</code></pre>"
    prose = quoted + "\nRequired correction: reject pending receipts."
    result = parse(overview("🟢 Looks good", prose, "None"))
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested" and "---\nNo bugs found." in text
    assert "Required correction: reject pending receipts." in text


@pytest.mark.parametrize("container", ["ul", "ol"])
def test_quoted_provenance_ordinary_list_corrections_remain_live(container):
    prose = ("<" + container + "><li>Required correction: reject <code>pending_receipts</code>"
             " before dispatch.</li></" + container + ">")
    result = parse(overview("🟢 Looks good", prose, "None"))
    [(kind, text)] = result["findings"]
    assert kind == "changes-requested"
    assert "Required correction: reject pending_receipts before dispatch." in text


@pytest.mark.parametrize("quoted", [
    "<pre>unclosed", "<code>outer<code>nested</code>",
    "<blockquote>outer<blockquote>nested</blockquote>",
])
def test_quoted_provenance_unsupported_html_is_ambiguous(quoted):
    result = parse(overview("🟢 Looks good", "No bugs found.", "None", quoted, MISSED))
    assert result["ambiguous"] and not result["findings"]


def test_bare_changes_requested_without_explicit_correction_is_now_ambiguous():
    prose = "The coordinator accepts boolean receipt identities as integers."
    result = parse(prose)
    assert result["findings"] == []
    assert result["ambiguous"]
    assert repair_request(HEAD, 0, [], [], reviews=[copilot_review(
        prose, state="CHANGES_REQUESTED")]) is None


def test_mixed_summary_excludes_only_complete_validation_sentences():
    correction = "Required correction: reject pending receipts before dispatch."
    prose = "The exact-head verification remains pending. " + correction
    result = parse(overview("🔵 Needs a closer look", prose, "None"))
    assert result["findings"] == [("changes-requested", correction)]
    assert not result["ambiguous"]


@pytest.mark.parametrize("prose", [
    "This deserves another look.",
    "Exact-head verification is still pending. Receipt behavior remains unclear.",
])
def test_ambiguous_prose_is_not_a_blind_fixer_or_an_approval(tmp_path, prose):
    body = overview("🔵 Needs a closer look", prose, "None")
    result = parse(body)
    assert result["ambiguous"] and result["findings"] == []
    review = copilot_review(body, state="CHANGES_REQUESTED")
    assert not copilot_review_valid(HEAD, [review], [])
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0


@pytest.mark.parametrize("disclosure", [
    '<details><summary>Unknown scope</summary>Maybe a finding.</details>',
    '<details><summary>Previously missed (1)</summary><details><summary>Title</summary>Fix it.',
    '<details><summary>Previously missed (1)</details></summary>',
    '<summary>Previously missed (1)</summary>',
    '<details><summary>Previously missed (1)</summary><summary>Other</summary></details>',
    '<details><summary>Previously missed (1)<details>Nested</details></summary></details>',
    '<details/><summary>Previously missed (1)</summary>',
    '<details><summary>Previously missed (1)</summary></details',
])
def test_unknown_or_unbounded_disclosures_are_ambiguous_not_fix_evidence(disclosure):
    result = parse(overview("🟡 Changes recommended", "Required correction: fix parser.", "None", disclosure))
    assert result["ambiguous"] and result["findings"] == []
    assert "ambiguous" in result["classifications"]


@pytest.mark.parametrize("bound", ["chars", "depth", "events", "disclosures"])
def test_parser_resource_bounds_reject_whole_input_not_forward_prefix(bound):
    suffix = {
        "chars": "x" * evidence.MAX_BODY_CHARS,
        "depth": "<span>" * (evidence.MAX_PARSE_DEPTH + 1) + "x" + "</span>" * (evidence.MAX_PARSE_DEPTH + 1),
        "events": "<br>" * (evidence.MAX_PARSE_EVENTS + 1),
        "disclosures": section("History", "") * (evidence.MAX_DISCLOSURES + 1),
    }[bound]
    result = parse(BODY_ONLY + suffix)
    assert result["ambiguous"] and result["findings"] == []
    assert "limit" in result["reason"]


def test_exact_body_limit_is_not_truncated():
    prefix = "Required correction: reject pending receipts. "
    result = parse(prefix + "x" * (evidence.MAX_BODY_CHARS - len(prefix)))
    assert not result["ambiguous"] and len(result["findings"][0][1]) == evidence.MAX_BODY_CHARS


def test_intact_active_section_count_mismatch_preserves_whole_without_history():
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section("Previously missed (unknown)", "Unstructured positive finding. " +
                            RESOLVED + missed_item("Reject pending receipts", "a.py:1", "Current tail.")))
    result = parse(body)
    assert not result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed"
    assert "Previously missed (unknown)" in text and "Current tail." in text
    assert "Unstructured positive finding" in text and "Synthetic resolved finding" not in text


def test_open_zero_with_positive_body_content_is_not_silently_inline():
    body = overview("🔵 Needs a closer look", "Unclear summary.", "None",
                    section("Open (0)", "Required correction: reject pending receipts."))
    result = parse(body)
    assert result["ambiguous"] and not result["findings"]


@pytest.mark.parametrize("count, children", [
    (0, ""),
    (1, missed_item("First correction", "a.py:1", "First detail.")),
])
def test_explicit_active_section_preserves_unstructured_positive_content(count, children):
    body = overview("🔵 Needs a closer look", "No code issues were found.", "None",
                    section(f"Previously missed ({count})", children + "\nRequired correction: retain this tail."))
    result = parse(body)
    assert any("Required correction: retain this tail." in text for _, text in result["findings"])


def test_plain_commented_disclosures_do_not_gain_overview_authority():
    body = MISSED  # No ccr-overview-v2 marker.
    assert repair_request(HEAD, 0, [], [], reviews=[copilot_review(body)]) is None


def test_unrelated_unknown_disclosure_does_not_drop_known_active_section():
    body = BODY_ONLY + section("Future footer", "Ambiguous explanatory text.")
    result = parse(body)
    assert result["ambiguous"]
    [(kind, text)] = result["findings"]
    assert kind == "previously-missed" and "Defer synthetic transient state" in text
    assert "Ambiguous explanatory text" not in text
    request = repair_request(HEAD, 0, [], [], reviews=[copilot_review(body)])
    assert len(_evidence(request)["review_findings"]) == 1
    assert not copilot_review_valid(HEAD, [copilot_review(body)], [])


def test_ambiguous_body_does_not_suppress_independent_thread_evidence():
    review = copilot_review(overview("🔵 Needs a closer look", "Unclear.", "None"), state="CHANGES_REQUESTED")
    request = repair_request(HEAD, 0, [{"id": "thread-real", "isResolved": False,
                                      "comments": [{"body": "Current thread finding."}]}], [], reviews=[review])
    assert _evidence(request)["review_findings"] == [{"thread": "thread-real", "comment": "Current thread finding."}]


def test_changed_body_to_ambiguous_before_dispatch_never_consumes_attempt(tmp_path):
    class Changed(ReviewApi):
        def get_all(self, route, *, collection=None):
            if route.endswith("/reviews?per_page=100") and self.review_reads:
                self.reviews = [copilot_review(overview("🔵 Needs a closer look", "Unclear.", "None"),
                                              state="CHANGES_REQUESTED")]
            return super().get_all(route, collection=collection)
    api = Changed([copilot_review(BODY_ONLY)])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and api.fix_attempts == 0
    assert not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0
