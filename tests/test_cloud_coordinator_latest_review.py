"""Direct seam regressions for PR33 review 5388069795."""
import json

import pytest

from deploy.cloud_coordinator import neutral_reconciliation_request, review_task_request
from deploy.workflow_lifecycle import pull_event
from test_cloud_coordinator import (
    APP_OWNER_ID, BASE, HEAD, Coordinator, CoordinatorError, FakeApi, StateStore,
    enrolled_record,
)


def test_legacy_enrollment_reports_unsupported_upgrade_without_changing_state(tmp_path):
    api = FakeApi()
    store = StateStore(tmp_path / "state.json")
    store.enroll({"issue": 16, "comment": 123, "head": HEAD, "base": BASE})
    # Preserve outstanding claims and command fences; fresh enrollment is not recovery.
    store.record_event("123")
    store.claim_action("legacy-uncertain", {"kind": "fix", "issue": 16})
    store.mark_uncertain("legacy-uncertain")
    before = store.path.read_bytes()
    with pytest.raises(CoordinatorError, match=(
        "Unsupported legacy enrollment: missing pull/repository identity; "
        "automatic migration is not supported; preserve state and stop activation"
    )):
        Coordinator(api, store)._snapshot_pull(
            16, store.snapshot()["enrollments"]["16"], BASE,
        )
    assert store.path.read_bytes() == before
    assert not api.writes and not api.graphql_writes


def test_neutral_prompt_requires_per_hunk_decisions_before_ready_receipt():
    api = FakeApi()
    request = neutral_reconciliation_request({
        "issue": 16, "head": HEAD, "main_sha": BASE, "pull": api.pull,
    }, 0)
    prompt = request["body"]
    assert "each conflict hunk" in prompt
    assert "classification, decision, and rationale" in prompt
    assert "preserves both branch intents" in prompt
    assert "PR comment" in prompt and "before returning a `ready` receipt" in prompt
    assert "fresh review and checks" in prompt


def test_review_prompt_matches_report_schema_and_inventories_every_changed_path():
    api = FakeApi()
    files = [
        {
            "filename": f"src/module_{index:02}.py",
            "status": "removed" if index == 2 else "modified",
            "sha": f"{index + 1:040x}",
        }
        for index in range(27)
    ]
    snapshot = {
        "issue": 16,
        "head": HEAD,
        "main_sha": BASE,
        "pull": api.pull,
        "files": files,
        "files_complete": True,
    }
    source_action = {
        "task_id": "source-task",
        "head": HEAD,
        "receipt_session_id": "source-session",
        "receipt_comment_id": 777,
    }
    request = review_task_request(
        snapshot, source_action, 888,
        "Hermes-Review-Anchor: hermes-coordinator-review-anchor:synthetic",
    )
    prompt = request["body"]

    assert "exactly the keys `path` and `comment`" in prompt
    assert "1-8" in prompt and "1,000" in prompt
    assert "independently compute" in prompt
    assert "Git blob" in prompt and "deleted" in prompt and "`null`" in prompt
    assert all(item["filename"] in prompt for item in files)
    template_line = next(
        line for line in prompt.splitlines()
        if line.startswith('{"schema":"hermes-independent-review-report-v1"')
    )
    template = json.loads(template_line)
    assert set(template) == {
        "schema", "nonce", "session_id", "repository", "repository_id", "pr",
        "anchor_comment_id", "role", "head", "base", "source_start_head",
        "source_session_id", "source_comment_id", "verdict", "summary",
        "findings", "files", "report",
    }
    assert set(template["findings"][0]) == {"path", "comment"}
    assert set(template["files"]) == {item["filename"] for item in files}


