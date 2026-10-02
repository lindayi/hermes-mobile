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
            "prompt": receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE),
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


def v2_binding(result="ready"):
    task, action, pull, comments = binding()
    action["main_sha"] = BASE
    comments[0]["body"] = receipt_body(result).replace(
        "Hermes-Task-Receipt: v1", "Hermes-Task-Receipt: v2",
    ).replace(f"task={TASK_ID}\n", "")
    return task, action, pull, comments


@pytest.mark.parametrize("result", ["ready", "conflict_incompatible", "policy_broken"])
def test_v2_echoes_session_not_task_but_host_still_binds_saved_task(result):
    task, action, pull, comments = v2_binding(result)
    proof = validate_task_receipt(task, action, pull, comments, now=NOW)
    assert proof["result"] == result
    assert proof["version"] == "v2"
    assert proof["task_id"] == TASK_ID
    assert proof["session_id"] == SESSION_ID
    assert proof["base"] == action["main_sha"]
    assert "task=" not in proof["body"]
    for identity in (task, task["sessions"][0]):
        field = "id" if identity is task else "task_id"
        identity[field] = "unrelated-task"
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)
        identity[field] = TASK_ID


@pytest.mark.parametrize("change", [
    "missing_dispatch_base", "wrong_dispatch_base", "wrong_base", "current_not_dispatch_base",
    "session", "nonce", "pr", "start_head", "head", "result", "mixed_shape",
    "duplicate_field", "duplicate_comment", "mixed_versions", "edited", "trailing_text",
    "before_session", "after_session", "wrong_session_owner", "wrong_session_repository",
])
def test_v2_rejects_unbound_ambiguous_edited_or_out_of_interval_receipts(change):
    task, action, pull, comments = v2_binding()
    pull["base"]["sha"] = "d" * 40
    if change == "missing_dispatch_base":
        del action["main_sha"]
    elif change == "wrong_dispatch_base":
        action["main_sha"] = "e" * 40
    elif change in {"wrong_base", "current_not_dispatch_base"}:
        value = "e" * 40 if change == "wrong_base" else pull["base"]["sha"]
        comments[0]["body"] = comments[0]["body"].replace(f"base={BASE}", f"base={value}")
    elif change in {"session", "nonce", "pr", "start_head", "head", "result"}:
        field_value = {"session": SESSION_ID, "nonce": NONCE, "pr": "16",
                       "start_head": START_HEAD, "head": RESULT_HEAD, "result": "ready"}
        comments[0]["body"] = comments[0]["body"].replace(
            f"{change}={field_value[change]}", f"{change}=wrong",
        )
    elif change == "mixed_shape":
        comments[0]["body"] = comments[0]["body"].replace("session=", f"task={TASK_ID}\nsession=")
    elif change == "duplicate_field":
        comments[0]["body"] += f"\nnonce={NONCE}"
    elif change in {"duplicate_comment", "mixed_versions"}:
        comments.append(dict(comments[0], id=778))
        if change == "mixed_versions":
            comments[-1]["body"] = receipt_body().replace(f"base={BASE}", f"base={pull['base']['sha']}")
    elif change == "edited":
        comments[0]["updated_at"] = "2026-10-01T12:05:01Z"
    elif change == "trailing_text":
        comments[0]["body"] += "\n"
    elif change in {"before_session", "after_session"}:
        timestamp = "2026-10-01T12:00:30Z" if change == "before_session" else "2026-10-01T12:05:01Z"
        comments[0].update(created_at=timestamp, updated_at=timestamp)
    else:
        field = "owner" if change == "wrong_session_owner" else "repository"
        task["sessions"][0][field] = {"id": 42}
    if change == "nonce":
        assert validate_task_receipt(task, action, pull, comments, now=NOW) is None
    else:
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)


def test_v2_first_observation_uses_dispatch_base_while_v1_keeps_initial_semantics():
    task, action, pull, comments = v2_binding()
    pull["base"]["sha"] = "d" * 40
    assert validate_task_receipt(task, action, pull, comments, now=NOW)["base"] == BASE
    comments[0]["body"] = receipt_body()
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)
    # Legacy v1 used main at initial validation, even if different at dispatch.
    comments[0]["body"] = receipt_body().replace(f"base={BASE}", f"base={pull['base']['sha']}")
    assert validate_task_receipt(task, action, pull, comments, now=NOW)["base"] == pull["base"]["sha"]


@pytest.mark.parametrize("author_id", [198982749.0, "198982749", True])
def test_v2_fresh_receipt_requires_strict_numeric_author(author_id):
    task, action, pull, comments = v2_binding()
    comments[0]["user"]["id"] = author_id
    assert validate_task_receipt(task, action, pull, comments, now=NOW) is None


def test_receipt_binds_task_session_nonce_pr_and_result_head():
    task, action, pull, comments = binding()

    proof = validate_task_receipt(task, action, pull, comments, now=NOW)

    assert proof == {
        "result": "ready", "comment_id": 777,
        "created_at": "2026-10-01T12:05:00Z", "body": receipt_body(),
        "task_id": TASK_ID, "session_id": SESSION_ID, "nonce": NONCE,
        "completed_at": "2026-10-01T12:05:00Z",
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
        prompt=receipt_instruction("different-nonce", pull_number=16, start_head=START_HEAD, base_sha=BASE),
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
