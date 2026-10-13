"""Public synthetic full-media review pairs, authored without a test renderer."""
import json
from types import SimpleNamespace

import pytest

from deploy.cloud_coordinator import ApiError, GhApi, StateStore, copilot_review_valid, repair_request
from deploy.review_evidence import OVERVIEW_MARKER, parse_body, review_body_disposition
from test_cloud_coordinator import (
    COPILOT_REVIEWER, HEAD, ProgressApi, _managed_cycle, refresh_owner_review,
)
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

CURRENT_COPILOT_RAW = (
    "<!-- ccr-overview-v2 -->\n\n### 🔵 Needs a closer look\n\n"
    "The operator procedure does not bind the active release, application virtualenv, "
    "or live configuration path, so its commands may fail or operate on the wrong source.\n\n"
    "<details open>\n<summary><strong>1 open finding</strong></summary>\n\n"
    "- [Register runtime dependency in pinned execution-source policy]"
    "(https://github.com/lindayi/hermes-mobile/pull/99#discussion_r4250499422) · New\n"
    "</details>\n\n"
    "<details>\n<summary><strong>Previously missed (1)</strong></summary>\n\n"
    "In code that hasn't changed since last review\n\n"
    "<details><summary>Operator commands omit deployed source, virtualenv, and live config</summary>\n\n"
    "`docs/photo-attachments-spec.md:292`\n\n"
    "These commands do not bind the deployed source, the bridge's virtualenv, or even a value "
    "for `HERMES_MOBILE_CONFIG`; a normal owner shell does not inherit the service environment. "
    "Since `backend.manage` imports locked dependencies at startup, bare `python3` may fail or "
    "may run a checkout different from the active release, so this is not a safe exact operator "
    "procedure. Use the active `hermes-mobile-deploy/current` working tree, the established "
    "application virtualenv, and the explicit live config path.\n</details>\n</details>\n\n"
    "🧠 **Review effort:** Balanced"
)
CURRENT_COPILOT_HTML = (
    "<h3>🔵 Needs a closer look</h3>"
    "<p>The operator procedure does not bind the active release, application virtualenv, "
    "or live configuration path, so its commands may fail or operate on the wrong source.</p>"
    "<details open><summary><strong>1 open finding</strong></summary>"
    "<ul><li><a href=\"https://github.com/lindayi/hermes-mobile/pull/99#discussion_r4250499422\">"
    "Register runtime dependency in pinned execution-source policy</a> · New</li></ul></details>"
    "<details><summary><strong>Previously missed (1)</strong></summary>"
    "In code that hasn't changed since last review"
    "<details><summary>Operator commands omit deployed source, virtualenv, and live config</summary>"
    "<p><code>docs/photo-attachments-spec.md:292</code></p>"
    "<p>These commands do not bind the deployed source, the bridge's virtualenv, or even a value "
    "for <code>HERMES_MOBILE_CONFIG</code>; a normal owner shell does not inherit the service "
    "environment. Since <code>backend.manage</code> imports locked dependencies at startup, bare "
    "<code>python3</code> may fail or may run a checkout different from the active release, so "
    "this is not a safe exact operator procedure. Use the active "
    "<code>hermes-mobile-deploy/current</code> working tree, the established application virtualenv, "
    "and the explicit live config path.</p></details></details>"
    "<p>🧠 <strong>Review effort:</strong> Balanced</p>"
)

LATEST_HUMAN_REVIEW_SUMMARY = (
    "The authorization-sensitive coordinator and review-evidence changes require "
    "final human security review despite extensive regression coverage."
)
LATEST_HUMAN_REVIEW_BODY = f"""<!-- ccr-overview-v2 -->

### 🔵 Needs a closer look

{LATEST_HUMAN_REVIEW_SUMMARY}

**0 open findings**

<details>
<summary><strong>2 resolved since last review</strong></summary>

- <picture><source media="(prefers-color-scheme: dark)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.svg"><img src="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.png" alt="Low severity" width="62" height="18" align="texttop"></picture> [Rename fixture to reflect Issue #106 provenance](https://github.com/lindayi/hermes-mobile/pull/107#discussion_r4254794564)
- <picture><source media="(prefers-color-scheme: dark)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.svg"><img src="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.png" alt="Low severity" width="62" height="18" align="texttop"></picture> [Correct provenance label from Issue #107 to Issue #106](https://github.com/lindayi/hermes-mobile/pull/107#discussion_r4254794531)
</details>

🧠 **Review effort:** Balanced"""
LATEST_HUMAN_REVIEW_HTML = f"""<h3>🔵 Needs a closer look</h3>
<p>{LATEST_HUMAN_REVIEW_SUMMARY}</p>
<p><strong>0 open findings</strong></p>
<details><summary><strong>2 resolved since last review</strong></summary>
<ul>
<li><picture><source media="(prefers-color-scheme: dark)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.svg"><img src="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.png" alt="Low severity" width="62" height="18" align="texttop"></picture> <a href="https://github.com/lindayi/hermes-mobile/pull/107#discussion_r4254794564">Rename fixture to reflect Issue #106 provenance</a></li>
<li><picture><source media="(prefers-color-scheme: dark)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.svg"><img src="https://github.githubassets.com/static/images/icons/copilot-code-review/low-v2-light.png" alt="Low severity" width="62" height="18" align="texttop"></picture> <a href="https://github.com/lindayi/hermes-mobile/pull/107#discussion_r4254794531">Correct provenance label from Issue #107 to Issue #106</a></li>
</ul></details>
<p>🧠 <strong>Review effort:</strong> Balanced</p>"""


