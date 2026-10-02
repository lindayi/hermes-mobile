"""Strict review-record selection and conservative body-only review findings.

Review bodies are untrusted data. Nothing here infers approval from prose or
returns executable instructions; callers must still bound and label the text.
"""

from __future__ import annotations

from datetime import datetime
import re


OVERVIEW_MARKER = "<!-- ccr-overview-v2 -->"
MAX_BODY_CHARS = 64 * 1024
MAX_BODY_FINDINGS = 8
FINDING_KINDS = frozenset({"previously-missed", "changes-requested"})
_MISSED_RE = re.compile(
    r"<summary\b[^>]*>\s*<strong>\s*Previously missed\b([^<]*)</strong>\s*</summary\s*>",
    re.IGNORECASE,
)
_OPEN_RE = re.compile(
    r"<summary>\s*<strong>\s*Open\s*\((\d{1,3})\)\s*</strong>\s*</summary>", re.IGNORECASE,
)
_DETAILS_TOKEN_RE = re.compile(r"<details\b[^>]*>|</details\s*>", re.IGNORECASE)
_SUMMARY_RE = re.compile(
    r"^\s*<summary\b[^>]*>(.*?)</summary\s*>", re.IGNORECASE | re.DOTALL,
)


def positive_id(value):
    return type(value) is int and value > 0


def review_timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def latest_reviews(reviews, reviewer_id):
    """Return every reviewer record tied at the latest instant, or None.

    The complete collection is validated before author filtering: a malformed
    record, user, or ID anywhere cannot be skipped to reuse an earlier record.
    """
    if not positive_id(reviewer_id) or not isinstance(reviews, list):
        return None
    record_ids, authored = set(), []
    for review in reviews:
        if not isinstance(review, dict):
            return None
        user = review.get("user")
        if not isinstance(user, dict) or not positive_id(user.get("id")):
            return None
        if "id" in review:
            if not positive_id(review["id"]) or review["id"] in record_ids:
                return None
            record_ids.add(review["id"])
        if user["id"] == reviewer_id:
            authored.append(review)
    stamps = [review_timestamp(review.get("submitted_at")) for review in authored]
    # PENDING reviews have no submitted_at; they cannot be ordered.
    if not authored or any(stamp is None for stamp in stamps):
        return None
    latest = max(stamps)
    return [review for review, stamp in zip(authored, stamps) if stamp == latest]


def _strip_markup(text):
    return re.sub(r"<[^>]*>", " ", text)


def _matching_details(body, opening):
    token = _DETAILS_TOKEN_RE.match(body, opening)
    if not token or token.group(0).lower().startswith("</"):
        return None
    depth = 1
    for nested in _DETAILS_TOKEN_RE.finditer(body, token.end()):
        if nested.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return token.start(), nested.start(), nested.end()
        else:
            depth += 1
    return None


def _enclosing_details(body, position):
    stack = []
    for token in _DETAILS_TOKEN_RE.finditer(body, 0, position):
        if token.group(0).lower().startswith("</"):
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(token.start())
    return stack[-1] if stack else None


def _previously_missed(body):
    """Return item texts from every Previously missed section.

    A section whose structure or count cannot be proven is forwarded whole as
    one item rather than silently dropped or split into invented findings.
    """
    items = []
    for match in _MISSED_RE.finditer(body):
        count = re.fullmatch(r"\s*\((\d{1,3})\)\s*", match.group(1))
        expected = int(count.group(1)) if count else None
        if expected == 0:
            continue
        parent = _enclosing_details(body, match.start())
        section = _matching_details(body, parent) if parent is not None else None
        parsed, malformed = [], section is None or expected is None
        end = section[2] if section else len(body)
        section_end = section[1] if section else len(body)
        position = match.end()
        while not malformed and position < section_end:
            token = _DETAILS_TOKEN_RE.search(body, position, section_end)
            if token is None:
                break
            if token.group(0).lower().startswith("</"):
                malformed = True
                break
            item = _matching_details(body, token.start())
            if item is None or item[2] > section_end:
                malformed = True
                break
            item_content = body[token.end():item[1]]
            summary = _SUMMARY_RE.match(item_content)
            if summary is None:
                malformed = True
                break
            parsed.append(f"{_strip_markup(summary.group(1)).strip()}\n"
                          f"{_strip_markup(item_content[summary.end():]).strip()}")
            position = item[2]
        if malformed or len(parsed) != expected:
            items.append(_strip_markup(body[match.start():end]).strip())
        else:
            items.extend(parsed)
    return items


def _overview_summary(body):
    without_details = body
    # Remove innermost disclosure blocks until none remain.
    for _ in range(16):
        reduced = re.sub(
            r"<details\b[^>]*>(?:(?!<details\b).)*?</details>", " ",
            without_details, flags=re.IGNORECASE | re.DOTALL,
        )
        if reduced == without_details:
            break
        without_details = reduced
    summary = without_details.split("\n---", 1)[0].replace(OVERVIEW_MARKER, "")
    return _strip_markup(summary).strip()


def _non_actionable_overview(body):
    summary = _overview_summary(body)
    # A validation-status clause is evidence of waiting; a domain object such
    # as a "pending receipt" in a requested correction is not.
    if re.search(r"(?:^|,\s*while\s+)(?:exact-head\s+)?(?:verification|validation)\s+"
                 r"(?:remains|is)\s+pending[.!]?\s*$",
                 summary, re.IGNORECASE | re.MULTILINE):
        return True
    return (
        re.search(r"^###\s+(?:🟢\s+)?Looks good\s*$", summary,
                  re.IGNORECASE | re.MULTILINE) is not None
        and re.search(r"^\*\*Findings:\*\*\s*None\s*$", summary,
                      re.IGNORECASE | re.MULTILINE) is not None
    )


def body_findings(reviews, head_sha, *, reviewer_id):
    """Return actionable body-only findings from the latest exact-head review.

    Pending-validation, no-findings and resolved-only overviews, stale heads,
    foreign authors, tied or malformed records, and approvals yield nothing.
    """
    latest = latest_reviews(reviews, reviewer_id)
    if not latest or len(latest) != 1:
        return []
    review = latest[0]
    state, body = review.get("state"), review.get("body")
    if (not isinstance(head_sha, str) or review.get("commit_id") != head_sha
            or not positive_id(review.get("id"))
            or type(state) is not str or state not in {"COMMENTED", "CHANGES_REQUESTED"}
            or not isinstance(body, str) or not body.strip()):
        return []
    body = body[:MAX_BODY_CHARS]
    found = []
    if OVERVIEW_MARKER in body:
        found = [("previously-missed", text) for text in _previously_missed(body)]
        if (not found and state == "CHANGES_REQUESTED"
                and not any(int(match.group(1)) > 0 for match in _OPEN_RE.finditer(body))
                and not _non_actionable_overview(body)):
            found = [("changes-requested", _overview_summary(body))]
    elif state == "CHANGES_REQUESTED":
        found = [("changes-requested", _strip_markup(body).strip())]
    return [
        {"review": review["id"], "head": head_sha,
         "submitted_at": review["submitted_at"], "kind": kind, "text": text}
        for kind, text in found if text.strip()
    ][:MAX_BODY_FINDINGS]
