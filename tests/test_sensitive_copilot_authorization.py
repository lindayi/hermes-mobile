import hashlib

import pytest

from deploy.cloud_coordinator import CoordinatorError, StateStore
from test_cloud_coordinator import (
    BASE,
    COPILOT_AGENT,
    COPILOT_REVIEWER,
    HEAD,
    OWNER,
    Coordinator,
    FakeApi,
)
from test_review_evidence import BODY_ONLY


REVIEW_ID = 5300000002
REVIEW_NODE_ID = "PRR_synthetic_copilot_sensitive"
SUBMITTED = "2026-10-01T12:30:00Z"
OWNER_COMMAND_ID = 70002


class SensitiveCopilotApi(FakeApi):
    def __init__(self):
        super().__init__(sensitive=True, authorize=False)
        self.copilot_body = BODY_ONLY
        self.copilot_state = "COMMENTED"
        self.copilot_head = HEAD
        self.copilot_author = COPILOT_REVIEWER
        self.updated_at = SUBMITTED
        self.last_edited_at = None
        self.includes_created_edit = False
        self.body_html = BODY_ONLY
        self.incomplete_reviews = False
        self.command(OWNER_REVIEW=False)

    @property
    def copilot_digest(self):
        return hashlib.sha256(self.copilot_body.encode("utf-8")).hexdigest()

    def command(self, *, OWNER_REVIEW=False, edited=False, digest=None):
        if OWNER_REVIEW:
            review_id, review_digest = self.owner_review_id, self.owner_review_digest
        else:
            review_id, review_digest = REVIEW_ID, digest or self.copilot_digest
        created = "2026-10-01T12:31:00Z"
        updated = "2026-10-01T12:32:00Z" if edited else created
        self.comments = [comment for comment in self.comments if comment.get("id") != OWNER_COMMAND_ID]
        self.comments.append({
            "id": OWNER_COMMAND_ID,
            "user": {"id": OWNER},
            "body": (f"/hermes authorize-sensitive {HEAD} review "
                     f"{review_id} {review_digest}"),
            "created_at": created,
            "updated_at": updated,
        })

    def _copilot_review(self):
        return {
            "id": REVIEW_ID,
            "node_id": REVIEW_NODE_ID,
            "state": self.copilot_state,
            "commit_id": self.copilot_head,
            "submitted_at": SUBMITTED,
            "body": self.copilot_body,
            "user": {
                "id": self.copilot_author,
                "login": "copilot-pull-request-reviewer",
            },
        }

    def get_all(self, route, *, collection=None):
        if route.endswith("/pulls/16/reviews?per_page=100"):
            if self.incomplete_reviews:
                raise CoordinatorError("synthetic incomplete review pagination")
            others = [
                review for review in super().get_all(route, collection=collection)
                if review.get("user", {}).get("id") != COPILOT_REVIEWER
            ]
            return [self._copilot_review(), *others]
        return super().get_all(route, collection=collection)

    def graphql(self, query, variables):
        if "PullRequestReview" in query and variables.get("id") == REVIEW_NODE_ID:
            return {"data": {"node": {
                "id": REVIEW_NODE_ID,
                "databaseId": REVIEW_ID,
                "submittedAt": SUBMITTED,
                "updatedAt": self.updated_at,
                "lastEditedAt": self.last_edited_at,
                "includesCreatedEdit": self.includes_created_edit,
                "state": self.copilot_state,
                "body": self.copilot_body,
                "bodyHTML": self.body_html,
                "commit": {"oid": self.copilot_head},
                "author": {"login": "copilot-pull-request-reviewer"},
                "pullRequest": {
                    "number": 16,
                    "repository": {
                        "nameWithOwner": "lindayi/hermes-mobile",
                        "databaseId": 1399942965,
                    },
                },
            }}}
        return super().graphql(query, variables)


def test_owner_command_binds_copilot_review_across_cold_restart_and_keeps_merge_gates(
        tmp_path):
    api = SensitiveCopilotApi()
    state_path = tmp_path / "coordinator-state.json"

    result = Coordinator(api, StateStore(state_path)).run(apply=True)
    enrollment = StateStore(state_path).snapshot()["enrollments"]["16"]

    assert enrollment["sensitive_sha"] == HEAD
    assert enrollment["sensitive_authorization"]["review_id"] == REVIEW_ID
    assert enrollment["sensitive_authorization"]["body_sha256"] == api.copilot_digest
    assert enrollment["targeted_review"] == {
        "review_id": REVIEW_ID,
        "reviewer_id": COPILOT_REVIEWER,
        "head_sha": HEAD,
        "state": "COMMENTED",
        "body_sha256": api.copilot_digest,
    }
    assert result["pull_requests"][0]["repair_requested"]
    assert not result["pull_requests"][0]["auto_merge_eligible"]
    assert api.fix_attempts == 1

    replay = Coordinator(api, StateStore(state_path)).run(apply=True)
    assert StateStore(state_path).snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD
    assert not replay["pull_requests"][0]["auto_merge_eligible"]
    assert api.fix_attempts == 1
    assert not api.graphql_writes


def test_sensitive_repair_uses_enrollment_without_merge_authorization(tmp_path):
    api = SensitiveCopilotApi()
    api.comments = [c for c in api.comments if c['id'] != OWNER_COMMAND_ID]
    store = StateStore(tmp_path / 'state.json')
    result = Coordinator(api, store).run(apply=True)
    enrollment = store.snapshot()['enrollments']['16']
    assert enrollment['sensitive_authorization'] is None
    assert result['pull_requests'][0]['repair_requested']
    assert api.fix_attempts == 1
    assert not result['pull_requests'][0]['auto_merge_eligible']
    assert not api.graphql_writes