@pytest.mark.parametrize("count", [0, 1, 8, 9])
def test_rendered_inventory_reports_total_and_cap(count):
    rendered = (
        f"<details><summary>Previously missed ({count})</summary>"
        + "".join(f"<details><summary>Target {index}</summary><p>Fix target {index}.</p>"
                  "</details>" for index in range(count))
        + "</details>"
    )
    review = review_pair(OVERVIEW_MARKER + "\nSynthetic inventory", rendered)
    result = review_body_disposition([review], HEAD, reviewer_id=review["user"]["id"])
    assert result["findings_total"] == count
    assert len(result["findings"]) == min(count, 8)
    assert result["truncated"] is (count > 8)
    assert result["inventory_complete"] is (count <= 8)


@pytest.mark.parametrize("concern", ["", "The source skips owner checks."])
def test_latest_human_security_overview_stays_ambiguous(concern):
    raw = LATEST_HUMAN_REVIEW_BODY
    rendered = LATEST_HUMAN_REVIEW_HTML
    if concern:
        raw = raw.replace(
            LATEST_HUMAN_REVIEW_SUMMARY,
            LATEST_HUMAN_REVIEW_SUMMARY + "\n\n" + concern,
        )
        rendered = rendered.replace(
            f"<p>{LATEST_HUMAN_REVIEW_SUMMARY}</p>",
            f"<p>{LATEST_HUMAN_REVIEW_SUMMARY}</p><p>{concern}</p>",
        )

    review = review_pair(raw, rendered, state="COMMENTED")
    review["id"] = 5499070544
    review["submitted_at"] = "2026-10-13T05:10:57Z"
    parsed = parse_body(raw, "COMMENTED", body_html=rendered)
    disposition = review_body_disposition(
        [review], HEAD, reviewer_id=COPILOT_REVIEWER,
    )

    assert parsed["ambiguous"] and not parsed["inventory_complete"]
    assert disposition["ambiguous"] and not disposition["inventory_complete"]
    assert "validation-only" not in parsed["classifications"]
    assert "validation-only" not in disposition["classifications"]


@pytest.mark.parametrize("legacy_heading", ["", "<h2>Copilot review overview</h2>"])
def test_current_copilot_overview_counts_findings_and_preserves_summary_context(legacy_heading):
    review = review_pair(CURRENT_COPILOT_RAW, legacy_heading + CURRENT_COPILOT_HTML,
                         state="COMMENTED")
    result = review_body_disposition([review], HEAD, reviewer_id=review["user"]["id"])

    assert result["inventory_complete"] is True
    assert result["ambiguous"] is False
    assert result["findings_total"] == 1
    [finding] = result["findings"]
    assert finding["review"] == review["id"] and finding["head"] == HEAD
    assert "The operator procedure does not bind the active release" in finding["text"]
    assert "These commands do not bind the deployed source" in finding["text"]
    assert "Register runtime dependency in pinned execution-source policy" not in finding["text"]


