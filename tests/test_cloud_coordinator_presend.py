"""Issue36: recover only proven pre-send auto-merge claims, with fresh fences."""
from copy import deepcopy

import pytest

from test_cloud_coordinator import (
    BASE, HEAD, Coordinator, CoordinatorError, FakeApi, StateStore, enrolled_record,
)


def setup_plan(tmp_path):
    api = FakeApi()
    store = StateStore(tmp_path / "state.json")
    store.enroll(enrolled_record())
    coordinator = Coordinator(api, store)
    plan = coordinator._build_plan(apply=False)
    return api, store, coordinator, plan


def supersede(api, store, coordinator, plan):
    action = plan["pull_requests"][0]["merge_action"]
    api.pull["head"]["repo"]["id"] = 1
    assert coordinator._enable_auto_merge(action, plan["snapshots"][0]) == "superseded"
    assert store.action(action["key"])["status"] == "superseded"
    assert not api.writes and not api.graphql_writes
    api.pull["head"]["repo"]["id"] = 1399942965
    return action


def test_presend_superseded_auto_merge_key_recovers_same_head_main(tmp_path):
    api, store, coordinator, plan = setup_plan(tmp_path)
    action = supersede(api, store, coordinator, plan)
    # Recovery is durable, not dependent on the coordinator instance.
    coordinator = Coordinator(api, StateStore(store.path))
    result = coordinator.run(apply=True)
    assert len(api.graphql_writes) == 1
    assert api.graphql_writes[0][1]["expectedHeadOid"] == HEAD
    assert store.action(action["key"])["status"] == "sent"
    assert result["pull_requests"][0]["auto_merge_requested"] is True
    coordinator.run(apply=True)
    assert len(api.graphql_writes) == 1


@pytest.mark.parametrize("status", ["sent", "sending", "uncertain", "superseded"])
def test_unproven_or_sent_keys_never_reopen_and_report_only_proven_send(tmp_path, status):
    api, store, coordinator, plan = setup_plan(tmp_path)
    action = plan["pull_requests"][0]["merge_action"]
    assert store.claim_action(action["key"], action)
    store.update_action(action["key"], status)
    before = store.action(action["key"])
    result = coordinator.run(apply=True)
    assert store.action(action["key"]) == before
    assert not api.graphql_writes
    assert result["pull_requests"][0]["auto_merge_requested"] is (status == "sent")


def test_retired_sent_key_never_reopens_or_reports_a_new_request(tmp_path):
    api, store, coordinator, plan = setup_plan(tmp_path)
    action = plan["pull_requests"][0]["merge_action"]
    assert store.claim_action(action["key"], action)
    store.update_action(action["key"], "sent")
    store.retire(16, "c" * 40)
    assert store.action(action["key"]) is None
    retired = store.snapshot()["retired"]
    result = coordinator.run(apply=True)
    assert store.snapshot()["retired"] == retired
    assert store.action(action["key"]) is None
    assert not api.graphql_writes
    assert result["pull_requests"][0]["auto_merge_requested"] is False


def test_plan_is_not_request_evidence_and_superseded_apply_reports_no_request(tmp_path):
    api, store, coordinator, plan = setup_plan(tmp_path)
    assert coordinator.run()["pull_requests"][0]["auto_merge_requested"] is False
    original_get = api.get
    injected = False

    def get(route):
        nonlocal injected
        value = deepcopy(original_get(route))
        if route == "repos/lindayi/hermes-mobile/pulls/16" and not injected:
            injected = True
            api.pull["head"]["repo"]["id"] = 1
        return value

    api.get = get
    summary = coordinator._apply(plan)[0]
    assert injected
    assert not api.graphql_writes
    assert summary["auto_merge_requested"] is False
    assert summary["auto_merge_eligible"] is False
    assert summary["reasons"]


