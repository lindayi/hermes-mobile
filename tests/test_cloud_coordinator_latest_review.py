"""Direct seam regressions for PR33 review 5388069795."""
import base64
from copy import deepcopy
import json
import subprocess

import pytest

from deploy.cloud_coordinator import (
    ApiError, _blob_bytes, _rest_list, copilot_review_valid,
    neutral_reconciliation_request, review_task_request,
)
from deploy.review_evidence import selected_copilot_sensitive_review
from deploy.task_receipts import ReceiptError
from deploy.workflow_lifecycle import pull_event
from test_cloud_coordinator import (
    APP_OWNER_ID, BASE, HEAD, Coordinator, CoordinatorError, FakeApi, StateStore,
    enrolled_record,
)


class ActorIdentityApi(FakeApi):
    """Synthetic authenticated actor payloads across both API transports."""

    def __init__(self, *, rest_change=None, graphql_change=None):
        super().__init__(sensitive=True, authorize=True)
        self.rest_change = rest_change or {}
        self.graphql_change = graphql_change or {}
        self.metadata_queries = []
        self.review_state = "COMMENTED"
        self.copilot_review_body = (
            "<!-- ccr-overview-v2 -->\n\n"
            "### 🔵 Needs a closer look\n\n"
            "The sensitive coordinator authorization and dispatch changes require "
            "final human review, with current-head checks and Copilot evidence "
            "still pending.\n\n**0 open findings**"
        )
        self.copilot_review_html = (
            "<h3>🔵 Needs a closer look</h3><p>"
            "The sensitive coordinator authorization and dispatch changes require "
            "final human review, with current-head checks and Copilot evidence "
            "still pending.</p><p><strong>0 open findings</strong></p>"
        )
        self.comments[-1]["body"] = (
            f"/hermes authorize-sensitive {HEAD} review "
            f"{self.copilot_review_id} {self.copilot_review_digest}"
        )

    def _current_copilot_review_record(self, *, graphql=False):
        review = super()._current_copilot_review_record(graphql=graphql)
        review["user"]["login"] = "copilot-pull-request-reviewer[bot]"
        review["user"].update(self.rest_change)
        return json.loads(json.dumps(review))

    def graphql(self, query, variables):
        response = deepcopy(super().graphql(query, variables))
        if "PullRequestReview" in query:
            self.metadata_queries.append((query, variables))
            if variables["id"] == self.copilot_review_node_id:
                response["data"]["node"]["author"].update(self.graphql_change)
        return json.loads(json.dumps(response))


def _actor_reviews(api):
    return _rest_list(api, "repos/lindayi/hermes-mobile/pulls/16/reviews?per_page=100")


def _actor_selected(api, reviews):
    return selected_copilot_sensitive_review(
        reviews, HEAD, api.copilot_review_id, api.copilot_review_digest,
        pull_author_id=api.pull["user"]["id"],
    )


@pytest.mark.parametrize("login", [
    "copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]",
])
def test_actor_identity_collector_binds_bot_presentation_without_prose_acceptance(login):
    api = ActorIdentityApi(rest_change={"login": login})
    reviews = _actor_reviews(api)
    review = reviews[0]
    assert review["updatedAt"] == api.review_submitted_at
    assert review["lastEditedAt"] is None
    assert review["includesCreatedEdit"] is False
    assert review["body"] == api.copilot_review_body
    assert review["body_html"] == api.copilot_review_html
    assert _actor_selected(api, reviews) == {
        "review_id": api.copilot_review_id, "reviewer_id": 175728472,
        "head_sha": HEAD, "state": "COMMENTED",
        "body_sha256": api.copilot_review_digest,
    }
    assert not copilot_review_valid(HEAD, reviews, [])
    query = api.metadata_queries[0][0]
    assert "__typename" in query and "... on Bot" in query and "... on User" in query
    assert not api.writes and not api.graphql_writes and api.task_posts == 0


