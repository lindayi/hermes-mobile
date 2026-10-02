from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from deploy.workflow_events import event_digest
from deploy.workflow_lifecycle import issue_event, pull_event
from deploy.workflow_notifications import Paths as NotificationPaths, process
from deploy.cloud_coordinator import StateStore
from deploy.workflow_lifecycle_sources import (
    LifecycleSourcePaths,
    LifecycleSourceError,
    collect_source_events,
    resolve_application_binding,
)


NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
GITHUB_OWNER = 5164171
APP_OWNER = "synthetic-mobile-owner"
HEAD = "a" * 40
MERGE = "b" * 40


class StarterApi:
    def __init__(self, comments):
        self.comments = comments

    def get_all(self, route, *, collection=None):
        assert route.startswith("repos/lindayi/hermes-mobile/issues/31/comments")
        return self.comments


class EmptyLifecycleApi:
    def __init__(self):
        self.comments = []
        self.writes = []
        self.graphql_writes = []

    def get(self, route):
        if route == "repos/lindayi/hermes-mobile":
            return {"id": 1399942965}
        if route == "user":
            return {"id": GITHUB_OWNER}
        if route == "repos/lindayi/hermes-mobile/commits/main":
            return {"sha": "f" * 40}
        raise AssertionError(f"Unexpected API read: {route}")

    def get_all(self, route, *, collection=None):
        if route.startswith("repos/lindayi/hermes-mobile/issues?"):
            return []
        if route.startswith("repos/lindayi/hermes-mobile/issues/31/comments?"):
            return self.comments
        raise AssertionError(f"Unexpected API list: {route}")


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def private_json(path, value, *, modified=None):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True))
    path.chmod(0o600)
    if modified is not None:
        os.utime(path, (modified, modified))
    return path


def app_fixture(root):
    from backend.notification_policy import default_preferences
    from backend.notifications import NotificationService

    root = private_directory(root)
    state_dir = private_directory(root / "app-state")
    config = private_json(root / "config.json", {"state_dir": str(state_dir)})
    auth = state_dir / "auth.sqlite"
    with sqlite3.connect(auth) as db:
        db.execute("CREATE TABLE users(id TEXT,role TEXT,status TEXT,profile TEXT)")
        db.executemany("INSERT INTO users VALUES(?,?,?,?)", [
            (APP_OWNER, "owner", "ready", "default"),
            ("synthetic-member", "member", "ready", "member"),
        ])
    auth.chmod(0o600)
    inbox = state_dir / "notifications.sqlite"
    notifications = NotificationService(inbox, clock=lambda: NOW.timestamp())
    with notifications._db() as db:
        db.execute(
            "INSERT INTO push_preferences VALUES(?,?,?)",
            (APP_OWNER, "synthetic-device", json.dumps(default_preferences())),
        )
    return NotificationPaths(
        config=config,
        delivery_state=root / "delivery" / "state.json",
        controller_state=root / "controller",
    ), state_dir, auth, inbox, notifications


def controller_evidence(paths):
    release = "c" * 32
    controller = private_directory(paths.controller_state)
    releases = private_directory(controller / "releases")
    stage = private_directory(releases / release)
    private_json(
        paths.delivery_state,
        {
            "version": 1,
            "latest_id": 303,
            "records": {
                f"{MERGE}:202": {
                    "status": "deployed",
                    "reason": "deployed",
                    "sha": MERGE,
                    "approval_run_id": 202,
                    "source_run_id": 101,
                    "deployment_id": 303,
                },
            },
            "last": {"status": "duplicate", "reason": "Consumed intent: deployed"},
        },
        modified=NOW.timestamp() - 1,
    )
    private_json(
        controller / "status.json",
        {"status": "succeeded", "git_sha": MERGE, "release": release},
        modified=NOW.timestamp() - 10,
    )
    private_json(stage / "git-provenance.json", {"git_sha": MERGE})
    (controller / "current").symlink_to(stage)


def test_real_starter_and_controller_sources_reach_owner_inbox_unchanged(tmp_path):
    notification_paths, state_dir, _, inbox, notifications = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    starter_path = private_json(
        tmp_path / "starter" / "state.json",
        {
            "version": 1,
            "commands": {
                "31:77": {
                    "issue": 31,
                    "command_id": 77,
                    "accepted_title_body_sha256": "d" * 64,
                    "accepted_at": "2026-10-01T20:58:00Z",
                    "phase": "failed",
                    "base_sha": "e" * 40,
                    "task_id": "task-77",
                    "blocker": "task_failed",
                    "receipt": {
                        "kind": "blocked",
                        "state": "sent",
                        "marker": "<!-- hermes-issue-starter:blocked:31:77 -->",
                        "pull_number": None,
                    },
                },
            },
        },
        modified=NOW.timestamp() - 1,
    )
    comment = {
        "id": 300,
        "user": {"id": GITHUB_OWNER},
        "body": (
            "<!-- hermes-issue-starter:blocked:31:77 -->\n"
            "Hermes issue starter is blocked for issue #31. Owner action is required; "
            "no uncertain task was automatically retried."
        ),
        "created_at": "2026-10-01T20:59:00Z",
        "updated_at": "2026-10-01T20:59:00Z",
    }
    merged = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "merged",
        occurred_at="2026-10-01T20:58:00Z",
        merge_sha=MERGE,
    )
    paths = LifecycleSourcePaths(
        notifications=notification_paths,
        starter_state=starter_path,
    )

    source_events = collect_source_events(
        [merged], api=StarterApi([comment]), paths=paths, now=NOW,
    )
    assert {item["reason"] for item in source_events} == {
        "issue_failed", "controller_verified",
    }

    store = StateStore(tmp_path / "coordinator" / "state.json")
    for item in [merged, *source_events]:
        store.record_lifecycle(item, now=NOW)
    owner, export_directory = resolve_application_binding(notification_paths)
    assert owner == APP_OWNER and owner != str(GITHUB_OWNER)
    store.write_lifecycle_export(
        now=NOW, owner_user_id=owner, directory=export_directory,
    )
    os.utime(state_dir / "workflow-events.json", (NOW.timestamp(), NOW.timestamp()))
    exported = json.loads((state_dir / "workflow-events.json").read_bytes())
    assert exported["owner_user_id"] == APP_OWNER
    assert exported["events"] == [merged, *source_events]

    first = process(notification_paths, apply=True, now=NOW)
    assert first["inbox_items"] == 3
    assert len(notifications.list_inbox(APP_OWNER)) == 3
    assert notifications.list_inbox("synthetic-member") == []
    with sqlite3.connect(state_dir / "workflow-notifications.sqlite") as db:
        stored = db.execute(
            "SELECT event_id,digest,recipient_id,status FROM events ORDER BY event_id"
        ).fetchall()
    assert sorted(stored) == sorted(
        (item["event_id"], event_digest(item), APP_OWNER, "acked")
        for item in exported["events"]
    )

    replay = collect_source_events(
        [merged], api=StarterApi([comment]), paths=paths, now=NOW,
    )
    assert replay == source_events
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 0
    assert len(notifications.list_inbox(APP_OWNER)) == 3


