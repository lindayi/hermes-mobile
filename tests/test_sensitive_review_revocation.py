"""PR44: selected-review revocation must reach the existing owner Inbox."""
import hashlib
import json
import os
import sqlite3

import pytest

from deploy.cloud_coordinator import Coordinator, CoordinatorError, StateStore
from test_cloud_coordinator import FakeApi, HEAD, OWNER


NOW = 1790888400


class RevocationApi(FakeApi):
    revocation = None

    def get_all(self, route, *, collection=None):
        rows = super().get_all(route, collection=collection)
        if route.endswith("/pulls/16/reviews?per_page=100") and self.revocation:
            owner = next((r for r in rows if r["id"] == self.owner_review_id), None)
            if owner and self.revocation == "edited":
                owner["body"] += " "
            elif owner and self.revocation == "removed":
                rows.remove(owner)
            elif owner and self.revocation == "dismissed":
                owner["state"] = "DISMISSED"
            elif owner and self.revocation == "superseded":
                rows.append(owner | {"id": self.owner_review_id + 1,
                                     "state": "CHANGES_REQUESTED", "body": "Not approved",
                                     "submitted_at": "2026-10-01T20:00:00Z"})
        return rows


def authorize(api, comment_id=124):
    api.authorize_sha_review = True
    api.comments.append({
        "id": comment_id, "user": {"id": OWNER},
        "body": (f"/hermes authorize-sensitive {api.head_sha} review "
                 f"{api.owner_review_id} {api.owner_review_digest}"),
        "updated_at": "2026-10-01T21:00:00Z",
    })


@pytest.mark.parametrize("change", ["edited", "removed", "dismissed", "superseded", "legacy"])
def test_regressions_revocation_commits_before_export(tmp_path, change):
    api = RevocationApi(sensitive=True, authorize=True)
    api.pending_required = True  # Isolate authorization from otherwise eligible merge.
    store = StateStore(tmp_path / "state.json")
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD
    if change == "legacy":
        data = store.snapshot()
        data["enrollments"]["16"].pop("sensitive_authorization")
        data["enrollments"]["16"].pop("targeted_review")
        store._save(data)
    else:
        api.revocation = change
    before = store.path.read_bytes()
    # Read-only planning may deny, but never modifies durable authorization.
    result = Coordinator(api, store, clock=lambda: NOW + 1).run()
    assert "sensitive" in result["pull_requests"][0]["reasons"]
    assert store.path.read_bytes() == before
    result = Coordinator(api, StateStore(store.path), clock=lambda: NOW + 2,
                         owner_user_id="synthetic-mobile-owner").run(apply=True)
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] is None
    assert enrollment["sensitive_authorization"] is None
    assert enrollment["targeted_review"] is None
    events = json.loads((tmp_path / "workflow-events.json").read_text())["events"]
    assert [e["reason"] for e in events] == ["sensitive_approval"]
    assert events[0]["head_sha"] == HEAD
    assert "sensitive" in result["pull_requests"][0]["reasons"]
    assert enrollment["attempts"] == 0 and api.fix_attempts == 0
    assert not api.graphql_writes
    # New selected review + genuinely fresh command supersedes this request,
    # without deleting its immutable event or trusting the previous selection.
    api.revocation = None
    api.owner_review_id += 2
    api.owner_review_body = api.owner_review_body.replace("c" * 64, "d" * 64)
    api.owner_review_digest = hashlib.sha256(api.owner_review_body.encode()).hexdigest()
    authorize(api, comment_id=125)
    Coordinator(api, StateStore(store.path), clock=lambda: NOW + 3,
                owner_user_id="synthetic-mobile-owner").run(apply=True)
    assert not (tmp_path / "workflow-events.json").exists()
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD
    assert events[0] in store.snapshot()["lifecycle_events"]