@pytest.mark.parametrize('location', ['after-summary', 'after-inventory'])
def test_misplaced_single_legacy_heading_is_ambiguous(location):
    heading = '<h2>Copilot review overview</h2>'
    rendered = CURRENT_COPILOT_HTML
    if location == 'after-summary':
        rendered = rendered.replace('</p>', '</p>' + heading, 1)
    else:
        rendered += heading
    result = parse_body(CURRENT_COPILOT_RAW, 'COMMENTED', body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings_total'] == 1


@pytest.mark.parametrize('heading', ['<h3>Looks good</h3>', '<h3>🔵 Needs a closer look</h3>',
                                     '<h2>Copilot review overview</h2>'])
@pytest.mark.parametrize('location', ['before', 'middle', 'after'])
def test_duplicate_provider_frame_headings_are_ambiguous(heading, location):
    rendered = '<h2>Copilot review overview</h2>' + CURRENT_COPILOT_HTML
    if location == 'before':
        rendered = heading + rendered
    elif location == 'middle':
        rendered = rendered.replace('</p>', '</p>' + heading, 1)
    else:
        rendered += heading
    result = parse_body(CURRENT_COPILOT_RAW, 'COMMENTED', body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings_total'] == 1


@pytest.mark.parametrize("legacy_heading", ["", "<h2>Copilot review overview</h2>"])
@pytest.mark.parametrize("text_position", ["before-status", "before-summary", "after-summary"])
def test_provider_summary_root_text_is_incomplete_without_crashing(legacy_heading, text_position):
    rendered = legacy_heading + CURRENT_COPILOT_HTML
    if text_position == "before-status":
        rendered = rendered.replace("<h3>", "Unclassified root text.<h3>", 1)
    elif text_position == "before-summary":
        rendered = rendered.replace("<p>", "Unclassified root text.<p>", 1)
    else:
        rendered = rendered.replace("</p>", "</p>Unclassified root text.", 1)
    result = parse_body(CURRENT_COPILOT_RAW, "COMMENTED", body_html=rendered)

    assert result["inventory_complete"] is False
    assert result["ambiguous"] is True
    assert result["findings_total"] == 1
    assert "These commands do not bind the deployed source" in result["findings"][0][1]


@pytest.mark.parametrize("rendered", [
    CURRENT_COPILOT_HTML.replace("1 open finding", "2 open findings"),
    CURRENT_COPILOT_HTML.replace("Previously missed (1)", "Previously missed (2)"),
    CURRENT_COPILOT_HTML.replace(
        "<details><summary><strong>Previously missed",
        "<details><summary><strong>1 open finding</strong></summary>"
        "<ul><li><a href=\"#discussion_r445\">Duplicate section</a></li></ul></details>"
        "<details><summary><strong>Previously missed",
    ),
    CURRENT_COPILOT_HTML.replace(
        "<h3>🔵 Needs a closer look</h3>",
        "<h3>🔵 Needs a closer look</h3><p>Unaccounted review prose.</p>",
    ),
])
def test_current_overview_count_mismatch_duplicate_or_extra_prose_is_incomplete(rendered):
    review = review_pair(CURRENT_COPILOT_RAW, rendered, state="COMMENTED")
    result = review_body_disposition([review], HEAD, reviewer_id=review["user"]["id"])

    assert result["inventory_complete"] is False


@pytest.mark.parametrize(("label", "count", "classification"), [
    ("1 open finding", 1, "inline"),
    ("2 open findings", 2, "inline"),
    ("0 open findings", 0, "inline"),
    ("1 resolved finding", 1, "resolved"),
    ("2 resolved findings", 2, "resolved"),
    ("0 resolved since last review", 0, "resolved"),
    ("1 resolved since last review", 1, "resolved"),
    ("2 resolved since last review", 2, "resolved"),
])
def test_count_leading_sections_require_matching_linked_items(label, count, classification):
    items = "".join(
        f'<li><a href="#discussion_r{index + 1}">Finding {index + 1}</a></li>'
        for index in range(count)
    )
    listing = f"<ul>{items}</ul>" if count else ""
    rendered = (
        f"<details><summary><strong>{label}</strong></summary>{listing}</details>"
    )
    result = parse_body(OVERVIEW_MARKER + "\nCounted section", "COMMENTED",
                        body_html=rendered)

    assert result["inventory_complete"] is True
    assert result["ambiguous"] is False
    assert classification in result["classifications"]


@pytest.mark.parametrize("item", [
    "<a>Finding without a target</a>",
    '<a href="#discussion_r42" href="https://example.invalid/">Duplicate targets</a>',
    '<a href="/issues/99">Unrelated relative link</a>',
    '<a href="https://example.invalid/#discussion_r42">External discussion-like link</a>',
    "Not linked",
])
def test_count_leading_sections_reject_unsupported_discussion_targets(item):
    rendered = (
        '<details><summary><strong>1 open finding</strong></summary>'
        f"<ul><li>{item}</li></ul></details>"
    )
    result = parse_body(OVERVIEW_MARKER + "\nCounted section", "COMMENTED",
                        body_html=rendered)

    assert result["inventory_complete"] is False
    assert result["ambiguous"] is True


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("targets", [
    (("#discussion_r1",), ("#discussion_r1",)),
    (("#discussion_r1",), ("https://github.com/lindayi/hermes-mobile/pull/103#discussion_r1",)),
    (("#discussion_r1", "#discussion_r2"),),
])
def test_counted_inventory_rejects_duplicate_or_multiple_discussion_targets(category, targets):
    count = len(targets)
    label = f"{count} {category} finding" + ("s" if count != 1 else "")
    items = "".join(
        "<li>" + "".join(f'<a href="{target}">Finding</a>' for target in item) + "</li>"
        for item in targets
    )
    rendered = f"<details><summary>{label}</summary><ul>{items}</ul></details>"
    result = parse_body(OVERVIEW_MARKER + "\nCounted section", "COMMENTED",
                        body_html=rendered)

    assert result["inventory_complete"] is False
    assert result["ambiguous"] is True
    assert result["findings"] == []


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("resolved_label", ["1 resolved finding", "1 resolved since last review"])
@pytest.mark.parametrize("resolved_target", [
    "#discussion_r1",
    "https://github.com/lindayi/hermes-mobile/pull/103#discussion_r1",
    "#DISCUSSION_R1",
    "#discussion_r2",
])
def test_counted_inventory_requires_disjoint_cross_section_targets(reverse, resolved_label, resolved_target):
    sections = [
        '<details><summary>1 open finding</summary><ul><li>'
        '<a href="#discussion_r1">Open finding</a></li></ul></details>',
        f'<details><summary>{resolved_label}</summary><ul><li>'
        f'<a href="{resolved_target}">Resolved finding</a></li></ul></details>',
    ]
    if reverse:
        sections.reverse()
    rendered = "".join(sections)
    review = review_pair(OVERVIEW_MARKER + "\nSynthetic counted inventory", rendered,
                         state="COMMENTED")
    result = review_body_disposition([review], HEAD, reviewer_id=review["user"]["id"])
    distinct = resolved_target == "#discussion_r2"
    assert result["inventory_complete"] is distinct
    assert result["ambiguous"] is not distinct
    assert result["findings"] == []
    # Inventory uncertainty must not erase independently bounded active evidence.
    with_active = parse_body(review["body"], "COMMENTED", body_html=HTML_ESCAPED + rendered)
    assert with_active["inventory_complete"] is distinct
    assert with_active["findings_total"] == 1
    assert "decode <details> only after validation." in with_active["findings"][0][1]


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("extra_anchor", [
    '<a href="https://example.invalid/">Unsupported link</a>',
    '<a>Missing target</a>',
    '<a href="#discussion_r1">Repeated target</a>',
])
def test_counted_inventory_rejects_extra_live_anchors(category, extra_anchor):
    rendered = (
        f"<details><summary>1 {category} finding</summary><ul><li>"
        '<a href="#discussion_r1">Finding</a>' + extra_anchor
        + "</li></ul></details>"
    )
    result = parse_body(OVERVIEW_MARKER + "\nCounted section", "COMMENTED",
                        body_html=rendered)

    assert result["inventory_complete"] is False
    assert result["ambiguous"] is True
    assert result["findings"] == []


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("quote_tag", ["code", "pre", "blockquote"])
def test_counted_inventory_keeps_quoted_anchors_inert_and_decorations(category, quote_tag):
    rendered = (
        f"<details><summary>1 {category} finding</summary><ul><li>"
        '<themed-picture><picture><source srcset="severity.svg">'
        '<img src="severity.png" alt="Medium severity"></picture></themed-picture>'
        '<a href="#discussion_r1"><strong>Finding</strong></a>'
        f'<{quote_tag}><a href="#discussion_r1">Quoted duplicate</a>'
        '<a href="https://example.invalid/">Quoted unsupported link</a>'
        f'<a>Quoted missing target</a></{quote_tag}>'
        "</li></ul></details>"
    )
    result = parse_body(OVERVIEW_MARKER + "\nCounted section", "COMMENTED",
                        body_html=rendered)

    assert result["inventory_complete"] is True
    assert result["ambiguous"] is False
    assert result["findings"] == []


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("location", ["item", "anchor", "summary"])
def test_counted_inventory_rejects_nested_live_disclosure(category, location):
    nested = ('<details><summary>Previously missed (1)</summary>'
              '<p>Reject stale credentials before writing.</p></details>')
    label = f'1 {category} finding'
    anchor = '<a href="#discussion_r1">Finding</a>'
    if location == "anchor":
        anchor = anchor.replace('</a>', nested + '</a>')
    item = anchor + (nested if location == "item" else '')
    summary = label + (nested if location == "summary" else '')
    rendered = f'<details><summary>{summary}</summary><ul><li>{item}</li></ul></details>'
    result = parse_body(OVERVIEW_MARKER + '\nCounted section', 'COMMENTED', body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("nested", [
    '<ul><li>Unclassified nested finding</li></ul>',
    '<ol><li>Unclassified nested finding</li></ol>',
    '<li>Unclassified nested finding</li>',
])
def test_counted_inventory_rejects_nested_list_items(category, nested):
    rendered = (
        f'<details><summary>1 {category} finding</summary><ul><li>'
        '<a href="#discussion_r1">Finding</a>' + nested + '</li></ul></details>'
    )
    result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED',
                        body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings'] == []


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("quote_tag", ["code", "pre", "blockquote"])
def test_counted_inventory_keeps_nested_quoted_lists_inert(category, quote_tag):
    rendered = (
        f'<details><summary>1 {category} finding</summary><ul><li>'
        '<a href="#discussion_r1">Finding</a>'
        f'<{quote_tag}><ul><li>Quoted example</li></ul></{quote_tag}>'
        '</li></ul></details>'
    )
    result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED',
                        body_html=rendered)
    assert result['inventory_complete'] is True
    assert result['ambiguous'] is False


@pytest.mark.parametrize("category", ["open", "resolved"])
@pytest.mark.parametrize("legacy_first", [False, True])
def test_counted_inventory_same_kind_sections_remain_incomplete(category, legacy_first):
    counted = (f'<details><summary>1 {category} finding</summary><ul><li>'
               '<a href="#discussion_r1">Finding</a></li></ul></details>')
    legacy_label = 'Open (1)' if category == 'open' else 'Resolved (1)'
    legacy = f'<details><summary>{legacy_label}</summary>Known legacy context</details>'
    for rendered in (counted + counted.replace('discussion_r1', 'discussion_r2'),
                     legacy + counted if legacy_first else counted + legacy):
        result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED',
                            body_html=rendered)
        assert result['inventory_complete'] is False
        assert result['ambiguous'] is True


@pytest.mark.parametrize("other_label", ['1 resolved finding', 'Resolved (1)', '1 resolved since last review'])
@pytest.mark.parametrize("reverse", [False, True])
def test_resolved_since_alias_rejects_duplicate_category(other_label, reverse):
    first = ('<details><summary>1 resolved since last review</summary><ul><li>'
             '<a href="#discussion_r1">Finding</a></li></ul></details>')
    second = (f'<details><summary>{other_label}</summary><ul><li>'
              '<a href="#discussion_r2">Other finding</a></li></ul></details>')
    result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED',
                        body_html=second + first if reverse else first + second)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True


