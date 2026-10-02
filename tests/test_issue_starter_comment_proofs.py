"""Exact public receipt proofs, including real producer-to-consumer evidence."""
from datetime import datetime, timezone

import pytest

from deploy.issue_starter import Coordinator, MAX_READ_FAILURES, StateStore
from deploy.workflow_lifecycle_sources import _issue_starter_events
from test_issue_starter import (
    enrollment_command, FakeApi, OWNER_ID, REPOSITORY, completed_task, issue_comment, pull_request,
    start_task, task,
)


NOW = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)
KEY = "28:9001"
INVALID_PROOFS = [
    "truncated", "prefix", "suffix", "missing_id", "zero_id", "negative_id",
    "bool_id", "float_id", "string_id", "missing_user", "nonowner",
    "float_owner", "string_owner", "missing_created", "missing_updated",
    "edited", "invalid_time", "naive_time", "before_acceptance", "future_time",
]


def corrupt(comment, change):
    if change == "truncated":
        comment["body"] = comment["body"].split("\n", 1)[0]
    elif change == "prefix":
        comment["body"] = "Copied: " + comment["body"]
    elif change == "suffix":
        comment["body"] += "\nextra"
    elif change.startswith("missing_"):
        comment.pop({"missing_created": "created_at", "missing_updated": "updated_at"}
                    .get(change, change.removeprefix("missing_")))
    elif change.endswith("_id"):
        comment["id"] = {"zero_id": 0, "negative_id": -1, "bool_id": True,
                         "float_id": 9102.0, "string_id": "9102"}[change]
    elif change in {"nonowner", "float_owner", "string_owner"}:
        comment["user"]["id"] = {"nonowner": 7, "float_owner": float(OWNER_ID),
                                  "string_owner": str(OWNER_ID)}[change]
    elif change == "edited":
        comment["updated_at"] = "2026-10-01T21:01:00Z"
    else:
        comment["created_at"] = comment["updated_at"] = {
            "invalid_time": "not-a-date", "naive_time": "2026-10-01T21:00:00",
            "before_acceptance": "2026-10-01T19:59:59Z",
            "future_time": "2026-10-01T22:00:01Z",
        }[change]


class ReceiptApi(FakeApi):
    def get_all(self, route, *, collection=None):
        assert route == f"repos/{REPOSITORY}/issues/28/comments?per_page=100"
        assert collection is None
        return self.comments


@pytest.mark.parametrize("path", ["response", "lost_response", "existing"])
@pytest.mark.parametrize("change", [c for c in INVALID_PROOFS
                                    if c not in {"truncated", "before_acceptance"}])
def test_invalid_enrollment_proof_never_hands_off_or_reposts(tmp_path, path, change):
    class CorruptEnrollmentApi(FakeApi):
        def post(self, route, body):
            response = super().post(route, body)
            if route.endswith("/issues/41/comments"):
                corrupt(response, change)
                if path in {"lost_response", "existing"}:
                    raise TimeoutError("synthetic response lost")
            return response

    api = CorruptEnrollmentApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    if path == "existing":
        comment = issue_comment(comment_id=9100, body=enrollment_command(),
                                created_at="2026-10-01T21:00:00Z")
        corrupt(comment, change)
        api.comments.append(comment)
    for _ in range(4):
        coordinator = worker(tmp_path, api)
        assert coordinator.run(apply=True)["handed_off"] == 0
        saved = coordinator.store.snapshot()["commands"][KEY]
        assert saved["phase"] != "handed_off"
        assert saved.get("enrollment_state") != "done"
    assert saved["phase"] == "handoff_failed"
    assert len([r for r, _ in api.posts if r.endswith("/issues/41/comments")]) == 1
    assert not any("completed cloud work" in b["body"] for r, b in api.posts
                   if r.endswith("/comments"))


def worker(tmp_path, api):
    return Coordinator(api, StateStore(tmp_path / "private" / "issue-starter.json"),
                       clock=lambda: NOW)


@pytest.mark.parametrize("kind", ["blocked", "completed", "started"])
@pytest.mark.parametrize("path", ["response", "sending", "uncertain"])
@pytest.mark.parametrize("change", INVALID_PROOFS)
def test_invalid_receipt_proof_never_becomes_sent_or_reposts(tmp_path, kind, path, change):
    class CorruptApi(ReceiptApi):
        def post(self, route, body):
            response = super().post(route, body)
            if route.endswith("/issues/28/comments"):
                corrupt(response, change)
            return response

    api = CorruptApi()
    start_task(tmp_path, api)
    coordinator = worker(tmp_path, api)
    record = coordinator.store.snapshot()["commands"][KEY]
    receipt = coordinator._receipt(kind, record, pull_number=41)
    if path != "response":
        receipt["state"] = path
        comment = issue_comment(comment_id=9102,
                                body=coordinator._receipt_text(record, receipt),
                                created_at="2026-10-01T21:00:00Z")
        corrupt(comment, change)
        api.comments.append(comment)
    coordinator.store.update(KEY, {"receipt": receipt})

    # A pending optional receipt must not prevent polling the actual task.
    for count in range(MAX_READ_FAILURES + 2):
        before = api.task_detail_reads
        assert worker(tmp_path, api).run(apply=True)["pending"] == 1
        assert api.task_detail_reads > before
        saved = coordinator.store.snapshot()["commands"][KEY]["receipt"]
        assert saved["state"] != "sent"
    assert saved["state"] == "abandoned"
    assert saved["lookup_failures"] == MAX_READ_FAILURES
    assert len([r for r, _ in api.posts if r.endswith("/issues/28/comments")]) == (
        1 if path == "response" else 0
    )


