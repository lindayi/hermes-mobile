"""Exercise the trusted issue-link workflow against documented API response shapes."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from deploy.cloud_coordinator import required_checks_pass

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/issue-link.yml"
REPOSITORY_ID = 1399942965
REPOSITORY_NODE_ID = "R_kgDOExample"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40


def policy():
    assert WORKFLOW.is_file(), "Issue-first PR policy must exist"
    return yaml.safe_load(WORKFLOW.read_text())


def workflow_script(step_id):
    steps = policy()["jobs"]["issue-link"]["steps"]
    return next(step["with"]["script"] for step in steps if step.get("id") == step_id)


def pull(*, body="", head=HEAD_SHA, base="main", state="open"):
    return {
        "id": 7012,
        "number": 12,
        "node_id": "PR_node_12",
        "state": state,
        "merged": False,
        "draft": False,
        "body": body,
        "head": {
            "ref": "feature",
            "sha": head,
            "repo": {"id": 9001, "full_name": "fork-owner/hermes-mobile"},
        },
        "base": {
            "ref": base,
            "sha": BASE_SHA,
            "repo": {
                "id": REPOSITORY_ID,
                "node_id": REPOSITORY_NODE_ID,
                "full_name": "lindayi/hermes-mobile",
            },
        },
    }


def issue(number, *, state="OPEN", repo_id=REPOSITORY_NODE_ID,
          repository="lindayi/hermes-mobile", typename="Issue", node_id=None):
    return {
        "__typename": typename,
        "id": node_id if node_id is not None else f"Issue_node_{number}",
        "number": number,
        "state": state,
        "repository": {"id": repo_id, "nameWithOwner": repository},
    }


def graph_page(pr, refs, *, total=None, has_next=False, cursor=None,
               repository_id=REPOSITORY_NODE_ID, repository="lindayi/hermes-mobile",
               pull_id="PR_node_12", pull_number=12, head=None, base=None, body=None):
    return {
        "repository": {
            "id": repository_id,
            "nameWithOwner": repository,
            "defaultBranchRef": {"name": "main"},
            "pullRequest": {
                "id": pull_id,
                "number": pull_number,
                "headRefName": pr["head"]["ref"],
                "headRefOid": head if head is not None else pr["head"]["sha"],
                "baseRefName": pr["base"]["ref"],
                "baseRefOid": pr["base"]["sha"],
                "body": pr["body"] or "",
                "baseRepository": {
                    "id": repository_id,
                    "nameWithOwner": repository,
                    "defaultBranchRef": {"name": "main"},
                },
                "closingIssuesReferences": {
                    "totalCount": len(refs) if total is None else total,
                    "nodes": refs,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                },
            },
        },
    }


HARNESS = r"""
const input = JSON.parse(process.argv[1]);
const payload = input.payload;
const script = input.script;
const phase = input.phase;
const calls = {pulls: [], graphql: [], statuses: [], checkCreates: [], checkUpdates: []};
const outputs = {}, errors = [], events = [];
const clone = value => JSON.parse(JSON.stringify(value));
const state = clone(payload.state || {checks: [], statuses: {}, nextCheckId: 9981});
let statusAttempts = 0;
const writeEvent = (api, args) => {
  const event = {api, args, before: clone(state), after: clone(state)};
  events.push(event);
  return event;
};
const pr = payload.pull;
const repo = {owner: "lindayi", repo: "hermes-mobile"};
const context = {
  repo, runId: 12345, serverUrl: "https://github.com",
  eventName: payload.eventName || "pull_request_target",
  ref: payload.ref || "refs/heads/main",
  payload: {
    repository: {id: payload.repositoryId || 1399942965,
      default_branch: payload.defaultBranch || "main"},
    pull_request: payload.eventName === "workflow_dispatch" ? undefined : pr,
    inputs: payload.inputs || {},
  },
};
const currentPull = index => payload.pull_reads?.[Math.min(index,
  payload.pull_reads.length - 1)] || payload.current_pull || pr;
