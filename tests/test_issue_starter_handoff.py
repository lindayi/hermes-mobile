"""Exercise actual starter output through the paired coordinator source scanner."""
import pytest

from deploy import cloud_coordinator

from test_issue_starter import (
    FakeApi, REPOSITORY, completed_task, issue_comment, make_coordinator,
    pull_request, start_task,
)


@pytest.fixture
def consumer():
    return cloud_coordinator


@pytest.mark.parametrize("race", ["none", "before_post", "after_post"])
def test_real_starter_command_is_sha_bound_at_consumer_scan(tmp_path, consumer, race):
    class PushApi(FakeApi):
        def post(self, route, body):
            if route.endswith("/issues/41/comments") and race == "before_post":
                self.pulls[0]["head"]["sha"] = "c" * 40
            return super().post(route, body)

    api = PushApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 1
    emitted = next(c for c in api.comments if c["id"] == 9101)
    assert emitted["body"] == "/hermes enroll " + "a" * 40
    if race == "after_post":
        api.pulls[0]["head"]["sha"] = "c" * 40

    class ScanApi:
        def get_all(self, route, *, collection=None):
            if route.startswith(f"repos/{REPOSITORY}/issues?"):
                return [{"number": 41, "pull_request": {"url": "pull/41"}}]
            if route.startswith(f"repos/{REPOSITORY}/issues/41/comments?"):
                return [emitted]  # Actual producer response, not an invented command.
            raise AssertionError(route)

        def get(self, route):
            assert route == f"repos/{REPOSITORY}/pulls/41"
            pull = api.pulls[0]
            return {**pull, "base": {**pull["base"], "sha": "b" * 40}}

    store = consumer.StateStore(tmp_path / "consumer" / "state.json")
    worker = consumer.Coordinator(ScanApi(), store)
    _, commands, processed, candidates = worker._scan_enrollments(store.snapshot())
    assert processed == [str(emitted["id"])]
    assert bool(commands) is (race == "none")
    assert bool(candidates) is (race == "none")
    if race == "none":
        assert candidates["41"]["head"] == "a" * 40
    store.commit_scan("2026-10-01T22:00:00Z", processed, commands=commands)
    # A mismatching command is consumed permanently, never rebound on a later scan.
    api.pulls[0]["head"]["sha"] = "a" * 40
    _, replay, processed_again, _ = worker._scan_enrollments(store.snapshot())
    assert replay == []
    assert processed_again == []


@pytest.mark.parametrize("outcome", ["response", "lost", "crash"])
@pytest.mark.parametrize("visible", [True, False])
def test_consumed_historical_enrollment_requires_fresh_handoff(tmp_path, consumer, outcome, visible):
    historical = issue_comment(comment_id=9100, body="/hermes enroll " + "a" * 40)

    class EnrollmentApi(FakeApi):
        def get(self, route):
            comments = super().get(route)
            if route.startswith(f"repos/{REPOSITORY}/issues/41/comments?"):
                return [c for c in comments if c["id"] != 9101 or visible]
            return comments

        def post(self, route, body):
            if route.endswith("/issues/41/comments"):
                saved = make_coordinator(tmp_path, self).store.snapshot()["commands"]["28:9001"]
                assert saved["enrollment_state"] == "started"
                assert saved["comment_high_water"] == historical["id"]
            response = super().post(route, body)
            if route.endswith("/issues/41/comments"):
                if outcome == "lost":
                    raise TimeoutError("synthetic response loss")
                if outcome == "crash":
                    raise KeyboardInterrupt("synthetic crash after POST")
            return response

    api = EnrollmentApi(pulls=[pull_request(head_sha="c" * 40)])
    api.comments.append(historical)

    class ScanApi:
        def get_all(self, route, *, collection=None):
            if route.startswith(f"repos/{REPOSITORY}/issues?"):
                return [{"number": 41, "pull_request": {"url": "pull/41"}}]
            if route.startswith(f"repos/{REPOSITORY}/issues/41/comments?"):
                return [c for c in api.comments if c["body"].startswith("/hermes enroll ")]
            raise AssertionError(route)

        def get(self, route):
            assert route == f"repos/{REPOSITORY}/pulls/41"
            return api.pulls[0]

    store = consumer.StateStore(tmp_path / "consumer" / "state.json")

    def scan():
        worker = consumer.Coordinator(ScanApi(), consumer.StateStore(store.path))
        _, commands, processed, candidates = worker._scan_enrollments(store.snapshot())
        store.commit_scan("2026-10-01T22:00:00Z", processed, commands=commands)
        return commands, processed, candidates

    # The real consumer permanently consumes the old exact command on another head.
    assert scan() == ([], [str(historical["id"])], {})
    api.pulls[0]["head"]["sha"] = "a" * 40
    assert scan() == ([], [], {})
    assert store.snapshot()["enrollments"] == {}
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    starter = make_coordinator(tmp_path, api)
    if outcome == "crash":
        with pytest.raises(KeyboardInterrupt, match="synthetic crash"):
            starter.run(apply=True)
    else:
        assert starter.run(apply=True)["handed_off"] == (outcome == "response")
    assert len([r for r, _ in api.posts if r.endswith("/issues/41/comments")]) == 1
    saved = starter.store.snapshot()["commands"]["28:9001"]
    assert saved["comment_high_water"] == historical["id"]
    assert saved["enrollment_state"] == {
        "response": "done", "lost": "uncertain", "crash": "started",
    }[outcome]

    # Restart may reconcile only the fresh comment, not historical proof or a retry.
    expected = outcome == "response" or visible
    for _ in range(3):
        make_coordinator(tmp_path, api).run(apply=True)
        saved = starter.store.snapshot()["commands"]["28:9001"]
        assert (saved["phase"] == "handed_off") is expected
        assert (saved["enrollment_state"] == "done") is expected
    assert len([r for r, _ in api.posts if r.endswith("/issues/41/comments")]) == 1
    assert len([r for r, _ in api.posts if r.endswith("/tasks")]) == 1
    assert len([c for c in api.graphql_calls if "markPullRequestReadyForReview" in c["query"]]) == 1

    # Only the actual fresh producer output can now enroll the consumer.
    commands, processed, candidates = scan()
    assert processed == ["9101"]
    assert len(commands) == 1 and candidates["41"]["head"] == "a" * 40
    enrollment = store.snapshot()["enrollments"]["41"]
    assert enrollment["authorized_head"] == "a" * 40
    assert enrollment["sensitive_sha"] is None
    assert scan()[1] == []


