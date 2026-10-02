"""Remote numeric identities must never accept Python numeric aliases."""
from copy import deepcopy

import pytest

from deploy import issue_starter as starter
import test_issue_starter as sibling
from test_issue_starter import (
    FakeApi, REPOSITORY, completed_task, make_coordinator, pull_request, start_task,
)


def replace_identity(value, path, kind):
    target = value
    for key in path[:-1]:
        target = target[key]
    original = target[path[-1]]
    if kind == "missing":
        del target[path[-1]]
    else:
        target[path[-1]] = {
            "float": float(original), "bool": True, "zero": 0, "negative": -1,
            "string": str(original), "null": None, "int": original,
        }[kind]
    return value


KINDS = ["float", "bool", "zero", "negative", "string", "null", "missing", "int"]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("field", ["user", "repository", "owner"])
@pytest.mark.parametrize("kind", KINDS)
def test_fixed_identity_gate_rejects_nonpositive_or_noninteger_ids(tmp_path, apply, field, kind):
    class IdentityApi(FakeApi):
        def get(self, route):
            value = super().get(route)
            if route == "user" and field == "user":
                return replace_identity(value, ("id",), kind)
            if route == f"repos/{REPOSITORY}" and field != "user":
                path = ("id",) if field == "repository" else ("owner", "id")
                return replace_identity(value, path, kind)
            return value

    api = IdentityApi()
    coordinator = make_coordinator(tmp_path, api)
    if kind == "int":
        result = coordinator.run(apply=apply)
        assert result["dispatched" if apply else "planned"] == 1
    else:
        with pytest.raises(starter.CoordinatorError, match="identity did not match"):
            coordinator.run(apply=apply)
        assert api.posts == []
        assert api.graphql_calls == []
        assert not coordinator.store.directory.exists()


@pytest.mark.parametrize("field", ["issue_number", "command_id", "command_owner"])
@pytest.mark.parametrize("kind", KINDS)
def test_fresh_issue_rejects_numeric_aliases_before_dispatch(tmp_path, field, kind):
    api = FakeApi()
    coordinator = make_coordinator(tmp_path, api)
    record = coordinator._collect(coordinator.store.snapshot())[0]
    assert coordinator.store.reserve(record)
    original_get = api.get

    def changed_evidence(route):
        value = deepcopy(original_get(route))
        if route == f"repos/{REPOSITORY}/issues/28" and field == "issue_number":
            replace_identity(value, ("number",), kind)
        if "/issues/28/comments?" in route and field != "issue_number":
            path = ("id",) if field == "command_id" else ("user", "id")
            replace_identity(value[0], path, kind)
        return value

    api.get = changed_evidence
    result = coordinator.run(apply=True)
    assert result["dispatched"] == (1 if kind == "int" else 0)
    assert len([route for route, _ in api.posts if route.endswith("/tasks")]) == (
        1 if kind == "int" else 0
    )
    if kind != "int":
        assert coordinator.store.snapshot()["commands"]["28:9001"]["phase"] == "stale_authorization"


def assert_no_handoff_writes(api):
    assert not any("markPullRequestReadyForReview" in call["query"]
                   for call in api.graphql_calls)
    assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)
    assert api.patches == []


@pytest.mark.parametrize("field", ["creator", "owner", "repository"])
@pytest.mark.parametrize("kind", KINDS)
def test_task_identity_rejects_numeric_aliases_before_handoff(tmp_path, field, kind):
    api = FakeApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = replace_identity(completed_task(), (field, "id"), kind)

    result = make_coordinator(tmp_path, api).run(apply=True)

    assert result["handed_off"] == (1 if kind == "int" else 0)
    if kind != "int":
        assert_no_handoff_writes(api)


