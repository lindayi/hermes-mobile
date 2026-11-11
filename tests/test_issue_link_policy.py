"""Run the trusted issue-link workflow script against synthetic GitHub responses."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/issue-link.yml"
HEAD_SHA = "a" * 40


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def issue(number, *, state="OPEN", repository="lindayi/hermes-mobile", node_id=None):
    return {"number": number, "state": state, "repository": {"nameWithOwner": repository}}


HARNESS = r"""
const input = JSON.parse(process.argv[1]);
const p = input.payload;
const calls = {checkCreates: [], checkUpdates: [], statuses: [], graphql: 0};
const errors = [];
const pull = p.pull || {number: 12, state: 'open', head: {sha: p.head}, base: {ref: 'main'}};
const context = {
  repo: {owner: 'lindayi', repo: 'hermes-mobile'}, runId: 1, serverUrl: 'https://github.com',
  eventName: p.eventName || 'pull_request_target', ref: p.ref || 'refs/heads/main',
  payload: {pull_request: {number: 12, head: {sha: p.head}}, inputs: p.inputs || {}},
};
const github = {
  rest: {
    repos: {createCommitStatus: async args => { calls.statuses.push(args); return {data: {}}; }},
    pulls: {get: async () => ({data: pull})},
    checks: {
      create: async args => { calls.checkCreates.push(args); return {data: {id: 77}}; },
      update: async args => { calls.checkUpdates.push(args); return {data: {}}; },
    },
  },
  graphql: async () => {
    calls.graphql++;
    return {repository: {pullRequest: {headRefOid: p.graphHead || p.head,
      closingIssuesReferences: {nodes: p.refs}}}};
  },
};
const core = {setFailed: message => errors.push(String(message))};
(async () => {
  try {
    await (new Function('github', 'context', 'core', 'return (async()=>{' + input.script + '})()'))(
      github, context, core);
  } catch (error) { errors.push(error.message); }
  console.log(JSON.stringify({calls, errors}));
})();
"""


def run_pipeline(payload):
    node = os.environ.get("HERMES_TEST_NODE") or shutil.which("node")
    assert node, "Managed test runner must provide Node"
    script = workflow()["jobs"]["issue-link"]["steps"][0]["with"]["script"]
    result = subprocess.run([node, "-e", HARNESS, json.dumps({"payload": payload, "script": script})],
                            capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout)


def make_payload(*, refs, **overrides):
    return {"head": HEAD_SHA, "refs": refs, **overrides}


def conclusion(result):
    updates = result["calls"]["checkUpdates"]
    return updates[-1]["conclusion"] if updates else None


def test_workflow_is_metadata_only_with_minimal_permissions():
    policy = workflow()
    job = policy["jobs"]["issue-link"]
    assert policy["permissions"] == {}
    assert job["permissions"] == {"checks": "write", "issues": "read", "pull-requests": "read",
                                  "statuses": "write"}
    assert all("checkout" not in step.get("uses", "") and "run" not in step for step in job["steps"])


def test_open_canonical_issue_reference_succeeds_on_exact_head():
    result = run_pipeline(make_payload(refs=[issue(7)]))
    assert result["errors"] == []
    assert result["calls"]["checkCreates"][0]["head_sha"] == HEAD_SHA
    assert result["calls"]["checkCreates"][0]["name"] == "issue-link"
    assert conclusion(result) == "success"
    # The legacy same-name commit status follows the check so older heads unblock.
    assert result["calls"]["statuses"][-1]["state"] == "success"
    assert result["calls"]["statuses"][-1]["sha"] == HEAD_SHA


@pytest.mark.parametrize("refs", [
    [], [issue(7, state="CLOSED")], [issue(7, repository="someone/else")],
])
def test_missing_closed_or_foreign_reference_fails(refs):
    result = run_pipeline(make_payload(refs=refs))
    assert result["errors"]
    assert conclusion(result) == "failure"
    assert result["calls"]["statuses"][-1]["state"] == "failure"


def test_changed_head_fails_instead_of_passing_stale_metadata():
    result = run_pipeline(make_payload(refs=[issue(7)], graphHead="b" * 40))
    assert conclusion(result) == "failure"
    moved = run_pipeline(make_payload(refs=[issue(7)], pull={
        "number": 12, "state": "open", "head": {"sha": "b" * 40}, "base": {"ref": "main"}}))
    assert conclusion(moved) == "failure"


def test_manual_recheck_requires_trusted_main_ref():
    inputs = {"pull_number": "12", "head_sha": HEAD_SHA}
    ok = run_pipeline(make_payload(refs=[issue(7)], eventName="workflow_dispatch", inputs=inputs))
    assert conclusion(ok) == "success"
    bad = run_pipeline(make_payload(refs=[issue(7)], eventName="workflow_dispatch",
                                    inputs=inputs, ref="refs/heads/feature"))
    assert bad["errors"] and bad["calls"]["checkCreates"] == []