def test_actor_identity_sensitive_command_and_cold_read_only_collector(tmp_path):
    api = ActorIdentityApi()
    path = tmp_path / "state.json"
    store = StateStore(path)
    coordinator = Coordinator(api, store, clock=lambda: 1790856660)
    result = coordinator.run(apply=True)
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] == HEAD
    assert enrollment["targeted_review"] == _actor_selected(api, _actor_reviews(api))
    assert store.event_seen("124")
    assert not result["pull_requests"][0]["review_valid"]
    assert not result["pull_requests"][0]["auto_merge_eligible"]
    assert not api.graphql_writes and api.task_posts == 0
    before, writes = path.read_bytes(), deepcopy(api.writes)
    restarted = Coordinator(api, StateStore(path), clock=lambda: 1790856661)
    result = restarted.run(apply=False)
    assert not result["pull_requests"][0]["review_valid"]
    assert path.read_bytes() == before
    assert api.writes == writes and not api.graphql_writes and api.task_posts == 0
    restarted.run(apply=True)
    assert StateStore(path).snapshot()["enrollments"]["16"]["targeted_review"] == (
        enrollment["targeted_review"]
    )
    assert not api.graphql_writes and api.task_posts == 0


@pytest.mark.parametrize("login", [
    "copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]",
])
@pytest.mark.parametrize(("rest_change", "graphql_change"), [
    ({"id": value}, {}) for value in [None, True, 175728472.0, "175728472", 198982749]
] + [
    ({}, {"databaseId": value})
    for value in [None, True, 175728472.0, "175728472", 198982749]
] + [
    ({"node_id": value}, {}) for value in [None, "", True, 1, "BOT_other"]
] + [
    ({}, {"id": value}) for value in [None, "", True, 1, "BOT_other"]
] + [
    ({"type": value}, {}) for value in [None, "", True, "User", "Organization"]
] + [
    ({}, {"__typename": value}) for value in [None, "", True, "User", "Organization"]
] + [
    ({"login": value}, {}) for value in [None, "", True, "arbitrary[bot]"]
] + [
    ({}, {"login": value}) for value in [None, "", True, "arbitrary"]
] + [
    ({"type": "User"}, {"__typename": "User"}),
    ({"id": 198982749}, {"databaseId": 198982749}),
])
def test_actor_identity_mismatch_blocks_collector_and_sensitive_command(
        tmp_path, login, rest_change, graphql_change):
    api = ActorIdentityApi(
        rest_change={"login": login} | rest_change, graphql_change=graphql_change,
    )
    reviews = _actor_reviews(api)
    assert "updatedAt" not in reviews[0]
    assert _actor_selected(api, reviews) is None
    store = StateStore(tmp_path / "state.json")
    result = Coordinator(api, store, clock=lambda: 1790856660).run(apply=True)
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    assert not result["pull_requests"][0]["auto_merge_eligible"]
    assert not api.graphql_writes and api.task_posts == 0


@pytest.mark.parametrize("side", ["rest", "graphql"])
@pytest.mark.parametrize("field", ["numeric", "node", "type", "login"])
def test_actor_identity_missing_fields_fail_closed(side, field):
    class MissingActor(ActorIdentityApi):
        def _current_copilot_review_record(self, *, graphql=False):
            review = super()._current_copilot_review_record(graphql=graphql)
            if side == "rest":
                review["user"].pop({
                    "numeric": "id", "node": "node_id", "type": "type", "login": "login",
                }[field])
            return review

        def graphql(self, query, variables):
            response = super().graphql(query, variables)
            if side == "graphql" and variables.get("id") == self.copilot_review_node_id:
                response["data"]["node"]["author"].pop({
                    "numeric": "databaseId", "node": "id",
                    "type": "__typename", "login": "login",
                }[field])
            return response

    api = MissingActor(rest_change={"login": "copilot-pull-request-reviewer"})
    assert _actor_selected(api, _actor_reviews(api)) is None
    assert not api.writes and not api.graphql_writes


