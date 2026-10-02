from datetime import datetime, timezone

import pytest

from deploy.task_receipts import ReceiptError, find_receipt, receipt_instruction


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


def parse(comments, *, complete=True):
    return find_receipt(
        comments,
        complete=complete,
        nonce=NONCE,
        task_id=TASK_ID,
        session_id=SESSION_ID,
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
    assert "nonce=" in receipt_instruction(NONCE)
    assert "task=" in receipt_instruction(NONCE)


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
