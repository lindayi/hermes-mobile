import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.issue_starter import (
    OWNER_ID,
    REPOSITORY_ID,
    Coordinator,
    CoordinatorError,
    StateStore,
    main,
)


REPOSITORY = "lindayi/hermes-mobile"
ISSUE_NUMBER = 28
COMMAND_ID = 9001
CREATED = "2026-10-01T20:00:00Z"


def issue_comment(*, user_id=OWNER_ID, body="/hermes start", comment_id=COMMAND_ID,
                  created_at=CREATED):
    return {
        "id": comment_id,
        "body": body,
        "created_at": created_at,
        "user": {"id": user_id},
    }


def issue(*, state="open", body="Please implement the public feature.", title="Example"):
    return {
        "number": ISSUE_NUMBER,
        "title": title,
        "body": body,
        "state": state,
        "updated_at": CREATED,
    }


def task(task_id="task-1", *, state="queued", artifacts=None, sessions=None,
         creator_id=OWNER_ID, owner_id=OWNER_ID, repository_id=REPOSITORY_ID):
    value = {
        "id": task_id,
        "state": state,
        "creator": {"id": creator_id},
        "owner": {"id": owner_id},
        "repository": {"id": repository_id},
        "artifacts": artifacts or [],
    }
    if sessions is not None:
        value["sessions"] = sessions
    return value


def completed_task(*, repository_id=REPOSITORY_ID, creator_id=OWNER_ID,
                   head_ref="copilot/issue-28", pull_id=3301, node_id="PR_kwDO123"):
    return task(
        state="completed",
        creator_id=creator_id,
        repository_id=repository_id,
        artifacts=[
            {
                "provider": "github",
                "type": "branch",
                "data": {"head_ref": head_ref, "base_ref": "main"},
            },
            {
                "provider": "github",
                "type": "pull",
                "data": {"id": pull_id, "global_id": node_id},
            },
        ],
        sessions=[
            {
                "id": "session-1",
                "task_id": "task-1",
                "state": "completed",
                "user": {"id": OWNER_ID},
                "owner": {"id": OWNER_ID},
                "repository": {"id": REPOSITORY_ID},
                "head_ref": head_ref,
                "base_ref": "main",
            }
        ],
    )


def pull_request(*, pull_id=3301, node_id="PR_kwDO123", head_ref="copilot/issue-28",
                 head_sha="a" * 40, body="Closes #28", repository_id=REPOSITORY_ID,
                 base_ref="main", draft=True):
    return {
        "id": pull_id,
        "node_id": node_id,
        "number": 41,
        "title": "Implement issue 28",
        "body": body,
        "state": "open",
        "merged": False,
        "draft": draft,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"id": repository_id},
        },
        "base": {
            "ref": base_ref,
            "repo": {"id": REPOSITORY_ID},
        },
    }