@pytest.mark.parametrize('boundary', ['command', 'review'])
@pytest.mark.parametrize('status', [None, 429, 500])
def test_transient_authorization_evidence_failure_preserves_state(tmp_path, monkeypatch, boundary, status):
    from deploy.cloud_coordinator import ApiError
    api = SensitiveCopilotApi()
    path = tmp_path / 'state.json'
    Coordinator(api, StateStore(path)).run(apply=True)
    before = path.read_bytes()
    api.writes.clear()
    api.graphql_writes.clear()
    get = api.get
    graphql = api.graphql

    def failing_get(route):
        if route.endswith(f'/issues/comments/{OWNER_COMMAND_ID}'):
            raise ApiError('synthetic temporary outage', status=status)
        return get(route)

    def failing_graphql(query, variables):
        if 'PullRequestReview' in query:
            raise ApiError('synthetic temporary outage', status=status)
        return graphql(query, variables)

    monkeypatch.setattr(api, 'get' if boundary == 'command' else 'graphql',
                        failing_get if boundary == 'command' else failing_graphql)
    with pytest.raises(ApiError, match='temporary outage'):
        Coordinator(api, StateStore(path)).run(apply=True)
    assert path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize('field, malformed', [
    (None, None), (None, []), (None, 'not a comment'),
    ('user', []), ('user', None), ('user', {'id': '1'}),
    ('body', []), ('body', None), ('created_at', []),
    ('created_at', 'invalid'), ('updated_at', {}), ('id', True),
    ('issue_url', []),
])
def test_malformed_current_comment_preserves_durable_authorization(tmp_path, field, malformed):
    from deploy.cloud_coordinator import ApiError
    api = SensitiveCopilotApi()
    path = tmp_path / 'state.json'
    Coordinator(api, StateStore(path)).run(apply=True)
    before = path.read_bytes()
    api.writes.clear()
    api.graphql_writes.clear()
    get = api.get

    def malformed_get(route):
        value = get(route)
        if route.endswith(f'/issues/comments/{OWNER_COMMAND_ID}'):
            return malformed if field is None else value | {field: malformed}
        return value

    api.get = malformed_get
    with pytest.raises(ApiError, match='^Malformed current owner-comment response$'):
        Coordinator(api, StateStore(path)).run(apply=True)
    assert path.read_bytes() == before
    assert api.writes == api.graphql_writes == []


@pytest.mark.parametrize("mutation", [
    "wrong_identity", "wrong_head", "negative_state", "edited_review", "edited_command",
    "body_changed", "wrong_digest", "ambiguous_latest", "legacy_report",
    "preauthorized_review",
])
def test_invalid_or_legacy_evidence_does_not_grant_sensitive_authorization(
        tmp_path, mutation):
    api = SensitiveCopilotApi()
    if mutation == "wrong_identity":
        api.copilot_author = COPILOT_AGENT
    elif mutation == "wrong_head":
        api.copilot_head = BASE
    elif mutation == "negative_state":
        api.copilot_state = "CHANGES_REQUESTED"
    elif mutation == "edited_review":
        api.updated_at = "2026-10-01T12:31:00Z"
        api.last_edited_at = api.updated_at
    elif mutation == "edited_command":
        api.command(edited=True)
    elif mutation == "body_changed":
        api.copilot_body += " "
    elif mutation == "wrong_digest":
        api.command(digest="d" * 64)
    elif mutation == "ambiguous_latest":
        api.extra_copilot_review = api._copilot_review() | {
            "id": REVIEW_ID + 1,
            "node_id": None,
        }
    elif mutation == "legacy_report":
        api.command(OWNER_REVIEW=True)
    elif mutation == "preauthorized_review":
        command = next(c for c in api.comments if c["id"] == OWNER_COMMAND_ID)
        command["created_at"] = command["updated_at"] = "2026-10-01T12:29:59Z"

    if mutation == "ambiguous_latest":
        original_get_all = api.get_all

        def duplicate_latest(route, *, collection=None):
            rows = original_get_all(route, collection=collection)
            if route.endswith("/pulls/16/reviews?per_page=100"):
                rows.insert(1, api.extra_copilot_review)
            return rows

        api.get_all = duplicate_latest

    state_path = tmp_path / f"{mutation}.json"
    Coordinator(api, StateStore(state_path)).run(apply=True)
    enrollment = StateStore(state_path).snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] is None
    assert enrollment["sensitive_authorization"] is None
    assert enrollment["targeted_review"] is None


def test_incomplete_review_pages_do_not_commit_owner_authorization(tmp_path):
    api = SensitiveCopilotApi()
    api.incomplete_reviews = True
    store = StateStore(tmp_path / "state.json")

    with pytest.raises(CoordinatorError, match="incomplete review pagination"):
        Coordinator(api, store).run(apply=True)

    assert store.snapshot()["enrollments"] == {}
    assert not api.graphql_writes


def test_changed_head_revokes_copilot_authorization_after_restart(tmp_path):
    api = SensitiveCopilotApi()
    state_path = tmp_path / "state.json"
    Coordinator(api, StateStore(state_path)).run(apply=True)
    assert StateStore(state_path).snapshot()["enrollments"]["16"]["sensitive_sha"] == HEAD

    new_head = "d" * 40
    api.head_sha = new_head
    api.pull["head"] = api.pull["head"] | {"sha": new_head}
    result = Coordinator(api, StateStore(state_path)).run(apply=True)

    enrollment = StateStore(state_path).snapshot()["enrollments"]["16"]
    assert enrollment["sensitive_sha"] is None
    assert enrollment["sensitive_authorization"] is None
    assert enrollment["targeted_review"] is None
    assert "sensitive" in result["pull_requests"][0]["reasons"]
    assert api.fix_attempts == 1
