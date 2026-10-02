from datetime import datetime, timezone

import pytest

from deploy.task_receipts import (
    ReceiptError, find_receipt, receipt_instruction, validate_task_receipt,
)


NOW = datetime.fromisoformat("2026-10-01T12:06:00+00:00")
NONCE = "sQb2j8J_Wac6hJ5sU1ZWVwO4GdI1xC7r8sVZgg"
TASK_ID = "task-123"
SESSION_ID = "session-456"
HEAD = "a" * 40
BASE = "b" * 40


def receipt_body(result="ready"):
    return (
        "Hermes-Task-Receipt: v1\n"
        f"nonce={NONCE}\n"
        f"task={TASK_ID}\n"
        f"session={SESSION_ID}\n"
        "pr=16\n"
        f"start_head={HEAD}\n"
        f"head={HEAD}\n"
        f"base={BASE}\n"
        f"result={result}"
    )


def receipt(*, author_id=198982749, body=None, created="2026-10-01T12:05:00Z",
            updated=None):
    return {
        "id": 777,
        "user": {"id": author_id},
        "body": receipt_body() if body is None else body,
        "created_at": created,
        "updated_at": created if updated is None else updated,
    }


def parse(comments, *, complete=True, nonce=NONCE, task_id=TASK_ID, session_id=SESSION_ID):
    return find_receipt(
        comments,
        complete=complete,
        nonce=nonce,
        task_id=task_id,
        session_id=session_id,
        pull_number=16,
        start_head=HEAD,
        head_sha=HEAD,
        base_sha=BASE,
        task_created_at="2026-10-01T12:00:00Z",
        session_created_at="2026-10-01T12:01:00Z",
        session_completed_at="2026-10-01T12:05:30Z",
        now=NOW,
    )


def test_receipt_is_full_exact_and_bound_to_task_session_pr_and_heads():
    assert parse([receipt()])["result"] == "ready"
    assert "nonce=" in receipt_instruction(NONCE, pull_number=16, start_head=HEAD, base_sha=BASE)
    assert "session=<COPILOT_AGENT_SESSION_ID>" in receipt_instruction(NONCE, pull_number=16, start_head=HEAD, base_sha=BASE)


@pytest.mark.parametrize("field", [
    "comment_author", "comment_id", "task_creator", "task_owner", "task_repository",
    "session_user", "session_owner", "session_repository", "artifact_pull", "pull_number",
])
@pytest.mark.parametrize("kind", ["float", "string", "bool", "null"])
def test_receipt_numeric_identities_require_exact_integers(completed_task_binding, field, kind):
    import json
    # JSON round-trip separates fixture aliases so each identity is tested alone.
    task, action, pull = json.loads(json.dumps(completed_task_binding))
    comment = receipt()
    targets = {
        "comment_author": (comment["user"], "id"),
        "comment_id": (comment, "id"),
        "task_creator": (task["creator"], "id"),
        "task_owner": (task["owner"], "id"),
        "task_repository": (task["repository"], "id"),
        "session_user": (task["sessions"][0]["user"], "id"),
        "session_owner": (task["sessions"][0]["owner"], "id"),
        "session_repository": (task["sessions"][0]["repository"], "id"),
        "artifact_pull": (task["artifacts"][1]["data"], "id"),
        "pull_number": (pull, "number"),
    }
    target, key = targets[field]
    target[key] = {"float": float(target[key]), "string": str(target[key]),
                   "bool": True, "null": None}[kind]
    if field == "comment_author":
        assert validate_task_receipt(task, action, pull, [comment], now=NOW) is None
        # An unauthenticated copy cannot poison an otherwise valid receipt.
        assert validate_task_receipt(task, action, pull, [comment, receipt()], now=NOW)
    else:
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, [comment], now=NOW)


@pytest.mark.parametrize("comment_id", [None, True, 0, -1, "777"])
def test_receipt_requires_a_positive_numeric_comment_identity(comment_id):
    item = receipt()
    item["id"] = comment_id

    with pytest.raises(ReceiptError):
        parse([item])


@pytest.mark.parametrize("field,value", [
    ("task", "other-task"),
    ("session", "other-session"),
    ("pr", "17"),
    ("start_head", "c" * 40),
    ("head", "c" * 40),
    ("base", "c" * 40),
])
def test_receipt_rejects_a_mismatched_binding(field, value):
    body = receipt_body().replace(
        f"{field}=" + {
            "task": TASK_ID,
            "session": SESSION_ID,
            "pr": "16",
            "start_head": HEAD,
            "head": HEAD,
            "base": BASE,
        }[field],
        f"{field}={value}",
    )
    with pytest.raises(ReceiptError):
        parse([receipt(body=body)])


