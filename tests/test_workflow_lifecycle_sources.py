from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from deploy.workflow_events import event_digest
from deploy.workflow_lifecycle import pull_event
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


def test_application_owner_binding_rejects_multiple_ready_default_owners(tmp_path):
    notification_paths, _, auth, _, _ = app_fixture(tmp_path / "app")
    with sqlite3.connect(auth) as db:
        db.execute(
            "INSERT INTO users VALUES(?,?,?,?)",
            ("second-owner", "owner", "ready", "default"),
        )

    with pytest.raises(LifecycleSourceError, match="Trusted application owner binding"):
        resolve_application_binding(notification_paths)