@pytest.mark.parametrize("comment_id", [9099, 9100])
def test_historical_post_response_is_not_fresh_enrollment_proof(tmp_path, comment_id):
    historical = issue_comment(comment_id=comment_id, body="/hermes enroll " + "a" * 40)

    class HistoricalResponseApi(FakeApi):
        def post(self, route, body):
            if route.endswith("/issues/41/comments"):
                self.posts.append((route, body))
                return historical
            return super().post(route, body)

    api = HistoricalResponseApi(pulls=[pull_request(draft=False)])
    api.comments.append(historical)
    if comment_id < 9100:
        api.comments.append(issue_comment(comment_id=9100, body="Unrelated comment"))
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    for _ in range(3):
        starter = make_coordinator(tmp_path, api)
        assert starter.run(apply=True)["handed_off"] == 0
        saved = starter.store.snapshot()["commands"]["28:9001"]
        assert saved["comment_high_water"] == 9100
        assert saved["enrollment_state"] == "uncertain"
    assert len([r for r, _ in api.posts if r.endswith("/issues/41/comments")]) == 1


def test_legacy_bare_comment_is_not_starter_handoff_proof(tmp_path):
    api = FakeApi(pulls=[pull_request()])
    api.comments.append({"id": 9009, "body": "/hermes enroll",
                         "user": {"id": 5164171}, "created_at": "2026-10-01T20:30:00Z"})
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 1
    assert [body["body"] for route, body in api.posts
            if route.endswith("/issues/41/comments")] == ["/hermes enroll " + "a" * 40]


@pytest.mark.parametrize("change", ["title", "body", "renamed", "body_edit"])
def test_issue_integrity_is_rechecked_before_handoff(tmp_path, change):
    api = FakeApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    if change in {"title", "body"}:
        api.current_issue[change] += " changed"
    elif change == "renamed":
        api.timeline.append({"event": "renamed", "created_at": "2026-10-01T20:30:00Z",
                             "rename": {"from": "Old title", "to": api.current_issue["title"]}})
    else:
        api.edit_evidence["lastEditedAt"] = "2026-10-01T20:30:00Z"
        api.edit_evidence["userContentEdits"]["nodes"] = [{"editedAt": "2026-10-01T20:30:00Z"}]
    assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 0
    assert not any("markPullRequestReadyForReview" in c["query"] for c in api.graphql_calls)
    assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)


@pytest.mark.parametrize("proof", ["bare", "different_sha", "nonowner"])
def test_uncertain_comment_cannot_reconcile_using_wrong_proof(tmp_path, proof):
    class LostApi(FakeApi):
        def post(self, route, body):
            response = super().post(route, body)
            if route.endswith("/issues/41/comments"):
                raise TimeoutError("response lost")
            return response

    api = LostApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 0
    posted = next(c for c in api.comments if c["id"] == 9101)
    if proof == "bare":
        posted["body"] = "/hermes enroll"
    elif proof == "different_sha":
        posted["body"] = "/hermes enroll " + "c" * 40
    else:
        posted["user"] = {"id": 99}
    for _ in range(3):
        assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 0
    assert len([r for r, _ in api.posts if r.endswith("/issues/41/comments")]) == 1


def test_push_immediately_before_ready_is_detected_without_retry_or_enrollment(tmp_path):
    class PushAtReadyApi(FakeApi):
        def post(self, route, body):
            if route == "graphql" and "markPullRequestReadyForReview" in body["query"]:
                assert "expectedHeadOid" not in body["query"]
                self.pulls[0]["head"]["sha"] = "c" * 40
            return super().post(route, body)

    api = PushAtReadyApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    for _ in range(4):
        assert make_coordinator(tmp_path, api).run(apply=True)["handed_off"] == 0
    assert api.pulls[0]["draft"] is False  # PR-level write, not a SHA attestation.
    assert len([c for c in api.graphql_calls if "markPullRequestReadyForReview" in c["query"]]) == 1
    assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)