@pytest.mark.parametrize("reject_commit", [False, True])
def test_newly_observed_merge_precedes_derived_deployment_through_inbox(
        tmp_path, monkeypatch, reject_commit):
    import deploy.cloud_coordinator as coordinator_module
    from test_cloud_coordinator import Coordinator, CoordinatorError, FakeApi, enrolled_record

    notification_paths, state_dir, _, inbox, notifications = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    store.enroll(enrolled_record(last_open_seen=True, last_open_head=HEAD))
    api = FakeApi(pull_state="closed", merged=True)
    api.pull.update(merge_commit_sha=MERGE, merged_at="2026-10-01T20:58:00Z")
    coordinator = Coordinator(api, store, clock=NOW.timestamp, lifecycle_source_paths=paths)
    before = store.path.read_bytes()
    export = state_dir / "workflow-events.json"
    original_commit = store.commit_scan

    def checked_commit(*args, **kwargs):
        # Source preparation must neither persist the merge nor publish deployment.
        assert store.path.read_bytes() == before
        assert not export.exists()
        assert api.writes == [] and api.graphql_writes == []
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(store, "commit_scan", checked_commit)
    if reject_commit:
        def capacity_failure(*args, **kwargs):
            with monkeypatch.context() as patch:
                patch.setattr(coordinator_module, "MAX_STATE_BYTES", 1)
                return checked_commit(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(store, "commit_scan", capacity_failure)
            with pytest.raises(CoordinatorError, match="safety bound"):
                coordinator.run(apply=True)
        assert store.path.read_bytes() == before
        assert store.snapshot()["lifecycle_events"] == []
        assert store.snapshot()["enrollments"]["16"]["active"] is True
        assert not export.exists()
        assert api.writes == [] and api.graphql_writes == []

    exports = []
    original_export = store.write_lifecycle_export

    def capture_export(*args, **kwargs):
        result = original_export(*args, **kwargs)
        events = json.loads(export.read_bytes())["events"]
        assert events == StateStore(store.path).snapshot()["lifecycle_events"]
        exports.append(events)
        return result

    monkeypatch.setattr(store, "write_lifecycle_export", capture_export)
    coordinator.run(apply=True)
    events = StateStore(store.path).snapshot()["lifecycle_events"]
    assert [event["reason"] for event in events] == ["merged", "controller_verified"]
    assert len(exports) == 2 and exports == [events, events]
    assert store.snapshot()["enrollments"]["16"]["active"] is False
    assert api.writes == [] and api.graphql_writes == []

    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 2
    with sqlite3.connect(inbox) as db:
        deliveries = db.execute("SELECT delivery_id FROM inbox ORDER BY rowid").fetchall()
    assert deliveries == [("workflow-event:v1:" + event["event_id"],) for event in events]
    assert notifications.list_inbox("synthetic-member") == []

    # A restarted coordinator preserves causal order and consumer deduplication.
    Coordinator(api, StateStore(store.path), clock=NOW.timestamp,
                lifecycle_source_paths=paths).run(apply=True)
    assert not export.exists()
    state = StateStore(store.path).snapshot()
    assert state["lifecycle_events"] == []
    assert state["lifecycle_context"]["events"] == events
    with sqlite3.connect(state_dir / "workflow-notifications.sqlite") as db:
        assert db.execute("SELECT count(*) FROM events WHERE status='acked'").fetchone()[0] == 2
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone()[0] == 2


@pytest.mark.parametrize("failure", ["issue", "comment", "commit"])
def test_source_events_wait_for_atomic_scan_commit(tmp_path, monkeypatch, failure):
    from deploy.cloud_coordinator import Coordinator, CoordinatorError
    from test_cloud_coordinator import FakeApi

    notification_paths, state_dir, _, _, _ = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    paths = LifecycleSourcePaths(
        notifications=notification_paths,
        starter_state=tmp_path / "starter" / "state.json",
    )
    merged = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "merged", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE,
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    store.commit_scan("2026-10-01T10:00:00Z", [], lifecycle_events=[merged], now=NOW)
    before = store.path.read_bytes()
    before_state = store.snapshot()

    class ScanApi(FakeApi):
        fail_scan = True

        def get_all(self, route, *, collection=None):
            rows = super().get_all(route, collection=collection)
            if self.fail_scan and (
                (failure == "issue" and "/issues?" in route)
                or (failure == "comment" and "/issues/16/comments?" in route)
            ):
                return [*rows, {}]
            return rows

    api = ScanApi()
    # Real collection from private synthetic controller/ledger/provenance files,
    # independent of the PR being scanned, not a fabricated collector result.
    source_events = collect_source_events([merged], api=api, paths=paths, now=NOW)
    assert len(source_events) == 1
    assert source_events[0]["reason"] == "controller_verified"
    coordinator = Coordinator(
        api, store, clock=NOW.timestamp, lifecycle_source_paths=paths,
    )

    def fail_commit(*args, **kwargs):
        raise CoordinatorError("simulated scan commit failure")

    with monkeypatch.context() as patch:
        if failure == "commit":
            patch.setattr(store, "commit_scan", fail_commit)
        with pytest.raises(CoordinatorError, match="malformed|scan commit failure"):
            coordinator.run(apply=True)

    assert store.snapshot() == before_state
    assert store.path.read_bytes() == before
    assert api.writes == [] and api.graphql_writes == [] and api.fix_attempts == 0
    assert not (state_dir / "workflow-events.json").exists()

    api.fail_scan = False
    coordinator.run(apply=True)
    after = store.snapshot()
    assert after["cursor"] != before_state["cursor"]
    assert "123" in after["events"]
    assert after["lifecycle_events"] == [merged, *source_events]
    assert json.loads((state_dir / "workflow-events.json").read_bytes())["events"] == [
        merged, *source_events,
    ]


@pytest.mark.parametrize("change,failure", [
    ("authorized", "api"), ("head_changed", "api"),
    ("authorized", "handoff"), ("authorized", "race"), ("authorized", "commit"),
])
def test_committed_scan_refreshes_source_export_before_handoff(tmp_path, change, failure):
    from deploy.cloud_coordinator import Coordinator, CoordinatorError
    from test_cloud_coordinator import FakeApi

    notification_paths, state_dir, _, _, notifications = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    paths = LifecycleSourcePaths(notifications=notification_paths,
                                 starter_state=tmp_path / "starter" / "state.json")
    error = CoordinatorError("post-scan API failure") if failure != "handoff" else RuntimeError(
        "post-scan handoff failure")

    class ScanStore(StateStore):
        armed = False
        committed = False

        def commit_scan(self, *args, **kwargs):
            if self.armed and failure == "commit":
                raise error
            result = super().commit_scan(*args, **kwargs)
            if self.armed:
                self.committed = True
                if failure == "race":
                    api.head_sha = "c" * 40
                    api.pull["head"]["sha"] = api.head_sha
            return result

    class FailingApi(FakeApi):
        def get(self, route):
            if store.committed and failure == "api":
                raise error
            return super().get(route)

        def get_all(self, route, *, collection=None):
            if (store.committed and failure == "race"
                    and f"/commits/{HEAD}/statuses?" in route):
                return []
            return super().get_all(route, collection=collection)

        def graphql_write(self, query, variables):
            if store.committed and failure == "handoff":
                assert "markPullRequestReadyForReview" in query
                raise error
            return super().graphql_write(query, variables)

    store = ScanStore(tmp_path / "coordinator" / "state.json")
    merged = pull_event({"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
                        "merged", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE)
    store.record_lifecycle(merged, now=NOW)
    api = FailingApi(sensitive=True, unresolved=True)
    Coordinator(api, store, clock=NOW.timestamp, lifecycle_source_paths=paths).run(apply=True)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    old_events = json.loads(export.read_bytes())["events"]
    approval = next(e for e in old_events if e["reason"] == "sensitive_approval")
    outcomes = [e for e in old_events if e["reason"] != "sensitive_approval"]
    assert {e["reason"] for e in outcomes} == {"merged", "controller_verified"}
    process(notification_paths, apply=True, now=NOW)
    ack_db = state_dir / "workflow-notifications.sqlite"
    with sqlite3.connect(ack_db) as db:
        ack_before = db.execute("SELECT event_id,digest,status FROM events ORDER BY event_id").fetchall()
    assert ack_before and all(row[2] == "acked" for row in ack_before)
    inbox_before = notifications.list_inbox(APP_OWNER)
    assert inbox_before
    before_bytes, before_mtime = export.read_bytes(), export.stat().st_mtime_ns
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    api.pull["draft"] = True
    api.review_state = "PENDING"
    if change == "authorized":
        api.comments.append({"id": 124, "user": {"id": GITHUB_OWNER},
                             "body": f"/hermes authorize-sensitive {HEAD}",
                             "updated_at": "2026-10-01T20:59:00Z"})
    else:
        api.head_sha = "c" * 40
        api.pull["head"]["sha"] = api.head_sha
    writes, graphql_writes = list(api.writes), list(api.graphql_writes)
    store.armed = True
    coordinator = Coordinator(api, store, clock=lambda: NOW.timestamp() + 60,
                              lifecycle_source_paths=paths)
    if failure == "race":
        coordinator.run(apply=True)
    else:
        with pytest.raises(type(error)) as caught:
            coordinator.run(apply=True)
        assert caught.value is error
    assert api.writes == writes and api.graphql_writes == graphql_writes
    state = store.snapshot()
    if failure == "commit":
        assert approval in state["lifecycle_events"]
        assert all(event in state["lifecycle_events"] for event in outcomes)
    else:
        assert approval in state["lifecycle_context"]["events"]
        assert all(event in state["lifecycle_context"]["events"] for event in outcomes)
    if failure == "commit":
        assert not store.committed
        assert export.read_bytes() == before_bytes
        assert export.stat().st_mtime_ns == before_mtime
    else:
        assert store.committed
        exported = json.loads(export.read_bytes())["events"] if export.exists() else []
        assert approval not in exported
        assert all(event not in exported for event in outcomes)
    with sqlite3.connect(ack_db) as db:
        assert db.execute("SELECT event_id,digest,status FROM events ORDER BY event_id").fetchall() == ack_before
    assert notifications.list_inbox(APP_OWNER) == inbox_before


def test_application_owner_binding_rejects_multiple_ready_default_owners(tmp_path):
    notification_paths, _, auth, _, _ = app_fixture(tmp_path / "app")
    with sqlite3.connect(auth) as db:
        db.execute(
            "INSERT INTO users VALUES(?,?,?,?)",
            ("second-owner", "owner", "ready", "default"),
        )

    with pytest.raises(LifecycleSourceError, match="Trusted application owner binding"):
        resolve_application_binding(notification_paths)


@pytest.mark.parametrize("failure", ["capacity", "record", "invalid", "scan"])
@pytest.mark.parametrize("existing", [False, True])
def test_whole_preparation_failure_preserves_state_before_remote_writes(
        tmp_path, monkeypatch, failure, existing):
    from deploy import cloud_coordinator as module
    from test_cloud_coordinator import FakeApi, enrolled_record, valid_pr

    notification_paths, state_dir, _, _, _ = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    paths = LifecycleSourcePaths(notifications=notification_paths,
                                 starter_state=tmp_path / "absent-starter.json")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    api = FakeApi(unresolved=True)
    coordinator = module.Coordinator(api, store, clock=NOW.timestamp,
                                     lifecycle_source_paths=paths)
    coordinator.run(apply=True)
    fix = next(a for a in store.actions().values() if a["kind"] == "fix")
    api.complete_task(fix["task_id"], fix)
    api.pull["draft"] = True
    api.review_state = "PENDING"
    merged = pull_event({"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
                        "merged", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE)
    store.enroll(enrolled_record(issue=31, comment=55, last_open_seen=True))
    if existing:
        store.record_lifecycle(merged, now=NOW)
    original_get = api.get
    def get(route):
        if route.endswith("/pulls/31"):
            return valid_pr(number=31, state="closed", merged=True,
                            merged_at="2026-10-01T20:58:00Z", merge_commit_sha=MERGE)
        return original_get(route)
    monkeypatch.setattr(api, "get", get)
    before = store.path.read_bytes()
    snapshot = store.snapshot()
    exported = (state_dir / "workflow-events.json")
    export_before = exported.read_bytes() if exported.exists() else None
    writes, graphql = list(api.writes), list(api.graphql_writes)
    original_add = store._add_lifecycle_events
    def add(data, events, *, now):
        if any(e.get("reason") == "controller_verified" for e in events):
            if failure == "record":
                raise module.CoordinatorError("injected lifecycle record failure")
            if failure == "invalid":
                events = [dict(e, outcome="invalid") if e.get("reason") == "controller_verified"
                          else e for e in events]
        return original_add(data, events, now=now)
    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_add_lifecycle_events", add)
        if failure == "capacity":
            patcher.setattr(module, "MAX_LIFECYCLE_EVENTS", 1)
        if failure == "scan":
            def fail(*args, **kwargs):
                raise module.CoordinatorError("injected scan failure")
            patcher.setattr(store, "commit_scan", fail)
        with pytest.raises(module.CoordinatorError):
            coordinator.run(apply=True)
    assert api.writes == writes and api.graphql_writes == graphql
    assert store.path.read_bytes() == before
    assert store.snapshot() == snapshot
    assert (exported.read_bytes() if exported.exists() else None) == export_before
    coordinator.run(apply=True)
    assert {e["reason"] for e in store.snapshot()["lifecycle_events"]} >= {
        "merged", "controller_verified"}
    assert api.graphql_writes != graphql


def test_unacknowledged_lifecycle_capacity_failure_preserves_export_before_writes(tmp_path):
    from test_cloud_coordinator import Coordinator, CoordinatorError, FakeApi, enrolled_record

    notification_paths, state_dir, _, _, notifications = app_fixture(tmp_path / "app")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    for number in range(256):
        store.record_lifecycle(
            issue_event(
                number + 100, "issue_failed",
                occurred_at="2026-10-01T20:58:00Z", incident=str(number),
            ),
            now=NOW,
        )
    owner, export_directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(
        now=NOW, owner_user_id=owner, directory=export_directory,
    )
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=False, now=NOW)["status"] == "plan"
    api = FakeApi(pull_state="closed", merged=True)
    store.enroll(enrolled_record(last_open_seen=True, last_open_head=HEAD))
    before = store.path.read_bytes()
    export_before = export.read_bytes()
    coordinator = Coordinator(
        api, store, clock=NOW.timestamp,
        lifecycle_source_paths=LifecycleSourcePaths(
            notifications=notification_paths,
            starter_state=tmp_path / "starter" / "state.json",
        ),
    )
    assert coordinator.run()["mode"] == "plan"
    assert store.path.read_bytes() == before
    assert export.read_bytes() == export_before
    assert not (state_dir / "workflow-notifications.sqlite").exists()
    assert not api.writes and not api.graphql_writes

    with pytest.raises(CoordinatorError, match="capacity"):
        coordinator.run(apply=True)

    assert store.path.read_bytes() == before
    assert store.snapshot()["lifecycle_events"] and len(
        store.snapshot()["lifecycle_events"]
    ) == 256
    assert export.read_bytes() == export_before
    assert not api.writes and not api.graphql_writes
    assert notifications.list_inbox(APP_OWNER) == []

    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 256
    result = coordinator.run(apply=True)

    assert result["mode"] == "apply"
    assert [event["reason"] for event in store.snapshot()["lifecycle_events"]] == ["merged"]
    assert len(store.snapshot()["lifecycle_context"]["events"]) == 256
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    with sqlite3.connect(state_dir / "notifications.sqlite") as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone()[0] == 257
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("tamper", [
    "digest", "recipient", "inbox_id", "schema", "repository", "owner", "pending",
])
def test_lifecycle_ack_snapshot_rejects_malformed_or_foreign_rows(tmp_path, tamper):
    from deploy.workflow_notifications import Blocked, read_lifecycle_acknowledgements

    notification_paths, state_dir, _, _, _ = app_fixture(tmp_path / "app")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="ack-test",
    )
    store.record_lifecycle(item, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    process(notification_paths, apply=True, now=NOW)
    adapter = state_dir / "workflow-notifications.sqlite"
    with sqlite3.connect(adapter) as db:
        if tamper == "digest":
            db.execute("UPDATE events SET digest=? WHERE event_id=?", ("0" * 64, item["event_id"]))
        elif tamper == "recipient":
            db.execute(
                "UPDATE events SET recipient_id='synthetic-member' WHERE event_id=?",
                (item["event_id"],),
            )
        elif tamper == "inbox_id":
            db.execute("UPDATE events SET inbox_id=NULL WHERE event_id=?", (item["event_id"],))
        elif tamper == "schema":
            db.execute("UPDATE binding SET version=2")
        elif tamper == "repository":
            db.execute("UPDATE binding SET repository_id=1")
        elif tamper == "pending":
            db.execute(
                "UPDATE events SET status='pending',inbox_id=NULL WHERE event_id=?",
                (item["event_id"],),
            )
        else:
            db.execute("UPDATE binding SET owner_user_id='synthetic-member'")

    if tamper == "pending":
        acknowledgements = read_lifecycle_acknowledgements(adapter, APP_OWNER, [item])
        store.begin_preparation()
        assert store.retire_acknowledged_lifecycle_events(acknowledgements, APP_OWNER) == ()
        assert store.snapshot()["lifecycle_events"] == [item]
        store.discard_preparation()
    else:
        with pytest.raises(Blocked):
            read_lifecycle_acknowledgements(adapter, APP_OWNER, [item])


@pytest.mark.parametrize("context_count", [0, 1, 2], ids=[
    "active-active", "context-active", "context-context",
])
def test_lifecycle_ack_snapshot_rejects_duplicate_inbox_bindings(
        tmp_path, monkeypatch, context_count):
    from deploy.cloud_coordinator import Coordinator, CoordinatorError
    from deploy.workflow_notifications import Blocked, read_lifecycle_acknowledgements

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "absent-starter.json",
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    api = EmptyLifecycleApi()
    coordinator = Coordinator(api, store, clock=NOW.timestamp, lifecycle_source_paths=paths)
    items = [
        issue_event(31, "issue_failed", occurred_at="2026-10-01T20:58:00Z",
                    incident=f"duplicate-inbox-{number}")
        for number in range(3)
    ]
    export = state_dir / "workflow-events.json"
    for batch in (items[:context_count], items[context_count:]):
        if not batch:
            continue
        for item in batch:
            store.record_lifecycle(item, now=NOW)
        store.write_lifecycle_export(now=NOW, owner_user_id=APP_OWNER, directory=state_dir)
        os.utime(export, (NOW.timestamp(), NOW.timestamp()))
        assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == len(batch)
        if batch == items[:context_count]:
            coordinator.run(apply=True)

    snapshot = store.snapshot()
    assert snapshot["lifecycle_events"] == items[context_count:]
    assert (snapshot.get("lifecycle_context") or {}).get("events", []) == items[:context_count]
    adapter = state_dir / "workflow-notifications.sqlite"
    with sqlite3.connect(adapter) as db:
        assert db.execute("SELECT count(DISTINCT inbox_id) FROM events").fetchone() == (3,)
        # Deliberate ledger corruption, not a normal consumer ACK path.
        db.execute(
            "UPDATE events SET inbox_id=(SELECT inbox_id FROM events WHERE event_id=?) "
            "WHERE event_id=?", (items[0]["event_id"], items[1]["event_id"]),
        )
    before = {path: path.read_bytes() for path in (store.path, export, adapter, inbox)}
    with pytest.raises(Blocked, match="Inbox binding"):
        read_lifecycle_acknowledgements(
            adapter, APP_OWNER,
            [*snapshot["lifecycle_events"],
             *(snapshot.get("lifecycle_context") or {}).get("events", [])],
        )

    def unexpected_retirement(*_args, **_kwargs):
        pytest.fail("Invalid ACK snapshot reached retirement")

    monkeypatch.setattr(store, "retire_acknowledged_lifecycle_events", unexpected_retirement)
    with pytest.raises(CoordinatorError, match="Lifecycle ACK or owner evidence") as caught:
        coordinator.run(apply=True)
    assert isinstance(caught.value.__cause__, Blocked)
    assert store.snapshot() == snapshot
    assert {path: path.read_bytes() for path in before} == before
    assert api.writes == [] and api.graphql_writes == []


def test_lifecycle_ack_survives_physical_inbox_retention_and_replay(tmp_path):
    from deploy.cloud_coordinator import Coordinator
    from deploy.workflow_notifications import read_lifecycle_acknowledgements

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "absent-starter.json",
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    acked = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="inbox-retention",
    )
    pending = pull_event(
        {"issue": 32, "head": HEAD, "enrollment": {"comment": 55}},
        "controller_verified", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE,
    )
    for item in (acked, pending):
        store.record_lifecycle(item, now=NOW)
    store.write_lifecycle_export(now=NOW, owner_user_id=APP_OWNER, directory=state_dir)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    original_export = export.read_bytes()
    result = process(notification_paths, apply=True, now=NOW)
    assert result["inbox_items"] == 1
    assert result["deferred"] == [{
        "event_id": pending["event_id"], "status": "deferred",
        "reason": "deployment_evidence_unavailable",
    }]
    adapter = state_dir / "workflow-notifications.sqlite"
    acknowledgements = read_lifecycle_acknowledgements(adapter, APP_OWNER, [acked, pending])
    inbox_id = acknowledgements[acked["event_id"]]["inbox_id"]
    assert acknowledgements == {
        acked["event_id"]: {
            "digest": event_digest(acked), "status": "acked", "inbox_id": inbox_id,
        },
        pending["event_id"]: {
            "digest": event_digest(pending), "status": "pending", "inbox_id": None,
        },
    }
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT id,user_id,delivery_id FROM inbox").fetchall() == [
            (inbox_id, APP_OWNER, "workflow-event:v1:" + acked["event_id"]),
        ]
        # Model physical retention, not UI dismissal; keep foreign keys enforced.
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM notification_policy WHERE inbox_id=?", (inbox_id,))
        db.execute("DELETE FROM inbox WHERE id=?", (inbox_id,))
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    consumer_before = {path: path.read_bytes() for path in (adapter, inbox)}
    assert read_lifecycle_acknowledgements(
        adapter, APP_OWNER, [acked, pending],
    ) == acknowledgements

    api = EmptyLifecycleApi()
    Coordinator(api, store, clock=NOW.timestamp, lifecycle_source_paths=paths).run(apply=True)
    state = StateStore(store.path).snapshot()
    assert state["lifecycle_context"]["events"] == [acked]
    assert state["lifecycle_events"] == [pending]
    assert json.loads(export.read_bytes())["events"] == [pending]
    assert {path: path.read_bytes() for path in consumer_before} == consumer_before

    # Replay the old export after retirement and Inbox removal. The retained ACK
    # deduplicates the delivered event; absence never ACKs the pending deployment.
    export.write_bytes(original_export)
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=False, now=NOW)["status"] == "plan"
    replay = process(notification_paths, apply=True, now=NOW)
    assert replay["inbox_items"] == 0
    assert replay["deferred"] == result["deferred"]
    assert {path: path.read_bytes() for path in consumer_before} == consumer_before
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox").fetchone() == (0,)

    Coordinator(
        api, StateStore(store.path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    restarted = StateStore(store.path).snapshot()
    assert restarted["lifecycle_context"]["events"] == [acked]
    assert restarted["lifecycle_events"] == [pending]
    assert json.loads(export.read_bytes())["events"] == [pending]
    assert {path: path.read_bytes() for path in consumer_before} == consumer_before
    assert api.writes == [] and api.graphql_writes == []


def test_lifecycle_ack_reader_rejects_unsafe_paths_and_sidecars(tmp_path, monkeypatch):
    import deploy.workflow_notifications as notifications_module
    from deploy.workflow_notifications import Blocked, read_lifecycle_acknowledgements

    notification_paths, state_dir, _, _, _ = app_fixture(tmp_path / "app")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="path-test",
    )
    store.record_lifecycle(item, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    process(notification_paths, apply=True, now=NOW)
    adapter = state_dir / "workflow-notifications.sqlite"

    alias = tmp_path / "adapter-alias.sqlite"
    alias.symlink_to(adapter)
    with pytest.raises(Blocked):
        read_lifecycle_acknowledgements(alias, APP_OWNER, [item])

    sidecar = Path(f"{adapter}-wal")
    sidecar.write_bytes(b"")
    with pytest.raises(Blocked):
        read_lifecycle_acknowledgements(adapter, APP_OWNER, [item])
    sidecar.unlink()

    with monkeypatch.context() as patch:
        patch.setattr(notifications_module, "MAX_STATE_BYTES", 1)
        with pytest.raises(Blocked):
            read_lifecycle_acknowledgements(adapter, APP_OWNER, [item])


def test_lifecycle_source_batches_retire_only_acked_outcomes_and_replay_after_restart(tmp_path):
    from test_cloud_coordinator import Coordinator

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    starter_path = tmp_path / "starter" / "state.json"
    coordinator_path = tmp_path / "coordinator" / "state.json"
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=starter_path,
    )
    api = EmptyLifecycleApi()
    commands = {}
    comments = []
    store = StateStore(coordinator_path)

    for batch in range(3):
        for number in range(batch * 120 + 1, (batch + 1) * 120 + 1):
            marker = f"<!-- hermes-issue-starter:blocked:31:{number} -->"
            commands[f"31:{number}"] = {
                "issue": 31, "command_id": number,
                "accepted_title_body_sha256": "d" * 64,
                "accepted_at": "2026-10-01T20:00:00Z", "phase": "failed",
                "receipt": {"kind": "blocked", "state": "sent", "marker": marker},
            }
            comments.append({
                "id": 20000 + number, "user": {"id": GITHUB_OWNER},
                "body": (
                    f"{marker}\nHermes issue starter is blocked for issue #31. "
                    "Owner action is required; no uncertain task was automatically retried."
                ),
                "created_at": f"2026-10-01T20:{number // 60:02d}:{number % 60:02d}Z",
                "updated_at": f"2026-10-01T20:{number // 60:02d}:{number % 60:02d}Z",
            })
        api.comments = comments
        private_json(starter_path, {"version": 1, "commands": commands})
        store = StateStore(coordinator_path)
        Coordinator(
            api, store, clock=NOW.timestamp, lifecycle_source_paths=paths,
        ).run(apply=True)
        export = state_dir / "workflow-events.json"
        os.utime(export, (NOW.timestamp(), NOW.timestamp()))

        if batch < 2:
            assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 120

    state = StateStore(coordinator_path).snapshot()
    assert len(state["lifecycle_context"]["events"]) == 240
    assert len(state["lifecycle_events"]) == 120
    assert len({event["event_id"] for event in [
        *state["lifecycle_context"]["events"], *state["lifecycle_events"],
    ]}) == 360
    assert process(notification_paths, apply=False, now=NOW)["status"] == "plan"
    with sqlite3.connect(state_dir / "workflow-notifications.sqlite") as db:
        assert db.execute("SELECT count(*) FROM events WHERE status='acked'").fetchone()[0] == 240
        assert db.execute(
            "SELECT count(*) FROM events WHERE status='pending'"
        ).fetchone()[0] == 0

    later = NOW + timedelta(days=2)
    unacked_digests = {
        event["event_id"]: event_digest(event) for event in state["lifecycle_events"]
    }
    restarted = Coordinator(
        api, StateStore(coordinator_path), clock=lambda: later.timestamp(),
        lifecycle_source_paths=paths,
    )
    restarted.run(apply=True)
    replayed = StateStore(coordinator_path).snapshot()["lifecycle_events"]
    assert len(replayed) == 120
    assert {event["event_id"]: event_digest(event) for event in replayed} == unacked_digests
    export = state_dir / "workflow-events.json"
    os.utime(export, (later.timestamp(), later.timestamp()))
    assert process(notification_paths, apply=True, now=later)["inbox_items"] == 120
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone()[0] == 360
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone()[0] == 0

    restarted.run(apply=True)
    state = StateStore(coordinator_path).snapshot()
    assert state["lifecycle_events"] == []
    assert len(state["lifecycle_context"]["events"]) == 360
    assert not export.exists()


@pytest.mark.parametrize("crash_point", ["before_retirement_commit", "after_retirement_commit"])
def test_ack_retirement_crashes_recover_without_losing_inbox_or_history(
        tmp_path, monkeypatch, crash_point):
    from test_cloud_coordinator import Coordinator

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    store_path = tmp_path / "coordinator" / "state.json"
    store = StateStore(store_path)
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="crash-test",
    )
    store.record_lifecycle(item, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    before, exported_before = store_path.read_bytes(), export.read_bytes()
    api = EmptyLifecycleApi()

    if crash_point == "before_retirement_commit":
        def crash_commit(*_args, **_kwargs):
            raise SystemExit("crash before retirement commit")
        monkeypatch.setattr(store, "commit_scan", crash_commit)
    else:
        def crash_export(**_kwargs):
            raise SystemExit("crash after retirement commit")
        monkeypatch.setattr(store, "write_lifecycle_export", crash_export)

    with pytest.raises(SystemExit):
        Coordinator(
            api, store, clock=NOW.timestamp, lifecycle_source_paths=paths,
        ).run(apply=True)

    if crash_point == "before_retirement_commit":
        assert store_path.read_bytes() == before
        assert export.read_bytes() == exported_before
    else:
        assert StateStore(store_path).snapshot()["lifecycle_events"] == []
        assert StateStore(store_path).snapshot()["lifecycle_context"]["events"] == [item]
        assert export.read_bytes() == exported_before
    assert api.writes == [] and api.graphql_writes == []

    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    state = StateStore(store_path).snapshot()
    assert state["lifecycle_events"] == []
    assert state["lifecycle_context"]["events"] == [item]
    assert not export.exists()
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone()[0] == 1


def test_lifecycle_owner_is_rechecked_before_retirement_commit(tmp_path, monkeypatch):
    from deploy.cloud_coordinator import Coordinator, CoordinatorError
    import deploy.workflow_lifecycle_sources as lifecycle_sources

    notification_paths, state_dir, auth, _, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="owner-race",
    )
    store.record_lifecycle(item, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    before, exported_before = store.path.read_bytes(), export.read_bytes()
    original = lifecycle_sources.resolve_application_binding
    calls = 0

    def change_owner_before_recheck(paths=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(auth) as db:
                db.execute(
                    "UPDATE users SET id='replacement-owner' WHERE id=?",
                    (APP_OWNER,),
                )
        return original(paths)

    monkeypatch.setattr(lifecycle_sources, "resolve_application_binding", change_owner_before_recheck)
    with pytest.raises(CoordinatorError, match="changed before state commit"):
        Coordinator(
            EmptyLifecycleApi(), store, clock=NOW.timestamp, lifecycle_source_paths=paths,
        ).run(apply=True)

    assert store.path.read_bytes() == before
    assert export.read_bytes() == exported_before
    assert not (state_dir / "workflow-notifications.sqlite").exists()


@pytest.mark.parametrize("change", [
    {"repository_id": 1}, {"repository": "foreign/repository"},
    {"owner_user_id": "foreign-owner"}, {"unexpected": True},
])
def test_lifecycle_context_requires_closed_owner_repository_binding(tmp_path, change):
    from deploy.cloud_coordinator import CoordinatorError

    store = StateStore(tmp_path / "coordinator" / "state.json")
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="context-test",
    )
    store.record_lifecycle(item, now=NOW)
    data = json.loads(store.path.read_text())
    data["lifecycle_context"] = {
        "version": 1, "repository_id": 1399942965,
        "repository": "lindayi/hermes-mobile",
        "owner_user_id": APP_OWNER, "events": [item],
    } | change
    store.path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    store.path.chmod(0o600)

    if "owner_user_id" in change:
        store = StateStore(store.path)
        store.begin_preparation()
        with pytest.raises(CoordinatorError, match="owner or repository binding"):
            store.retire_acknowledged_lifecycle_events({}, APP_OWNER)
        store.discard_preparation()
    else:
        with pytest.raises(CoordinatorError, match="lifecycle context"):
            StateStore(store.path).snapshot()


def test_context_is_not_ack_authority_and_preserves_persistent_incident_payload():
    from deploy.workflow_lifecycle import filter_acknowledged_replays

    original = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "policy_broken", occurred_at="2026-10-01T20:58:00Z",
        incident="up-to-date-policy",
    )
    regenerated = pull_event(
        {"issue": 31, "head": "c" * 40, "enrollment": {"comment": 55}},
        "policy_broken", occurred_at="2026-10-01T20:59:00Z",
        incident="up-to-date-policy",
    )
    assert regenerated["event_id"] == original["event_id"]
    assert regenerated != original

    assert filter_acknowledged_replays(
        [regenerated], context=[original], now=NOW,
    ) == [original]
    assert filter_acknowledged_replays(
        [original], active=[original], context=[original], now=NOW,
    ) == []
    assert filter_acknowledged_replays(
        [regenerated], context=[original],
        acknowledgements={original["event_id"]: {
            "digest": event_digest(original), "status": "acked", "inbox_id": "durable-inbox",
        }},
        now=NOW,
    ) == []