class FakeApi:
    def __init__(self, *, comments=None, current_issue=None, timeline=None,
                 task_response=None, task_detail=None, pulls=None):
        self.comments = list(comments or [issue_comment()])
        self.current_issue = current_issue or issue()
        self.timeline = list(timeline or [])
        self.task_response = task_response or task()
        self.task_detail = task_detail or self.task_response
        self.pulls = list(pulls or [])
        self.posts = []
        self.patches = []

    def get(self, route):
        if route == "user":
            return {"id": OWNER_ID}
        if route == f"repos/{REPOSITORY}":
            return {
                "id": REPOSITORY_ID,
                "full_name": REPOSITORY,
                "default_branch": "main",
                "owner": {"id": OWNER_ID},
            }
        if route == f"repos/{REPOSITORY}/commits/main":
            return {"sha": "b" * 40}
        if route.startswith(f"repos/{REPOSITORY}/issues?"):
            return [self.current_issue] if self.current_issue["state"] == "open" else []
        if route.startswith(f"repos/{REPOSITORY}/issues/{ISSUE_NUMBER}/comments?"):
            return self.comments
        if route.startswith(f"repos/{REPOSITORY}/issues/41/comments?"):
            return self.comments
        if route.startswith(f"repos/{REPOSITORY}/issues/{ISSUE_NUMBER}/timeline?"):
            return self.timeline
        if route == f"repos/{REPOSITORY}/issues/{ISSUE_NUMBER}":
            return self.current_issue
        if route.startswith(f"agents/repos/{REPOSITORY}/tasks/"):
            return self.task_detail
        if route.startswith(f"repos/{REPOSITORY}/pulls?"):
            return self.pulls
        if route == f"repos/{REPOSITORY}/pulls/41":
            return self.pulls[0]
        raise AssertionError(f"Unexpected GET {route}")

    def post(self, route, body):
        self.posts.append((route, body))
        if route == f"agents/repos/{REPOSITORY}/tasks":
            return self.task_response
        if route.endswith(f"/issues/41/comments"):
            comment = {
                "id": 9101,
                "body": body["body"],
                "created_at": "2026-10-01T21:00:00Z",
                "user": {"id": OWNER_ID},
            }
            self.comments.append(comment)
            return comment
        if route.endswith(f"/issues/{ISSUE_NUMBER}/comments"):
            comment = {
                "id": 9102,
                "body": body["body"],
                "created_at": "2026-10-01T21:00:00Z",
                "user": {"id": OWNER_ID},
            }
            self.comments.append(comment)
            return comment
        raise AssertionError(f"Unexpected POST {route}")

    def patch(self, route, body):
        self.patches.append((route, body))
        for pull in self.pulls:
            if route.endswith(f"/pulls/{pull['number']}"):
                pull["draft"] = body["draft"]
                return pull
        raise AssertionError(f"Unexpected PATCH {route}")


def make_coordinator(tmp_path, api):
    return Coordinator(api, StateStore(tmp_path / "private" / "issue-starter.json"))


def start_task(tmp_path, api):
    result = make_coordinator(tmp_path, api).run(apply=True)
    assert result["dispatched"] == 1
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1
    return result


def test_read_only_plan_uses_exact_owner_command_and_creates_no_state(tmp_path):
    api = FakeApi()
    state_path = tmp_path / "private" / "issue-starter.json"
    result = Coordinator(api, StateStore(state_path)).run()

    assert result["planned"] == 1
    assert not state_path.parent.exists()
    assert not list(tmp_path.rglob("*.lock"))
    assert api.posts == []
    assert api.patches == []