@pytest.mark.parametrize("ack_initial", [False, True])
def test_regressions_revocation_episode_reaches_real_inbox_after_restart(tmp_path, ack_initial):
    from deploy.workflow_lifecycle_sources import LifecycleSourcePaths
    from deploy.workflow_notifications import process
    from test_workflow_lifecycle_sources import app_fixture, APP_OWNER, NOW as CLOCK

    paths, state_dir, _, inbox, notifications = app_fixture(tmp_path / "app")
    sources = LifecycleSourcePaths(notifications=paths,
                                   starter_state=tmp_path / "starter" / "state.json")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    api = RevocationApi(sensitive=True)
    api.pending_required = True
    export = state_dir / "workflow-events.json"

    def poll():
        return Coordinator(api, StateStore(store.path), clock=CLOCK.timestamp,
                           lifecycle_source_paths=sources).run(apply=True)

    def consume():
        os.utime(export, (CLOCK.timestamp(), CLOCK.timestamp()))
        return process(paths, apply=True, now=CLOCK)

    poll()
    initial = json.loads(export.read_text())["events"][0]
    if ack_initial:
        assert consume()["inbox_items"] == 1
    authorize(api)
    poll()
    assert not export.exists()
    if ack_initial:
        assert initial in store.snapshot()["lifecycle_context"]["events"]
    else:
        assert initial in store.snapshot()["lifecycle_events"]
    api.revocation = "edited"
    poll()
    assert export.exists(), "old ACK must not suppress the renewed approval request"
    renewed = json.loads(export.read_text())["events"]
    assert len(renewed) == 1  # Never re-export older unACKed request episodes.
    assert renewed[0]["event_id"] != initial["event_id"]
    assert consume()["inbox_items"] == 1
    assert consume()["inbox_items"] == 0
    poll()
    assert not export.exists()
    assert renewed[0] in store.snapshot()["lifecycle_context"]["events"]

    # Restoring the same review does not restore consumed authorization.
    api.revocation = None
    poll()
    assert not export.exists()
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    # A NEW command may select the very same review ID/body; its next revocation
    # must still be a distinct authorization episode, not a review-digest identity.
    authorize(api, comment_id=125)
    poll()
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD
    assert not export.exists()
    api.revocation = "dismissed"
    poll()
    again = json.loads(export.read_text())["events"]
    assert len(again) == 1
    assert again[0]["event_id"] not in {initial["event_id"], renewed[0]["event_id"]}
    assert consume()["inbox_items"] == 1
    poll()
    poll()
    assert not export.exists()
    history = store.snapshot()["lifecycle_context"]["events"]
    assert renewed[0] in history and again[0] in history
    assert initial in (history if ack_initial else store.snapshot()["lifecycle_events"])
    with sqlite3.connect(inbox) as db:
        deliveries = db.execute("SELECT delivery_id FROM inbox WHERE user_id=?", (APP_OWNER,)).fetchall()
    assert len(deliveries) == (3 if ack_initial else 2)
    assert len(set(deliveries)) == len(deliveries)
    assert notifications.list_inbox("synthetic-member") == []
    assert store.snapshot()["enrollments"]["16"]["attempts"] == 0
    assert api.fix_attempts == 0 and not api.graphql_writes