@pytest.mark.parametrize("content", [
    '', '<p>Unclassified prose</p>',
    '<ul><li>Unlinked finding</li></ul>',
    '<ul><li><a href="#discussion_r1">Finding</a><a>Extra anchor</a></li></ul>',
    '<ul><li><a href="#discussion_r1">Finding</a><ul><li>Hidden item</li></ul></li></ul>',
    '<ul><li><a href="#discussion_r1">Finding</a>'
    '<details><summary>Unknown finding</summary>Hidden prose</details></li></ul>',
    '<ul><li><a href="#discussion_r1">One</a></li><li><a href="#discussion_r2">Two</a></li></ul>',
])
def test_resolved_since_alias_requires_complete_linked_inventory(content):
    rendered = f'<details><summary>1 resolved since last review</summary>{content}</details>'
    result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED', body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings'] == []


@pytest.mark.parametrize("label", ['1 resolved since last review extra', '1 resolved finding extra'])
def test_unsupported_counted_labels_remain_ambiguous_without_hiding_active(label):
    rendered = (f'<details><summary>{label}</summary><ul><li>'
                '<a href="#discussion_r1">Finding</a></li></ul></details>')
    result = parse_body(OVERVIEW_MARKER + '\nCounted inventory', 'COMMENTED',
                        body_html=rendered + HTML_ESCAPED)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings_total'] == 1
    assert 'decode <details> only after validation.' in result['findings'][0][1]