const defaultGraph = () => {
  const refs = payload.refs || [];
  const after = calls.graphql.at(-1)?.variables?.after || null;
  if (payload.graph_responses) {
    return payload.graph_responses[Math.min(calls.graphql.length - 1,
      payload.graph_responses.length - 1)];
  }
  if (after && payload.next_graph) return payload.next_graph;
  return graphPayload(refs);
};
function graphPayload(refs) {
  const graphPull = payload.graph_pull || pr;
  return {
    repository: {
      id: payload.graph_repository_id || "R_kgDOExample",
      nameWithOwner: payload.graph_repository || "lindayi/hermes-mobile",
      defaultBranchRef: {name: "main"},
      pullRequest: {
        id: payload.graph_pull_id || "PR_node_12",
        number: payload.graph_pull_number || 12,
        headRefName: graphPull.head.ref,
        headRefOid: graphPull.head.sha,
        baseRefName: graphPull.base.ref,
        baseRefOid: graphPull.base.sha,
        body: graphPull.body || "",
        baseRepository: {id: "R_kgDOExample", nameWithOwner: "lindayi/hermes-mobile",
          defaultBranchRef: {name: "main"}},
        closingIssuesReferences: {
          totalCount: payload.total_count === undefined ? refs.length : payload.total_count,
          nodes: refs,
          pageInfo: {hasNextPage: payload.has_next || false,
            endCursor: payload.end_cursor || null},
        },
      },
    },
  };
}
const core = {
  setFailed: message => errors.push(String(message)),
  setOutput: (name, value) => { outputs[name] = String(value); },
  info: () => {},
};
const github = {rest: {
  pulls: {get: async args => {
    const index = calls.pulls.length;
    calls.pulls.push(args);
    events.push({api: 'pulls.get', state: clone(state)});
    if (payload.pull_error) throw new Error("pull API unavailable");
    return {data: JSON.parse(JSON.stringify(currentPull(index)))};
  }},
  checks: {
    create: async args => {
      const event = writeEvent('checks.create', args);
      if (payload.check_create_error) throw new Error("check create unavailable");
      calls.checkCreates.push(args);
      const id = state.nextCheckId++;
      state.checks.push({...args, id});
      event.after = clone(state);
      return {data: {id: payload.check_create_bad_id ? null : id}};
    },
    update: async args => {
      const event = writeEvent('checks.update', args);
      if (payload.check_update_error === true || payload.check_update_error === args.conclusion) {
        throw new Error("check update unavailable");
      }
      calls.checkUpdates.push(args);
      const check = state.checks.find(check => check.id === args.check_run_id);
      if (check) Object.assign(check, args);
      event.after = clone(state);
      if (payload.check_update_error_after_write === args.conclusion) {
        throw new Error("check update outcome unknown after write");
      }
      return {data: {id: args.check_run_id}};
    },
  },
  repos: {createCommitStatus: async args => {
    const event = writeEvent('statuses.create', args);
    const attempt = statusAttempts++;
    if (payload.status_error === true || payload.status_error === args.state ||
        payload.status_error === "final" && phase === "publish" ||
        payload.status_error === "pending" && phase === "pending" && attempt === 0) {
      throw new Error("status API unavailable");
    }
    calls.statuses.push(args);
    state.statuses[args.sha] = args.state;
    event.after = clone(state);
    return {data: {}};
  }},
}, graphql: async (query, variables) => {
  calls.graphql.push({query, variables});
  if (payload.graphql_error) throw new Error("GraphQL unavailable");
  const result = defaultGraph();
  if (result?.errors?.length) {
    const error = new Error(result.errors.map(item => item.message).join('; '));
    error.name = 'GraphqlResponseError';
    error.errors = result.errors;
    error.data = result.data;
    throw error;
  }
  return result;
}};
for (const [key, value] of Object.entries(payload.env || {})) process.env[key] = value;
(async () => {
  let thrown = null;
  try {
    await (new Function("require", "github", "context", "core",
      "return (async()=>{" + script + "})()"))(require, github, context, core);
  } catch (error) {
    thrown = error.message;
    core.setFailed(error.message);
  }
  console.log(JSON.stringify({calls, outputs, errors, thrown, state, events}));
})().catch(error => { console.error(error); process.exit(1); });
"""


def run_script(step_id, payload):
    node = os.environ.get("HERMES_TEST_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("Managed test runner must provide Node through HERMES_TEST_NODE or PATH")
    result = subprocess.run(
        [node, "-e", HARNESS,
         json.dumps({"payload": payload, "script": workflow_script(step_id), "phase": step_id})],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return json.loads(result.stdout)


def step_environment(outputs, *, validation_result=None):
    env = {
        "PULL_NUMBER": outputs.get("pull_number", ""),
        "HEAD_SHA": outputs.get("head_sha", ""),
        "SNAPSHOT_DIGEST": outputs.get("snapshot_digest", ""),
        "CHECK_RUN_ID": outputs.get("check_run_id", ""),
        "RELATION_DIGEST": outputs.get("relation_digest", ""),
    }
    if validation_result is not None:
        env["VALIDATION_RESULT"] = validation_result
    return env


def make_payload(*, body="", refs=None, **overrides):
    pr = pull(body=body)
    payload = {"pull": pr, "refs": refs if refs is not None else [issue(7)]}
    payload.update(overrides)
    return payload


def run_pipeline(payload, *, validation_result=None, publish_pull=None,
                 graph_responses=None):
    pending = run_script("pending", payload)
    validation = None
    published = None
    if not pending["errors"] and validation_result is None:
        validation_payload = {**payload, "pull_reads": [payload.get("pull")]}
        validation = run_script("validate", {
            **validation_payload, "state": pending["state"],
            "env": step_environment(pending["outputs"]),
            **({"graph_responses": graph_responses[:1]} if graph_responses else {}),
        })
        validation_result = "success" if not validation["errors"] else "failure"
    publish_payload = {**payload, "state": (validation or pending)["state"]}
    if publish_pull is not None:
        publish_payload["current_pull"] = publish_pull
    if graph_responses:
        publish_payload["graph_responses"] = graph_responses[1:2]
    outputs = {**pending["outputs"], **(validation["outputs"] if validation else {})}
    publish_payload["env"] = step_environment(
        outputs, validation_result=validation_result or "failure")
    published = run_script("publish", publish_payload)
    return pending, validation, published


def prior_success():
    return {
        "checks": [{"id": 9000, "name": "issue-link", "head_sha": HEAD_SHA,
                    "status": "completed", "conclusion": "success"}],
        "statuses": {HEAD_SHA: "success"}, "nextCheckId": 9981,
    }


def app_bound_gate(state):
    # Replay script-written state at each API boundary. Only the server-assigned
    # GitHub Actions app identity and status timestamp are synthetic metadata.
    checks = [{**check, "app": {"id": 15368}} for check in state["checks"]
              if check["head_sha"] == HEAD_SHA]
    statuses = [{"context": "issue-link", "state": value,
                 "created_at": "2026-10-03T02:00:00Z"}
                for sha, value in state["statuses"].items() if sha == HEAD_SHA]
    return required_checks_pass(
        [{"context": "issue-link", "app_id": 15368}], checks, statuses, complete=True)


@pytest.mark.parametrize("event", ["pull_request_target", "workflow_dispatch"])
@pytest.mark.parametrize("phase,status_error", [
    ("pending", None), ("pending", "pending"),
    ("publish", None), ("publish", "final"),
])
def test_app_gate_blocks_at_publication_api_boundaries(event, phase, status_error):
    payload = make_payload(state=prior_success(), eventName=event,
                           inputs={"pull_number": "12", "head_sha": HEAD_SHA},
                           status_error=status_error)
    assert app_bound_gate(payload["state"]), "Reproduce an eligible prior app check"
    if phase == "pending":
        result = run_script("pending", payload)
    else:
        pending, validation, result = run_pipeline(payload)
        assert pending["errors"] == validation["errors"] == []
        assert not app_bound_gate(pending["state"])
    writes = [entry for entry in result["events"] if "after" in entry]
    assert writes
    for index, entry in enumerate(writes):
        allowed = phase == "publish" and status_error is None and index == len(writes) - 1
        assert app_bound_gate(entry["after"]) is allowed, (phase, index, entry)
        if allowed:
            # The compatibility write must already have succeeded before the
            # authoritative check is even attempted, not be repaired afterward.
            assert entry["api"] == "checks.update"
            assert entry["before"]["statuses"][HEAD_SHA] == "success"
            assert not app_bound_gate(entry["before"])
    assert bool(result["errors"]) is (status_error is not None)


@pytest.mark.parametrize("options", [
    {"status_error": "success"},
    {"check_update_error": "success"},
    {"status_error": "success", "check_update_error": True},
    {"check_update_error": True},
    {"check_update_error": True, "status_error": "final"},
])
def test_failed_final_publication_attempts_both_blocking_artifacts(options):
    pending, validation, published = run_pipeline(make_payload(
        state=prior_success(), **options))
    assert pending["errors"] == validation["errors"] == []
    assert published["errors"]
    writes = [entry for entry in published["events"] if "after" in entry]
    assert any(entry["api"] == "checks.update" and
               entry["args"]["conclusion"] == "failure" for entry in writes)
    assert any(entry["api"] == "statuses.create" and
               entry["args"]["state"] == "failure" for entry in writes)
    assert all(not app_bound_gate(entry["after"]) for entry in writes)
    if not options.get("check_update_error") or options["check_update_error"] == "success":
        assert published["state"]["checks"][-1]["conclusion"] == "failure"
    if options.get("status_error") != "final":
        assert published["state"]["statuses"][HEAD_SHA] == "failure"


@pytest.mark.parametrize("cleanup_error", [None, "check", "status", "both"])
def test_uncertain_success_write_attempts_each_rollback_independently(cleanup_error):
    pending, validation, published = run_pipeline(make_payload(
        state=prior_success(), check_update_error_after_write="success",
        check_update_error="failure" if cleanup_error in {"check", "both"} else None,
        status_error="failure" if cleanup_error in {"status", "both"} else None))
    assert pending["errors"] == validation["errors"] == []
    assert published["errors"]
    writes = [entry for entry in published["events"] if "after" in entry]
    assert [(entry["api"], entry["args"].get("conclusion", entry["args"].get("state")))
            for entry in writes] == [
        ("statuses.create", "success"), ("checks.update", "success"),
        ("checks.update", "failure"), ("statuses.create", "failure"),
    ]
    assert not app_bound_gate(writes[0]["after"])
    assert writes[1]["before"]["statuses"][HEAD_SHA] == "success"
    # A committed success followed by a lost response is observable. Ordering is
    # not atomic rollback: a failed app cleanup leaves that success eligible even
    # if the compatibility failure lands (the app-bound predicate ignores it).
    assert app_bound_gate(writes[1]["after"])
    assert app_bound_gate(published["state"]) is (cleanup_error in {"check", "both"})
    assert published["state"]["statuses"][HEAD_SHA] == (
        "success" if cleanup_error in {"status", "both"} else "failure")


def assert_check_blocks(state):
    latest = [check for check in state["checks"] if check["head_sha"] == HEAD_SHA][-1]
    assert latest["status"] != "completed" or latest.get("conclusion") != "success"


@pytest.mark.parametrize("case", ["pull_read_error", "body_changed_before_pending"])
def test_event_head_prior_success_is_invalidated_before_initial_read(case):
    payload = make_payload(state=prior_success())
    if case == "pull_read_error":
        payload["pull_error"] = True
    else:
        payload["current_pull"] = pull(body="Canonical link removed while queued")
    pending = run_script("pending", payload)
    assert pending["errors"]
    read = next(event for event in pending["events"] if event["api"] == "pulls.get")
    assert read["state"]["statuses"][HEAD_SHA] == "pending"
    assert_check_blocks(read["state"])
    assert pending["outputs"]["head_sha"] == HEAD_SHA
    assert pending["outputs"]["pull_number"] == "12"
    assert "snapshot_digest" not in pending["outputs"]


@pytest.mark.parametrize("options", [
    {"check_create_error": True}, {"check_create_bad_id": True},
    {"status_error": "pending"},
])
def test_pending_independently_attempts_both_invalidation_writes(options):
    pending = run_script("pending", make_payload(state=prior_success(), **options))
    assert pending["errors"]
    assert {event["api"] for event in pending["events"]} >= {
        "statuses.create", "checks.create"}
    assert pending["outputs"]["head_sha"] == HEAD_SHA
    if options.get("status_error"):
        assert_check_blocks(pending["state"])
    else:
        assert pending["state"]["statuses"][HEAD_SHA] == "pending"


@pytest.mark.parametrize("case", [
    "pull_read_error", "body_changed_before_pending", "check_create_error",
    "check_create_bad_id", "pending_status_error",
])
def test_always_finalizes_prior_success_after_pending_failure(case):
    payload = make_payload(state=prior_success())
    if case == "pull_read_error":
        payload["pull_error"] = True
    elif case == "body_changed_before_pending":
        payload["current_pull"] = pull(body="Canonical link removed while queued")
    elif case == "pending_status_error":
        payload["status_error"] = "pending"
    else:
        payload[case] = True
    pending, validation, published = run_pipeline(payload)
    assert pending["errors"] and validation is None and published["errors"]
    assert published["state"]["statuses"][HEAD_SHA] == "failure"
    if case != "check_create_error":
        assert_check_blocks(published["state"])
        assert published["state"]["checks"][-1]["conclusion"] == "failure"
    assert not any(status["state"] == "success" for status in published["calls"]["statuses"])


@pytest.mark.parametrize("options", [
    {}, {"check_create_error": True}, {"status_error": True},
])
def test_always_without_pending_outputs_invalidates_only_authenticated_event_head(options):
    published = run_script("publish", make_payload(
        state=prior_success(), pull_error=True, env={}, **options))
    assert published["errors"]
    assert {event["api"] for event in published["events"]} >= {
        "statuses.create", "checks.create"}
    assert published["calls"]["pulls"] == []
    if not options.get("status_error"):
        assert published["state"]["statuses"][HEAD_SHA] == "failure"
    if not options.get("check_create_error"):
        assert_check_blocks(published["state"])
        assert published["state"]["checks"][-1]["conclusion"] == "failure"
        # Missing pending evidence must invalidate the app first, not introduce
        # the same stale-success window in the always/failure path.
        assert all(not app_bound_gate(entry["after"]) for entry in published["events"]
                   if "after" in entry)
    else:
        assert app_bound_gate(published["state"]), "Legacy failure cannot block an app-only gate"


def test_final_check_update_error_still_invalidates_legacy_status():
    payload = make_payload(state=prior_success())
    pending, validation, _ = run_pipeline(payload)
    published = run_script("publish", {
        **payload, "state": pending["state"], "check_update_error": True,
        "env": step_environment({**pending["outputs"], **validation["outputs"]},
                                validation_result="success"),
    })
    assert published["errors"]
    assert_check_blocks(published["state"])
    assert published["state"]["statuses"][HEAD_SHA] == "failure"


def test_always_without_dispatch_outputs_reauthenticates_before_failure_writes():
    published = run_script("publish", make_payload(
        state=prior_success(), eventName="workflow_dispatch",
        inputs={"pull_number": "12", "head_sha": HEAD_SHA}, env={}))
    assert published["errors"]
    assert published["events"][0]["api"] == "pulls.get"
    assert published["state"]["statuses"][HEAD_SHA] == "failure"
    assert_check_blocks(published["state"])


@pytest.mark.parametrize("options", [{}, {"check_create_error": True}])
def test_authorized_dispatch_pending_failure_still_finalizes_status(options):
    payload = make_payload(state=prior_success(), eventName="workflow_dispatch",
                           inputs={"pull_number": "12", "head_sha": HEAD_SHA},
                           status_error="pending", **options)
    pending, validation, published = run_pipeline(payload)
    assert pending["errors"] and validation is None and published["errors"]
    assert pending["events"][0]["api"] == "pulls.get"
    assert published["state"]["statuses"][HEAD_SHA] == "failure"


@pytest.mark.parametrize("options", [
    {"ref": "refs/heads/feature"},
    {"inputs": {"pull_number": "12", "head_sha": "c" * 40}},
    {"inputs": {"pull_number": "012", "head_sha": HEAD_SHA}},
    {"pull_error": True}, {"repositoryId": 1},
    {"current_pull": pull(base="release")},
])
def test_unauthorized_dispatch_never_writes_even_in_always_fallback(options):
    payload = make_payload(state=prior_success(), eventName="workflow_dispatch",
                           inputs={"pull_number": "12", "head_sha": HEAD_SHA})
    payload.update(options)
    pending, validation, published = run_pipeline(payload)
    assert pending["errors"] and validation is None and published["errors"]
    for result in (pending, published):
        assert result["state"] == prior_success()
        assert not result["calls"]["statuses"]
        assert not result["calls"]["checkCreates"]
        assert not result["calls"]["checkUpdates"]


@pytest.mark.parametrize("event", ["pull_request_target", "workflow_dispatch"])
@pytest.mark.parametrize("env", [{"PULL_NUMBER": "12"}, {"HEAD_SHA": HEAD_SHA}])
def test_partial_matching_pending_outputs_recover_only_failure_on_authorized_head(event, env):
    published = run_script("publish", make_payload(
        state=prior_success(), eventName=event,
        inputs={"pull_number": "12", "head_sha": HEAD_SHA}, env=env))
    assert published["errors"]
    assert published["state"]["statuses"][HEAD_SHA] == "failure"
    assert_check_blocks(published["state"])
    if event == "workflow_dispatch":
        assert published["events"][0]["api"] == "pulls.get"


@pytest.mark.parametrize("missing", ["snapshot_digest", "check_run_id"])
def test_missing_pending_evidence_never_allows_success_in_fallback(missing):
    payload = make_payload(state=prior_success())
    pending, validation, _ = run_pipeline(payload)
    outputs = {**pending["outputs"], **validation["outputs"]}
    del outputs[missing]
    published = run_script("publish", {
        **payload, "state": pending["state"],
        "env": step_environment(outputs, validation_result="success"),
    })
    assert published["errors"]
    assert_check_blocks(published["state"])
    assert published["state"]["statuses"][HEAD_SHA] == "failure"
    assert published["calls"]["graphql"] == []


@pytest.mark.parametrize("options", [
    {"pull": pull(base="release")}, {"repositoryId": 1},
    {"defaultBranch": "trunk"}, {"eventName": "push"},
    {"env": {"PULL_NUMBER": "12", "HEAD_SHA": "c" * 40}},
])
def test_always_fallback_does_not_write_outside_authenticated_event_binding(options):
    published = run_script("publish", make_payload(state=prior_success(), **options))
    assert published["errors"]
    assert published["state"] == prior_success()
    assert published["events"] == []


def test_total_write_outage_is_reported_not_claimed_as_successful_invalidation():
    pending, validation, published = run_pipeline(make_payload(
        state=prior_success(), check_create_error=True, status_error=True))
    assert pending["errors"] and validation is None and published["errors"]
    for phase in (pending, published):
        assert {event["api"] for event in phase["events"]} == {
            "checks.create", "statuses.create"}
        assert phase["state"] == prior_success()


def test_workflow_uses_trusted_exact_head_check_runs_and_bounded_manual_rechecks():
    data = policy()
    events = data.get("on", data.get(True))
    assert events["pull_request_target"] == {
        "branches": ["main"],
        "types": ["opened", "edited", "synchronize", "reopened", "ready_for_review"]}
    assert events["workflow_dispatch"]["inputs"] == {
        "pull_number": {
            "description": "Open pull request number",
            "required": True,
            "type": "string",
        },
        "head_sha": {
            "description": "Expected current pull head SHA",
            "required": True,
            "type": "string",
        },
    }
    assert data["permissions"] == {}
    assert data["concurrency"]["cancel-in-progress"] is False
    job = data["jobs"]["issue-link"]
    assert job["permissions"] == {
        "checks": "write", "issues": "read", "pull-requests": "read", "statuses": "write"}
    for step in job["steps"]:
        assert step["uses"] == "actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd"
        assert "${{" not in step["with"]["script"]
        assert "secrets." not in json.dumps(step)
        assert "run" not in step
    assert "checkout" not in WORKFLOW.read_text()
    assert [step["id"] for step in job["steps"]] == ["pending", "validate", "publish"]


def test_canonical_manual_link_without_body_keyword_passes_and_publishes_head_check_run():
    payload = make_payload(body="A description without a closing keyword.", refs=[issue(7)])
    pending, validation, published = run_pipeline(payload)

    assert pending["errors"] == validation["errors"] == published["errors"] == []
    assert pending["calls"]["checkCreates"][0]["name"] == "issue-link"
    assert pending["calls"]["checkCreates"][0]["head_sha"] == HEAD_SHA
    assert pending["calls"]["checkCreates"][0]["status"] == "in_progress"
    assert pending["calls"]["checkCreates"][0]["output"]["title"]
    assert set(pending["calls"]["checkCreates"][0]) <= {
        "owner", "repo", "name", "head_sha", "status", "started_at", "details_url", "output"}
    assert pending["calls"]["statuses"][0]["sha"] == HEAD_SHA
    assert pending["calls"]["statuses"][0]["context"] == "issue-link"
    assert pending["calls"]["statuses"][0]["state"] == "pending"
    assert published["calls"]["checkUpdates"][0]["check_run_id"] == 9981
    assert published["calls"]["checkUpdates"][0]["status"] == "completed"
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "success"
    assert "head_sha" not in published["calls"]["checkUpdates"][0]
    assert set(published["calls"]["checkUpdates"][0]) <= {
        "owner", "repo", "check_run_id", "status", "conclusion", "completed_at",
        "details_url", "output"}
    assert published["calls"]["statuses"][0]["state"] == "success"
    assert all("app" not in status for status in published["calls"]["statuses"])
    assert validation["calls"]["graphql"][0]["variables"]["number"] == 12


@pytest.mark.parametrize("step", ["validate", "publish"])
def test_octokit_direct_result_accepts_valid_canonical_link_in_each_reader(step):
    import hashlib

    payload = make_payload(body="Manual canonical link; no keyword.")
    pending = run_script("pending", payload)
    outputs = {**pending["outputs"], "relation_digest": hashlib.sha256(
        json.dumps([["Issue_node_7", 7]], separators=(",", ":")).encode()).hexdigest()}
    result = run_script(step, {
        **payload, "graph_responses": [graph_page(payload["pull"], [issue(7)])],
        "env": step_environment(outputs, validation_result="success"),
    })
    assert result["errors"] == []
    assert len(result["calls"]["graphql"]) == 1
    if step == "publish":
        assert result["calls"]["checkUpdates"][-1]["conclusion"] == "success"
        assert result["calls"]["statuses"][-1]["state"] == "success"


@pytest.mark.parametrize("step", ["validate", "publish"])
def test_octokit_partial_data_with_graphql_errors_throws_and_blocks(step):
    payload = make_payload()
    pending = run_script("pending", payload)
    result = run_script(step, {
        **payload,
        "graph_responses": [{"data": graph_page(payload["pull"], [issue(7)]),
                             "errors": [{"message": "Canonical references unavailable"}]}],
        "env": step_environment(pending["outputs"], validation_result="success"),
    })
    assert result["errors"] == ["Canonical references unavailable"]
    if step == "publish":
        assert result["calls"]["checkUpdates"][-1]["conclusion"] == "failure"
        assert result["calls"]["statuses"][-1]["state"] == "failure"
    else:
        assert result["thrown"] == "Canonical references unavailable"
        assert "relation_digest" not in result["outputs"]


def test_closing_keyword_without_canonical_edge_does_not_pass():
    pending, validation, published = run_pipeline(
        make_payload(body="Closes #7", refs=[]))
    assert pending["errors"] == []
    assert validation["errors"]
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"
    assert published["calls"]["statuses"][0]["state"] == "failure"


def test_every_linked_issue_must_be_open_same_repository_non_pr_issue():
    refs = [issue(7), issue(8)]
    pending, validation, published = run_pipeline(
        make_payload(body="Several tracked issues.", refs=refs))
    assert pending["errors"] == validation["errors"] == published["errors"] == []

    for invalid in (
        issue(8, state="CLOSED"),
        issue(8, repo_id="R_other", repository="other/project"),
        issue(8, typename="PullRequest"),
        issue(8, node_id=""),
    ):
        _, validation, published = run_pipeline(
            make_payload(body="Closes #7", refs=[issue(7), invalid]))
        assert validation["errors"]
        assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"


@pytest.mark.parametrize("body,refs", [
    ("", []),
    ("Closes #7", []),
    ("", [issue(7, state="CLOSED")]),
    ("", [issue(7, repo_id="R_other", repository="other/project")]),
    ("", [issue(7, typename="PullRequest")]),
    ("", [issue(7), issue(7)]),
    ("", [issue(7), issue(7, node_id="Issue_node_7_other")]),
    ("", [issue(7), issue(8, node_id="Issue_node_7")]),
])
def test_missing_or_malformed_canonical_references_fail_closed(body, refs):
    pending, validation, published = run_pipeline(make_payload(body=body, refs=refs))
    assert pending["errors"] == []
    assert validation["errors"]
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"
    assert published["calls"]["statuses"][0]["state"] == "failure"


def test_graphql_pagination_must_be_complete_and_coherent():
    pr = pull(body="Two links.")
    first = graph_page(pr, [issue(7)], total=2, has_next=True, cursor="cursor-1")
    second = graph_page(pr, [issue(8)], total=2)
    pending = run_script("pending", make_payload(body="Two links.", refs=[]))
    result = run_script("validate", {
        **make_payload(body="Two links.", refs=[]), "graph_responses": [first, second],
        "env": step_environment(pending["outputs"]),
    })
    assert result["errors"] == []
    assert [call["variables"].get("after") for call in result["calls"]["graphql"]] == [
        None, "cursor-1"]


def test_excessive_incomplete_and_malformed_graphql_connections_fail():
    pending = run_script("pending", make_payload(refs=[issue(7)]))
    payload = make_payload(refs=[issue(7)])
    payload["total_count"] = 11
    result = run_script("validate", {
        **payload,
        "env": step_environment(pending["outputs"]),
    })
    assert result["errors"]

    malformed = make_payload(refs=[issue(7)])
    malformed["graph_responses"] = [{"repository": {}}]
    result = run_script("validate", {
        **malformed,
        "env": step_environment(pending["outputs"]),
    })
    assert result["errors"]


@pytest.mark.parametrize("changed", [
    {"head": {"ref": "feature", "sha": "c" * 40,
              "repo": {"id": 9001, "full_name": "fork-owner/hermes-mobile"}}},
    {"body": "Changed body"},
    {"base": {"ref": "other", "sha": BASE_SHA,
              "repo": {"id": REPOSITORY_ID, "node_id": REPOSITORY_NODE_ID,
                       "full_name": "lindayi/hermes-mobile"}}},
    {"state": "closed"},
])
def test_final_publication_rejects_stale_pr_metadata(changed):
    current = copy.deepcopy(pull(body="Closes #7"))
    current.update(changed)
    payload = make_payload(body="Closes #7")
    pending, validation, published = run_pipeline(payload, publish_pull=current)
    assert pending["errors"] == validation["errors"] == []
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"
    assert published["calls"]["statuses"][0]["state"] == "failure"


def test_relation_change_between_validation_and_publication_blocks_success():
    original = pull()
    graph_responses = [
        graph_page(original, [issue(7)]),
        graph_page(original, [issue(8)]),
    ]
    pending, validation, published = run_pipeline(
        make_payload(refs=[issue(7)]), graph_responses=graph_responses)
    assert pending["errors"] == validation["errors"] == []
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"
    assert published["calls"]["statuses"][0]["state"] == "failure"


def test_manual_recheck_requires_trusted_main_and_exact_current_head():
    payload = make_payload(eventName="workflow_dispatch", inputs={
        "pull_number": "12", "head_sha": HEAD_SHA})
    pending, validation, published = run_pipeline(payload)
    assert pending["errors"] == validation["errors"] == published["errors"] == []
    assert pending["calls"]["checkCreates"][0]["head_sha"] == HEAD_SHA

    wrong_branch = {**payload, "ref": "refs/heads/feature"}
    result = run_script("pending", wrong_branch)
    assert result["errors"]
    assert result["calls"]["checkCreates"] == []

    wrong_head = {**payload, "inputs": {"pull_number": "12", "head_sha": "c" * 40}}
    result = run_script("pending", wrong_head)
    assert result["errors"]
    assert result["calls"]["checkCreates"] == []


@pytest.mark.parametrize("overrides", [
    {"repositoryId": 1},
    {"defaultBranch": "trunk"},
    {"pull": pull(base="release")},
])
def test_initial_repository_and_base_identity_must_match(overrides):
    payload = make_payload(**overrides)
    result = run_script("pending", payload)
    assert result["errors"]
    assert result["calls"]["checkCreates"] == []
    assert result["calls"]["statuses"] == []


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_failed_cancelled_or_skipped_validation_never_publishes_success(result):
    pending = run_script("pending", make_payload())
    published = run_script("publish", {
        **make_payload(),
        "env": step_environment(pending["outputs"], validation_result=result),
    })
    assert published["calls"]["checkUpdates"][0]["conclusion"] == "failure"
    assert published["calls"]["statuses"][0]["state"] == "failure"


@pytest.mark.parametrize("step,options", [
    ("pending", {"pull_error": True}),
    ("pending", {"check_create_error": True}),
    ("pending", {"status_error": "pending"}),
    ("validate", {"graphql_error": True}),
    ("publish", {"check_update_error": True}),
    ("publish", {"status_error": "final"}),
])
def test_api_errors_never_produce_success(step, options):
    payload = make_payload(**options)
    if step == "pending":
        result = run_script(step, payload)
        assert result["errors"]
        assert result["thrown"] is not None
        return
    pending = run_script("pending", payload)
    validation = None
    env = step_environment(pending["outputs"])
    verdict = "success"
    if step in {"validate", "publish"}:
        validation = run_script("validate", {**payload, "env": env})
        if step == "validate":
            assert validation["errors"]
            verdict = "failure"
        else:
            assert validation["errors"] == []
        env = step_environment(
            {**pending["outputs"], **validation["outputs"]},
            validation_result=verdict)
    published = run_script("publish", {
        **payload, "env": step_environment(
            {**pending["outputs"], **(validation["outputs"] if validation else {})},
            validation_result=verdict),
    })
    assert published["errors"]
    if step == "publish" and options == {"check_update_error": True}:
        # Compatibility success precedes the authoritative write, then is cleared
        # if that write fails. No intermediate app-bound state may pass.
        assert [status["state"] for status in published["calls"]["statuses"]] == [
            "success", "failure"]
        assert published["state"]["statuses"][HEAD_SHA] == "failure"
    else:
        assert all(status["state"] != "success" for status in published["calls"]["statuses"])
    assert all(not app_bound_gate(entry["after"]) for entry in published["events"]
               if "after" in entry)
    if step == "publish" and options == {"status_error": "final"}:
        assert [run["conclusion"] for run in published["calls"]["checkUpdates"]] == [
            "failure"]


def test_workflow_check_run_interoperates_with_app_bound_coordinator_gate():
    pending, validation, published = run_pipeline(make_payload())
    assert validation["errors"] == published["errors"] == []
    # Replay actual script writes; only GitHub's returned app identity is synthetic.
    check_run = {**published["state"]["checks"][-1],
                 "app": {"id": 15368, "name": "GitHub Actions"}}
    legacy_status = {**published["calls"]["statuses"][-1],
                     "created_at": "2026-10-03T02:00:00Z"}
    assert check_run["head_sha"] == legacy_status["sha"] == HEAD_SHA
    required = [{"context": "issue-link", "app_id": 15368}]
    assert required_checks_pass(required, [check_run], [legacy_status], complete=True)
    assert not required_checks_pass(required, [], [legacy_status], complete=True)
    assert not required_checks_pass(required, [{**check_run, "app": {"id": 1}}],
                                    [legacy_status], complete=True)
    assert "app" not in legacy_status
