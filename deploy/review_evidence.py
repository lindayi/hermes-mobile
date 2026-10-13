"""Strict review selection and bounded structural review-body dispositions.

Body text is untrusted evidence, never approval or executable instructions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re

OVERVIEW_MARKER = "<!-- ccr-overview-v2 -->"
_COMMENTED_GATE_STATUS_SUMMARIES = frozenset({
    "The operationally sensitive coordinator gate lacks verified latest-head CI "
    "and required review evidence.",
    "The coordinator’s authorization and progress seam is security-sensitive, "
    "and exact-head owner authorization remains absent.",
})
MAX_BODY_CHARS = 64 * 1024
MAX_INDEPENDENT_REVIEW_CHARS = 4096
MAX_BODY_FINDINGS = 8
MAX_PARSE_EVENTS = 4096
MAX_PARSE_DEPTH = 32
MAX_DISCLOSURES = 256
FINDING_KINDS = frozenset({"previously-missed", "changes-requested"})
INDEPENDENT_REVIEW_SCHEMA = "hermes-independent-agent-review-v1"
COPILOT_REVIEWER_ID = 175728472
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def positive_id(value):
    return type(value) is int and value > 0


def review_timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-json-key")
        result[key] = value
    return result


def parse_independent_review_body(body, head_sha):
    """Parse the small, explicit owner-published independent-review assertion."""
    if (not isinstance(body, str) or not body or len(body) > MAX_INDEPENDENT_REVIEW_CHARS
            or not isinstance(head_sha, str) or _SHA_RE.fullmatch(head_sha) is None):
        return None
    try:
        report = json.loads(body, object_pairs_hook=_unique_object)
    except (RecursionError, ValueError):
        return None
    if (not isinstance(report, dict)
            or set(report) != {
                "schema", "reviewed_head_sha", "review_method", "verdict", "evidence_sha256",
            }
            or report.get("schema") != INDEPENDENT_REVIEW_SCHEMA
            or report.get("reviewed_head_sha") != head_sha
            or report.get("review_method") != "independent-agent"
            or report.get("verdict") != "pass"
            or not isinstance(report.get("evidence_sha256"), str)
            or _DIGEST_RE.fullmatch(report["evidence_sha256"]) is None):
        return None
    return report


def _review_submission(review):
    submitted = review_timestamp(review.get("submitted_at"))
    if submitted is None:
        return None
    updated = review_timestamp(review.get("updatedAt"))
    if updated is None or updated != submitted:
        return None
    if "lastEditedAt" not in review:
        return None
    edited = review.get("lastEditedAt")
    if edited not in (None, ""):
        if review_timestamp(edited) is None:
            return None
        return None
    if review.get("includesCreatedEdit") is not False:
        return None
    for key in ("updated_at", "updatedAt"):
        if key != "updatedAt" and key in review:
            updated = review_timestamp(review.get(key))
            if updated is None or updated != submitted:
                return None
    for key in ("last_edited_at", "lastEditedAt"):
        if key != "lastEditedAt" and key in review:
            edited = review.get(key)
            if edited in (None, ""):
                continue
            if review_timestamp(edited) is None:
                return None
            return None
    for key in ("includes_created_edit", "includesCreatedEdit"):
        if key != "includesCreatedEdit" and key in review and review.get(key) is not False:
            return None
    return submitted


def selected_independent_agent_review(reviews, head_sha, review_id, body_sha256, *, owner_id):
    """Return the exact latest owner-published positive COMMENT review, if valid."""
    if (not positive_id(owner_id) or not isinstance(head_sha, str)
            or _SHA_RE.fullmatch(head_sha) is None
            or not positive_id(review_id) or not isinstance(body_sha256, str)
            or _DIGEST_RE.fullmatch(body_sha256) is None):
        return None
    latest = latest_reviews(reviews, owner_id)
    if not latest or len(latest) != 1:
        return None
    review = latest[0]
    body = review.get("body")
    if (review.get("id") != review_id or review.get("state") != "COMMENTED"
            or review.get("commit_id") != head_sha or not isinstance(body, str)
            or len(body) > MAX_INDEPENDENT_REVIEW_CHARS
            or _review_submission(review) is None
            or review.get("dismissed") is True
            or review.get("dismissed_at") not in (None, "")):
        return None
    actual_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if actual_digest != body_sha256:
        return None
    report = parse_independent_review_body(body, head_sha)
    if report is None:
        return None
    return {
        "review_id": review_id,
        "reviewer_id": owner_id,
        "head_sha": head_sha,
        "state": "COMMENTED",
        "body_sha256": body_sha256,
        "evidence_sha256": report["evidence_sha256"],
    }


def current_independent_agent_review(
        reviews, head_sha, *, owner_id, expected=None, complete=True):
    """Select the latest authenticated positive independent-agent COMMENT review."""
    if complete is not True:
        return None
    latest = latest_reviews(reviews, owner_id)
    if not latest or len(latest) != 1:
        return None
    review = latest[0]
    if _review_submission(review) is None:
        return None
    body = review.get("body")
    if not isinstance(body, str):
        return None
    selected = selected_independent_agent_review(
        reviews, head_sha, review.get("id"),
        hashlib.sha256(body.encode("utf-8")).hexdigest(), owner_id=owner_id,
    )
    if selected is None or (expected is not None and selected != expected):
        return None
    return selected


def sensitive_review_authorized(
        reviews, head_sha, owner_authorization, targeted_review, *, owner_id,
        owner_command=None, pull_author_id=None, complete=True):
    """Bind explicit owner approval to one exact, current Copilot review."""
    if (not isinstance(owner_authorization, dict)
            or set(owner_authorization) != {
                "actor_id", "head_sha", "state", "review_id", "body_sha256",
                "command_comment_id", "command_body_sha256", "command_created_at",
            }
            or owner_authorization.get("actor_id") != owner_id
            or owner_authorization.get("head_sha") != head_sha
            or owner_authorization.get("state") != "approved"
            or not positive_id(owner_authorization.get("command_comment_id"))
            or not isinstance(owner_authorization.get("command_body_sha256"), str)
            or _DIGEST_RE.fullmatch(owner_authorization["command_body_sha256"]) is None
            or review_timestamp(owner_authorization.get("command_created_at")) is None
            or not isinstance(owner_command, dict)
            or set(owner_command) != {
                "comment_id", "actor_id", "body", "created_at", "updated_at",
            }
            or owner_command.get("comment_id") != owner_authorization.get("command_comment_id")
            or owner_command.get("actor_id") != owner_id
            or not isinstance(owner_command.get("body"), str)
            or owner_command.get("created_at") != owner_authorization.get("command_created_at")
            or owner_command.get("updated_at") != owner_command.get("created_at")
            or not isinstance(targeted_review, dict)
            or set(targeted_review) != {
                "review_id", "reviewer_id", "head_sha", "state", "body_sha256",
            }):
        return False
    expected_command = (
        f"/hermes authorize-sensitive {head_sha} review "
        f"{owner_authorization['review_id']} {owner_authorization['body_sha256']}"
    )
    command_body = owner_command["body"]
    if (command_body != expected_command
            or hashlib.sha256(command_body.encode("utf-8")).hexdigest()
            != owner_authorization["command_body_sha256"]):
        return False
    expected = selected_copilot_sensitive_review(
        reviews, head_sha, owner_authorization.get("review_id"),
        owner_authorization.get("body_sha256"), pull_author_id=pull_author_id,
        complete=complete,
    )
    if expected is None or targeted_review != expected:
        return False
    selected = latest_reviews(reviews, COPILOT_REVIEWER_ID)[0]
    return (review_timestamp(owner_authorization["command_created_at"])
            >= _review_submission(selected))


def selected_copilot_sensitive_review(
        reviews, head_sha, review_id, body_sha256, *, pull_author_id, complete=True):
    """Return the unique latest unedited full-media Copilot review bound to a head."""
    if (complete is not True or not positive_id(pull_author_id)
            or pull_author_id == COPILOT_REVIEWER_ID
            or not isinstance(head_sha, str) or _SHA_RE.fullmatch(head_sha) is None
            or not positive_id(review_id) or not isinstance(body_sha256, str)
            or _DIGEST_RE.fullmatch(body_sha256) is None):
        return None
    latest = latest_reviews(reviews, COPILOT_REVIEWER_ID)
    if not latest or len(latest) != 1:
        return None
    review = latest[0]
    body, body_html = review.get("body"), review.get("body_html")
    if (review.get("id") != review_id
            or review.get("state") not in ("COMMENTED", "APPROVED")
            or review.get("commit_id") != head_sha
            or review.get("dismissed") is True
            or review.get("dismissed_at") not in (None, "")
            or _review_submission(review) is None
            or not isinstance(body, str) or len(body) > MAX_BODY_CHARS
            or not isinstance(body_html, str) or len(body_html) > MAX_BODY_CHARS
            or hashlib.sha256(body.encode("utf-8")).hexdigest() != body_sha256):
        return None
    return {
        "review_id": review_id,
        "reviewer_id": COPILOT_REVIEWER_ID,
        "head_sha": head_sha,
        "state": review["state"],
        "body_sha256": body_sha256,
    }


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
        if not positive_id(review.get("id")) or review["id"] in record_ids:
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


def current_copilot_review_valid(reviews, head_sha, *, pull_author_id, complete=True):
    """Accept only genuine, submitted, current-head Copilot reviewer evidence."""
    if (complete is not True or not positive_id(pull_author_id)
            or pull_author_id == 175728472 or not isinstance(head_sha, str)
            or _SHA_RE.fullmatch(head_sha) is None):
        return False
    selected = latest_reviews(reviews, 175728472)
    return bool(selected) and all(
        review.get("state") in ("COMMENTED", "APPROVED")
        and review.get("commit_id") == head_sha
        and review.get("dismissed") is not True
        and review.get("dismissed_at") in (None, "")
        for review in selected
    )


@dataclass
class _Element:
    tag: str
    children: list = field(default_factory=list)
    href: str | None = None


class _DisclosureParser(HTMLParser):
    """One bounded parse; strict nesting rather than browser error recovery."""

    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = _Element("root")
        self.stack = [self.root]
        self.events = self.disclosures = 0

    def feed_review(self, body):
        self.source = body
        self.line_offsets = [0] + [match.end() for match in re.finditer("\n", body)]
        self.feed(body)
        if self.rawdata:
            raise ValueError("unfinished-markup")
        self.close()
        if self.rawdata:
            raise ValueError("unfinished-markup")

    def _event(self):
        self.events += 1
        if self.events > MAX_PARSE_EVENTS:
            raise ValueError("event-limit")

    def handle_starttag(self, tag, attrs):
        self._event()
        if tag in {"script", "style", "template", "iframe", "object"}:
            raise ValueError("unsupported-element")
        if len(self.stack) > MAX_PARSE_DEPTH:
            raise ValueError("depth-limit")
        if tag == "details":
            self.disclosures += 1
            if self.disclosures > MAX_DISCLOSURES:
                raise ValueError("disclosure-limit")
            if any(node.tag == "summary" for node in self.stack) and not any(
                node.tag in {"code", "pre", "blockquote"} for node in self.stack
            ):
                raise ValueError("details-in-summary")
        if tag == "summary" and not any(
            node.tag in {"code", "pre", "blockquote"} for node in self.stack
        ):
            parent = self.stack[-1]
            if parent.tag != "details" or any(
                not isinstance(child, str) or child.strip() for child in parent.children
            ):
                raise ValueError("misplaced-summary")
        hrefs = [value for name, value in attrs if name == "href"]
        href = hrefs[0] if len(hrefs) == 1 else None
        node = _Element(tag, href=href)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag in {"details", "summary"}:
            raise ValueError("self-closing-disclosure")
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        self._event()
        line, column = self.getpos()
        start = self.line_offsets[line - 1] + column
        end = self.source.find(">", start)
        if end < 0 or not re.fullmatch(r"</" + re.escape(tag) + r"\s*>",
                                      self.source[start:end + 1], re.IGNORECASE):
            raise ValueError("malformed-closing-tag")
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ValueError("unbalanced-markup")
        self.stack.pop()

    def handle_data(self, data):
        self._event()
        # HTMLParser returns unfinished tags as data at EOF. Do not recover a
        # partial disclosure as summary prose or approve a parsed prefix.
        if re.search(r"</?[A-Za-z]|<!--", data):
            raise ValueError("unfinished-disclosure")
        self.stack[-1].children.append(data)

    def handle_entityref(self, name):
        self._event()
        self.stack[-1].children.append(unescape("&" + name + ";"))

    def handle_charref(self, name):
        self._event()
        self.stack[-1].children.append(unescape("&#" + name + ";"))

    def handle_comment(self, data):
        self._event()

    def handle_decl(self, decl):
        raise ValueError("unsupported-declaration")

    def unknown_decl(self, data):
        raise ValueError("unsupported-declaration")

    def handle_pi(self, data):
        raise ValueError("unsupported-processing-instruction")


def _summary(node):
    return next((child for child in node.children
                 if isinstance(child, _Element) and child.tag == "summary"), None)


def _section_kind(node):
    summary = _summary(node)
    label = " ".join(_text(summary).split()) if summary else ""
    if summary and _text(summary) != _text(summary, quoted=False):
        return "ambiguous", label
    if re.fullmatch(r"\d{1,3} resolved since last review", label, re.IGNORECASE):
        return "counted-resolved", label
    counted = re.fullmatch(
        r"(\d{1,3}) (open|resolved) (findings?)", label, re.IGNORECASE,
    )
    if counted:
        count = int(counted[1])
        plural = counted[3].lower() == "findings"
        if plural == (count != 1):
            return ("counted-open" if counted[2].lower() == "open"
                    else "counted-resolved"), label
        return "ambiguous", label
    # Only complete section labels confer scope; a finding title containing
    # 'resolved' or 'history' must not silently become an exclusion boundary.
    for kind, pattern in (
        ("active", r"Previously missed(?:\s*\([^()]*\))?"),
        ("resolved", r"Resolved(?: since last review)?(?:\s*\(\d+\))?"),
        ("history", r"(?:Review history|History|Previously addressed)(?:\s*\(\d+\))?"),
        ("inline", r"Open\s*\(\d{1,3}\)"),
    ):
        if re.fullmatch(pattern, label, re.IGNORECASE):
            return kind, label
    return "ambiguous", label


_DISCUSSION_HREF_RE = re.compile(
    r"(?:#discussion_r[1-9]\d*|"
    r"https://github\.com/lindayi/hermes-mobile/pull/[1-9]\d+#discussion_r[1-9]\d*)",
    re.IGNORECASE,
)


def _discussion_targets(node, *, links=None):
    targets = set()
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if isinstance(child, _Element):
            if child.tag in {"code", "pre", "blockquote"}:
                continue
            if child.tag in {"ul", "ol", "li"}:
                # A nested inventory item is not covered by the outer li count.
                return set()
            if child.tag == "a":
                # Count live anchors, not distinct supported destinations. An
                # extra or unsupported anchor makes the entire item ambiguous.
                if targets or not isinstance(child.href, str) or not (
                    _DISCUSSION_HREF_RE.fullmatch(child.href)
                ):
                    return set()
                # Fragment and absolute links identify the same review comment.
                target = child.href.rsplit("#", 1)[1].lower()
                targets.add(target)
                if links is not None:
                    links[target] = child.href
            pending.extend(reversed(child.children))
    return targets


def _has_inert_disclosure(root):
    pending = [(root, False)]
    while pending:
        node, quoted = pending.pop()
        if not isinstance(node, _Element):
            continue
        quoted = quoted or node.tag in {"code", "pre", "blockquote"}
        if quoted and node.tag == "details":
            return True
        pending.extend((child, quoted) for child in reversed(node.children))
    return False


def _counted_section_targets(node, expected, *, links=None):
    """Return validated normalized targets, or None for an incomplete section."""
    # Counted links cannot stand in for nested, unclassified live findings.
    if next(_disclosures(node), None) is not None:
        return None
    body = [child for child in node.children if child is not _summary(node)]
    elements = [child for child in body if isinstance(child, _Element)]
    if any(isinstance(child, str) and child.strip() for child in body):
        return None
    if expected == 0:
        return set() if not elements else None
    if len(elements) != 1 or elements[0].tag != "ul":
        return None
    items = []
    for child in elements[0].children:
        if isinstance(child, str) and not child.strip():
            continue
        if not isinstance(child, _Element) or child.tag != "li":
            return None
        items.append(child)
    targets = [_discussion_targets(item, links=links) for item in items]
    unique_targets = set().union(*targets)
    if (len(items) != expected
            or any(len(item_targets) != 1 for item_targets in targets)
            or len(unique_targets) != expected):
        return None
    return unique_targets


def _text(node, *, exclude_details=False, exclude_history=False, quoted=True, render_quote=None,
          literal=False, normalize_whitespace=False):
    # Iterative even though parser depth is capped: hostile nesting never drives
    # Python recursion. Inline markup preserves word boundaries as authored.
    boundary = object()
    pieces, pending = [], [node]
    while pending:
        current = pending.pop()
        if current is None:
            continue
        if current is boundary:
            pieces.append("\n")
            continue
        if isinstance(current, str):
            # Collapse only authored text-node whitespace for classification;
            # structural boundaries and forwarded literal context stay intact.
            pieces.append(re.sub(r"[ \t\r\n\f]+", " ", current)
                          if normalize_whitespace and not literal else current)
            continue
        if not literal and current.tag in {"code", "pre", "blockquote"}:
            text = _text(current, literal=True)
            pieces.append((render_quote(text) if render_quote else text) if quoted else " ")
            continue
        if not literal and current.tag == "details" and (
            exclude_details or (exclude_history and _section_kind(current)[0] in {"resolved", "history"})
        ):
            pieces.append("\n")
            continue
        if current.tag in {"summary", "details", "p", "div", "li", "br", "hr",
                           "h1", "h2", "h3", "h4", "pre", "blockquote"}:
            pieces.append("\n")
            pending.append(boundary)
        pending.extend(reversed(current.children))
    return "".join(pieces)


def _has_live_content(node):
    prose = _text(node, exclude_history=True, quoted=False, normalize_whitespace=True).strip()
    intro = "In code that hasn't changed since last review"
    # Match the same whitespace-normalized intro as the item-shape check, but
    # retain sentence/block boundaries for the existing complete-status grammar.
    intro_pattern = r"\s+".join(re.escape(word) for word in intro.split())
    prose = re.sub(r"^" + intro_pattern + r"(?=\s|$)", "", prose).strip()
    # Reuse the complete neutral-prose grammar, not a new keyword heuristic.
    # Active disclosure scope still admits genuine prose without a correction label.
    kind, _ = _summary_disposition(prose, overview=True, state="COMMENTED")
    return kind not in {"no-findings", "validation-only"}


def _disclosures(node):
    """Nearest disclosure descendants, not nested sections counted twice."""
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if isinstance(child, _Element):
            if child.tag in {"code", "pre", "blockquote"}:
                continue
            if child.tag == "details":
                yield child
            else:
                pending.extend(reversed(child.children))


def _without_metadata(node):
    children = list(node.children)
    index = 0
    while index < len(children):
        child = children[index]
        if isinstance(child, str) and not child.strip() or (
            isinstance(child, _Element) and child.tag == "br"
        ):
            index += 1
            continue
        if (index == 0 and isinstance(child, str)
                and re.fullmatch(r"🧠\s+", child)):
            index += 1
            continue
        if not isinstance(child, _Element) or child.tag != "strong":
            break
        label = _text(child).strip()
        values = {
            "Findings:": r"(?:None|\d+)",
            "Review effort:": r"(?:Light|Balanced|Thorough)",
        }.get(label)
        if (not values or _text(child, quoted=False).strip() != label
                or index + 1 >= len(children) or not isinstance(children[index + 1], str)):
            break
        match = re.match(r"\s*" + values + r"(?=\s|$)", children[index + 1], re.IGNORECASE)
        if not match:
            break
        index += 1
        children[index] = children[index][match.end():]
    return _Element(node.tag, children[index:])


def _provider_summary(root):
    """Return only the summary paragraph in the current structured overview frame."""
    children = [
        child for child in root.children
        if not isinstance(child, str) or child.strip()
    ]
    tags = [child.tag if isinstance(child, _Element) else None for child in children[:3]]
    if tags == ["h2", "h3", "p"]:
        if _text(children[0], quoted=False).strip() != "Copilot review overview":
            return None
        status_node, summary = children[1], children[2]
    elif tags[:2] == ["h3", "p"]:
        status_node, summary = children[0], children[1]
    else:
        return None
    status = " ".join(_text(status_node, quoted=False).split())
    if not re.fullmatch(
            r"(?:[🟢🔵🟡] )?(?:Looks good|Needs a closer look|Changes recommended)",
            status):
        return None
    if _text(summary, quoted=False) != _text(summary):
        return None
    return summary


def _zero_open_metadata(node):
    """Only the provider's whole, standalone zero-open paragraph is metadata."""
    if not isinstance(node, _Element) or node.tag != "p":
        return False
    children = [child for child in node.children if not isinstance(child, str) or child.strip()]
    if (len(children) != 1 or not isinstance(children[0], _Element)
            or children[0].tag != "strong"
            or not all(isinstance(child, str) for child in children[0].children)):
        return False
    return " ".join(_text(children[0]).split()) == "0 open findings"