@pytest.mark.parametrize("extra", [
    '', '<details><summary>0 resolved since last review</summary></details>',
    '<details><summary>1 resolved since last review</summary><ul><li>'
    '<themed-picture><picture><source srcset="severity.svg"><img src="severity.png"></picture>'
    '</themed-picture><a href="#discussion_r1">Resolved finding</a></li></ul></details>',
])
def test_standalone_zero_open_metadata_accepts_clean_provider_frame(extra):
    rendered = ('<h3>🟢 Looks good</h3><p>No issues were found.</p>'
                '<p><strong>0 open findings</strong></p>' + extra
                + '<p>🧠 <strong>Review effort:</strong> Balanced</p>')
    result = parse_body(OVERVIEW_MARKER + '\nSynthetic clean provider frame', 'COMMENTED',
                        body_html=rendered)
    assert result['inventory_complete'] is True
    assert result['ambiguous'] is False
    assert result['findings'] == []


@pytest.mark.parametrize("extra", [
    '<p><strong>0 open findings</strong></p>',
    '<details><summary>0 open findings</summary></details>',
    '<details><summary>1 open finding</summary><ul><li>'
    '<a href="#discussion_r1">Finding</a></li></ul></details>',
    '<details><summary>Open (0)</summary></details>',
    '<details><summary>Open (1)</summary>Legacy finding</details>',
    '<p><strong>1 open finding</strong></p>',
])
@pytest.mark.parametrize("reverse", [False, True])
def test_standalone_zero_open_metadata_rejects_conflicting_or_duplicate_open(extra, reverse):
    zero = '<p><strong>0 open findings</strong></p>'
    rendered = '<h3>🟢 Looks good</h3><p>No issues were found.</p>' + (
        extra + zero if reverse else zero + extra)
    result = parse_body(OVERVIEW_MARKER + '\nSynthetic provider frame', 'COMMENTED',
                        body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings'] == []


@pytest.mark.parametrize("metadata", [
    '<p><strong>1 open finding</strong></p>',
    '<p><strong>0 open findings</strong> Unclassified prose.</p>',
    '<p>Unclassified prose. <strong>0 open findings</strong></p>',
    '<p><strong>0 open findings Unclassified prose.</strong></p>',
    '<p><strong><a href="#discussion_r1">0 open findings</a></strong></p>',
])
def test_standalone_count_metadata_never_hides_unclassified_content(metadata):
    rendered = '<h3>🟢 Looks good</h3><p>No issues were found.</p>' + metadata
    result = parse_body(OVERVIEW_MARKER + '\nSynthetic provider frame', 'COMMENTED',
                        body_html=rendered + HTML_ESCAPED)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings_total'] == 1


def test_standalone_zero_open_metadata_does_not_discard_freeform_summary():
    rendered = ('<h3>🟢 Looks good</h3><p>The change deserves another look.</p>'
                '<p><strong>0 open findings</strong></p>')
    result = parse_body(OVERVIEW_MARKER + '\nSynthetic provider frame', 'COMMENTED',
                        body_html=rendered)
    assert result['inventory_complete'] is False
    assert result['ambiguous'] is True
    assert result['findings'] == []


def test_count_leading_section_inside_quotation_is_incomplete():
    rendered = CURRENT_COPILOT_HTML.replace(
        "<details open><summary><strong>1 open finding</strong></summary>",
        "<blockquote><details open><summary><strong>1 open finding</strong></summary>",
    ).replace(
        "</ul></details><details><summary><strong>Previously missed",
        "</ul></details></blockquote><details><summary><strong>Previously missed",
    )
    result = parse_body(CURRENT_COPILOT_RAW, "COMMENTED", body_html=rendered)

    assert result["inventory_complete"] is False
    assert result["ambiguous"] is True
    assert len(result["findings"]) == 1


def test_current_review_resumes_completed_repair_without_replaying_or_losing_thread(
        tmp_path):
    api = ProgressApi(unresolved=True)
    api.comments[0]["body"] = f"/hermes enroll {HEAD}"
    incomplete = HTML_ESCAPED.replace("Previously missed (1)", "Previously missed (2)")
    api.progress_review = review_pair(RAW_ESCAPED, incomplete, state="COMMENTED")
    path = tmp_path / "state.json"

    first = _managed_cycle(api, path)["pull_requests"][0]
    assert first["repair_requested"]
    store = StateStore(path)
    source_action = next(
        item for item in store.actions().values()
        if item["kind"] == "fix" and item["attempt"] == 1
    )
    assert source_action["repair_fingerprints_complete"] is False

    result_head = "c" * 40
    api.head_sha = result_head
    api.pull["head"]["sha"] = result_head
    api.complete_task(source_action["task_id"], source_action, head_sha=result_head)
    refresh_owner_review(api, result_head, submitted_at="2026-10-01T12:06:00Z")
    api.progress_review = review_pair(
        CURRENT_COPILOT_RAW.replace("/pull/99#", "/pull/16#").replace("4250499422", "445"),
        CURRENT_COPILOT_HTML.replace("/pull/99#", "/pull/16#").replace("4250499422", "445"),
        state="COMMENTED",
    ) | {
        "id": api.progress_review["id"] + 1,
        "commit_id": result_head,
        "submitted_at": "2026-10-01T12:10:00Z",
    }
    original_graphql = api.graphql

    def authenticated_threads(query, variables):
        response = original_graphql(query, variables)
        if "reviewThreads" in query:
            [thread] = response["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            thread["comments"]["nodes"][0].update({
                "author": {"databaseId": COPILOT_REVIEWER},
                "createdAt": "2026-10-01T12:09:00Z",
                "pullRequestReview": {
                    "databaseId": api.progress_review["id"],
                    "commit": {"oid": result_head},
                },
            })
        return response

    api.graphql = authenticated_threads

    second = _managed_cycle(api, path)["pull_requests"][0]
    saved = StateStore(path).snapshot()
    enrollment = saved["enrollments"]["16"]
    progress = enrollment["repair_progress"]
    assert progress["legacy_unknown"] is True
    assert progress["evaluated_task_ids"] == [source_action["task_id"]]
    assert progress["resolved_fingerprints"] == []
    assert progress["consecutive_no_progress"] == 0
    assert enrollment["attempts"] == 2
    proof = next(
        item for item in enrollment["receipt_proofs"]
        if item["task_id"] == source_action["task_id"]
    )
    assert proof["status"] == "completed" and proof["receipt_result"] == "ready"
    assert proof["attempt"] == 1 and proof["repair_fingerprints_complete"] is False
    assert second["repair_requested"]

    fix_posts = [
        body for route, body in api.writes if route.endswith("/tasks")
    ]
    assert len(fix_posts) == 2
    assert "Please resolve this finding." in fix_posts[1]["prompt"]
    assert "Operator commands omit deployed source, virtualenv, and live config" in (
        fix_posts[1]["prompt"]
    )

    _managed_cycle(api, path)
    assert api.fix_attempts == 2
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 2
    active_fixes = [
        item for item in StateStore(path).actions().values()
        if item["kind"] == "fix" and item.get("status") == "sent"
    ]
    assert len(active_fixes) == 1 and active_fixes[0]["attempt"] == 2
    targets = active_fixes[0]["repair_target_map"].values()
    assert any(target == {"kind": "thread", "target": "prrt kw1"} for target in targets)
    assert any(
        target["kind"] == "finding"
        and "operator commands omit deployed source" in target["target"]
        for target in targets
    )


