# Owner-authorized issue starter

Issue #28 adds a separate, opt-in worker for starting cloud agent tasks from an
authorized public issue. It does not activate a service, alter GitHub settings,
merge changes, or deploy code. The implementation uses GitHub's public-preview
agent-task REST API (`POST` and `GET
/agents/repos/{owner}/{repo}/tasks[/{task_id}]`; see the
[official API reference](https://docs.github.com/en/rest/agent-tasks/agent-tasks)).
The contract may change.

## Authorization and data boundary

The only kickoff authorization is the exact `/hermes start` comment on an open,
non-PR issue, authored by GitHub user `5164171` in repository
`lindayi/hermes-mobile` (repository ID `1399942965`). An issue author, label,
issue body, or other commenter does not authorize work. The worker verifies the
authenticated GitHub user and repository before acting.

The durable owner-private state records the immutable issue number, comment ID,
accepted title/body digest, and comment timestamp before dispatch. It sends only
the public issue title/body as explicitly untrusted task context; it does not
include local credentials, private sessions, or arbitrary thread comments. The
issue content is JSON-escaped in the task prompt. Before accepting that snapshot,
the worker verifies bounded GraphQL `Issue.lastEditedAt` and
`Issue.userContentEdits` history plus timestamped REST `renamed` timeline events.
Edits after authorization, incomplete pagination/evidence, closed issues, and
stale commands fail closed. Both collection and preflight require the owner command
to have a valid `created_at` and an identical explicit `updated_at`; edited or
unverifiable commands cannot dispatch or hand off. Reopening requires a newer owner
command and verified
terminal evidence for any earlier remote task; closed authorization does not clear
an active or response-uncertain task fence.

## Use

Run the default command for a read-only plan. It performs authenticated reads,
but creates no state file, lock, or bytecode:

```sh
python3 scripts/issue_starter.py
```

Writes require both explicit flags:

```sh
python3 scripts/issue_starter.py --once --apply
```

Use `--state PATH` only for an owner-private state location. The default is
`$XDG_STATE_HOME/hermes-mobile-issue-starter/state.json`, or
`~/.local/state/hermes-mobile-issue-starter/state.json`. State and lock files
must remain owner-only; symlinks, unsafe permissions, malformed state, and
oversized histories are rejected. Serialization and the 4 MiB byte cap are checked
before opening a temporary state file; an oversized update leaves the previous
state byte-for-byte intact and readable.

`gh` must be authenticated as the fixed repository owner with a user token that
has the GitHub Agent tasks repository permission (read/write). GitHub App
installation tokens are not supported by these endpoints. No credential value
is read or persisted by the worker. CLI prompt and update-notifier behavior is
disabled for each invocation. Apply one bounded poll at a time; repeated
invocations may be needed while a task is running. Poll failures, uncertain
responses, and unverified identities are escalated rather than used to select
or dispatch arbitrary tasks.

## Dispatch and handoff

Before the task POST, state durably reserves the issue/comment identity and the
current trusted `main` SHA. The request uses `create_pull_request: true` and
`base_ref: "main"`. Its fixed instructions require reading `AGENTS.md`, a
plain-paragraph PR description with a literal `Closes #N` reference and all
required evidence fields from the repository template, managed strict TDD, exact
test/review evidence, cloud-only execution, and no merge, production access,
settings, or permissions changes. Keep the plain-paragraph description and
literal closing reference for readability; neither its punctuation nor its
contents prove issue linkage. Optional rich evidence belongs in comments, not
the description.

The worker polls only the persisted task ID. It requires the task's repository
and owner/creator identity, a completed task with a bounded positive
`session_count`, and the task's unique GitHub branch/PR artifacts. The documented
[task-detail GET endpoint](https://docs.github.com/rest/agent-tasks/agent-tasks#get-a-task-by-repo)
includes `sessions` (in the second `allOf` member of its OpenAPI response schema),
unlike the task-list summary. Coordinator continuation binds the persisted task
to that authenticated session's identity, dispatch nonce, branch and completion
time, then validates the exact result receipt. `session_count` alone is not
evidence that tests or reviews passed. For each handoff it reads the actual
`PullRequest.closingIssuesReferences` GraphQL connection from the fixed
repository, follows every bounded page, and requires exactly one matching
issue number and repository ID. It binds the GraphQL repository and PR node,
number, head branch and SHA, base branch, and body to the detailed REST pull.
Null, partial, malformed, duplicate, inconsistent, or unbounded results fail
closed. Every page must describe the same PR snapshot, and a fresh REST pull
must match after collection. The body digest and exact head are reserved with
the handoff and rechecked before and after readiness and enrollment operations;
linkage is never cached across a changed body or head. There is no fallback to
description parsing or caller-supplied linkage flags. Include baseline, scope,
acceptance, RED/GREEN, exact tests and review evidence, rollout, and
merged-versus-deployed state. Unsupported source claims remain blocked. No
renderer or additional runtime dependency is introduced.

If the task leaves the PR as a draft, a durable one-time
readiness reservation invokes GitHub's `markPullRequestReadyForReview` GraphQL
mutation for the verified PR node ID. Fresh task, artifact, PR, and head evidence
is checked again before enrollment; uncertain mutations are never blindly retried.

Readiness is a PR-level presentation/workflow operation, not merge authority or
an atomic head attestation. The official GraphQL
[`MarkPullRequestReadyForReviewInput`](https://docs.github.com/en/graphql/reference/input-objects#markpullrequestreadyforreviewinput)
contains only `clientMutationId` and `pullRequestId` (verified by live read-only
`__type` introspection); **it does not support `expectedHeadOid`**. A push at the
mutation boundary can therefore mark a newer head ready. Fresh post-mutation
reads block enrollment on a detected change; uncertain writes are not retried.

Only after that proof, the worker posts exactly `/hermes enroll <40lowerhex>`,
using the reserved verified head SHA and owner-authenticated API client. Before
posting, it captures the PR comment high-water ID and reserves a fresh enrollment,
even if an exact historical command exists: that command may already have been
consumed on a different head. Only an exact, immutable owner comment with a positive
ID above this boundary can confirm the POST response or reconcile a started/uncertain
send; an uncertain send is never reposted. The
paired cloud coordinator recognizes this command in its complete scan, requires
the SHA to match the current PR head, and persists the bound head. A mismatch is
consumed without enrollment and cannot authorize a later head or be replayed.
Bound enrollment cannot inherit authority on a subsequent push; an explicit
new owner command is needed. A newer exact-head command renews an active bound
enrollment without replacing its initial `authorized_head`; it retains unresolved
task claims, durable receipt proofs and the repair budget, and clears sensitive
authorization. Coordinator-dispatched fixer continuation requires an
unchanged exact task/session/nonce receipt with result `ready`, chained from the
immutable initial head. The default [v2 receipt contract](cloud-coordinator-spec.md)
echoes the exposed `COPILOT_AGENT_SESSION_ID` plus the fixed dispatch nonce,
PR/start head/dispatch-time main and result head; only the parent authenticated
Task API binds the saved task UUID. Strict v1 receipts remain readable. Missing
session environment is a blocker, not grounds to guess IDs or grant credentials.
The child finishes after pushing/focused checks rather than waiting for parent-run
CI/review. A receipt does not carry sensitive authorization, review approval, or
check success to a new head. Recorded base provenance survives unrelated main
advance, including before first receipt observation or after compaction/restart;
behind proven heads still require bounded neutral reconciliation and fresh gates.

The owner's legacy exact `/hermes enroll` remains broad/manual coordinator
authorization. The starter never emits it or accepts it as proof of its own
SHA-bound handoff. Because both clients use the same owner identity, historical
bare comments cannot be distinguished from manual authorization by GitHub author
ID: review/reconcile any legacy starter comments before activation. Install the
paired consumer before enabling this producer; it accepts only the SHA-bound
command for this handoff, with no fallback to bare commands.
The starter does not import the coordinator at runtime. A task completion is not
a passing test, review, required check, approval, merge, or deployment. Existing
coordinator and repository protections remain authoritative.

Only fixed-text completion and blocker receipts are posted publicly. Routine
polling is silent. Comment/task response uncertainty is reconciled from durable
state and remote markers. Receipt marker lookups are bounded and an irreconcilable
receipt is durably abandoned without reposting; optional receipts do not block
task polling or unrelated dispatch. The worker does not blindly repeat task
dispatch, readiness updates, or comments.

## Operations and verification boundary

The user service and timer files under `deploy/` are disabled templates only;
they expect the canonical checkout at `%h/projects/hermes-mobile-git` and a configured owner `gh`
login in that user environment. They contain no install target and are not
installed or enabled here. A parent operator must review this code and perform
a read-only live API probe before any activation. No authenticated live probe,
task dispatch, enrollment, service installation, settings change, or production
action is performed by this implementation task. Tests use only synthetic
GitHub API fixtures through the managed test runner. The producer-to-consumer test
runs the actual in-checkout starter and coordinator enrollment scan. Coordinator
tests also exercise fixer receipts across result heads, fresh review/check evidence,
restart, and replay. There is no absent-consumer skip or external source override.

Coordinator and notification services run under the same Unix account and user
manager as their owner, not a separate service identity. Unit sandbox settings
restrict access but do not isolate the service from all files and user-level
resources available to that account. `NoNewPrivileges` prevents gaining new
privileges through execution but does not remove the account's existing access.
`ProtectKernelModules` is deliberately omitted from these user units because the
unprivileged user manager cannot apply it and reports `218/CAPABILITIES`. Other
tested hardening remains enabled. This source change is not proof of successful
host activation; the parent operator owns that qualification.

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py python -- \
    tests/test_issue_starter.py tests/test_issue_starter_unit.py \
    tests/test_issue_starter_comment_proofs.py tests/test_issue_starter_remote_identities.py \
    tests/test_issue_starter_handoff.py tests/test_issue_starter_paired_lifecycle.py \
    tests/test_cloud_coordinator.py tests/test_cloud_coordinator_unit.py \
    tests/test_cloud_coordinator_latest_review.py \
    tests/test_cloud_coordinator_presend.py tests/test_task_receipts.py \
    tests/test_task_receipts_lifecycle.py tests/test_workflow_lifecycle_sources.py \
    tests/test_workflow_notifications_integrated.py
```