@pytest.mark.parametrize("fence", [
    "head", "main", "head-repo", "base-repo", "base-ref", "draft",
    "review", "threads", "checks", "task", "strict", "conversation",
    "owner", "repository",
])
@pytest.mark.parametrize("sensitive", [False, True])
def test_recovery_rechecks_fresh_fences_after_planning(tmp_path, fence, sensitive):
    api, store, coordinator, plan = setup_plan(tmp_path)
    supersede(api, store, coordinator, plan)
    api.sensitive = sensitive
    plan = coordinator._build_plan(apply=False)
    original_get = api.get
    if fence == "head":
        api.pull["head"]["sha"] = "c" * 40
    elif fence == "main":
        api.advance_main = True
    elif fence == "draft":
        api.pull["draft"] = True
    elif fence == "review":
        api.review_state = "CHANGES_REQUESTED"
    elif fence == "threads":
        api.unresolved = True
    elif fence == "checks":
        api.pending_required = True
    elif fence == "task":
        api.active_agent = True
    elif fence == "strict":
        api.strict_protection = False
    elif fence == "conversation":
        api.conversation_resolution = False
    else:
        route = "user" if fence == "owner" else "repos/lindayi/hermes-mobile"
        api.get = lambda path: {"id": 1} if path == route else original_get(path)
    if fence in {"head-repo", "base-repo", "base-ref"}:
        injected = False

        def get(path):
            nonlocal injected
            value = deepcopy(original_get(path))
            if path == "repos/lindayi/hermes-mobile/pulls/16" and not injected:
                injected = True
                if fence == "head-repo":
                    api.pull["head"]["repo"]["id"] = 1
                elif fence == "base-repo":
                    api.pull["base"]["repo"]["id"] = 1
                else:
                    api.pull["base"]["ref"] = "other"
            return value

        api.get = get
    if fence in {"owner", "repository"}:
        with pytest.raises(CoordinatorError):
            coordinator._apply(plan)
    else:
        summary = coordinator._apply(plan)[0]
        assert summary["auto_merge_requested"] is False
        if fence in {"head-repo", "base-repo", "base-ref"}:
            assert injected
    assert not api.graphql_writes


def test_recovery_allows_fresh_sensitive_paths_without_separate_owner_grant(tmp_path):
    api, store, coordinator, plan = setup_plan(tmp_path)
    action = supersede(api, store, coordinator, plan)
    plan = coordinator._build_plan(apply=False)
    api.sensitive = True

    summary = coordinator._apply(plan)[0]

    assert summary["auto_merge_requested"] is True
    assert summary["auto_merge_eligible"] is True
    assert len(api.graphql_writes) == 1
    assert api.graphql_writes[0][1]["expectedHeadOid"] == HEAD
    assert store.action(action["key"])["status"] == "sent"
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] is None
    assert enrollment["sensitive_authorization"] is None
    assert not any(key.endswith(":sensitive") for key in store.snapshot()["outbox"])
    Coordinator(api, StateStore(store.path)).run(apply=True)
    assert len(api.graphql_writes) == 1


@pytest.mark.parametrize("fence", ["head-repo", "base-repo", "base-ref", "malformed-repo"])
def test_apply_keeps_early_pull_identity_failures_strict(tmp_path, fence):
    api, store, coordinator, plan = setup_plan(tmp_path)
    before = store.snapshot()
    if fence == "head-repo":
        api.pull["head"]["repo"]["id"] = 1
    elif fence == "base-repo":
        api.pull["base"]["repo"]["id"] = 1
    elif fence == "base-ref":
        api.pull["base"]["ref"] = "other"
    else:
        api.pull["head"]["repo"]["id"] = True

    with pytest.raises(CoordinatorError, match="Pull request identity"):
        coordinator._apply(plan)

    assert store.snapshot() == before
    assert not api.writes and not api.graphql_writes


def test_recovered_claim_lost_response_stays_uncertain_across_restart(tmp_path):
    api, store, coordinator, plan = setup_plan(tmp_path)
    action = supersede(api, store, coordinator, plan)
    api.uncertain_merge = True
    coordinator.run(apply=True)
    assert len(api.graphql_writes) == 1
    assert store.action(action["key"])["status"] == "uncertain"
    # No remote proof of enablement: never turn uncertainty back into pre-send.
    api.pull["auto_merge"] = None
    coordinator = Coordinator(api, StateStore(store.path))
    result = coordinator.run(apply=True)
    assert len(api.graphql_writes) == 1
    assert store.action(action["key"])["status"] == "uncertain"
    assert result["pull_requests"][0]["auto_merge_requested"] is False