@pytest.mark.parametrize("rendered", [
    "<details><summary>Previously missed (2)</summary>"
    "<details><summary>One target</summary><p>Fix it.</p></details></details>",
    "<details><summary>Unknown inventory</summary><p>Uncertain prose.</p></details>",
    "<details",
])
def test_malformed_or_ambiguous_inventory_is_never_complete(rendered):
    result = parse_body(OVERVIEW_MARKER + "\nSynthetic inventory", "CHANGES_REQUESTED",
                        body_html=rendered)
    assert result["inventory_complete"] is False
    assert result["truncated"] is False
    assert result["findings_total"] == len(result["findings"])


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


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("prefix, marked", [
    (OVERVIEW_MARKER + "\n", True), (OVERVIEW_MARKER + "\r\n", True),
    ("", False), ("prose\n" + OVERVIEW_MARKER + "\n", False),
    (" " + OVERVIEW_MARKER + "\n", False), ("\n" + OVERVIEW_MARKER + "\n", False),
    (OVERVIEW_MARKER + " trailing\n", False), ("`" + OVERVIEW_MARKER + "`\n", False),
])
def test_review5392668833_overview_sections_require_raw_marker(tmp_path, state, prefix, marked):
    section = (
        "<details><summary>Previously missed (1)</summary>"
        "<details><summary>Synthetic receipt defect</summary>"
        "<p>A transient receipt consumes the bounded budget.</p></details></details>")
    # No explicit correction clause: only a canonical overview can confer scope.
    review = review_pair(prefix + section, section, state=state)
    result = parse_body(review["body"], state, body_html=section)
    assert bool(result["findings"]) is marked
    if not marked:
        assert "active" not in result["classifications"]
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    assert (request is not None) is marked
    if marked:
        [finding] = _evidence(request)["review_findings"]
        assert finding["kind"] == "previously-missed"
        assert "A transient receipt consumes the bounded budget." in finding["comment"]
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert plan["repair_requested"] is marked
    assert not plan["review_valid"] and not copilot_review_valid(HEAD, [review], [])
    assert api.fix_attempts == int(marked) and not api.graphql_writes
    saved = StateStore(path).snapshot()
    assert saved["enrollments"]["16"]["attempts"] == int(marked)
    assert sum(action["kind"] == "fix" for action in saved["actions"].values()) == int(marked)


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
def test_review5392668833_bare_correction_is_independent_of_overview_sections(tmp_path, state):
    correction = "Required correction: reject pending receipts."
    rendered = (f"<p>{correction}</p>"
                "<details><summary>Open (1)</summary>NEVER FORWARD inline context.</details>"
                "<details><summary>Unknown scope</summary>NEVER FORWARD unknown context.</details>")
    review = review_pair(correction + "\n" + rendered, rendered, state=state)
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    expected = state == "CHANGES_REQUESTED"
    assert (request is not None) is expected
    if expected:
        [finding] = _evidence(request)["review_findings"]
        assert finding["kind"] == "changes-requested" and finding["comment"] == correction
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert plan["repair_requested"] is expected
    assert not plan["review_valid"] and not api.graphql_writes
    assert api.fix_attempts == int(expected)
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == int(expected)


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
    api.pending_required = True
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"]
    assert plan["review_valid"] is (count == "0" and shape in {"empty", "intro-only"})
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


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("count", ["1", "unknown"])
@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_review5392668833_quoted_only_items_never_dispatch(tmp_path, state, count, tag):
    section = (
        f"<details><summary>Previously missed ({count})</summary>"
        "<p>In code that hasn't changed since last review</p>"
        "<details><summary><code>NEVER FORWARD quoted defect</code></summary>"
        f"<{tag}>Required correction: NEVER FORWARD this quoted example.</{tag}>"
        "<details><summary>History (1)</summary>NEVER FORWARD historical prose.</details>"
        "</details></details>")
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state=state)
    result = parse_body(review["body"], state, body_html=section)
    assert result["findings"] == []
    assert "no-findings" in result["classifications"]
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    assert not copilot_review_valid(HEAD, [review], [])
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    saved = StateStore(path).snapshot()
    assert saved["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in saved["actions"].values())
    # Reject this local item without hiding an independently genuine section.
    request = repair_request(HEAD, 0, [], [], reviews=[review | {
        "body_html": HTML_ESCAPED + section}])
    [finding] = _evidence(request)["review_findings"]
    assert "decode <details> only after validation." in finding["comment"]
    assert "NEVER FORWARD" not in finding["comment"]


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("tag", ["code", "pre", "blockquote"])
def test_review5392668833_matched_items_keep_only_live_finding_with_literal_context(tmp_path, state, tag):
    section = (
        "<details><summary>Previously missed (2)</summary>"
        "<details><summary><code>NEVER FORWARD quoted defect</code></summary>"
        f"<{tag}>Required correction: NEVER FORWARD this quoted example.</{tag}></details>"
        "<details><summary>Live receipt defect</summary>"
        "<p>A transient receipt consumes the bounded budget.</p>"
        f"<{tag}>KEEP literal &lt;details&gt; and validation is pending.</{tag}>"
        "<details><summary>Resolved (1)</summary>NEVER FORWARD resolved prose.</details>"
        "</details></details>")
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state=state)
    request = repair_request(HEAD, 0, [], [], reviews=[review])
    [finding] = _evidence(request)["review_findings"]
    assert finding["kind"] == "previously-missed"
    assert finding["review"] == review["id"] and finding["head"] == HEAD
    assert finding["submitted_at"] == review["submitted_at"] and "thread" not in finding
    assert "Live receipt defect" in finding["comment"]
    assert "A transient receipt consumes the bounded budget." in finding["comment"]
    assert "KEEP literal <details> and validation is pending." in finding["comment"]
    assert "NEVER FORWARD" not in request["body"]
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 1 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 1


