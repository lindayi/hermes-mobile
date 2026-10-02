"""Exact, bounded Copilot task completion receipts."""

from datetime import datetime, timezone
import re


COPILOT_AGENT_ID = 198982749
RECEIPT_RESULTS = {"ready", "conflict_incompatible", "policy_broken"}
SHA_RE = re.compile(r"[0-9a-f]{40}")


class ReceiptError(ValueError):
    """A receipt is missing, conflicting, edited, stale, or unbound."""


def receipt_instruction(nonce, *, pull_number, start_head, base_sha):
    return (
        "After pushing your result and running focused checks, post exactly one "
        "issue comment on this PR, using the exact ordered fields below, with no "
        "additional text, code fences or trailing newline. "
        "Copy nonce, pr, start_head and base exactly: base is the fixed dispatch-time "
        "main SHA, not main at completion. Read your session ID from the exposed "
        "COPILOT_AGENT_SESSION_ID environment variable; if it is missing, report an "
        "honest blocker, do not guess an ID or emit a ready receipt. The parent "
        "binds task identity through its authenticated task API; do not discover "
        "or echo a task UUID, obtain extra credentials, or request an owner comment. "
        "Use the pushed PR head SHA and one result from the closed list. "
        "Do not wait for CI or review after pushing and focused checks; the parent "
        "controller handles CI/review. A ready receipt is not passing CI and does "
        "not claim review, merge, or deployment success.\n\n"
        "Hermes-Task-Receipt: v2\n"
        f"nonce={nonce}\n"
        "session=<COPILOT_AGENT_SESSION_ID>\n"
        f"pr={pull_number}\n"
        f"start_head={start_head}\n"
        "head=<current-pull-head-sha>\n"
        f"base={base_sha}\n"
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
        version = "v2" if body.startswith("Hermes-Task-Receipt: v2\n") else "v1"
        receipt_base = dispatch_base_sha if version == "v2" else base_sha
        if not isinstance(receipt_base, str) or SHA_RE.fullmatch(receipt_base) is None:
            raise ReceiptError("Receipt dispatch base is missing")
        matches = [
            result for result in RECEIPT_RESULTS
            if body == _expected_body(
                nonce, task_id, session_id, pull_number,
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
            or not isinstance(pull_head, dict) or not isinstance(pull_base, dict)
            or type(pull.get("id")) is not int
            or pull.get("node_id") != action["pull_node_id"]
            or pull.get("number") != action.get("issue")
            or pull["id"] != action["pull_id"]
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
            if (data.get("id") != action["pull_id"]
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