def test_cli_default_plan_creates_no_state_directory_or_lock(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    result = main([], api_factory=FakeApi)

    assert result == 0
    assert json.loads(capsys.readouterr().out)["planned"] == 1
    assert not (tmp_path / "state").exists()
    assert not list(tmp_path.rglob("*.lock"))


def test_cli_requires_once_for_apply(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--apply"], api_factory=FakeApi)

    assert error.value.code == 2


@pytest.mark.parametrize(
    "comment, issue_value, timeline, expected",
    [
        (issue_comment(user_id=2), issue(), [], 0),
        (issue_comment(body="please /hermes start"), issue(), [], 0),
        (issue_comment(body="/hermes start "), issue(), [], 0),
        (issue_comment(), issue(state="closed"), [], 0),
        (issue_comment(), issue(), [{"event": "edited", "created_at": "2026-10-01T20:01:00Z"}], 0),
        (issue_comment(), issue(body=None), [], 0),
    ],
)
def test_untrusted_stale_or_unverifiable_issue_evidence_never_dispatches(
    tmp_path, comment, issue_value, timeline, expected
):
    api = FakeApi(comments=[comment], current_issue=issue_value, timeline=timeline)
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["dispatched"] == expected
    assert not any(route.endswith("/agents/tasks") for route, _ in api.posts)


def test_dispatch_is_reserved_and_uses_bounded_issue_only_prompt(tmp_path):
    api = FakeApi()
    start_task(tmp_path, api)
    route, request = next(item for item in api.posts if item[0].endswith("/tasks"))
    assert route == f"agents/repos/{REPOSITORY}/tasks"
    assert request["create_pull_request"] is True
    assert request["base_ref"] == "main"
    assert "AGENTS.md" in request["prompt"]
    assert "Closes #28" in request["prompt"]
    assert "managed" in request["prompt"].lower()
    assert "do not merge" in request["prompt"].lower()
    assert "production" in request["prompt"].lower()
    state = json.loads((tmp_path / "private" / "issue-starter.json").read_text())
    saved = state["commands"][f"{ISSUE_NUMBER}:{COMMAND_ID}"]
    assert saved["issue"] == ISSUE_NUMBER
    assert saved["command_id"] == COMMAND_ID
    assert saved["accepted_title_body_sha256"]
    assert saved["accepted_at"] == CREATED
    assert saved["task_id"] == "task-1"


def test_response_uncertainty_consumes_reservation_without_reposting(tmp_path):
    class UncertainApi(FakeApi):
        def post(self, route, body):
            if route.endswith("/tasks"):
                self.posts.append((route, body))
                raise TimeoutError("response lost")
            return super().post(route, body)

    api = UncertainApi()
    store = StateStore(tmp_path / "private" / "issue-starter.json")
    first = Coordinator(api, store).run(apply=True)
    second = Coordinator(api, store).run(apply=True)

    assert first["blocked"] == 1
    assert second["dispatched"] == 0
    assert len([item for item in api.posts     if item[0].endswith("/tasks")]) == 1
    saved = json.loads(store.path.read_text())["commands"][f"{ISSUE_NUMBER}:{COMMAND_ID}"]
    assert saved["phase"] == "unknown"


def test_repeated_exact_task_read_failures_are_bounded_and_escalated(tmp_path):
    class MissingTaskApi(FakeApi):
        task_reads = 0

        def get(self, route):
            if route.startswith(f"agents/repos/{REPOSITORY}/tasks/"):
                self.task_reads += 1
                from deploy.issue_starter import ApiError
                raise ApiError("synthetic failure")
            return super().get(route)

    api = MissingTaskApi()
    store = StateStore(tmp_path / "private" / "issue-starter.json")
    start_task(tmp_path, api)
    results = [Coordinator(api, store).run(apply=True) for _ in range(6)]

    assert api.task_reads == 3
    assert results[4]["pending"] == 0
    assert json.loads(store.path.read_text())["commands"][f"{ISSUE_NUMBER}:{COMMAND_ID}"]["phase"] == "unknown"
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1


def test_unknown_task_response_is_consumed_without_reposting(tmp_path):
    class UnknownResponseApi(FakeApi):
        def post(self, route, body):
            if route.endswith("/tasks"):
                self.posts.append((route, body))
                return {"state": "queued"}
            return super().post(route, body)

    api = UnknownResponseApi()
    store = StateStore(tmp_path / "private" / "issue-starter.json")
    first = Coordinator(api, store).run(apply=True)
    second = Coordinator(api, store).run(apply=True)

    assert first["blocked"] == 1
    assert second["dispatched"] == 0
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1
    assert json.loads(store.path.read_text())["commands"][f"{ISSUE_NUMBER}:{COMMAND_ID}"]["phase"] == "unknown"


def test_reserved_dispatch_resumes_after_crash_before_send(tmp_path):
    api = FakeApi()
    store = StateStore(tmp_path / "private" / "issue-starter.json")
    store.reserve({
        "issue": ISSUE_NUMBER,
        "command_id": COMMAND_ID,
        "accepted_title_body_sha256": hashlib.sha256(
            ("Example\0Please implement the public feature.").encode()
        ).hexdigest(),
        "accepted_at": CREATED,
        "phase": "reserved",
        "base_sha": "b" * 40,
        "task_id": None,
    })

    result = Coordinator(api, store).run(apply=True)

    assert result["dispatched"] == 1
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1


def test_crash_after_task_send_keeps_dispatch_consumed(tmp_path):
    class CrashAfterSendStore(StateStore):
        def update(self, key, changes):
            if changes.get("phase") == "task_created":
                raise RuntimeError("simulated crash after remote response")
            return super().update(key, changes)

    api = FakeApi()
    path = tmp_path / "private" / "issue-starter.json"
    with pytest.raises(RuntimeError):
        Coordinator(api, CrashAfterSendStore(path)).run(apply=True)

    result = Coordinator(api, StateStore(path)).run(apply=True)

    assert result["dispatched"] == 0
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1
    assert json.loads(path.read_text())["commands"][f"{ISSUE_NUMBER}:{COMMAND_ID}"]["phase"] == "unknown"


def test_replayed_owner_command_never_creates_a_second_task(tmp_path):
    api = FakeApi()
    start_task(tmp_path, api)

    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["dispatched"] == 0
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 1


def test_issue_edit_after_reservation_blocks_dispatch(tmp_path):
    api = FakeApi()
    original_get = api.get

    def edit_before_fresh_check(route):
        if route == f"repos/{REPOSITORY}/issues/{ISSUE_NUMBER}":
            api.current_issue = issue(body="Changed after the command.")
        return original_get(route)

    api.get = edit_before_fresh_check
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["dispatched"] == 0
    assert not any(item[0].endswith("/tasks") for item in api.posts)


@pytest.mark.parametrize("state", ["queued", "in_progress", "waiting_for_user", "idle"])
def test_nonterminal_or_unknown_task_state_never_hands_off(tmp_path, state):
    api = FakeApi()
    start_task(tmp_path, api)
    api.task_detail = task(state=state)
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == 0
    assert not any(route.endswith("/pulls/41/comments") for route, _ in api.posts)
    assert api.patches == []


def test_completed_bound_task_marks_its_draft_pr_ready_and_enrolls_once(tmp_path):
    pull = pull_request()
    api = FakeApi(pulls=[pull])
    start_task(tmp_path, api)
    api.task_detail = completed_task()

    first = make_coordinator(tmp_path, api).run(apply=True)
    second = make_coordinator(tmp_path, api).run(apply=True)

    assert first["handed_off"] == 1
    assert second["handed_off"] == 0
    assert api.patches == [(f"repos/{REPOSITORY}/pulls/41", {"draft": False})]
    enrollment = [
        body["body"] for route, body in api.posts
        if route.endswith("/issues/41/comments")
    ]
    assert enrollment == ["/hermes enroll"]
    assert api.pulls[0]["draft"] is False


def test_uncertain_enrollment_comment_reconciles_without_duplicate_comment(tmp_path):
    class LostEnrollmentResponseApi(FakeApi):
        lost = False

        def post(self, route, body):
            response = super().post(route, body)
            if route.endswith("/issues/41/comments") and body["body"] == "/hermes enroll" and not self.lost:
                self.lost = True
                raise TimeoutError("response lost")
            return response

    api = LostEnrollmentResponseApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()

    first = make_coordinator(tmp_path, api).run(apply=True)
    make_coordinator(tmp_path, api).run(apply=True)
    final = make_coordinator(tmp_path, api).run(apply=True)

    assert first["blocked"] == 1
    assert final["handed_off"] == 1
    assert len([
        item for item in api.posts
        if item[0].endswith("/issues/41/comments") and item[1]["body"] == "/hermes enroll"
    ]) == 1


def test_uncertain_readiness_never_repeats_patch_when_pr_remains_draft(tmp_path):
    class LostReadinessApi(FakeApi):
        def patch(self, route, body):
            self.patches.append((route, body))
            raise TimeoutError("response lost before state change")

    api = LostReadinessApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()

    first = make_coordinator(tmp_path, api).run(apply=True)
    second = make_coordinator(tmp_path, api).run(apply=True)
    third = make_coordinator(tmp_path, api).run(apply=True)

    assert first["blocked"] == 1
    assert second["blocked"] == 0
    assert third["handed_off"] == 0
    assert len(api.patches) == 1
    assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)


@pytest.mark.parametrize(
    "completed, pull",
    [
        (completed_task(repository_id=99), pull_request()),
        (completed_task(creator_id=7), pull_request()),
        (completed_task(head_ref="copilot/other"), pull_request()),
        (completed_task(), pull_request(head_ref="copilot/other")),
        (completed_task(), pull_request(repository_id=99)),
        (completed_task(), pull_request(base_ref="other")),
        (completed_task(), pull_request(body="Unrelated change")),
    ],
)
def test_wrong_task_or_pull_identity_is_never_handed_off(tmp_path, completed, pull):
    api = FakeApi(pulls=[pull])
    start_task(tmp_path, api)
    api.task_detail = completed

    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == 0
    assert not any(route.endswith("/pulls/41/comments") for route, _ in api.posts)
    assert api.patches == []


def test_task_head_race_blocks_readiness_and_enrollment(tmp_path):
    pull = pull_request(head_sha="a" * 40)
    api = FakeApi(pulls=[pull])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    original_get = api.get

    def change_head_after_reservation(route):
        if route == f"repos/{REPOSITORY}/pulls/41":
            api.pulls[0]["head"]["sha"] = "c" * 40
        return original_get(route)

    api.get = change_head_after_reservation

    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == 0
    assert api.patches == []
    assert not any(route.endswith("/pulls/41/comments") for route, _ in api.posts)


def test_task_completion_requires_successful_matching_session(tmp_path):
    api = FakeApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    api.task_detail["sessions"][0]["state"] = "failed"

    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == 0
    assert api.patches == []


def test_changed_task_pull_binding_after_reservation_blocks_handoff(tmp_path):
    api = FakeApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    original_get = api.get

    def change_task_artifact(route):
        if route.startswith(f"agents/repos/{REPOSITORY}/tasks/"):
            value = completed_task(pull_id=9900, node_id="PR_other")
            return value
        return original_get(route)

    api.get = change_task_artifact
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == 0
    assert api.patches == []
    assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)


