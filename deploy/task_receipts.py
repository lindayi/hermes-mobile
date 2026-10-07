"""Exact, bounded Copilot task completion receipts."""

from datetime import datetime, timezone
import json
import re


COPILOT_AGENT_ID = 198982749
RECEIPT_RESULTS = {"ready", "conflict_incompatible", "policy_broken"}
SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
V2_RECEIPT_HEADER = "Hermes-Task-Receipt: v2"
V2_RECEIPT_FIELDS = {"nonce", "session", "pr", "start_head", "head", "base", "result"}
# Whole-comment limits leave room for a short quoted report and eight-line receipt.
V2_TRANSPORT_MAX_BYTES = 8192
V2_TRANSPORT_MAX_LINES = 64
REVIEW_REPORT_SCHEMA = "hermes-independent-review-report-v1"
REVIEW_REPORT_HEADER = "Hermes-Review-Anchor: "
REVIEW_REPORT_ROLE = "independent-reviewer"
REVIEW_REPORT_VERDICTS = {"pass", "changes_requested"}
REVIEW_REPORT_REQUIRED_FIELDS = (
    "schema", "nonce", "session_id", "repository", "repository_id", "pr",
    "anchor_comment_id", "role", "head", "base", "source_start_head",
    "source_session_id", "source_comment_id", "verdict", "summary", "findings",
    "files", "report",
)
REVIEW_REPORT_FINDING_FIELDS = ("path", "comment")
MAX_REVIEW_REPORT_BYTES = 64 * 1024
MAX_REVIEW_REPORT_LINES = 64
MAX_REVIEW_REPORT_FINDINGS = 8
MAX_REVIEW_REPORT_FILES = 64
MAX_REVIEW_REPORT_TEXT = 1000


class ReceiptError(ValueError):
    """A receipt is missing, conflicting, edited, stale, or unbound."""


def receipt_instruction(nonce, *, pull_number, start_head, base_sha):
    return (
        "Before any source edits, verify that nonce, pr, start_head and base are all "
        "present and complete in the template below, and read a nonblank "
        "COPILOT_AGENT_SESSION_ID from your exposed environment. If any fixed "
        "binding is missing or incomplete, or the session variable is missing or "
        "blank, stop without source edits and report an explicit blocker; do not "
        "guess, substitute values, or emit any receipt.\n\n"
        "After pushing your result and running focused checks, post exactly one "
        "issue comment on this PR. Put the exact receipt fields below in one "
        "contiguous, unquoted final block, with no extra fields or unquoted prose. "
        "The posting transport may prepend an unchanged Markdown blockquote, "
        "separated from the receipt by an ASCII blank line (empty or only spaces/tabs), "
        "or reorder fields; do not quote or fence the receipt itself, and include each "
        "field exactly once. Keep the entire comment within "
        f"{V2_TRANSPORT_MAX_BYTES} UTF-8 bytes and {V2_TRANSPORT_MAX_LINES} LF-delimited lines. "
        "Copy nonce, pr, start_head and base exactly: base is the fixed dispatch-time "
        "main SHA, not main at completion. The parent "
        "binds task identity through its authenticated task API; do not discover "
        "or echo a task UUID, obtain extra credentials, or request an owner comment. "
        "Replace the session label with that exact session ID and "
        "the head label with the pushed PR head SHA; these "
        "replacement labels are not receipt values. Choose one result from the closed list. "
        "Do not wait for CI or review after pushing and focused checks; the parent "
        "controller handles CI/review. A ready receipt is not passing CI and does "
        "not claim review, merge, or deployment success.\n\n"
        f"{V2_RECEIPT_HEADER}\n"
        f"nonce={nonce}\n"
        f"pr={pull_number}\n"
        f"start_head={start_head}\n"
        f"base={base_sha}\n"
        "session=SESSION_ID_REPLACE_ME\n"
        "head=PUSHED_PULL_HEAD_SHA_REPLACE_ME\n"
        "result=ready|conflict_incompatible|policy_broken"
    )