def test_lifecycle_context_duplicate_json_fields_are_rejected(tmp_path):
    from deploy.cloud_coordinator import CoordinatorError

    store = StateStore(tmp_path / "coordinator" / "state.json")
    item = issue_event(
        31, "issue_failed", occurred_at="2026-10-01T20:58:00Z", incident="duplicate-context",
    )
    store.record_lifecycle(item, now=NOW)
    data = json.loads(store.path.read_text())
    data["lifecycle_context"] = {
        "version": 1, "repository_id": 1399942965,
        "repository": "lindayi/hermes-mobile",
        "owner_user_id": APP_OWNER, "events": [item],
    }
    store.path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    store.path.chmod(0o600)
    raw = store.path.read_text()
    store.path.write_text(raw.replace(
        '"repository_id":1399942965',
        '"repository_id":1399942965,"repository_id":1399942965',
        1,
    ))

    with pytest.raises(CoordinatorError, match="unreadable"):
        StateStore(store.path).snapshot()


@pytest.mark.parametrize("proof", ["valid", "wrong_sha", "stale_ledger"])
def test_acked_merge_context_supports_only_exact_later_controller_proof(tmp_path, proof):
    from test_cloud_coordinator import Coordinator

    notification_paths, state_dir, _, _, _ = app_fixture(tmp_path / "app")
    controller_evidence(notification_paths)
    if proof == "wrong_sha":
        private_json(
            notification_paths.controller_state / "status.json",
            {"status": "succeeded", "git_sha": "c" * 40, "release": "c" * 32},
            modified=NOW.timestamp() - 10,
        )
    elif proof == "stale_ledger":
        ledger = notification_paths.delivery_state
        os.utime(ledger, (NOW.timestamp() - 2 * 86400, NOW.timestamp() - 2 * 86400))
    store = StateStore(tmp_path / "coordinator" / "state.json")
    merged = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "merged", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE,
    )
    store.record_lifecycle(merged, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )

    Coordinator(
        EmptyLifecycleApi(), StateStore(store.path), clock=NOW.timestamp,
        lifecycle_source_paths=paths,
    ).run(apply=True)
    state = StateStore(store.path).snapshot()
    assert state["lifecycle_context"]["events"] == [merged]
    expected_deployments = [event for event in state["lifecycle_events"]
                            if event["reason"] == "controller_verified"]
    assert bool(expected_deployments) is (proof == "valid")
    if proof == "valid":
        deployed = expected_deployments[0]
        assert deployed["merge_sha"] == MERGE and deployed["head_sha"] == HEAD
        assert event_digest(state["lifecycle_context"]["events"][0]) == event_digest(merged)
        assert json.loads(export.read_bytes())["events"] == [deployed]
        os.utime(export, (NOW.timestamp(), NOW.timestamp()))
        assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
        Coordinator(
            EmptyLifecycleApi(), StateStore(store.path), clock=NOW.timestamp,
            lifecycle_source_paths=paths,
        ).run(apply=True)
        final = StateStore(store.path).snapshot()
        assert final["lifecycle_events"] == []
        assert [event["reason"] for event in final["lifecycle_context"]["events"]] == [
            "merged", "controller_verified",
        ]
    else:
        assert state["lifecycle_events"] == []
        assert not export.exists()