@pytest.mark.parametrize("field", [
    "body", "head", "state", "repository-id", "repository-name", "pull",
    "submitted", "updated", "edited", "created-edit", "missing-node",
    "review-id", "review-node", "missing-html",
])
def test_actor_identity_does_not_bypass_review_metadata_binding(tmp_path, field):
    class MismatchedReview(ActorIdentityApi):
        def graphql(self, query, variables):
            response = super().graphql(query, variables)
            if variables.get("id") != self.copilot_review_node_id:
                return response
            node = response["data"]["node"]
            if field == "body":
                node["body"] += " "
            elif field == "head":
                node["commit"]["oid"] = BASE
            elif field == "state":
                node["state"] = "CHANGES_REQUESTED"
            elif field == "repository-id":
                node["pullRequest"]["repository"]["databaseId"] = 1
            elif field == "repository-name":
                node["pullRequest"]["repository"]["nameWithOwner"] = "other/repo"
            elif field == "pull":
                node["pullRequest"]["number"] = 17
            elif field == "submitted":
                node["submittedAt"] = "2026-10-01T12:00:01Z"
            elif field == "updated":
                node["updatedAt"] = "2026-10-01T12:00:01Z"
            elif field == "edited":
                node["lastEditedAt"] = "2026-10-01T12:00:01Z"
            elif field == "created-edit":
                node["includesCreatedEdit"] = True
            elif field == "missing-node":
                response["data"]["node"] = None
            elif field == "review-id":
                node["databaseId"] += 1
            elif field == "review-node":
                node["id"] = "PRR_other"
            else:
                node.pop("bodyHTML")
            return response

    api = MismatchedReview()
    assert _actor_selected(api, _actor_reviews(api)) is None
    store = StateStore(tmp_path / "state.json")
    Coordinator(api, store, clock=lambda: 1790856660).run(apply=True)
    assert store.snapshot()["enrollments"]["16"]["sensitive_sha"] is None
    assert not api.graphql_writes and api.task_posts == 0


@pytest.mark.parametrize("malformed", [
    None, [], {"data": None}, {"data": []}, {"errors": [{"message": "synthetic"}]},
])
def test_actor_identity_malformed_api_preserves_state_without_writes(tmp_path, malformed):
    class MalformedActor(ActorIdentityApi):
        def graphql(self, query, variables):
            if "PullRequestReview" in query:
                return malformed
            return super().graphql(query, variables)

    api = MalformedActor()
    store = StateStore(tmp_path / "state.json")
    store.enroll(enrolled_record())
    before = store.path.read_bytes()
    with pytest.raises(ApiError):
        Coordinator(api, store, clock=lambda: 1790856660).run(apply=True)
    assert store.path.read_bytes() == before
    assert not api.writes and not api.graphql_writes and api.task_posts == 0


@pytest.mark.parametrize("status", [404, 429, 503])
def test_actor_identity_api_failure_never_grants_sensitive_authority(status):
    class FailedActor(ActorIdentityApi):
        def graphql(self, query, variables):
            if "PullRequestReview" in query:
                raise ApiError("Synthetic metadata failure", status=status)
            return super().graphql(query, variables)

    api = FailedActor()
    if status == 404:
        assert _actor_selected(api, _actor_reviews(api)) is None
    else:
        with pytest.raises(ApiError, match="Synthetic metadata failure"):
            _actor_reviews(api)
    assert not api.writes and not api.graphql_writes


@pytest.mark.parametrize("wrapped", [False, True])
def test_github_blob_envelope_decodes_exact_bytes(wrapped):
    data = bytes(range(256))
    encoded = base64.b64encode(data).decode("ascii")
    content = (
        "".join(encoded[index:index + 60] + "\n" for index in range(0, len(encoded), 60))
        if wrapped else encoded
    )
    envelope = json.loads(json.dumps({
        "sha": "f" * 40, "node_id": "synthetic-blob",
        "url": f"https://api.github.com/repos/lindayi/hermes-mobile/git/blobs/{'f' * 40}",
        "content": content, "encoding": "base64", "size": len(data),
    }))

    class BlobApi:
        def get(self, route):
            assert route == f"repos/lindayi/hermes-mobile/git/blobs/{'f' * 40}"
            return envelope

    assert _blob_bytes(BlobApi(), "f" * 40) == data