def _summary_content(root, overview, *, exclude=None, exclude_zero_open=False):
    children = []
    for child in root.children:
        if child is exclude:
            continue
        if exclude_zero_open and _zero_open_metadata(child):
            continue
        if overview and isinstance(child, _Element):
            if child.tag == "hr":
                break
            if child.tag == "p":
                child = _without_metadata(child)
            label = " ".join(_text(child, quoted=False).split())
            if (child.tag == "h2" and label == "Copilot review overview"
                    or child.tag == "h3" and re.fullmatch(
                        r"(?:[🟢🔵🟡] )?(?:Looks good|Needs a closer look|Changes recommended)", label)
                    ):
                continue
        children.append(child)
    return _Element("root", children)


def _summary_disposition(summary, *, overview, state, context=None):
    # Classification sees only live prose; literal context is rendered separately
    # and can be forwarded only after live prose independently proves a finding.
    prose = summary.strip()
    context = (context if context is not None else summary).strip()
    if overview and state == "COMMENTED" and prose in _COMMENTED_GATE_STATUS_SUMMARIES:
        return "validation-only", prose
    if not prose:
        return ("ambiguous", context) if context else ("no-findings", "")
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", prose) if part.strip()]
    validation = re.compile(
        r"(?:the )?(?:exact-head )?(?:verification|validation) "
        r"(?:remains|is)(?: still)? pending[.!]?", re.IGNORECASE)
    neutral_prefix = re.compile(
        r"^It changes [^.!?]+, while\s+",
        re.IGNORECASE)
    # Recognize whole negative verdicts, never arbitrary prose containing 'no'.
    # Scope suffixes are deliberately finite: an unrestricted 'in ...' would
    # swallow a same-sentence defect/correction after an otherwise neutral prefix.
    no_findings = re.compile(
        r"No (?:code )?(?:issues|bugs|defects|problems|vulnerabilities|findings) "
        r"(?:were )?(?:found|identified|detected)"
        r"(?: in (?:(?:the|this|these) )?(?:(?:reviewed|synthetic|current|proposed|latest) )?"
        r"(?:code|changes?|diff|patch|implementation))?[.!]?", re.IGNORECASE)
    negatives = [bool(no_findings.fullmatch(sentence)) for sentence in sentences]
    def is_validation_status(sentence):
        # A neutral template clause is status, not a general 'pending' keyword.
        clause = neutral_prefix.sub("", sentence)
        return bool(validation.fullmatch(clause))

    waits = [is_validation_status(sentence) for sentence in sentences]
    if any(waits) and all(wait or negative for wait, negative in zip(waits, negatives)):
        return "validation-only", prose
    if all(negatives):
        return "no-findings", prose
    if any(waits):
        # Remove only whole validation sentences; retain every other sentence,
        # including domain findings and uncertain prose, for disposition below.
        prose = "\n".join(sentence for wait, sentence in zip(waits, sentences) if not wait)
        context = "\n".join(
            sentence for sentence in re.split(r"(?<=[.!?])\s+|\n+", context)
            if sentence.strip() and not is_validation_status(sentence.strip())
        )
    # Summary prose needs an explicit correction clause, not a modal/bug keyword.
    affirmative = "\n".join(sentence for sentence, wait, negative in zip(sentences, waits, negatives)
                            if not wait and not negative)
    actionable = re.search(
        r"(?:^|\n+|[.!?]\s+|,\s*(?:but|however)\s+)"
        r"(?:required|requested) (?:correction|change):\s*\S", affirmative, re.IGNORECASE)
    if actionable:
        return ("active" if state == "CHANGES_REQUESTED" else "ambiguous"), context
    return "ambiguous", prose