def test_reopened_issue_requires_a_new_owner_command(tmp_path):
    api = FakeApi()
    start_task(tmp_path, api)
    api.current_issue = issue()
    api.comments = [
        issue_comment(),
        issue_comment(comment_id=9002, created_at="2026-10-01T21:00:00Z"),
    ]
    api.timeline = [
        {"event": "closed", "created_at": "2026-10-01T20:30:00Z"},
        {"event": "reopened", "created_at": "2026-10-01T20:40:00Z"},
    ]
    api.task_detail = task(state="failed")

    make_coordinator(tmp_path, api).run(apply=True)
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["dispatched"] == 1
    assert len([item for item in api.posts if item[0].endswith("/tasks")]) == 2


def test_authenticated_identity_must_be_fixed_owner_and_repository(tmp_path):
    api = FakeApi()

    def wrong_identity(route):
        value = original_get(route)
        if route == "user":
            return {"id": 12}
        return value

    original_get = api.get
    api.get = wrong_identity
    with pytest.raises(CoordinatorError):
        make_coordinator(tmp_path, api).run(apply=True)
    assert api.posts == []


def test_private_state_rejects_symlinked_directory_and_readonly_snapshot_is_clean(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    store = StateStore(link / "state.json")

    with pytest.raises(CoordinatorError):
        store.snapshot()
    assert not list(tmp_path.rglob("*.lock"))
    with pytest.raises(CoordinatorError):
        store.reserve({"issue": ISSUE_NUMBER, "command_id": COMMAND_ID})


def test_cli_help_does_not_create_bytecode(tmp_path):
    cache = tmp_path / "pycache"
    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(cache))
    script = Path(__file__).parents[1] / "scripts" / "issue_starter.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "--apply" in result.stdout
    assert not cache.exists()
