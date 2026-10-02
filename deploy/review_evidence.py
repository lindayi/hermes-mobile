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
    r"<summary>\s*<strong>\s*Previously missed\s*\((\d{1,3})\)\s*</strong>\s*</summary>",
    re.IGNORECASE,
)
_OPEN_RE = re.compile(
    r"<summary>\s*<strong>\s*Open\s*\(\d{1,3}\)\s*</strong>\s*</summary>", re.IGNORECASE,
)
_DETAILS_OPEN_RE = re.compile(r"<details\b[^>]*>", re.IGNORECASE)
_DETAILS_CLOSE_RE = re.compile(r"</details>", re.IGNORECASE)
_ITEM_RE = re.compile(
    r"<details\b[^>]*>\s*<summary>(.*?)</summary>(.*?)</details>",
    re.IGNORECASE | re.DOTALL,
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


def _previously_missed(body):
    """Return item texts from every Previously missed section.

    A section whose structure or count cannot be proven is forwarded whole as
    one item rather than silently dropped or split into invented findings.
    """
    items = []
    for match in _MISSED_RE.finditer(body):
        expected = int(match.group(1))
        if expected == 0:
            continue
        position, parsed, malformed = match.end(), [], False
        while True:
            opening = _DETAILS_OPEN_RE.search(body, position)
            closing = _DETAILS_CLOSE_RE.search(body, position)
            closing = closing.start() if closing else -1
            if closing < 0:
                malformed, end = True, len(body)
                break
            if opening is None or opening.start() > closing:
                end = closing
                break
            item = _ITEM_RE.match(body, opening.start())
            if not item:
                malformed, end = True, closing
                break
            parsed.append(f"{_strip_markup(item.group(1)).strip()}\n"
                          f"{_strip_markup(item.group(2)).strip()}")
            position = item.end()
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
            or state not in {"COMMENTED", "CHANGES_REQUESTED"}
            or not isinstance(body, str) or not body.strip()):
        return []
    body = body[:MAX_BODY_CHARS]
    found = []
    if OVERVIEW_MARKER in body:
        found = [("previously-missed", text) for text in _previously_missed(body)]
        if not found and state == "CHANGES_REQUESTED" and not _OPEN_RE.search(body):
            found = [("changes-requested", _overview_summary(body))]
    elif state == "CHANGES_REQUESTED":
        found = [("changes-requested", _strip_markup(body).strip())]
    return [
        {"review": review["id"], "head": head_sha,
         "submitted_at": review["submitted_at"], "kind": kind, "text": text}
        for kind, text in found if text.strip()
    ][:MAX_BODY_FINDINGS]