def parse_body(body, state, *, body_html=None):
    """Classify a complete body; ambiguity is not repair evidence or approval.

    Findings are (kind, quoted text) pairs. Classifications are explicit section
    dispositions, including non-actionable sections, for callers/diagnostics.
    """
    result = {"classifications": [], "findings": [], "ambiguous": False, "reason": None,
              "findings_total": 0, "truncated": False, "inventory_complete": True,
              "inline_targets": [], "inline_count": 0}
    parser = _DisclosureParser()
    try:
        if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
            raise ValueError("body-limit")
        if (not isinstance(body_html, str) or not body_html.strip()
                or len(body_html) > MAX_BODY_CHARS):
            raise ValueError("rendered-body-limit")
        overview = body == OVERVIEW_MARKER or body.startswith(
            (OVERVIEW_MARKER + "\n", OVERVIEW_MARKER + "\r\n"))
        parser.feed_review(body_html)
        if len(parser.stack) != 1:
            raise ValueError("unclosed-markup")
    except (ValueError, AssertionError) as exc:
        return dict(result, classifications=["ambiguous"], ambiguous=True, reason=str(exc),
                    inventory_complete=False)

    if not overview and state != "CHANGES_REQUESTED":
        return dict(result, classifications=["ambiguous"], ambiguous=True,
                    reason="unstructured-comment", inventory_complete=False)

    sections = list(_disclosures(parser.root)) if overview else []
    if overview:
        headings = []
        heading_positions = []
        children = [node for node in parser.root.children
                    if not isinstance(node, str) or node.strip()]
        for position, node in enumerate(children):
            if not isinstance(node, _Element) or node.tag not in {'h2', 'h3'}:
                continue
            label = ' '.join(_text(node, quoted=False).split())
            if (node.tag == 'h2' and label == 'Copilot review overview'
                    or node.tag == 'h3' and re.fullmatch(
                        r'(?:[🟢🔵🟡] )?(?:Looks good|Needs a closer look|Changes recommended)', label)):
                headings.append(node.tag)
                heading_positions.append(position)
        if (headings not in ([], ['h2'], ['h3'], ['h2', 'h3'])
                or heading_positions != list(range(len(headings)))):
            result['classifications'].append('ambiguous')
            result['ambiguous'] = True
            result['inventory_complete'] = False
    positive_open = False
    counted_sections = set()
    counted_targets = set()
    zero_open_count = sum(_zero_open_metadata(child) for child in parser.root.children) if overview else 0
    open_section_count = zero_open_count
    if zero_open_count > 1:
        result["classifications"].append("ambiguous")
        result["ambiguous"] = True
    matched_active_sections = []
    active_section_seen = False
    resolved_section_seen = False
    history_section_seen = False
    for node in sections:
        kind, label = _section_kind(node)
        result["classifications"].append(kind)
        if kind == "ambiguous":
            result["ambiguous"] = True
        elif kind in {"counted-open", "counted-resolved"}:
            category = "open" if kind == "counted-open" else "resolved"
            count = int(re.match(r"\d+", label)[0])
            duplicate = category in counted_sections or (
                open_section_count > 0 if category == "open" else resolved_section_seen
            )
            links = {}
            targets = _counted_section_targets(node, count, links=links)
            if duplicate or targets is None or counted_targets.intersection(targets):
                result["classifications"][-1] = "ambiguous"
                result["ambiguous"] = True
                result["inventory_complete"] = False
            else:
                counted_sections.add(category)
                counted_targets.update(targets)
                result["classifications"][-1] = (
                    "inline" if category == "open" else "resolved"
                )
                if category == "open":
                    open_section_count += 1
                    result["inline_count"] = count
                    result["inline_targets"] = [links[target] for target in sorted(targets)]
                    if count:
                        positive_open = True
                else:
                    resolved_section_seen = True
        elif kind == "inline":
            if open_section_count:
                result["classifications"][-1] = "ambiguous"
                result["ambiguous"] = True
                result["inventory_complete"] = False
            open_section_count += 1
            count = int(re.search(r"\((\d+)\)$", label)[1])
            result["inline_count"] = count
            if count:
                positive_open = True
            elif any(_text(child).strip() for child in node.children if child is not _summary(node)):
                result["ambiguous"] = True
        elif kind == "active":
            children = list(_disclosures(node))
            count = re.search(r"\((\d{1,3})\)$", label)
            expected = int(count[1]) if count else None
            content = _Element("root", [
                child for child in node.children if child is not _summary(node)
            ])
            outside_items = " ".join(_text(content, exclude_details=True).split())
            intro = "In code that hasn't changed since last review"
            only_intro = outside_items in {"", intro}
            if (only_intro and expected == len(children)
                    and all(_summary(child) is not None for child in children)):
                texts = [_text(child, exclude_history=True).strip() for child in children
                         if _has_live_content(child)]
                if len(texts) != expected:
                    result["inventory_complete"] = False
                elif expected == 1:
                    matched_active_sections.append((expected, texts))
            else:
                result["inventory_complete"] = False
                # Count/shape uncertainty may preserve a populated active section,
                # but its label, intro and history cannot themselves be a finding.
                texts = ([_text(node, exclude_history=True).strip()]
                         if _has_live_content(content) else [])
            if not texts:
                result["classifications"][-1] = "no-findings"
            result["findings"].extend(("previously-missed", text) for text in texts if text)
            if active_section_seen:
                result["classifications"][-1] = "ambiguous"
                result["ambiguous"] = True
                result["inventory_complete"] = False
            active_section_seen = True
        elif kind == "resolved":
            if resolved_section_seen:
                result["classifications"][-1] = "ambiguous"
                result["ambiguous"] = True
                result["inventory_complete"] = False
            resolved_section_seen = True
        elif kind == "history":
            if history_section_seen:
                result["classifications"][-1] = "ambiguous"
                result["ambiguous"] = True
                result["inventory_complete"] = False
            history_section_seen = True

    if overview and _has_inert_disclosure(parser.root):
        result["classifications"].append("ambiguous")
        result["ambiguous"] = True
        result["inventory_complete"] = False

    provider_summary = _provider_summary(parser.root) if overview else None
    attach_provider_summary = (
        provider_summary is not None
        and result["inventory_complete"] is True
        and result["ambiguous"] is False
        and open_section_count == 1
        and "open" in counted_sections
        and len(matched_active_sections) == 1
        and len(result["findings"]) == 1
        and result["findings"][0][0] == "previously-missed"
    )
    summary_root = _summary_content(
        parser.root, overview, exclude=provider_summary if attach_provider_summary else None,
        exclude_zero_open=zero_open_count == 1 and not result["ambiguous"],
    )
    summary = _text(summary_root, exclude_details=True, quoted=False).strip()
    # Keep quoted nodes opaque through footer/framing/status removal as well as
    # classification. Fresh render tokens cannot collide with any input text;
    # expand once only after a disposition has been determined from live prose.
    prefix = "\x00"
    while prefix in body_html or prefix in _text(parser.root):
        prefix *= 2
    quotations = []

    def render_quote(text):
        quotations.append(text)
        return prefix + str(len(quotations) - 1) + prefix

    context = _text(summary_root, exclude_details=True, render_quote=render_quote).strip()
    kind, prose = _summary_disposition(summary, overview=overview, state=state, context=context)
    prose = re.sub(re.escape(prefix) + r"(\d+)" + re.escape(prefix),
                   lambda match: quotations[int(match[1])], prose)
    if attach_provider_summary and kind in {"no-findings", "validation-only"}:
        summary_text = _text(provider_summary).strip()
        finding_kind, finding_text = result["findings"][0]
        result["findings"][0] = (
            finding_kind, summary_text + "\n\n" + finding_text,
        )
        kind = "no-findings"
    result["classifications"].append(kind)
    if kind == "active" and not positive_open and not result["findings"] and not result["ambiguous"]:
        result["findings"].append(("changes-requested", prose))
    elif kind == "ambiguous":
        result["ambiguous"] = True
    # Ambiguity is local: never quote the unknown section, but never erase
    # independently bounded active sections because an unrelated footer changed.
    if result["ambiguous"]:
        result["reason"] = "unclassified-content"
        result["inventory_complete"] = False
    result["findings_total"] = len(result["findings"])
    result["truncated"] = result["findings_total"] > MAX_BODY_FINDINGS
    if result["truncated"]:
        result["inventory_complete"] = False
    result["findings"] = result["findings"][:MAX_BODY_FINDINGS]
    return result