@pytest.mark.parametrize("proof", ["wrong_sha", "stale_ledger"])
def test_durable_retired_merge_reloads_before_late_controller_proof(tmp_path, proof):
    from test_cloud_coordinator import Coordinator

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    store = StateStore(tmp_path / "coordinator" / "state.json")
    merged = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "merged", occurred_at="2026-10-01T20:58:00Z", merge_sha=MERGE,
    )
    store.record_lifecycle(merged, now=NOW)
    owner, directory = resolve_application_binding(notification_paths)
    store.write_lifecycle_export(now=NOW, owner_user_id=owner, directory=directory)
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    assert not notification_paths.controller_state.exists()

    Coordinator(
        EmptyLifecycleApi(), store, clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    store_path = store.path
    del store
    retired = StateStore(store_path).snapshot()
    assert retired["lifecycle_events"] == []
    assert retired["lifecycle_context"]["events"] == [merged]
    assert not export.exists()

    controller_evidence(notification_paths)
    if proof == "wrong_sha":
        private_json(
            notification_paths.controller_state / "status.json",
            {"status": "succeeded", "git_sha": "d" * 40, "release": "c" * 32},
            modified=NOW.timestamp() - 10,
        )
    else:
        stale = NOW.timestamp() - 2 * 86400
        os.utime(notification_paths.delivery_state, (stale, stale))
    Coordinator(
        EmptyLifecycleApi(), StateStore(store_path), clock=NOW.timestamp,
        lifecycle_source_paths=paths,
    ).run(apply=True)
    rejected = StateStore(store_path).snapshot()
    assert rejected["lifecycle_events"] == []
    assert rejected["lifecycle_context"]["events"] == [merged]
    assert not export.exists()

    if proof == "wrong_sha":
        private_json(
            notification_paths.controller_state / "status.json",
            {"status": "succeeded", "git_sha": MERGE, "release": "c" * 32},
            modified=NOW.timestamp() - 10,
        )
    else:
        os.utime(notification_paths.delivery_state, (NOW.timestamp() - 1,) * 2)
    later = NOW + timedelta(minutes=1)
    Coordinator(
        EmptyLifecycleApi(), StateStore(store_path), clock=lambda: later.timestamp(),
        lifecycle_source_paths=paths,
    ).run(apply=True)
    state = StateStore(store_path).snapshot()
    assert state["lifecycle_context"]["events"] == [merged]
    assert len(state["lifecycle_events"]) == 1
    deployed = state["lifecycle_events"][0]
    assert deployed["reason"] == "controller_verified"
    assert (deployed["pr_number"], deployed["head_sha"], deployed["merge_sha"]) == (
        merged["pr_number"], HEAD, MERGE,
    )
    assert event_digest(state["lifecycle_context"]["events"][0]) == event_digest(merged)
    assert json.loads(export.read_bytes())["events"] == [deployed]
    os.utime(export, (later.timestamp(), later.timestamp()))
    assert process(notification_paths, apply=True, now=later)["inbox_items"] == 1
    Coordinator(
        EmptyLifecycleApi(), StateStore(store_path), clock=lambda: later.timestamp(),
        lifecycle_source_paths=paths,
    ).run(apply=True)
    final = StateStore(store_path).snapshot()
    assert final["lifecycle_events"] == []
    assert final["lifecycle_context"]["events"] == [merged, deployed]
    assert not export.exists()
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone() == (2,)
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone() == (0,)


def write_starter_outcomes(path, api, phases):
    commands, comments = {}, []
    for number, phase in enumerate(phases, 1):
        marker = f"<!-- hermes-issue-starter:blocked:31:{number} -->"
        occurred = f"2026-10-01T20:58:{number:02d}Z"
        commands[f"31:{number}"] = {
            "issue": 31, "command_id": number,
            "accepted_title_body_sha256": "d" * 64,
            "accepted_at": "2026-10-01T20:00:00Z", "phase": phase,
            "receipt": {"kind": "blocked", "state": "sent", "marker": marker},
        }
        comments.append({
            "id": 20000 + number, "user": {"id": GITHUB_OWNER},
            "body": (
                f"{marker}\nHermes issue starter is blocked for issue #31. "
                "Owner action is required; no uncertain task was automatically retried."
            ),
            "created_at": occurred, "updated_at": occurred,
        })
    api.comments = comments
    private_json(path, {"version": 1, "commands": commands})


def test_interrupted_consumer_ack_preserves_mixed_producer_outcomes(tmp_path, monkeypatch):
    from deploy import workflow_notifications as adapter
    from test_cloud_coordinator import Coordinator

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    api = EmptyLifecycleApi()
    write_starter_outcomes(paths.starter_state, api, ["failed", "unknown", "handoff_uncertain"])
    store_path = tmp_path / "coordinator" / "state.json"
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    acked, pending, unreserved = StateStore(store_path).snapshot()["lifecycle_events"]
    assert [item["reason"] for item in (acked, pending, unreserved)] == [
        "issue_failed", "execution_uncertain", "execution_uncertain",
    ]
    export = state_dir / "workflow-events.json"
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    original_connection = adapter._state_connection

    class InterruptedConnection:
        def __init__(self, db):
            self.db = db
            self.interrupt = False

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, parameters=()):
            result = self.db.execute(sql, parameters)
            if sql.startswith("UPDATE events SET status='acked'"):
                self.interrupt = parameters[2] == pending["event_id"]
            return result

        def commit(self):
            if self.interrupt:
                raise SystemExit("interrupted after Inbox commit before consumer ACK commit")
            self.db.commit()

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "_state_connection",
                      lambda path: InterruptedConnection(original_connection(path)))
        with pytest.raises(SystemExit, match="before consumer ACK commit"):
            process(notification_paths, apply=True, now=NOW)
    ledger = state_dir / adapter.ADAPTER_STATE_NAME
    with sqlite3.connect(ledger) as db:
        rows = db.execute(
            "SELECT event_id,digest,status,inbox_id FROM events ORDER BY created_at,event_id"
        ).fetchall()
        assert {row[0]: row[2] for row in rows} == {
            acked["event_id"]: "acked", pending["event_id"]: "pending",
        }
        assert next(row for row in rows if row[0] == pending["event_id"])[1:] == (
            event_digest(pending), "pending", None,
        )
    with sqlite3.connect(inbox) as db:
        pending_inbox = db.execute(
            "SELECT id FROM inbox WHERE user_id=? AND delivery_id=?",
            (APP_OWNER, "workflow-event:v1:" + pending["event_id"]),
        ).fetchone()[0]
        assert db.execute("SELECT count(*) FROM inbox").fetchone() == (2,)
    consumer_before = {path: path.read_bytes() for path in (ledger, inbox)}

    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    state = StateStore(store_path).snapshot()
    assert state["lifecycle_context"]["events"] == [acked]
    assert state["lifecycle_events"] == [pending, unreserved]
    assert json.loads(export.read_bytes())["events"] == [pending, unreserved]
    assert {path: path.read_bytes() for path in consumer_before} == consumer_before
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 2
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 0
    with sqlite3.connect(ledger) as db:
        assert db.execute(
            "SELECT digest,status,inbox_id FROM events WHERE event_id=?",
            (pending["event_id"],),
        ).fetchone() == (event_digest(pending), "acked", pending_inbox)
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone() == (3,)
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone() == (0,)
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    final = StateStore(store_path).snapshot()
    assert final["lifecycle_events"] == []
    assert final["lifecycle_context"]["events"] == [acked, pending, unreserved]
    assert not export.exists()