@pytest.mark.parametrize("change", [
    {"content": "YQ\n$==\n"},
    {"content": "YQ=\n"},
    {"content": "YQ===\n"},
    {"content": "YQ==\nYQ==\n"},
    {"content": "Y Q==\n"},
    {"content": "YQ==\t\n"},
    {"content": "YQ==\r\n"},
    {"content": "YQ==\u00a0\n"},
    {"content": "YQ==\u2028\n"},
    {"content": None},
    {"sha": "e" * 40},
    {"encoding": "utf-8"},
    {"size": 2},
])
def test_github_blob_envelope_rejects_malformed_data(change):
    class BlobApi:
        def get(self, route):
            return {"sha": "f" * 40, "content": "YQ==\n",
                    "encoding": "base64", "size": 1} | change

    with pytest.raises(ReceiptError, match="Independent review blob .*malformed"):
        _blob_bytes(BlobApi(), "f" * 40)


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


def test_neutral_prompt_keeps_hunk_audit_out_of_the_receipt_carrier():
    from deploy.task_receipts import receipt_instruction

    api = FakeApi()
    request = neutral_reconciliation_request({
        "issue": 16, "head": HEAD, "main_sha": BASE, "pull": api.pull,
    }, 0)
    prompt = request["body"] + "\n\n" + receipt_instruction(
        "N" * 42, pull_number=16, start_head=HEAD, base_sha=BASE, neutral=True,
    )
    assert "every conflict hunk" in prompt
    assert "file/hunk identity, classification, decision, and rationale" in prompt
    assert "complete audit, including file/hunk identity" in prompt
    assert "preserves both branch intents" in prompt
    assert "a `ready` receipt requires its exact commit SHA as the pushed result head" in prompt
    assert "receipt-only PR reply" in prompt
    assert "complete serialized carrier" in prompt
    assert "provider's unchanged quoted anchor" in prompt
    assert "before the single publication" in prompt
    assert "stop with a transport blocker before publishing" in prompt
    assert "one `reply_to_comment` call" in prompt
    assert "fresh review and checks" in prompt
    assert "separate PR comment with conflict decisions" not in prompt