def review_body_disposition(reviews, head_sha, *, reviewer_id):
    """Select exactly one authenticated current-head review, then classify it."""
    empty = {"classifications": [], "findings": [], "ambiguous": False, "reason": None,
             "findings_total": 0, "truncated": False, "inventory_complete": True,
             "inline_targets": [], "inline_count": 0}
    latest = latest_reviews(reviews, reviewer_id)
    if latest is None:
        if (not isinstance(reviews, list)
                or any(not isinstance(review, dict)
                       or not isinstance(review.get("user"), dict)
                       or not positive_id(review["user"].get("id"))
                       or not positive_id(review.get("id"))
                       or review["user"]["id"] == reviewer_id for review in reviews)
                or len({review["id"] for review in reviews}) != len(reviews)):
            return dict(empty, inventory_complete=False)
        return empty
    if len(latest) != 1:
        return dict(empty, inventory_complete=False)
    review = latest[0]
    state, body = review.get("state"), review.get("body")
    if not isinstance(head_sha, str) or review.get("commit_id") != head_sha:
        return empty
    if (type(state) is str and (
            state in {"DISMISSED", "PENDING"}
            or state in {"COMMENTED", "APPROVED"} and body == "")):
        return empty
    if (type(state) is not str or state not in {"COMMENTED", "APPROVED", "CHANGES_REQUESTED"}
            or not isinstance(body, str) or not body.strip()):
        return dict(empty, inventory_complete=False)
    result = parse_body(body, state, body_html=review.get("body_html"))
    result["findings"] = [
        {"review": review["id"], "head": head_sha,
         "submitted_at": review["submitted_at"], "kind": kind, "text": text}
        for kind, text in result["findings"]
    ]
    return result


def body_findings(reviews, head_sha, *, reviewer_id):
    """Compatibility interface: only explicit active, current body evidence."""
    return review_body_disposition(reviews, head_sha, reviewer_id=reviewer_id)["findings"]