@pytest.mark.parametrize("change", ["authorized", "head_changed", "retired", "unobserved", "wrong_decision"])
@pytest.mark.parametrize("retain_outcomes", [False, True])
def test_export_omits_obsolete_approval_without_rewriting_history(tmp_path, change, retain_outcomes):
    now = 1790856660
    store = StateStore(tmp_path / "state.json")
    enrollment = enrolled_record(last_open_seen=True, last_open_head=HEAD)
    store.enroll(enrollment)
    snapshot = {"issue": 16, "head": HEAD, "enrollment": enrollment}
    approval = pull_event(
        snapshot, "sensitive_approval", occurred_at="2026-10-01T12:10:00Z",
        decision="authorize_sensitive_action",
    )
    history = [pull_event(
        snapshot, reason, occurred_at="2026-10-01T12:10:00Z",
        merge_sha="e" * 40 if reason in {"merged", "controller_verified"} else None,
    ) for reason in ("merged", "controller_verified", "task_failed")] if retain_outcomes else []
    for event in [approval, *history]:
        store.record_lifecycle(event, now=now)
    export = tmp_path / "workflow-events.json"
    store.write_lifecycle_export(now=now, owner_user_id=APP_OWNER_ID)
    assert json.loads(export.read_text())["events"] == [approval, *history]

    if change == "authorized":
        store.authorize_sensitive(16, HEAD)
    elif change == "head_changed":
        store.commit_scan(None, [], observations=[(16, "c" * 40)], now=now)
    elif change == "retired":
        store.commit_scan(None, [], retirements=[16], now=now)
    else:
        # Synthetic preexisting state: absence of current observation/decision proof.
        data = store.snapshot()
        if change == "unobserved":
            data["enrollments"]["16"].pop("last_open_head")
        else:
            data["lifecycle_events"][0]["decision"] = "approve_production"
        store._save(data)
    before = store.path.read_bytes()
    retained = store.snapshot()["lifecycle_events"]

    # Reopening StateStore proves the filter uses durable state, not process memory.
    restarted = StateStore(store.path)
    for tick in (now + 1, now + 2):
        restarted.write_lifecycle_export(now=tick, owner_user_id=APP_OWNER_ID)
        if history:
            assert json.loads(export.read_text())["events"] == history
        else:
            assert not export.exists()
        assert store.path.read_bytes() == before
        assert store.snapshot()["lifecycle_events"] == retained


@pytest.mark.parametrize("authorize", [False, True])
def test_apply_refreshes_approval_export_from_current_scan(tmp_path, authorize):
    api = FakeApi(sensitive=True)
    store = StateStore(tmp_path / "state.json")
    coordinator = Coordinator(api, store, clock=lambda: 1790856660)
    coordinator.run(apply=True)
    export = tmp_path / "workflow-events.json"
    old_event = json.loads(export.read_text())["events"][0]
    assert old_event["head_sha"] == HEAD
    if authorize:
        api.authorize_sha_review = True
        api.comments.append({
            "id": 124, "user": {"id": 5164171},
            "body": (f"/hermes authorize-sensitive {HEAD} review "
                     f"{api.owner_review_id} {api.owner_review_digest}"),
            "updated_at": "2026-10-01T12:10:00Z",
        })
    else:
        api.head_sha = "c" * 40
        api.pull["head"]["sha"] = api.head_sha
    coordinator.run(apply=True)
    exported = json.loads(export.read_text())["events"] if export.exists() else []
    approvals = [event for event in exported if event["reason"] == "sensitive_approval"]
    assert [event["head_sha"] for event in approvals] == ([] if authorize else ["c" * 40])
    assert old_event in store.snapshot()["lifecycle_events"]
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == (HEAD if authorize else None)


@pytest.mark.parametrize("field", ["creator", "repository"])
@pytest.mark.parametrize("identity", [
    "exact", "float", "string", "true", "false", "zero", "negative",
    "null", "missing", "list", "object",
])
@pytest.mark.parametrize("artifacts", [False, True])
@pytest.mark.parametrize("neutral", [False, True])
def test_task_post_requires_exact_positive_integer_identity(
        tmp_path, field, identity, artifacts, neutral):
    class TaskResponseApi(FakeApi):
        def write(self, route, body):
            response = super().write(route, body)
            if route.endswith("/tasks"):
                expected = response[field]["id"]
                malformed = {
                    "exact": expected, "float": float(expected), "string": str(expected),
                    "true": True, "false": False, "zero": 0, "negative": -expected,
                    "null": None, "list": [], "object": {},
                }
                response[field] = {} if identity == "missing" else {"id": malformed[identity]}
                if not artifacts:
                    response.pop("artifacts")
            return response

    api = TaskResponseApi(unresolved=True)
    if neutral:
        api.pull.update(mergeable=False, mergeable_state="dirty")
    store = StateStore(tmp_path / "state.json")
    Coordinator(api, store, clock=lambda: 1790856660).run(apply=True)
    action = next(a for a in store.actions().values() if a["kind"] == "fix")
    assert action["status"] == ("sent" if identity == "exact" else "uncertain")
    # A POST was attempted: retain its reserved attempt and never resend ambiguity.
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 1
    assert api.fix_attempts == 1
    if identity != "exact":
        assert action["blocker"] == "execution_uncertain"
        events = store.snapshot()["lifecycle_events"]
        assert [event["reason"] for event in events] == ["execution_uncertain"]
        Coordinator(api, StateStore(store.path), clock=lambda: 1790856661).run(apply=True)
        assert store.action(action["key"])["status"] == "uncertain"
        assert store.snapshot()["enrollments"]["16"]["attempts"] == 1
        assert store.snapshot()["lifecycle_events"] == events
        assert api.fix_attempts == 1
    assert not api.graphql_writes