@pytest.mark.parametrize("count", ["0", "1", "unknown"])
@pytest.mark.parametrize("separator", ["\n", "<br>"])
def test_review5392848133_wrapped_intro_never_dispatches(tmp_path, count, separator):
    intro = f"<p>In code that hasn't{separator} changed since last review</p>"
    section = f"<details><summary>Previously missed ({count})</summary>{intro}</details>"
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state="COMMENTED")
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    api = ReviewApi([review])
    api.pending_required = True
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"]
    assert plan["review_valid"] is (count == "0")
    assert api.fix_attempts == 0 and not api.graphql_writes
    saved = StateStore(path).snapshot()
    assert saved["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in saved["actions"].values())


@pytest.mark.parametrize("separator", ["\n", "<br>"])
def test_review5392848133_wrapped_intro_preserves_matched_siblings(separator):
    section = (
        "<details><summary>Previously missed (2)</summary>"
        f"<p>In code that hasn't{separator} changed since last review</p>"
        "<details><summary><code>NEVER FORWARD quoted title</code></summary>"
        "<blockquote>NEVER FORWARD quoted correction.</blockquote></details>"
        "<details><summary>Live receipt defect</summary>"
        "<p>A transient receipt consumes the bounded budget.</p>"
        "<code>KEEP &lt;details&gt;</code></details></details>")
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state="COMMENTED")
    [finding] = _evidence(repair_request(HEAD, 0, [], [], reviews=[review]))["review_findings"]
    assert "Live receipt defect" in finding["comment"]
    assert "KEEP <details>" in finding["comment"]
    assert "NEVER FORWARD" not in finding["comment"]
    assert "Previously missed" not in finding["comment"]


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("shape", ["matched", "fallback-zero", "fallback-unknown"])
@pytest.mark.parametrize("prose", [
    "No bugs found.",
    "Exact-head verification remains pending.",
    "No code defects were found in the reviewed changes. "
    "The exact-head verification is still pending.",
])
def test_review5392848133_status_only_live_content_never_dispatches(tmp_path, state, shape, prose):
    intro = "<p>In code that hasn't<br> changed since last review</p>"
    content = f"<p>{prose}</p>"
    if shape == "matched":
        content = ("<details><summary><code>NEVER FORWARD quoted title</code></summary>"
                   + content + "</details>")
    count = {"matched": "1", "fallback-zero": "0", "fallback-unknown": "unknown"}[shape]
    section = f"<details><summary>Previously missed ({count})</summary>{intro}{content}</details>"
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state=state)
    result = parse_body(review["body"], state, body_html=section)
    assert result["findings"] == []
    assert "no-findings" in result["classifications"]
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    assert not copilot_review_valid(HEAD, [review], [])
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    saved = StateStore(path).snapshot()
    assert saved["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in saved["actions"].values())
    # A status-only section cannot hide an independent genuine section.
    request = repair_request(HEAD, 0, [], [], reviews=[review | {"body_html": section + HTML_ESCAPED}])
    [finding] = _evidence(request)["review_findings"]
    assert "decode <details> only after validation." in finding["comment"]
    assert "NEVER FORWARD" not in finding["comment"]
    if shape == "matched":
        genuine = ("<details><summary>Live receipt defect</summary>"
                   "<p>A transient receipt consumes the bounded budget.</p>"
                   "<blockquote>KEEP &lt;details&gt; and No bugs found.</blockquote></details>")
        siblings = section.replace("Previously missed (1)", "Previously missed (2)")
        siblings = siblings[:-len("</details>")] + genuine + "</details>"
        request = repair_request(HEAD, 0, [], [], reviews=[review | {"body_html": siblings}])
        [finding] = _evidence(request)["review_findings"]
        assert "Live receipt defect" in finding["comment"]
        assert "KEEP <details> and No bugs found." in finding["comment"]
        assert "NEVER FORWARD" not in finding["comment"]
    # Complete neutral prose must not suppress a correction in the same item/section.
    mixed = section.replace(f"<p>{prose}</p>", f"<p>{prose} Required correction: preserve "
                            "<code>&lt;summary&gt;</code> in literal context.</p>")
    request = repair_request(HEAD, 0, [], [], reviews=[review | {"body_html": mixed}])
    [finding] = _evidence(request)["review_findings"]
    assert prose in finding["comment"]
    assert "Required correction: preserve <summary> in literal context." in finding["comment"]


@pytest.mark.parametrize("state", ["COMMENTED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("shape", ["matched", "fallback-zero", "fallback-unknown"])
@pytest.mark.parametrize("prose", [
    "No bugs\nfound.",
    "Exact-head verification remains\npending.",
])
def test_review5393153136_soft_text_newlines_never_dispatch(tmp_path, state, shape, prose):
    content = f"<p>{prose}</p>"
    if shape == "matched":
        content = "<details><summary><code>Example</code></summary>" + content + "</details>"
    count = {"matched": "1", "fallback-zero": "0", "fallback-unknown": "unknown"}[shape]
    section = (f"<details><summary>Previously missed ({count})</summary>"
               "<p>In code that hasn't changed since last review</p>" + content + "</details>")
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state=state)
    result = parse_body(review["body"], state, body_html=section)
    assert result["findings"] == []
    assert "no-findings" in result["classifications"]
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    assert not copilot_review_valid(HEAD, [review], [])
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    saved = StateStore(path).snapshot()
    assert saved["enrollments"]["16"]["attempts"] == 0
    assert not any(action["kind"] == "fix" for action in saved["actions"].values())
    # Independent genuine evidence must survive without an extra status finding.
    [finding] = _evidence(repair_request(HEAD, 0, [], [], reviews=[
        review | {"body_html": section + HTML_ESCAPED}]))["review_findings"]
    assert "decode <details> only after validation." in finding["comment"]
    if shape == "matched":
        sibling = ("<details><summary>Live receipt defect</summary>"
                   "<p>A transient receipt consumes the bounded budget.</p></details>")
        siblings = section.replace("Previously missed (1)", "Previously missed (2)")
        siblings = siblings[:-len("</details>")] + sibling + "</details>"
        [finding] = _evidence(repair_request(HEAD, 0, [], [], reviews=[
            review | {"body_html": siblings}]))["review_findings"]
        assert "Live receipt defect" in finding["comment"]
        assert "Example" not in finding["comment"]
    # Classification normalization must not alter plain or quoted finding context.
    literal = "KEEP\n  &lt;details&gt;\tand " + prose
    mixed = section.replace(f"<p>{prose}</p>", f"<p>{prose} Required correction: retain context.</p>"
                            + "".join(f"<{tag}>{literal}</{tag}>"
                                      for tag in ("code", "pre", "blockquote")))
    [(_, text)] = parse_body(review["body"], state, body_html=mixed)["findings"]
    assert prose + " Required correction: retain context." in text
    assert text.count("KEEP\n  <details>\tand " + prose) == 3
    [finding] = _evidence(repair_request(HEAD, 0, [], [], reviews=[
        review | {"body_html": mixed}]))["review_findings"]
    assert "Required correction: retain context." in finding["comment"]


@pytest.mark.parametrize("shape", ["matched", "fallback"])
def test_review5392848133_status_blocks_keep_existing_sentence_boundaries(tmp_path, shape):
    content = ("<p>No bugs found</p>"
               "<p>Exact-head verification remains pending</p>")
    if shape == "matched":
        content = "<details><summary><code>Example</code></summary>" + content + "</details>"
    count = "1" if shape == "matched" else "unknown"
    section = (f"<details><summary>Previously missed ({count})</summary>"
               "<p>In code that hasn't\n changed since last review</p>" + content + "</details>")
    review = review_pair(OVERVIEW_MARKER + "\n" + section, section, state="COMMENTED")
    assert repair_request(HEAD, 0, [], [], reviews=[review]) is None
    api = ReviewApi([review])
    path = tmp_path / "state.json"
    plan = _managed_cycle(api, path)["pull_requests"][0]
    assert not plan["repair_requested"] and not plan["review_valid"]
    assert api.fix_attempts == 0 and not api.graphql_writes
    assert StateStore(path).snapshot()["enrollments"]["16"]["attempts"] == 0
    mixed = section.replace("<p>No bugs found</p>", "<p>No bugs found</p>"
                            "<p>Required correction: retain <code>&lt;details&gt;</code>.</p>")
    [finding] = _evidence(repair_request(HEAD, 0, [], [], reviews=[
        review | {"body_html": mixed}]))["review_findings"]
    assert "Required correction: retain <details>." in finding["comment"]


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