@pytest.mark.parametrize("change", ["truncated", "prefix", "suffix", "edited", "missing_id"])
def test_reserved_receipt_with_damaged_owner_marker_is_not_reposted(tmp_path, change):
    api = ReceiptApi()
    start_task(tmp_path, api)
    coordinator = worker(tmp_path, api)
    record = coordinator.store.snapshot()["commands"][KEY]
    receipt = coordinator._receipt("blocked", record)
    comment = issue_comment(comment_id=9102, body=coordinator._receipt_text(record, receipt),
                            created_at="2026-10-01T21:00:00Z")
    corrupt(comment, change)
    api.comments.append(comment)
    coordinator.store.update(KEY, {"receipt": receipt})
    for _ in range(MAX_READ_FAILURES + 1):
        assert worker(tmp_path, api).run(apply=True)["pending"] == 1
        assert coordinator.store.snapshot()["commands"][KEY]["receipt"]["state"] != "sent"
    assert coordinator.store.snapshot()["commands"][KEY]["receipt"]["state"] == "abandoned"
    assert not any(r.endswith("/issues/28/comments") for r, _ in api.posts)


@pytest.mark.parametrize("kind", ["blocked", "completed", "started"])
@pytest.mark.parametrize("path", ["response", "reserved", "uncertain"])
def test_exact_receipt_proof_is_accepted_without_duplicate(tmp_path, kind, path):
    api = ReceiptApi()
    start_task(tmp_path, api)
    coordinator = worker(tmp_path, api)
    record = coordinator.store.snapshot()["commands"][KEY]
    receipt = coordinator._receipt(kind, record, pull_number=41)
    if path != "response":
        receipt["state"] = path
        api.comments.append(issue_comment(
            comment_id=9102, body=coordinator._receipt_text(record, receipt),
            created_at="2026-10-01T21:00:00Z",
        ))
    coordinator.store.update(KEY, {"receipt": receipt})
    for _ in range(2):
        assert worker(tmp_path, api).run(apply=True)["pending"] == 1
        assert coordinator.store.snapshot()["commands"][KEY]["receipt"]["state"] == "sent"
    assert len([r for r, _ in api.posts if r.endswith("/issues/28/comments")]) == (
        1 if path == "response" else 0
    )


@pytest.mark.parametrize("change", ["truncated", "edited"])
@pytest.mark.parametrize("response_lost", [False, True])
def test_damaged_actual_blocker_is_not_claimed_as_delivered(tmp_path, change, response_lost):
    class DamagedApi(ReceiptApi):
        def post(self, route, body):
            response = super().post(route, body)
            if route.endswith("/issues/28/comments"):
                corrupt(response, change)
                if response_lost:
                    raise TimeoutError("synthetic response lost")
            return response

    api = DamagedApi()
    start_task(tmp_path, api)
    api.task_detail = task(state="failed")
    coordinator = worker(tmp_path, api)
    for _ in range(MAX_READ_FAILURES + 2):
        worker(tmp_path, api).run(apply=True)
        record = coordinator.store.snapshot()["commands"][KEY]
        assert record["phase"] == "failed"
        assert record["receipt"]["state"] in {"uncertain", "abandoned"}
        assert _issue_starter_events(coordinator.store.path, api, NOW) == []
    assert record["receipt"]["state"] == "abandoned"
    assert len([r for r, _ in api.posts if r.endswith("/issues/28/comments")]) == 1


@pytest.mark.parametrize("path", ["response", "lost_response"])
def test_actual_blocker_receipt_reaches_lifecycle_consumer(tmp_path, path):
    class LostApi(ReceiptApi):
        def post(self, route, body):
            response = super().post(route, body)
            if path == "lost_response" and route.endswith("/issues/28/comments"):
                raise TimeoutError("synthetic response lost")
            return response

    api = LostApi()
    start_task(tmp_path, api)
    api.task_detail = task(state="failed")
    coordinator = worker(tmp_path, api)
    assert coordinator.run(apply=True)["blocked"] == 1
    if path == "lost_response":
        assert coordinator.store.snapshot()["commands"][KEY]["receipt"]["state"] == "uncertain"
        assert _issue_starter_events(coordinator.store.path, api, NOW) == []
        worker(tmp_path, api).run(apply=True)
    assert coordinator.store.snapshot()["commands"][KEY]["receipt"]["state"] == "sent"
    events = _issue_starter_events(coordinator.store.path, api, NOW)
    assert len(events) == 1
    assert events[0]["reason"] == "issue_failed"
    assert events[0]["occurred_at"] == "2026-10-01T21:00:00Z"
    worker(tmp_path, api).run(apply=True)
    assert _issue_starter_events(coordinator.store.path, api, NOW) == events
    assert len([r for r, _ in api.posts if r.endswith("/issues/28/comments")]) == 1