@pytest.mark.parametrize("failure", ["capacity", "replace", "incomplete_reviews"])
def test_regressions_revocation_failure_preserves_producer_export_and_consumer(
        tmp_path, monkeypatch, failure):
    from deploy import cloud_coordinator as module
    from deploy.workflow_lifecycle import pull_event
    from deploy.workflow_lifecycle_sources import LifecycleSourcePaths
    from deploy.workflow_notifications import process
    from test_workflow_lifecycle_sources import app_fixture, NOW as CLOCK

    paths, state_dir, _, inbox, _ = app_fixture(tmp_path / "app")
    sources = LifecycleSourcePaths(notifications=paths,
                                   starter_state=tmp_path / "starter" / "state.json")
    store = StateStore(tmp_path / "coordinator" / "state.json")
    api = RevocationApi(sensitive=True)
    api.pending_required = True
    coordinator = Coordinator(api, store, clock=CLOCK.timestamp, lifecycle_source_paths=sources)
    coordinator.run(apply=True)
    export = state_dir / "workflow-events.json"
    os.utime(export, (CLOCK.timestamp(), CLOCK.timestamp()))
    assert process(paths, apply=True, now=CLOCK)["inbox_items"] == 1
    authorize(api)
    coordinator.run(apply=True)
    # Retain a real exported/ACKed outcome so failed preparation also proves that
    # retirement and export replacement/removal did not leak out before commit.
    outcome = pull_event({"issue": 16, "head": HEAD,
                          "enrollment": store.snapshot()["enrollments"]["16"]},
                         "task_failed", occurred_at="2026-10-01T20:59:00Z")
    store.record_lifecycle(outcome, now=CLOCK)
    store.write_lifecycle_export(now=CLOCK, owner_user_id="synthetic-mobile-owner",
                                 directory=state_dir)
    os.utime(export, (CLOCK.timestamp(), CLOCK.timestamp()))
    assert process(paths, apply=True, now=CLOCK)["inbox_items"] == 1
    files = [store.path, export, inbox, state_dir / "workflow-notifications.sqlite"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    writes = list(api.writes), list(api.graphql_writes)
    api.revocation = "edited"
    with monkeypatch.context() as patcher:
        if failure == "capacity":
            save = store._save

            def fail_capacity(data):
                # Trigger the real pre-replace size fence at the scan save, not
                # a later outbox write after a successful (possibly smaller) scan.
                with monkeypatch.context() as capacity:
                    capacity.setattr(module, "MAX_STATE_BYTES", 1)
                    save(data)

            patcher.setattr(store, "_save", fail_capacity)
        elif failure == "replace":
            replace = os.replace

            def fail_replace(source, destination):
                if destination == store.path:
                    raise OSError("synthetic state replace failure")
                return replace(source, destination)

            patcher.setattr(os, "replace", fail_replace)
        else:
            get_all = api.get_all

            def fail_reviews(route, *, collection=None):
                if "/reviews?" in route:
                    raise CoordinatorError("synthetic incomplete review pagination")
                return get_all(route, collection=collection)

            patcher.setattr(api, "get_all", fail_reviews)
        with pytest.raises((CoordinatorError, OSError), match="safety bound|replace failure|pagination"):
            coordinator.run(apply=True)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert (api.writes, api.graphql_writes) == writes
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD
    Coordinator(api, StateStore(store.path), clock=CLOCK.timestamp,
                lifecycle_source_paths=sources).run(apply=True)
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    renewed = json.loads(export.read_text())["events"]
    assert [event["reason"] for event in renewed] == ["sensitive_approval"]
    assert outcome in store.snapshot()["lifecycle_context"]["events"]
    assert api.fix_attempts == 0 and not api.graphql_writes


@pytest.mark.parametrize("generation", [True, -1, 2**31, 2**31 - 1])
def test_regressions_revocation_generation_fails_closed_at_bound(tmp_path, generation):
    api = RevocationApi(sensitive=True, authorize=True)
    api.pending_required = True
    store = StateStore(tmp_path / "state.json")
    Coordinator(api, store, clock=lambda: NOW).run(apply=True)
    data = store.snapshot()
    data["enrollments"]["16"]["sensitive_generation"] = generation
    store._save(data)
    api.revocation = "edited"
    before = store.path.read_bytes()
    writes = list(api.writes)
    with pytest.raises(CoordinatorError, match="episode"):
        Coordinator(api, StateStore(store.path), clock=lambda: NOW,
                    owner_user_id="synthetic-mobile-owner").run(apply=True)
    assert store.path.read_bytes() == before
    assert not (tmp_path / "workflow-events.json").exists()
    assert api.writes == writes and not api.graphql_writes


def test_regressions_revocation_new_head_and_enrollment_scope(tmp_path):
    api = RevocationApi(sensitive=True, authorize=True)
    api.comments[0]["body"] = f"/hermes enroll {HEAD}"
    api.pending_required = True
    store = StateStore(tmp_path / "state.json")

    def poll():
        return Coordinator(api, StateStore(store.path), clock=lambda: NOW,
                           owner_user_id="synthetic-mobile-owner").run(apply=True)

    poll()
    api.revocation = "edited"
    poll()
    export = tmp_path / "workflow-events.json"
    old = json.loads(export.read_text())["events"][0]
    # Explicit renewal scopes a new decision and cannot inherit sensitive consent.
    new_head = "c" * 40
    api.head_sha = new_head
    api.pull["head"]["sha"] = new_head
    api.comments.append({"id": 126, "user": {"id": OWNER},
                         "body": f"/hermes enroll {new_head}",
                         "created_at": "2026-10-01T21:00:00Z",
                         "updated_at": "2026-10-01T21:00:00Z"})
    poll()
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] is None
    assert enrollment["sensitive_generation"] == 0
    events = json.loads(export.read_text())["events"]
    assert len(events) == 1 and events[0]["head_sha"] == new_head
    assert events[0]["event_id"] != old["event_id"]
    assert old in store.snapshot()["lifecycle_events"]
    assert enrollment["authorized_head"] == HEAD
    assert enrollment["owner_authorized_head"] == new_head
    assert enrollment["attempts"] == 0 and api.fix_attempts == 0
    assert not api.graphql_writes