def test_receipt_ignores_other_authors_and_rejects_copied_or_edited_receipts():
    assert parse([receipt(author_id=123)]) is None
    copied = "quoted receipt:\n" + receipt_body()
    with pytest.raises(ReceiptError):
        parse([receipt(body=copied)])
    with pytest.raises(ReceiptError):
        parse([receipt(updated="2026-10-01T12:05:10Z")])


@pytest.mark.parametrize("kwargs", [
    {"created": "2026-10-01T11:59:59Z"},
    {"created": "2026-10-01T12:05:31Z"},
    {"created": "2026-10-01T12:06:01Z"},
])
def test_receipt_must_be_within_documented_task_session_and_current_times(kwargs):
    with pytest.raises(ReceiptError):
        parse([receipt(**kwargs)])


def test_incomplete_pages_or_conflicting_receipts_never_yield_success():
    with pytest.raises(ReceiptError):
        parse([receipt()], complete=False)
    with pytest.raises(ReceiptError):
        parse([receipt(), receipt(body=receipt_body("policy_broken"))])


def test_only_closed_result_codes_are_accepted():
    with pytest.raises(ReceiptError):
        parse([receipt(body=receipt_body("success"))])


@pytest.mark.parametrize("field", ["nonce", "task_id", "session_id"])
@pytest.mark.parametrize("invalid", ["", " \t\n", None, 123, True, {}, []])
def test_receipt_rejects_blank_or_nonstring_identity(field, invalid):
    expected = {"nonce": NONCE, "task_id": TASK_ID, "session_id": SESSION_ID}
    body = receipt_body().replace(expected[field], str(invalid))
    with pytest.raises(ReceiptError, match="identity is incomplete"):
        parse([receipt(body=body)], **{field: invalid})


@pytest.fixture
def completed_task_binding():
    owner = {"id": 5164171}
    repository = {"id": 1399942965}
    action = {
        "issue": 16, "task_id": TASK_ID, "dispatch_nonce": NONCE,
        "task_created_at": "2026-10-01T12:00:00Z",
        "owner_id": owner["id"], "repository_id": repository["id"],
        "pull_id": 160000016, "pull_node_id": "PR_node_16",
        "head_ref": "topic", "head": HEAD,
    }
    task = {
        "id": TASK_ID, "state": "completed", "creator": owner, "owner": owner,
        "repository": repository, "created_at": action["task_created_at"],
        "updated_at": "2026-10-01T12:05:30Z",
        "artifacts": [
            {"provider": "github", "type": "branch",
             "data": {"head_ref": "topic", "base_ref": "main"}},
            {"provider": "github", "type": "pull",
             "data": {"id": action["pull_id"], "global_id": action["pull_node_id"]}},
        ],
        "sessions": [{
            "id": SESSION_ID, "task_id": TASK_ID, "state": "completed",
            "user": owner, "owner": owner, "repository": repository,
            "head_ref": "topic", "base_ref": "main", "prompt": receipt_instruction(NONCE, pull_number=16, start_head=HEAD, base_sha=BASE),
            "created_at": "2026-10-01T12:01:00Z",
            "completed_at": "2026-10-01T12:05:30Z",
        }],
    }
    pull = {"number": 16, "id": action["pull_id"], "node_id": action["pull_node_id"],
            "head": {"sha": HEAD}, "base": {"sha": BASE}}
    return task, action, pull