def _time(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ReceiptError("Receipt timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReceiptError("Receipt timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ReceiptError("Receipt timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identity(value, expected):
    return isinstance(value, dict) and type(value.get("id")) is int and value["id"] == expected


def _nonblank_string(value, *, limit=256):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _bounded_path(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 200
        and re.fullmatch(r"[A-Za-z0-9_./-]+", value) is not None
        and not value.startswith("/")
        and ".." not in value.split("/")
    )


def _expected_body(nonce, task_id, session_id, pull_number, start_head,
                   head_sha, base_sha, result, *, version="v1"):
    if version not in {"v1", "v2"}:
        raise ReceiptError("Unsupported receipt version")
    task_field = f"task={task_id}\n" if version == "v1" else ""
    return (
        f"Hermes-Task-Receipt: {version}\n"
        f"nonce={nonce}\n"
        f"{task_field}"
        f"session={session_id}\n"
        f"pr={pull_number}\n"
        f"start_head={start_head}\n"
        f"head={head_sha}\n"
        f"base={base_sha}\n"
        f"result={result}"
    )


def _is_blockquote(line):
    return re.match(r"^ {0,3}>", line) is not None


def _v2_fields(body):
    # Bound allocation before encoding, then bound scans and splitting by bytes.
    if not isinstance(body, str) or len(body) > V2_TRANSPORT_MAX_BYTES:
        raise ReceiptError("Task receipt exceeds the transport byte budget")
    try:
        byte_count = len(body.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ReceiptError("Task receipt is not valid UTF-8") from error
    if byte_count > V2_TRANSPORT_MAX_BYTES:
        raise ReceiptError("Task receipt exceeds the transport byte budget")
    if body.count("\n") >= V2_TRANSPORT_MAX_LINES:
        raise ReceiptError("Task receipt exceeds the transport line budget")
    if ("\r" in body or body.endswith("\n")
            or body.count(V2_RECEIPT_HEADER) != 1):
        raise ReceiptError("Task receipt transport is ambiguous or noncanonical")
    lines = body.split("\n")
    positions = [index for index, line in enumerate(lines) if line == V2_RECEIPT_HEADER]
    if len(positions) != 1:
        raise ReceiptError("Task receipt header is not an unquoted standalone line")
    position = positions[0]
    if any(line.strip(" \t") and not _is_blockquote(line) for line in lines[:position]):
        raise ReceiptError("Task receipt has untrusted text before its block")
    # Without an ASCII blank line, raw receipt lines can be lazy quote content.
    if position and lines[position - 1].strip(" \t"):
        raise ReceiptError("Task receipt is not separated from its quoted prefix")
    block = lines[position:position + 1 + len(V2_RECEIPT_FIELDS)]
    if len(block) != 1 + len(V2_RECEIPT_FIELDS):
        raise ReceiptError("Task receipt fields are incomplete")
    fields = {}
    for line in block[1:]:
        key, separator, value = line.partition("=")
        if (not separator or key not in V2_RECEIPT_FIELDS or not value
                or key in fields):
            raise ReceiptError("Task receipt fields are malformed or ambiguous")
        fields[key] = value
    if set(fields) != V2_RECEIPT_FIELDS or position + len(block) != len(lines):
        raise ReceiptError("Task receipt fields or surrounding text do not match")
    return fields


def receipt_body_matches(body, nonce, task_id, session_id, pull_number,
                         start_head, head_sha, base_sha, result, *, version):
    if version == "v1":
        return body == _expected_body(
            nonce, task_id, session_id, pull_number, start_head, head_sha,
            base_sha, result, version=version,
        )
    if version != "v2":
        return False
    try:
        fields = _v2_fields(body)
    except ReceiptError:
        return False
    return fields == {
        "nonce": nonce,
        "session": session_id,
        "pr": str(pull_number),
        "start_head": start_head,
        "head": head_sha,
        "base": base_sha,
        "result": result,
    }


def find_receipt(comments, *, complete, nonce, task_id, session_id,
                 pull_number, start_head, head_sha, base_sha,
                 task_created_at, session_created_at, session_completed_at,
                 now, dispatch_base_sha=None):
    if (complete is not True or not isinstance(comments, list)
            or len(comments) > 10000
            or not all(isinstance(comment, dict) for comment in comments)
            or not all(_nonblank_string(value) for value in
                       (nonce, task_id, session_id))
            or type(pull_number) is not int or pull_number < 1
            or any(not isinstance(value, str) or SHA_RE.fullmatch(value) is None
                   for value in (start_head, head_sha, base_sha))):
        raise ReceiptError("Receipt identity is incomplete")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ReceiptError("Receipt clock must include a timezone")
    now = now.astimezone(timezone.utc)
    task_time = _time(task_created_at)
    session_time = _time(session_created_at)
    completed_time = _time(session_completed_at)
    if not task_time <= session_time <= completed_time <= now:
        raise ReceiptError("Task session chronology is invalid")

    found = []
    for comment in comments:
        author, body = comment.get("user"), comment.get("body")
        if (not _identity(author, COPILOT_AGENT_ID)
                or not isinstance(body, str) or nonce not in body):
            continue
        if comment.get("updated_at") != comment.get("created_at"):
            raise ReceiptError("Task receipt was edited")
        created = _time(comment.get("created_at"))
        if not session_time <= created <= completed_time or created > now:
            raise ReceiptError("Task receipt is outside the documented session interval")
        version = "v2" if V2_RECEIPT_HEADER in body else "v1"
        receipt_base = dispatch_base_sha if version == "v2" else base_sha
        if not isinstance(receipt_base, str) or SHA_RE.fullmatch(receipt_base) is None:
            raise ReceiptError("Receipt dispatch base is missing")
        matches = [
            result for result in RECEIPT_RESULTS
            if receipt_body_matches(
                body, nonce, task_id, session_id, pull_number,
                start_head, head_sha, receipt_base, result, version=version,
            )
        ]
        if len(matches) != 1:
            raise ReceiptError("Task receipt fields or result do not match")
        comment_id = comment.get("id")
        if type(comment_id) is not int or comment_id <= 0:
            raise ReceiptError("Task receipt comment identity is malformed")
        found.append({"result": matches[0], "comment_id": comment_id,
                      "created_at": comment["created_at"], "body": body,
                      **({"version": "v2"} if version == "v2" else {})})
    if len(found) > 1:
        raise ReceiptError("Conflicting or duplicate task receipts")
    return found[0] if found else None


def _reply_transport_json(body, *, anchor_prefix):
    if not isinstance(body, str):
        raise ReceiptError("Review report body is missing")
    if len(body) > MAX_REVIEW_REPORT_BYTES:
        raise ReceiptError("Review report exceeds the transport safety bounds")
    try:
        byte_count = len(body.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ReceiptError("Review report is not valid UTF-8") from error
    if (byte_count > MAX_REVIEW_REPORT_BYTES or body.count("\n") >= MAX_REVIEW_REPORT_LINES
            or "\r" in body):
        raise ReceiptError("Review report exceeds the transport safety bounds")
    lines = body.split("\n")
    json_index = None
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip():
            json_index = index
            break
    if json_index is None:
        raise ReceiptError("Review report body is empty")
    separator = lines[json_index - 1]
    if json_index == 0 or (
            separator.strip(" \t")
            and not (_is_blockquote(separator)
                     and not re.sub(r"^ {0,3}> ?", "", separator).strip(" \t"))):
        raise ReceiptError("Review report is not separated from its quoted prefix")
    prefix_lines = lines[:json_index]
    if not prefix_lines or not any(_is_blockquote(line) for line in prefix_lines):
        raise ReceiptError("Review report is missing the quoted anchor preamble")
    if any(line.strip(" \t") and not _is_blockquote(line) for line in prefix_lines):
        raise ReceiptError("Review report has untrusted text outside the quote")
    unquoted = [
        re.sub(r"^ {0,3}> ?", "", line)
        for line in prefix_lines if line.strip(" \t")
    ]
    if not unquoted or unquoted[0] != anchor_prefix:
        raise ReceiptError("Review report quote does not match the saved owner anchor")
    payload = lines[json_index].strip()
    try:
        report = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ReceiptError("Review report JSON is malformed") from error
    if json.dumps(report, separators=(",", ":")) != payload:
        raise ReceiptError("Review report JSON must be a single exact compact object")
    return report, payload


def _valid_review_report(report, *, nonce, session_id, pull_number, head_sha, base_sha,
                         source_start_head, source_session_id, source_comment_id,
                         anchor_comment_id):
    if not isinstance(report, dict):
        return None
    if set(report) not in (
            set(REVIEW_REPORT_REQUIRED_FIELDS),
            set(REVIEW_REPORT_REQUIRED_FIELDS) | {"progress_disposition"}):
        return None
    if "progress_disposition" in report:
        disposition = report["progress_disposition"]
        if (not isinstance(disposition, dict)
                or set(disposition) != {"version", "resolved"}
                or type(disposition.get("version")) is not int
                or disposition["version"] != 1
                or not isinstance(disposition.get("resolved"), list)
                or len(disposition["resolved"]) > 32
                or any(not isinstance(target, str)
                       or SHA256_RE.fullmatch(target) is None
                       for target in disposition["resolved"])
                or len(set(disposition["resolved"])) != len(disposition["resolved"])):
            return None
    if (
            report.get("schema") != REVIEW_REPORT_SCHEMA
            or report.get("nonce") != nonce
            or report.get("session_id") != session_id
            or report.get("repository") != "lindayi/hermes-mobile"
            or report.get("repository_id") != 1399942965
            or report.get("pr") != pull_number
            or report.get("anchor_comment_id") != anchor_comment_id
            or report.get("role") != REVIEW_REPORT_ROLE
            or report.get("head") != head_sha
            or report.get("base") != base_sha
            or report.get("source_start_head") != source_start_head
            or report.get("source_session_id") != source_session_id
            or report.get("source_comment_id") != source_comment_id
            or report.get("verdict") not in REVIEW_REPORT_VERDICTS
            or not _nonblank_string(report.get("summary"), limit=MAX_REVIEW_REPORT_TEXT)
            or not _nonblank_string(report.get("report"), limit=MAX_REVIEW_REPORT_TEXT)
            or not isinstance(report.get("findings"), list)
            or len(report["findings"]) > MAX_REVIEW_REPORT_FINDINGS
            or not isinstance(report.get("files"), dict)
            or not 1 <= len(report["files"]) <= MAX_REVIEW_REPORT_FILES
    ):
        return None
    for finding in report["findings"]:
        if (not isinstance(finding, dict)
                or set(finding) != set(REVIEW_REPORT_FINDING_FIELDS)
                or not _bounded_path(finding.get("path"))
                or not _nonblank_string(finding.get("comment"), limit=MAX_REVIEW_REPORT_TEXT)):
            return None
    for path, digest in report["files"].items():
        if (not _bounded_path(path)
                or digest is not None and (
                    not isinstance(digest, str)
                    or SHA256_RE.fullmatch(digest) is None
                )):
            return None
    if report["verdict"] == "pass" and report["findings"]:
        return None
    if report["verdict"] == "changes_requested" and not report["findings"]:
        return None
    return report


def find_review_report(comments, *, complete, anchor_prefix, nonce, session_id,
                       pull_number, head_sha, base_sha, source_start_head,
                       source_session_id, source_comment_id, anchor_comment_id,
                       session_created_at, session_completed_at, now):
    if (complete is not True or not isinstance(comments, list)
            or len(comments) > 10000
            or not all(isinstance(comment, dict) for comment in comments)
            or not _nonblank_string(anchor_prefix, limit=256)
            or not all(_nonblank_string(value) for value in (
                nonce, session_id, source_session_id,
            ))
            or type(source_comment_id) is not int or source_comment_id <= 0
            or type(anchor_comment_id) is not int or anchor_comment_id <= 0
            or type(pull_number) is not int or pull_number < 1
            or any(not isinstance(value, str) or SHA_RE.fullmatch(value) is None
                   for value in (head_sha, base_sha, source_start_head))):
        raise ReceiptError("Review report identity is incomplete")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ReceiptError("Review report clock must include a timezone")
    now = now.astimezone(timezone.utc)
    session_time = _time(session_created_at)
    completed_time = _time(session_completed_at)
    if not session_time <= completed_time <= now:
        raise ReceiptError("Review session chronology is invalid")
    found = []
    for comment in comments:
        author, body = comment.get("user"), comment.get("body")
        if (not _identity(author, COPILOT_AGENT_ID)
                or not isinstance(body, str) or nonce not in body):
            continue
        if comment.get("updated_at") != comment.get("created_at"):
            raise ReceiptError("Review report was edited")
        created = _time(comment.get("created_at"))
        if not session_time <= created <= completed_time or created > now:
            raise ReceiptError("Review report is outside the documented session interval")
        report, payload = _reply_transport_json(body, anchor_prefix=anchor_prefix)
        report = _valid_review_report(
            report,
            nonce=nonce,
            session_id=session_id,
            pull_number=pull_number,
            head_sha=head_sha,
            base_sha=base_sha,
            source_start_head=source_start_head,
            source_session_id=source_session_id,
            source_comment_id=source_comment_id,
            anchor_comment_id=anchor_comment_id,
        )
        if report is None:
            raise ReceiptError("Review report fields or bindings do not match")
        comment_id = comment.get("id")
        if type(comment_id) is not int or comment_id <= 0:
            raise ReceiptError("Review report comment identity is malformed")
        found.append({
            "comment_id": comment_id,
            "created_at": comment["created_at"],
            "body": body,
            "payload": payload,
            "report": report,
        })
    if len(found) > 1:
        raise ReceiptError("Conflicting or duplicate review reports")
    return found[0] if found else None


def validate_task_receipt(task, action, pull, comments, *, now):
    task_id = action.get("task_id")
    nonce = action.get("dispatch_nonce")
    created_at = action.get("task_created_at")
    pull_head = pull.get("head") if isinstance(pull, dict) else None
    pull_base = pull.get("base") if isinstance(pull, dict) else None
    if (not _nonblank_string(nonce, limit=128)
            or not _nonblank_string(task_id, limit=128)
            or type(action.get("pull_id")) is not int
            or not _nonblank_string(action.get("pull_node_id"), limit=256)
            or type(action.get("issue")) is not int
            or not isinstance(pull_head, dict) or not isinstance(pull_base, dict)
            or not _identity(pull, action["pull_id"])
            or pull.get("node_id") != action["pull_node_id"]
            or type(pull.get("number")) is not int
            or pull.get("number") != action["issue"]
            or any(not isinstance(value, str) or SHA_RE.fullmatch(value) is None
                   for value in (pull_head.get("sha"), pull_base.get("sha")))):
        raise ReceiptError("Receipt claim or pull identity is incomplete")
    if (not isinstance(task, dict) or task.get("id") != task_id
            or task.get("created_at") != created_at
            or task.get("state") != "completed"
            or not _identity(task.get("creator"), action.get("owner_id"))
            or not _identity(task.get("owner"), action.get("owner_id"))
            or not _identity(task.get("repository"), action.get("repository_id"))):
        raise ReceiptError("Task identity does not match the durable dispatch claim")

    artifacts = task.get("artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 20:
        raise ReceiptError("Task artifacts are unavailable or incomplete")
    branches, pulls = [], []
    for artifact in artifacts:
        if (not isinstance(artifact, dict) or artifact.get("provider") != "github"
                or artifact.get("type") not in {"pull", "branch"}
                or not isinstance(artifact.get("data"), dict)):
            raise ReceiptError("Task artifact is not a documented GitHub artifact")
        data = artifact["data"]
        if artifact["type"] == "branch":
            if (data.get("head_ref") != action.get("head_ref")
                    or data.get("base_ref") != "main"):
                raise ReceiptError("Task branch artifact does not match the dispatch")
            branches.append(data)
        else:
            if (not _identity(data, action["pull_id"])
                    or (data.get("global_id") is not None
                        and data.get("global_id") != action["pull_node_id"])):
                raise ReceiptError("Task pull artifact does not match the enrolled pull request")
            pulls.append(data)
    if len(branches) != 1 or len(pulls) != 1:
        raise ReceiptError("Task must identify exactly one dispatched pull request and branch")

    sessions = task.get("sessions")
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 100:
        raise ReceiptError("Task session evidence is unavailable or incomplete")
    matches = []
    for session in sessions:
        if not isinstance(session, dict):
            raise ReceiptError("Task session evidence is malformed")
        prompt = session.get("prompt")
        if isinstance(prompt, str) and nonce in prompt:
            if (not _nonblank_string(session.get("id"))
                    or session.get("task_id") != task_id
                    or session.get("state") != "completed"
                    or not _identity(session.get("user"), action.get("owner_id"))
                    or not _identity(session.get("owner"), action.get("owner_id"))
                    or not _identity(session.get("repository"), action.get("repository_id"))
                    or session.get("head_ref") != action.get("head_ref")
                    or session.get("base_ref") != "main"):
                raise ReceiptError("Task session does not match the durable dispatch claim")
            matches.append(session)
    if len(matches) != 1:
        raise ReceiptError("Task nonce does not identify exactly one returned session")
    session = matches[0]
    task_updated = _time(task.get("updated_at"))
    completed_at = _time(session.get("completed_at"))
    if task_updated < completed_at or task_updated > now.astimezone(timezone.utc):
        raise ReceiptError("Task update chronology is invalid")
    receipt = find_receipt(
        comments,
        complete=True,
        nonce=nonce,
        task_id=task_id,
        session_id=session["id"],
        pull_number=pull.get("number"),
        start_head=action.get("head"),
        head_sha=pull_head.get("sha"),
        base_sha=pull_base.get("sha"),
        dispatch_base_sha=action.get("main_sha"),
        task_created_at=created_at,
        session_created_at=session.get("created_at"),
        session_completed_at=session.get("completed_at"),
        now=now,
    )
    if receipt is None:
        return None
    # Return chronology only after the exact session and its receipt validate.
    # Task updated_at and coordinator observation time are not completion proof.
    return {
        **receipt,
        "task_id": task_id,
        "session_id": session["id"],
        "nonce": nonce,
        "start_head": action["head"],
        "head": pull_head["sha"],
        "base": action["main_sha"] if receipt.get("version") == "v2" else pull_base["sha"],
        "completed_at": session["completed_at"],
    }