def test_persistent_coordinator_incident_replays_canonical_retired_payload(tmp_path):
    from deploy.cloud_coordinator import Coordinator as CloudCoordinator
    from test_cloud_coordinator import Coordinator, FakeApi

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    api = FakeApi(strict_protection=False)
    store_path = tmp_path / "coordinator" / "state.json"
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    original = StateStore(store_path).snapshot()["lifecycle_events"]
    assert len(original) == 1 and original[0]["reason"] == "policy_broken"
    export = state_dir / "workflow-events.json"
    assert json.loads(export.read_bytes())["events"] == original
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    retired = StateStore(store_path).snapshot()
    assert retired["lifecycle_events"] == []
    assert retired["lifecycle_context"]["events"] == original
    assert not export.exists()
    ledger = state_dir / "workflow-notifications.sqlite"
    consumer_before = {path: path.read_bytes() for path in (ledger, inbox)}

    later = NOW + timedelta(hours=1)
    api.head_sha = "d" * 40
    api.pull["head"]["sha"] = api.head_sha
    class ReplayCoordinator(CloudCoordinator):
        def _build_plan(self, *, apply):
            plan = super()._build_plan(apply=apply)
            regenerated, = plan["pull_requests"][0]["lifecycle_events"]
            assert regenerated["event_id"] == original[0]["event_id"]
            assert regenerated["head_sha"] == api.head_sha != original[0]["head_sha"]
            assert regenerated["occurred_at"] == later.isoformat().replace("+00:00", "Z")
            assert regenerated["occurred_at"] != original[0]["occurred_at"]
            return plan

    for _ in range(2):
        result = ReplayCoordinator(
            api, StateStore(store_path), clock=lambda: later.timestamp(),
            lifecycle_source_paths=paths,
        ).run(apply=True)
        assert "up-to-date-policy" in result["pull_requests"][0]["reasons"]
        replayed = StateStore(store_path).snapshot()
        assert replayed["lifecycle_events"] == []
        assert replayed["lifecycle_context"]["events"] == original
        assert event_digest(replayed["lifecycle_context"]["events"][0]) == event_digest(original[0])
        assert not export.exists()
    assert {path: path.read_bytes() for path in consumer_before} == consumer_before
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone() == (0,)


