"""Strict review selection and bounded structural review-body dispositions.

Body text is untrusted evidence, never approval or executable instructions.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
import re

OVERVIEW_MARKER = "<!-- ccr-overview-v2 -->"
MAX_BODY_CHARS = 64 * 1024
MAX_BODY_FINDINGS = 8
MAX_PARSE_EVENTS = 4096
MAX_PARSE_DEPTH = 32
MAX_DISCLOSURES = 256
FINDING_KINDS = frozenset({"previously-missed", "changes-requested"})


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


@dataclass
class _Element:
    tag: str
    children: list = field(default_factory=list)


class _DisclosureParser(HTMLParser):
    """One bounded parse; strict nesting rather than browser error recovery."""

    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]
        self.events = self.disclosures = 0
        self.overview = False

    def feed_review(self, body):
        # GitHub supplies Markdown, not rendered HTML. Shield code before the
        # sole HTML parse; complete HTML tokens protect quoted attributes. Line
        # fences use same-character >= length closers, unlike exact inline runs.
        runs = {}
        for match in re.finditer(r"`+", body):
            runs.setdefault(len(match[0]), []).append(match.start())
        tokens = re.compile(
            r"<!--.*?-->|</?[A-Za-z](?:[^<>\"']|\"[^\"]*\"|'[^']*')*>|"
            r"^(?P<indented>(?: {4}| {0,3}\t)[ \t]*)(?=</?(?i:details|summary)\b)|"
            r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)|`+",
            re.DOTALL | re.MULTILINE)
        cursor = fed = 0
        while match := tokens.search(body, cursor):
            cursor = match.end()
            if match["indented"]:
                # Four-column Markdown examples cannot confer disclosure scope.
                # Do not attempt general indented-code/HTML block rendering.
                raise ValueError("indented-disclosure")
            fence = match["fence"]
            if fence:
                # Indented code is outside this bounded fence grammar.
                if len(match["indent"]) > 3 or "\t" in match["indent"]:
                    raise ValueError("unsupported-code-fence")
                if fence[0] == "`" and "`" in match["info"]:
                    # A same-line exact span (```code```) is inline, not a
                    # fence with a backtick-bearing info string. Other invalid
                    # info shapes stay unsupported rather than guessing scope.
                    positions = runs[len(fence)]
                    index = bisect_right(positions, match.start("fence"))
                    if index == len(positions) or positions[index] >= cursor:
                        raise ValueError("unsupported-code-fence")
                    end = positions[index] + len(fence)
                else:
                    closer = re.compile(
                        r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\r?$",
                        re.MULTILINE).search(body, cursor)
                    if closer is None:
                        raise ValueError("unclosed-code-fence")
                    end = closer.end()
            elif match[0].startswith("`"):
                # Index exact inline run lengths once, avoiding suffix rescans.
                positions = runs[len(match[0])]
                index = bisect_right(positions, match.start())
                if index == len(positions):
                    continue  # An unmatched inline backtick is literal Markdown.
                end = positions[index] + len(match[0])
            else:
                continue
            self.feed(body[fed:match.start()])
            if self.rawdata:
                raise ValueError("code-in-incomplete-markup")
            self._event()
            self.stack[-1].children.append(body[match.start():end])
            cursor = fed = end
        self.feed(body[fed:])
        self.close()

    def _event(self):
        self.events += 1
        if self.events > MAX_PARSE_EVENTS:
            raise ValueError("event-limit")

    def handle_starttag(self, tag, attrs):
        self._event()
        if len(self.stack) > MAX_PARSE_DEPTH:
            raise ValueError("depth-limit")
        if tag == "details":
            self.disclosures += 1
            if self.disclosures > MAX_DISCLOSURES:
                raise ValueError("disclosure-limit")
            if any(node.tag == "summary" for node in self.stack):
                raise ValueError("details-in-summary")
        if tag == "summary":
            parent = self.stack[-1]
            if parent.tag != "details" or any(
                isinstance(child, _Element) or child.strip() for child in parent.children
            ):
                raise ValueError("misplaced-summary")
        node = _Element(tag)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag in {"details", "summary"}:
            raise ValueError("self-closing-disclosure")
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self._event()
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ValueError("unbalanced-markup")
        self.stack.pop()

    def handle_data(self, data):
        self._event()
        # HTMLParser returns unfinished tags as data at EOF. Do not recover a
        # partial disclosure as summary prose or approve a parsed prefix.
        if re.search(r"</?(?:details|summary)\b", data, re.IGNORECASE):
            raise ValueError("unfinished-disclosure")
        self.stack[-1].children.append(data)

    def handle_comment(self, data):
        self._event()
        if data.strip() == "ccr-overview-v2" and len(self.stack) == 1:
            self.overview = True

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


def _text(node, *, exclude_details=False, exclude_history=False):
    # Iterative even though parser depth is capped: hostile nesting never drives
    # Python recursion. Inline markup preserves word boundaries as authored.
    pieces, pending = [], [node]
    while pending:
        current = pending.pop()
        if current is None:
            continue
        if isinstance(current, str):
            pieces.append(current)
            continue
        if current.tag == "details" and (
            exclude_details or (exclude_history and _section_kind(current)[0] in {"resolved", "history"})
        ):
            pieces.append("\n")
            continue
        if current.tag in {"summary", "details", "p", "div", "li", "br", "hr"}:
            pending.append("\n")
        pending.extend(reversed(current.children))
    return "".join(pieces)


def _disclosures(node):
    """Nearest disclosure descendants, not nested sections counted twice."""
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if isinstance(child, _Element):
            if child.tag == "details":
                yield child
            else:
                pending.extend(reversed(child.children))


def _summary_disposition(summary, *, overview, state):
    lines = summary.split("\n---", 1)[0].splitlines() if overview else summary.splitlines()
    # Remove only known overview framing, not arbitrary headings or prose.
    prose = "\n".join(line for line in lines if not (overview and re.fullmatch(
        r"\s*(?:## Copilot review overview|### (?:[🟢🔵🟡] )?"
        r"(?:Looks good|Needs a closer look|Changes recommended)|"
        r"\*\*(?:Review effort|Findings):\*\*.*)\s*", line, re.IGNORECASE))).strip()
    if not prose:
        return "no-findings", ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", prose) if part.strip()]
    validation = re.compile(
        r"(?:the )?(?:exact-head )?(?:verification|validation) "
        r"(?:remains|is)(?: still)? pending[.!]?", re.IGNORECASE)
    # Recognize whole negative verdicts, never arbitrary prose containing 'no'.
    # Scope suffixes are deliberately finite: an unrestricted 'in ...' would
    # swallow a same-sentence defect/correction after an otherwise neutral prefix.
    no_findings = re.compile(
        r"No (?:code )?(?:issues|bugs|defects|problems|vulnerabilities|findings) "
        r"(?:were )?(?:found|identified|detected)"
        r"(?: in (?:(?:the|this|these) )?(?:(?:reviewed|synthetic|current|proposed|latest) )?"
        r"(?:code|changes?|diff|patch|implementation))?[.!]?", re.IGNORECASE)
    negatives = [bool(no_findings.fullmatch(sentence)) for sentence in sentences]
    waits = []
    for sentence in sentences:
        # A neutral template clause is status, not a general 'pending' keyword.
        clause = re.sub(r"^It changes [^.!?]+, while\s+", "", sentence, flags=re.IGNORECASE)
        waits.append(bool(validation.fullmatch(clause)))
    if any(waits) and all(wait or negative for wait, negative in zip(waits, negatives)):
        return "validation-only", prose
    if all(negatives):
        return "no-findings", prose
    if any(waits):
        # Remove only whole validation sentences; retain every other sentence,
        # including domain findings and uncertain prose, for disposition below.
        prose = "\n".join(sentence for wait, sentence in zip(waits, sentences) if not wait)
    # Bare CHANGES_REQUESTED is already an explicit request. Overview summaries
    # need affirmative correction/defect evidence, not a generic closer-look label.
    # Keep negative verdicts as quoted context, but never mistake their nouns
    # for affirmative defect evidence in a mixed or otherwise uncertain summary.
    affirmative = "\n".join(sentence for sentence, wait, negative in zip(sentences, waits, negatives)
                            if not wait and not negative)
    actionable = re.search(
        r"\b(?:required (?:correction|change)|request(?:ed)?:|must|should|"
        r"(?:please )?(?:reject|fix|prevent|ensure|validate|remove|preserve|add)\b|"
        r"(?:fails? to|incorrectly|bug|defect|vulnerability))", affirmative, re.IGNORECASE)
    if actionable or (not overview and state == "CHANGES_REQUESTED"):
        return ("active" if state == "CHANGES_REQUESTED" else "ambiguous"), prose
    if overview and re.search(r"^### (?:🟢 )?Looks good\s*$", summary, re.MULTILINE | re.IGNORECASE) and re.search(
        r"^\*\*Findings:\*\* None\s*$", summary, re.MULTILINE | re.IGNORECASE
    ):
        return "no-findings", prose
    return "ambiguous", prose


def parse_body(body, state):
    """Classify a complete body; ambiguity is not repair evidence or approval.

    Findings are (kind, quoted text) pairs. Classifications are explicit section
    dispositions, including non-actionable sections, for callers/diagnostics.
    """
    result = {"classifications": [], "findings": [], "ambiguous": False, "reason": None}
    parser = _DisclosureParser()
    try:
        if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
            raise ValueError("body-limit")
        parser.feed_review(body)
        if len(parser.stack) != 1:
            raise ValueError("unclosed-markup")
    except (ValueError, AssertionError) as exc:
        return dict(result, classifications=["ambiguous"], ambiguous=True, reason=str(exc))

    if not parser.overview and state != "CHANGES_REQUESTED":
        return dict(result, classifications=["ambiguous"], ambiguous=True, reason="unstructured-comment")

    sections = list(_disclosures(parser.root))
    positive_open = False
    for node in sections:
        kind, label = _section_kind(node)
        result["classifications"].append(kind)
        if kind == "ambiguous":
            result["ambiguous"] = True
        elif kind == "inline":
            count = int(re.search(r"\((\d+)\)$", label)[1])
            if count:
                positive_open = True
            elif any(_text(child).strip() for child in node.children if child is not _summary(node)):
                result["ambiguous"] = True
        elif kind == "active":
            children = list(_disclosures(node))
            count = re.search(r"\((\d{1,3})\)$", label)
            expected = int(count[1]) if count else None
            outside_items = _text(_Element("root", [
                child for child in node.children if child is not _summary(node)
            ]), exclude_details=True).strip()
            only_intro = outside_items in {"", "In code that hasn't changed since last review"}
            if (only_intro and expected == len(children)
                    and all(_summary(child) is not None for child in children)):
                texts = [_text(child, exclude_history=True).strip() for child in children]
            else:
                # The explicit active section is sound; only its item count/shape
                # is uncertain. Quote it whole without inventing separate items.
                texts = [_text(node, exclude_history=True).strip()]
            result["findings"].extend(("previously-missed", text) for text in texts if text)

    summary = _text(parser.root, exclude_details=True).strip()
    kind, prose = _summary_disposition(summary, overview=parser.overview, state=state)
    result["classifications"].append(kind)
    if kind == "active" and not positive_open and not result["findings"] and not result["ambiguous"]:
        result["findings"].append(("changes-requested", prose))
    elif kind == "ambiguous":
        result["ambiguous"] = True
    # Ambiguity is local: never quote the unknown section, but never erase
    # independently bounded active sections because an unrelated footer changed.
    if result["ambiguous"]:
        result["reason"] = "unclassified-content"
    result["findings"] = result["findings"][:MAX_BODY_FINDINGS]
    return result


def review_body_disposition(reviews, head_sha, *, reviewer_id):
    """Select exactly one authenticated current-head review, then classify it."""
    empty = {"classifications": [], "findings": [], "ambiguous": False, "reason": None}
    latest = latest_reviews(reviews, reviewer_id)
    if not latest or len(latest) != 1:
        return empty
    review = latest[0]
    state, body = review.get("state"), review.get("body")
    if (not isinstance(head_sha, str) or review.get("commit_id") != head_sha
            or not positive_id(review.get("id"))
            or type(state) is not str or state not in {"COMMENTED", "CHANGES_REQUESTED"}
            or not isinstance(body, str) or not body.strip()):
        return empty
    result = parse_body(body, state)
    result["findings"] = [
        {"review": review["id"], "head": head_sha,
         "submitted_at": review["submitted_at"], "kind": kind, "text": text}
        for kind, text in result["findings"]
    ]
    return result


def body_findings(reviews, head_sha, *, reviewer_id):
    """Compatibility interface: only explicit active, current body evidence."""
    return review_body_disposition(reviews, head_sha, reviewer_id=reviewer_id)["findings"]