@pytest.mark.parametrize("field", [
    "artifact_id", "list_id", "list_number", "detail_id", "detail_number",
    "head_repository", "base_repository",
])
@pytest.mark.parametrize("kind", KINDS)
def test_task_pull_binding_requires_exact_positive_remote_ids(tmp_path, field, kind):
    # ID/number 1 makes True a real equality alias, not merely a mismatched ID.
    pull = {**pull_request(pull_id=1), "number": 1}
    api = FakeApi(pulls=[pull])
    task = completed_task(pull_id=1)
    if field == "artifact_id":
        replace_identity(task["artifacts"][1]["data"], ("id",), kind)
    original_get = api.get

    def changed_evidence(route):
        value = deepcopy(original_get(route))
        if "/pulls?" in route and field.startswith("list_"):
            replace_identity(value[0], (field.removeprefix("list_"),), kind)
        if route.endswith("/pulls/1"):
            if field.startswith("detail_"):
                replace_identity(value, (field.removeprefix("detail_"),), kind)
            elif field.endswith("_repository"):
                replace_identity(value, (field.split("_")[0], "repo", "id"), kind)
        return value

    api.get = changed_evidence
    result = make_coordinator(tmp_path, api)._find_task_pull(task, 28)
    assert (result is not None) is (kind == "int")
    assert_no_handoff_writes(api)


@pytest.mark.parametrize("read_number", [5, 9, 11])
@pytest.mark.parametrize("field", ["id", "number", "head_repository", "base_repository"])
@pytest.mark.parametrize("kind", KINDS)
def test_current_pull_revalidates_numeric_ids_at_each_write_boundary(
    tmp_path, read_number, field, kind,
):
    api = FakeApi(pulls=[pull_request()])
    start_task(tmp_path, api)
    api.task_detail = completed_task()
    original_get = api.get
    changed_reads = []

    def changed_evidence(route):
        value = deepcopy(original_get(route))
        if route.endswith("/pulls/41") and api.pull_detail_reads == read_number:
            path = (field.split("_")[0], "repo", "id") if "_" in field else (field,)
            replace_identity(value, path, kind)
            changed_reads.append(api.pull_detail_reads)
        return value

    api.get = changed_evidence
    result = make_coordinator(tmp_path, api).run(apply=True)

    assert changed_reads == [read_number]
    assert result["handed_off"] == (1 if kind == "int" else 0)
    if kind != "int":
        assert not any(route.endswith("/issues/41/comments") for route, _ in api.posts)
        assert len([call for call in api.graphql_calls
                    if "markPullRequestReadyForReview" in call["query"]]) == (
            0 if read_number == 5 else 1
        )
        assert api.patches == []


@pytest.mark.parametrize("case", ["unauthorized", "nonterminal", "wrong_identity", "head_race"])
def test_negative_endpoint_assertions_detect_synthetic_writes(tmp_path, monkeypatch, case):
    """Mutation proof: exercise the sibling assertions, not copied predicates."""
    original_run = starter.Coordinator.run
    injected = []

    def run_with_unreported_write(self, **kwargs):
        result = original_run(self, **kwargs)
        if kwargs.get("apply") and result["dispatched"] == 0:
            route = (f"agents/repos/{REPOSITORY}/tasks" if case == "unauthorized"
                     else f"repos/{REPOSITORY}/issues/41/comments")
            self.api.post(route, {"body": "synthetic unexpected write"})
            injected.append(route)
        return result

    monkeypatch.setattr(starter.Coordinator, "run", run_with_unreported_write)
    with pytest.raises(AssertionError) as caught:
        if case == "unauthorized":
            sibling.test_untrusted_stale_or_unverifiable_issue_evidence_never_dispatches(
                tmp_path, sibling.issue_comment(user_id=2), sibling.issue(), [], 0,
            )
        elif case == "nonterminal":
            sibling.test_nonterminal_or_unknown_task_state_never_hands_off(tmp_path, "queued")
        elif case == "wrong_identity":
            sibling.test_wrong_task_or_pull_identity_is_never_handed_off(
                tmp_path, completed_task(repository_id=99), pull_request(),
            )
        else:
            sibling.test_task_head_race_blocks_readiness_and_enrollment(tmp_path)
    assert len(injected) == 1
    # Do not let an unrelated result/count assertion masquerade as the proof.
    statement = str(caught.traceback[-1].statement)
    assert "assert not any" in statement
    assert ('"/tasks"' if case == "unauthorized" else '"/issues/41/comments"') in statement