def test_retention_context_growth_byte_boundary_is_atomic_and_restart_safe(tmp_path, monkeypatch):
    from deploy import cloud_coordinator
    from test_cloud_coordinator import Coordinator, CoordinatorError

    notification_paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    paths = LifecycleSourcePaths(
        notifications=notification_paths, starter_state=tmp_path / "starter" / "state.json",
    )
    api = EmptyLifecycleApi()
    store_path = tmp_path / "coordinator" / "state.json"
    export = state_dir / "workflow-events.json"
    ledger = state_dir / "workflow-notifications.sqlite"
    write_starter_outcomes(paths.starter_state, api, ["failed"])
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    first = StateStore(store_path).snapshot()["lifecycle_events"][0]
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    write_starter_outcomes(paths.starter_state, api, ["failed", "failed"])
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    second = StateStore(store_path).snapshot()["lifecycle_events"][0]
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 1
    unacked = issue_event(
        31, "execution_uncertain", occurred_at="2026-10-01T20:59:00Z",
        incident="byte-bound-unacked",
    )
    store = StateStore(store_path)
    store.record_lifecycle(unacked, now=NOW)
    store.write_lifecycle_export(now=NOW, owner_user_id=APP_OWNER, directory=state_dir)
    baseline = StateStore(store_path).snapshot()
    assert baseline["lifecycle_context"]["events"] == [first]
    assert baseline["lifecycle_events"] == [second, unacked]
    write_starter_outcomes(paths.starter_state, api, ["failed", "failed", "unknown"])
    before = {path: path.read_bytes() for path in (store_path, export, inbox, ledger)}
    bound = len(before[store_path]) + 32
    attempted = []
    original_save = store._save

    def capture_save(data):
        attempted.append(json.loads(json.dumps(data)))
        return original_save(data)

    monkeypatch.setattr(store, "_save", capture_save)
    with monkeypatch.context() as patch:
        patch.setattr(cloud_coordinator, "MAX_STATE_BYTES", bound)
        with pytest.raises(CoordinatorError, match="prior state was preserved"):
            Coordinator(
                api, store, clock=NOW.timestamp, lifecycle_source_paths=paths,
            ).run(apply=True)
        assert {path: path.read_bytes() for path in before} == before
        assert StateStore(store_path).snapshot() == baseline
    assert len(attempted) == 1
    prepared = attempted[0]
    assert prepared["lifecycle_context"]["events"] == [first, second]
    assert prepared["lifecycle_events"][0] == unacked
    assert len(prepared["lifecycle_events"]) == 2
    assert len(json.dumps(prepared, separators=(",", ":"), sort_keys=True).encode()) > bound
    prepared["lifecycle_context"]["events"].remove(second)
    assert len(json.dumps(prepared, separators=(",", ":"), sort_keys=True).encode()) <= bound
    assert store.snapshot() == baseline
    assert not api.writes and not api.graphql_writes

    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    recovered = StateStore(store_path).snapshot()
    assert recovered["lifecycle_context"]["events"] == [first, second]
    assert recovered["lifecycle_events"][0] == unacked
    assert len(recovered["lifecycle_events"]) == 2
    assert json.loads(export.read_bytes())["events"] == recovered["lifecycle_events"]
    assert inbox.read_bytes() == before[inbox] and ledger.read_bytes() == before[ledger]
    os.utime(export, (NOW.timestamp(), NOW.timestamp()))
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 2
    assert process(notification_paths, apply=True, now=NOW)["inbox_items"] == 0
    Coordinator(
        api, StateStore(store_path), clock=NOW.timestamp, lifecycle_source_paths=paths,
    ).run(apply=True)
    final = StateStore(store_path).snapshot()
    assert final["lifecycle_events"] == []
    assert final["lifecycle_context"]["events"] == [
        first, second, *recovered["lifecycle_events"],
    ]
    assert not export.exists()
    with sqlite3.connect(inbox) as db:
        assert db.execute("SELECT count(*) FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchone() == (4,)
        assert db.execute(
            "SELECT count(*) FROM inbox WHERE user_id='synthetic-member'"
        ).fetchone() == (0,)
