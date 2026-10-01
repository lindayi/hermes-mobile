"""Exact, bounded Copilot task completion receipts."""

from datetime import datetime, timezone


COPILOT_AGENT_ID = 198982749
RECEIPT_RESULTS = {"ready", "conflict_incompatible", "policy_broken"}


class ReceiptError(ValueError):
    """A receipt is missing, conflicting, edited, stale, or unbound."""


def receipt_instruction(nonce):
    return (
        "When the task is complete, post exactly one issue comment on this PR, "
        "with no additional text, using the following fields. Use the exact task "
        "and session IDs returned by GitHub, the current PR head and main base SHAs, "
        "and one result from the closed list. This is a receipt only; do not claim "
        "CI, review, merge, or deployment success.\n\n"
        "Hermes-Task-Receipt: v1\n"
        f"nonce={nonce}\n"
        "task=<returned-task-id>\n"
        "session=<returned-session-id>\n"
        "pr=<exact-pull-number>\n"
        "start_head=<dispatched-head-sha>\n"
        "head=<current-pull-head-sha>\n"
        "base=<current-main-sha>\n"
        "result=ready|conflict_incompatible|policy_broken"
    )


def _time(value):
    if not isinstance(value, str):
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


def _expected_body(nonce, task_id, session_id, pull_number, start_head,
                   head_sha, base_sha, result):
    return (
        "Hermes-Task-Receipt: v1\n"
        f"nonce={nonce}\n"
        f"task={task_id}\n"
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
                 now):
    if complete is not True:
        raise ReceiptError("Receipt comments are not completely paginated")
    if (not isinstance(comments, list) or not nonce or not isinstance(task_id, str)
            or not isinstance(session_id, str) or type(pull_number) is not int
            or pull_number < 1):
        raise ReceiptError("Receipt identity is incomplete")
    if now.tzinfo is None:
        raise ReceiptError("Receipt clock must include a timezone")
    now = now.astimezone(timezone.utc)
    task_time = _time(task_created_at)
    session_time = _time(session_created_at)
    completed_time = _time(session_completed_at)
    if not task_time <= session_time <= completed_time <= now:
        raise ReceiptError("Task session chronology is invalid")

    found = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        author = comment.get("user")
        body = comment.get("body")
        if (not isinstance(author, dict) or author.get("id") != COPILOT_AGENT_ID
                or not isinstance(body, str) or nonce not in body):
            continue
        if comment.get("updated_at") != comment.get("created_at"):
            raise ReceiptError("Task receipt was edited")
        created = _time(comment.get("created_at"))
        if not session_time <= created <= completed_time or created > now:
            raise ReceiptError("Task receipt is outside the documented session interval")
        matches = [
            result for result in RECEIPT_RESULTS
            if body == _expected_body(
                nonce, task_id, session_id, pull_number, start_head,
                head_sha, base_sha, result,
            )
        ]
        if len(matches) != 1:
            raise ReceiptError("Task receipt fields or result do not match")
        found.append({"result": matches[0], "comment_id": comment.get("id")})
    if len(found) > 1:
        raise ReceiptError("Conflicting or duplicate task receipts")
    return found[0] if found else None


def validate_task_receipt(task, action, pull, comments, *, now):
    task_id = action.get("task_id")
    nonce = action.get("dispatch_nonce")
    created_at = action.get("task_created_at")
    pull_head = pull.get("head") if isinstance(pull, dict) else None
    pull_base = pull.get("base") if isinstance(pull, dict) else None
    if (not isinstance(nonce, str) or not nonce
            or not isinstance(pull_head, dict) or not isinstance(pull_base, dict)):
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
    branches = []
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
        elif (data.get("id") != action.get("pull_id")
              or (data.get("global_id") is not None
                  and data.get("global_id") != action.get("pull_node_id"))):
            raise ReceiptError("Task pull artifact does not match the enrolled pull request")
    if len(branches) != 1:
        raise ReceiptError("Task must identify exactly one dispatched branch")

    sessions = task.get("sessions")
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 100:
        raise ReceiptError("Task session evidence is unavailable or incomplete")
    matches = []
    for session in sessions:
        if not isinstance(session, dict):
            raise ReceiptError("Task session evidence is malformed")
        prompt = session.get("prompt")
        if isinstance(prompt, str) and nonce in prompt:
            if (session.get("task_id") != task_id
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
    if task_updated < completed_at:
        raise ReceiptError("Task update predates the completed session")
    receipt = find_receipt(
        comments,
        complete=True,
        nonce=nonce,
        task_id=task_id,
        session_id=session.get("id"),
        pull_number=pull.get("number"),
        start_head=action.get("head"),
        head_sha=pull_head.get("sha"),
        base_sha=pull_base.get("sha"),
        task_created_at=created_at,
        session_created_at=session.get("created_at"),
        session_completed_at=session.get("completed_at"),
        now=now,
    )
    if receipt is None:
        return None
    return receipt
