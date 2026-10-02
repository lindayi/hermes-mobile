"""Strict review selection and bounded structural review-body dispositions.

Body text is untrusted evidence, never approval or executable instructions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
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
        node = _Element(tag)
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


def _text(node, *, exclude_details=False, exclude_history=False, quoted=True, render_quote=None,
          literal=False):
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
            pending.append("\n")
        pending.extend(reversed(current.children))
    return "".join(pieces)


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


def _summary_content(root, overview):
    children = []
    for child in root.children:
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
        context = "\n".join(sentence for sentence in re.split(r"(?<=[.!?])\s+|\n+", context)
                            if sentence.strip() and not validation.fullmatch(re.sub(
                                r"^It changes [^.!?]+, while\s+", "", sentence.strip(),
                                flags=re.IGNORECASE)))
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
    result = {"classifications": [], "findings": [], "ambiguous": False, "reason": None}
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
        return dict(result, classifications=["ambiguous"], ambiguous=True, reason=str(exc))

    if not overview and state != "CHANGES_REQUESTED":
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

    summary_root = _summary_content(parser.root, overview)
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