@pytest.mark.parametrize("artifact_type", ["pull", "branch"])
@pytest.mark.parametrize("change", ["missing", "duplicate", "conflicting", "conflicting_duplicate"])
def test_completed_task_requires_exactly_one_matching_artifact(
        completed_task_binding, artifact_type, change):
    from copy import deepcopy
    task, action, pull = completed_task_binding
    artifact = next(item for item in task["artifacts"] if item["type"] == artifact_type)
    if change == "missing":
        task["artifacts"].remove(artifact)
    elif change == "duplicate":
        task["artifacts"].append(deepcopy(artifact))
    else:
        if change == "conflicting_duplicate":
            artifact = deepcopy(artifact)
            task["artifacts"].append(artifact)
        key, value = ("id", 160000017) if artifact_type == "pull" else ("head_ref", "other")
        artifact["data"][key] = value
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, [receipt()], now=NOW)


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", 160000017), ("id", 160000016.0),
    ("id", "160000016"), ("id", True),
    ("node_id", None), ("node_id", "PR_node_other"),
    ("number", None), ("number", 17), ("number", 16.0),
])
def test_completed_task_rejects_actual_pull_mismatch(completed_task_binding, field, value):
    task, action, pull = completed_task_binding
    if value is None:
        pull.pop(field)
    else:
        pull[field] = value
    # Even a receipt agreeing with the supplied pull cannot override the durable action.
    body = receipt_body().replace("pr=16\n", f"pr={pull.get('number')}\n")
    with pytest.raises(ReceiptError, match="Receipt claim or pull identity is incomplete"):
        validate_task_receipt(task, action, pull, [receipt(body=body)], now=NOW)


@pytest.mark.parametrize("field,value", [
    ("pull_id", None), ("pull_id", 160000016.0),
    ("pull_node_id", None), ("pull_node_id", ""),
    ("issue", None), ("issue", 16.0),
])
def test_completed_task_requires_durable_pull_identity(completed_task_binding, field, value):
    task, action, pull = completed_task_binding
    action[field] = value
    # Do not let equally missing or numerically aliased metadata establish identity.
    pull[{"pull_id": "id", "pull_node_id": "node_id", "issue": "number"}[field]] = value
    if field in {"pull_id", "pull_node_id"}:
        task["artifacts"][1]["data"]["id" if field == "pull_id" else "global_id"] = value
    with pytest.raises(ReceiptError, match="Receipt claim or pull identity is incomplete"):
        validate_task_receipt(task, action, pull, [receipt()], now=NOW)


@pytest.mark.parametrize("field", ["task_id", "session_id", "nonce"])
@pytest.mark.parametrize("invalid", ["", " \t\n", None, 123, True, {}, []])
def test_completed_task_rejects_blank_or_nonstring_identity(completed_task_binding, field, invalid):
    task, action, pull = completed_task_binding
    session = task["sessions"][0]
    if field == "task_id":
        task["id"] = action["task_id"] = session["task_id"] = invalid
    elif field == "session_id":
        session["id"] = invalid
    else:
        action["dispatch_nonce"] = invalid
        session["prompt"] = receipt_instruction(invalid, pull_number=16, start_head=HEAD, base_sha=BASE)
    expected = {"nonce": NONCE, "task_id": TASK_ID, "session_id": SESSION_ID}
    body = receipt_body().replace(expected[field], str(invalid))
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, [receipt(body=body)], now=NOW)


@pytest.mark.parametrize("completed_at", [
    None, "invalid", "2026-10-01T12:05:30",
    "2026-10-01T12:00:30Z", "2026-10-01T12:04:59Z", "2026-10-01T12:06:01Z",
])
def test_completed_task_rejects_unproven_completion_chronology(
        completed_task_binding, completed_at):
    task, action, pull = completed_task_binding
    task["sessions"][0]["completed_at"] = completed_at
    # A recent mutable task timestamp cannot substitute for session chronology.
    task["updated_at"] = "2026-10-01T12:07:00Z"
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, [receipt()], now=NOW)


def test_completed_task_accepts_complete_exact_returned_identities(completed_task_binding):
    task, action, pull = completed_task_binding
    # IDs are opaque: retain the entire returned value, without normalization.
    task_id = "task_01234567-89ab-cdef-0123-456789abcdef"
    session_id = "session_abcdef01-2345-6789-abcd-ef0123456789"
    task["id"] = action["task_id"] = task["sessions"][0]["task_id"] = task_id
    task["sessions"][0]["id"] = session_id
    body = receipt_body().replace(TASK_ID, task_id).replace(SESSION_ID, session_id)
    assert validate_task_receipt(task, action, pull, [receipt(body=body)], now=NOW) == {
        "result": "ready", "comment_id": 777,
        "created_at": "2026-10-01T12:05:00Z", "body": body,
        "task_id": task_id, "session_id": session_id, "nonce": NONCE,
        "completed_at": "2026-10-01T12:05:30Z",
        "start_head": HEAD, "head": HEAD, "base": BASE,
    }
    for identity in (task_id, session_id):
        with pytest.raises(ReceiptError):
            validate_task_receipt(
                task, action, pull, [receipt(body=body.replace(identity, identity[:-1]))], now=NOW,
            )