@pytest.mark.parametrize(("hunks", "rationale"), [
    (0, ""),
    (2, ""),
    (56, ""),
    (1, "保存双方分支意图。" * 1500),
])
def test_neutral_merge_commit_retains_complete_audit_at_receipt_head(
        tmp_path, hunks, rationale):
    repository = tmp_path / f"merge-{hunks}-{len(rationale)}"
    repository.mkdir()

    def git(*args, check=True):
        return subprocess.run(
            ["git", "-C", str(repository), *args], check=check,
            capture_output=True, text=True,
        )

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.name", "Synthetic test")
    git("config", "user.email", "synthetic@example.invalid")
    if hunks:
        for index in range(hunks):
            path = repository / "shared" / f"hunk-{index:02}.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text("base behavior\n")
    else:
        (repository / "base.txt").write_text("base behavior\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    git("switch", "--quiet", "-c", "pull")
    if hunks:
        for index in range(hunks):
            (repository / "shared" / f"hunk-{index:02}.txt").write_text(
                "PR behavior\n",
            )
    else:
        (repository / "pr-only.txt").write_text("PR behavior\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "PR intent")
    git("switch", "--quiet", "main")
    if hunks:
        for index in range(hunks):
            (repository / "shared" / f"hunk-{index:02}.txt").write_text(
                "main behavior\n",
            )
    else:
        (repository / "main-only.txt").write_text("main behavior\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "main intent")
    git("switch", "--quiet", "pull")

    merge = git("merge", "--no-commit", "main", check=False)
    audit = []
    if hunks:
        assert merge.returncode != 0
        for index in range(hunks):
            path = f"shared/hunk-{index:02}.txt"
            (repository / path).write_text("main behavior\nPR behavior\n")
            audit.append(
                f"{index + 1}. {path} (hunk {index + 1}): "
                "classification=technical; decision=retain both branch intents; "
                f"rationale={rationale or 'Preserve main behavior and PR behavior.'}"
            )
        git("add", ".")
        audit_body = "\n".join(audit)
    else:
        assert merge.returncode == 0
        audit_body = (
            "No conflict hunks. Preserve both main behavior and PR behavior."
        )
    message = (
        "Reconcile current main with PR intent\n\n"
        "Complete conflict hunk audit:\n" + audit_body
    )
    git("commit", "--quiet", "-m", message)

    receipt_head = git("rev-parse", "HEAD").stdout.strip()
    commit = git("show", "-s", "--format=%B", receipt_head).stdout
    parents = git("rev-list", "--parents", "-n", "1", receipt_head).stdout.split()
    assert len(parents) == 3
    assert commit.startswith("Reconcile current main with PR intent\n\n")
    assert audit_body in commit
    if hunks == 56 or rationale:
        assert len(commit.encode("utf-8")) > 8192
    for item in audit:
        assert item in commit

    from deploy.task_receipts import _v2_fields

    payload = (
        "Hermes-Task-Receipt: v2\n"
        f"nonce={'N' * 42}\n"
        "pr=16\n"
        f"start_head={HEAD}\n"
        f"base={BASE}\n"
        "session=synthetic-session\n"
        f"head={receipt_head}\n"
        "result=ready"
    )
    carrier = "\n> Hermes coordinator: reserved anchor\n> \n\n" + payload
    assert _v2_fields(carrier)["head"] == receipt_head


@pytest.mark.parametrize("correction", [False, True])
def test_review_prompt_matches_report_schema_and_inventories_every_changed_path(correction):
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
        **({
            "retry_of": {
                "key": "prior-review",
                "task_id": "prior-task",
                "dispatch_nonce": "a" * 32,
                "main_sha": BASE,
            },
        } if correction else {}),
    )
    prompt = request["body"]

    assert "json.dumps(report, ensure_ascii=True, separators=(',', ':'))" in prompt
    assert "non-ASCII characters represented as JSON `\\u` escapes" in prompt
    assert "decoding those escapes preserves the original text" in prompt
    assert ("new, bounded corrective review task" in prompt) is correction
    assert "exactly the keys `path` and `comment`" in prompt
    assert "1-8" in prompt and "1000 characters" in prompt
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


def test_report_correction_reservation_is_distinct_single_use_and_budget_free(tmp_path):
    store = StateStore(tmp_path / "state.json")
    parent = {
        "key": f"review:16:{HEAD}:original",
        "kind": "review",
        "issue": 16,
        "head": HEAD,
        "main_sha": BASE,
        "source_task_id": "source-task",
        "source_comment_id": 777,
        "source_session_id": "source-session",
        "source_start_head": HEAD,
        "anchor_comment_id": 888,
        "anchor_prefix": "original-anchor",
        "dispatch_nonce": "original-nonce",
        "body": "original reviewer task",
    }
    assert store.claim_action(parent["key"], parent)
    store.accept_task(parent["key"], "review-task", "2026-10-01T12:00:00Z")
    store.update_action(
        parent["key"], "completed", report_error="invalid report",
        report_session_id="original-session",
        report_retry_allowed=True, report_retry_state="available",
    )
    correction = {
        "key": f"review-correction:16:{HEAD}:new",
        "kind": "review",
        "task_type": "report-correction",
        "correction_of": parent["key"],
        "issue": 16,
        "head": HEAD,
        "main_sha": BASE,
        "source_task_id": "source-task",
        "source_comment_id": 777,
        "source_session_id": "source-session",
        "source_start_head": HEAD,
        "anchor_comment_id": 889,
        "anchor_prefix": "correction-anchor",
        "dispatch_nonce": "new-nonce",
        "body": "new reviewer task",
    }

    assert store.claim_action(correction["key"], correction)
    store.update_action(correction["key"], "uncertain")
    duplicate = correction | {
        "key": f"review-correction:16:{HEAD}:duplicate",
        "anchor_comment_id": 890,
        "anchor_prefix": "duplicate-anchor",
        "dispatch_nonce": "third-nonce",
        "body": "third reviewer task",
    }

    assert not store.claim_action(duplicate["key"], duplicate)
    assert store.action(parent["key"])["task_id"] == "review-task"
    assert store.action(parent["key"])["report_retry_state"] == "reserved"
    assert store.action(parent["key"])["report_error"] == "invalid report"
    assert store.action(correction["key"])["status"] == "uncertain"
    assert set(store.actions()) == {parent["key"], correction["key"]}


@pytest.mark.parametrize("missing_identity", ["task_id", "report_session_id"])
def test_report_correction_requires_proven_parent_task_and_session(
        tmp_path, missing_identity):
    store = StateStore(tmp_path / "state.json")
    parent = {
        "key": f"review:16:{HEAD}:original",
        "kind": "review",
        "issue": 16,
        "head": HEAD,
        "main_sha": BASE,
        "source_task_id": "source-task",
        "source_comment_id": 777,
        "source_session_id": "source-session",
        "source_start_head": HEAD,
        "anchor_comment_id": 888,
        "anchor_prefix": "original-anchor",
        "dispatch_nonce": "original-nonce",
        "body": "original reviewer task",
    }
    assert store.claim_action(parent["key"], parent)
    store.accept_task(parent["key"], "review-task", "2026-10-01T12:00:00Z")
    store.update_action(
        parent["key"], "completed", report_error="invalid report",
        report_session_id="original-session",
        report_retry_allowed=True, report_retry_state="available",
    )
    store.update_action(parent["key"], "completed", **{missing_identity: None})
    correction = {
        "key": f"review-correction:16:{HEAD}:new",
        "kind": "review",
        "task_type": "report-correction",
        "correction_of": parent["key"],
        "issue": 16,
        "head": HEAD,
        "main_sha": BASE,
        "source_task_id": "source-task",
        "source_comment_id": 777,
        "source_session_id": "source-session",
        "source_start_head": HEAD,
        "anchor_comment_id": 889,
        "anchor_prefix": "correction-anchor",
        "dispatch_nonce": "new-nonce",
        "body": "new reviewer task",
    }

    assert not store.claim_action(correction["key"], correction)
    assert store.action(parent["key"])["report_retry_state"] == "available"
    assert correction["key"] not in store.actions()


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
                     f"{api.copilot_review_id} {api.copilot_review_digest}"),
            "created_at": "2026-10-01T12:10:00Z",
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
    enrollment = store.snapshot()["enrollments"]["16"]
    assert enrollment["attempts"] == (0 if neutral else 1)
    assert enrollment["neutral_attempts"] == (1 if neutral else 0)
    assert action.get("task_type") == ("neutral" if neutral else None)
    assert api.fix_attempts == 1
    if identity != "exact":
        assert action["blocker"] == "execution_uncertain"
        events = store.snapshot()["lifecycle_events"]
        assert [event["reason"] for event in events] == ["execution_uncertain"]
        Coordinator(api, StateStore(store.path), clock=lambda: 1790856661).run(apply=True)
        assert store.action(action["key"])["status"] == "uncertain"
        enrollment = store.snapshot()["enrollments"]["16"]
        assert enrollment["attempts"] == (0 if neutral else 1)
        assert enrollment["neutral_attempts"] == (1 if neutral else 0)
        assert store.snapshot()["lifecycle_events"] == events
        assert api.fix_attempts == 1
    assert not api.graphql_writes
