from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from deploy.workflow_events import event_digest
from deploy.workflow_lifecycle import issue_event, pull_event
from deploy.workflow_notifications import Paths as NotificationPaths, process
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


def test_pull_lifecycle_events_preserve_authenticated_linked_issue_identity():
    linked = pull_event(
        {
            "issue": 86, "head": HEAD,
            "enrollment": {
                "comment": 127, "starter_admission": {"issue_number": 85},
            },
        },
        "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
        incident="source-repair-no-progress-3-of-4",
        stop_detail={
            "cause": "no-progress", "used": 3, "remaining": 0, "limit": 3,
            "source_used": 4, "source_ceiling": 20, "stagnation_count": 3,
        },
    )
    unlinked = pull_event(
        {"issue": 86, "head": HEAD, "enrollment": {"comment": 127}},
        "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
        incident="source-repair-limit-20",
    )
    linked_policy = pull_event(
        {
            "issue": 86, "head": HEAD,
            "enrollment": {
                "comment": 127, "starter_admission": {"issue_number": 85},
            },
        },
        "policy_broken", occurred_at="2026-10-01T20:58:00Z",
        incident="up-to-date-policy",
    )
    unlinked_policy = pull_event(
        {"issue": 86, "head": HEAD, "enrollment": {"comment": 127}},
        "policy_broken", occurred_at="2026-10-01T20:58:00Z",
        incident="up-to-date-policy",
    )

    assert linked["issue_number"] == 85 and linked["pr_number"] == 86
    assert unlinked["issue_number"] == unlinked["pr_number"] == 86
    assert linked_policy["issue_number"] == linked_policy["pr_number"] == 86
    assert linked_policy["event_id"] == unlinked_policy["event_id"]


@pytest.mark.parametrize("source_used", [0, 1, 2, 21, True, "3"])
def test_stop_detail_rejects_impossible_source_reservations(source_used):
    with pytest.raises(ValueError):
        event = pull_event(
            {"issue": 86, "head": HEAD, "enrollment": {"comment": 127}},
            "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
            incident="source-repair-no-progress",
            stop_detail={
                "cause": "no-progress", "used": 3, "remaining": 0, "limit": 3,
                "source_used": source_used, "source_ceiling": 20, "stagnation_count": 3,
            },
        )
        from deploy.workflow_lifecycle import validate_event
        validate_event(event, now=NOW)


def test_linked_stop_event_upgrade_preserves_acknowledged_legacy_identity():
    from deploy.workflow_lifecycle import (
        filter_acknowledged_replays, merge_events, validate_event,
    )

    snapshot = {
        "issue": 86, "head": HEAD,
        "enrollment": {
            "comment": 127, "starter_admission": {"issue_number": 85},
        },
    }
    legacy = pull_event(
        snapshot, "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
        incident="source-repair-no-progress-3-of-4",
    )
    validate_event(legacy, now=NOW)
    upgraded = pull_event(
        snapshot, "execution_exhausted", occurred_at="2026-10-01T20:59:00Z",
        incident="source-repair-no-progress-3-of-4",
        stop_detail={
            "cause": "no-progress", "used": 3, "remaining": 0, "limit": 3,
            "source_used": 4, "source_ceiling": 20, "stagnation_count": 3,
        },
    )
    acknowledgement = {
        legacy["event_id"]: {
            "digest": event_digest(legacy), "status": "acked",
            "inbox_id": "synthetic-legacy-inbox",
        },
    }

    assert legacy["issue_number"] == legacy["pr_number"] == 86
    assert upgraded["issue_number"] == 85 and upgraded["pr_number"] == 86
    assert upgraded["event_id"] != legacy["event_id"]
    assert merge_events([legacy], [upgraded], now=NOW) == [legacy, upgraded]
    assert filter_acknowledged_replays(
        [legacy, upgraded], context=[legacy], acknowledgements=acknowledgement,
        now=NOW,
    ) == [upgraded]


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


def test_application_owner_binding_rejects_multiple_ready_default_owners(tmp_path):
    notification_paths, _, auth, _, _ = app_fixture(tmp_path / "app")
    with sqlite3.connect(auth) as db:
        db.execute(
            "INSERT INTO users VALUES(?,?,?,?)",
            ("second-owner", "owner", "ready", "default"),
        )

    with pytest.raises(LifecycleSourceError, match="Trusted application owner binding"):
        resolve_application_binding(notification_paths)


def test_context_is_not_ack_authority_and_preserves_persistent_incident_payload():
    from deploy.workflow_lifecycle import canonical_event, filter_acknowledged_replays

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

    detail = {
        "cause": "neutral-ceiling", "used": 3, "remaining": 0, "limit": 3,
        "source_used": 4, "source_ceiling": 20, "stagnation_count": 1,
    }
    exhausted = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
        incident="neutral-reconciliation-limit-3", stop_detail=detail,
    )
    legacy_exhausted = pull_event(
        {"issue": 31, "head": HEAD, "enrollment": {"comment": 55}},
        "execution_exhausted", occurred_at="2026-10-01T20:58:00Z",
        incident="neutral-reconciliation-limit-3",
    )
    assert canonical_event(exhausted, legacy_exhausted, now=NOW) == legacy_exhausted
    altered = exhausted | {"stop_detail": detail | {"source_used": 5}}
    assert altered["event_id"] == exhausted["event_id"]
    with pytest.raises(ValueError, match="immutable payload"):
        canonical_event(altered, exhausted, now=NOW)


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


