from datetime import datetime, timezone

import pytest

from deploy.task_receipts import ReceiptError, receipt_instruction, validate_task_receipt


NOW = datetime.fromisoformat("2026-10-01T12:06:00+00:00")
NONCE = "sQb2j8J_Wac6hJ5sU1ZWVwO4GdI1xC7r8sVZgg"
TASK_ID = "task-123"
SESSION_ID = "session-456"
START_HEAD = "a" * 40
RESULT_HEAD = "c" * 40
BASE = "b" * 40


def receipt_body(result="ready"):
    return (
        "Hermes-Task-Receipt: v1\n"
        f"nonce={NONCE}\n"
        f"task={TASK_ID}\n"
        f"session={SESSION_ID}\n"
        "pr=16\n"
        f"start_head={START_HEAD}\n"
        f"head={RESULT_HEAD}\n"
        f"base={BASE}\n"
        f"result={result}"
    )


def binding():
    owner = {"id": 5164171}
    repository = {"id": 1399942965}
    action = {
        "issue": 16, "task_id": TASK_ID, "task_created_at": "2026-10-01T12:00:00Z",
        "dispatch_nonce": NONCE, "owner_id": owner["id"],
        "repository_id": repository["id"], "pull_id": 160000016,
        "pull_node_id": "PR_node_16", "head_ref": "topic", "head": START_HEAD,
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
            "head_ref": "topic", "base_ref": "main",
            "prompt": receipt_instruction(NONCE),
            "created_at": "2026-10-01T12:01:00Z",
            "completed_at": "2026-10-01T12:05:00Z",
        }],
    }
    pull = {
        "number": 16, "id": action["pull_id"], "node_id": action["pull_node_id"],
        "head": {"sha": RESULT_HEAD}, "base": {"sha": BASE},
    }
    comments = [{
        "id": 777, "user": {"id": 198982749}, "body": receipt_body(),
        "created_at": "2026-10-01T12:05:00Z",
        "updated_at": "2026-10-01T12:05:00Z",
    }]
    return task, action, pull, comments


def test_receipt_binds_task_session_nonce_pr_and_result_head():
    task, action, pull, comments = binding()

    proof = validate_task_receipt(task, action, pull, comments, now=NOW)

    assert proof == {
        "result": "ready", "comment_id": 777,
        "created_at": "2026-10-01T12:05:00Z", "body": receipt_body(),
        "task_id": TASK_ID, "session_id": SESSION_ID, "nonce": NONCE,
        "start_head": START_HEAD, "head": RESULT_HEAD, "base": BASE,
    }


@pytest.mark.parametrize(("change", "expected"), [
    (lambda task, action, pull, comments: comments[0].update(
        updated_at="2026-10-01T12:05:01Z",
    ), "error"),
    (lambda task, action, pull, comments: comments[0].update(user={"id": 42}), None),
    (lambda task, action, pull, comments: comments[0].update(id=True), "error"),
    (lambda task, action, pull, comments: task["sessions"][0].update(id=""), "error"),
    (lambda task, action, pull, comments: task["sessions"][0].update(
        prompt=receipt_instruction("different-nonce"),
    ), "error"),
    (lambda task, action, pull, comments: pull["head"].update(sha="d" * 40), "error"),
    (lambda task, action, pull, comments: comments[0].update(
        body=receipt_body("policy_broken"),
    ), "policy_broken"),
])
def test_receipt_handles_edited_wrong_identity_and_typed_result(change, expected):
    task, action, pull, comments = binding()
    change(task, action, pull, comments)

    if expected == "error":
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)
    else:
        proof = validate_task_receipt(task, action, pull, comments, now=NOW)
        if expected is None:
            assert proof is None
        else:
            assert proof["result"] == expected


def test_missing_or_conflicting_receipts_do_not_prove_task_completion():
    task, action, pull, comments = binding()
    assert validate_task_receipt(task, action, pull, [], now=NOW) is None

    comments.append(comments[0] | {
        "id": 778, "body": receipt_body("policy_broken"),
    })
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)


def test_receipt_rejects_incomplete_task_session_and_artifact_evidence():
    task, action, pull, comments = binding()
    task["sessions"] = []
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)

    task, action, pull, comments = binding()
    task["artifacts"].pop()
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)
