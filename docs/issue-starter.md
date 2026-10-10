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
Its pure `_fold_issue_edit_page` page validator is shared with the paired
coordinator, so both clients apply one edit-history consistency contract.
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
and owner/creator identity, a completed task with exactly one authenticated,
completed session matching its task/user/owner/repository IDs, and the task's
unique GitHub branch/PR artifacts. The documented
[task-detail GET endpoint](https://docs.github.com/rest/agent-tasks/agent-tasks#get-a-task-by-repo)
includes `sessions` (in the second `allOf` member of its OpenAPI response schema),
unlike the task-list summary. Coordinator continuation binds the persisted task
to that authenticated session's identity, dispatch nonce, branch and completion
time, then validates the exact result receipt. `session_count` alone is not
evidence that tests or reviews passed. For each handoff it reads the actual
`PullRequest.closingIssuesReferences` GraphQL connection from the fixed
repository, follows every bounded page, and requires exactly one matching
issue number and repository ID. On every page, the outer GraphQL repository ID
must match the supported REST `base.repo.node_id` from the detailed pull, whose integer
`base.repo.id` must equal the fixed repository ID `1399942965`. Missing or malformed
node IDs fail closed, and the anchor must remain unchanged in the fresh REST read;
self-consistent GraphQL IDs and repository names alone are not sufficient. It also
binds the PR node, number, head branch and SHA, base branch, and body to that pull.
Null, partial, malformed, duplicate, inconsistent, or unbounded results fail
closed. Every page must describe the same PR snapshot, and a fresh REST pull
must match after collection. The body digest and exact head are reserved with
the handoff and rechecked before and after readiness and enrollment operations;
linkage is never cached across a changed body or head. There is no fallback to
description parsing or caller-supplied linkage flags. Include baseline, scope,
acceptance, RED/GREEN, exact tests and review evidence, rollout, and
merged-versus-deployed state. Unsupported source claims remain blocked. No
renderer or additional runtime dependency is introduced.

The documented [Start a task](https://docs.github.com/en/rest/agent-tasks/agent-tasks#start-a-task)
request accepts a prompt and task/branch options, but no originating issue binding.
For a completed task whose PR has no canonical closing edge, the starter may use
GitHub's documented GraphQL
[`addCloseIssueReferences`](https://docs.github.com/en/graphql/reference/issues#addcloseissuereferences)
mutation. It first requires a complete, coherent read proving the exact authorized
issue edge is absent; malformed, partial, paginated-incompletely, conflicting, or
changed evidence never proves absence. It binds the issue and PR node IDs from
authenticated API readback, the one uniquely authenticated completed task session,
the exact GitHub task artifacts, and the complete REST PR snapshot. It durably
reserves the one-shot write before sending it. An already-present original edge is
the zero-write path.

Before and after the mutation, the starter revalidates the owner issue/comment
authority, completed task and session, task artifacts, PR identity, head, base,
body, and draft state. It then independently reads the complete canonical
`closingIssuesReferences` connection and fresh REST snapshot. A lost or malformed
mutation response is reconciled only from that read; an uncertain write is never
retried. If that evidence is incomplete or changed, readiness and enrollment stay
blocked, and the provider-generated PR body remains byte-for-byte unchanged. The
shared consumer's authenticated canonical-link requirement is unchanged.

`addCloseIssueReferences` has no expected-head, expected-body, expected-base, or
expected-draft input. There is an unavoidable remote race between final validation
and the mutation; this operation is not an atomic handoff or compare-and-swap. The
post-mutation checks detect observed changes and block downstream readiness or
enrollment, but cannot undo the issue relation. The mutation is restricted to the
single issue and PR already bound to this authorized task; no arbitrary PR links,
body rewrites, credential changes, or consumer guard relaxations are permitted.

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

Only after that proof, the worker posts exactly
`/hermes enroll <40lowerhex> issue <N> body-sha256 <64lowerhex> source-task <task-id> source-session <session-id> source-command <start-comment-id>`,
using the reserved verified head SHA, originating issue number, exact raw UTF-8 PR
body digest, completed task/session IDs, and exact owner start-comment ID with the
owner-authenticated API client. The issue number is canonical
positive decimal, at most 2147483647. No trimming or Markdown normalization is
performed. Before posting, it captures the PR comment high-water ID and reserves a fresh enrollment,
even if an exact historical command exists: that command may already have been
consumed on a different head. Only an exact, immutable owner comment with a positive
ID above this boundary can confirm the POST response or reconcile a started/uncertain
send; an uncertain send is never reposted. Both direct POST confirmation and
uncertain-send reconciliation require a final fresh pull with `draft` explicitly
`false` before recording completion. A re-draft blocks completion while preserving
the consumed enrollment attempt: neither enrollment nor readiness is retried.
After upgrade, a `started`/`uncertain` record may also reconcile either exact
prior format: the version-2 comment without `source-command`, or the original
version-1 comment without `source-task`/`source-session`. Reconciliation requires
an immutable authenticated owner comment above the saved high-water and a matching
saved task/session binding (including a historical `link_intent.session_id`).
This read-only compatibility path never resends enrollment; malformed, edited,
stale or unbound evidence stays blocked.
This producer-side certification does not retract a comment already sent or make
the handoff atomic. The paired cloud coordinator authenticates the same immutable
owner command, exact current head and body digest, explicitly ready PR, and
canonical originating-issue closing edge before durable admission. Both clients
use the dependency-leaf `deploy/pull_handoff_binding.py`: at most 20 GraphQL pages,
100 nodes per page, and 60000 body characters, with unchanged repository anchors,
cursor/duplicate checks and a final REST snapshot (including base SHA) reread.
A complete stale-command rejection is consumed without enrollment and cannot
authorize a later head or be replayed. After all planning/source collection and
immediately before the atomic scan commit, the consumer repeats the command and
binding proof for every effective new starter admission or renewal. A late change
or incomplete read discards the entire prepared scan, including cursor, events,
receipt acceptance, lifecycle retirement and export, with no external write.
The consumer also reauthenticates the referenced completed task and its single
completed owner session, exact task artifacts, and source issue. It requires the
exact unedited owner `/hermes start` comment, unchanged issue body, no post-start
content edits or renames, and the start/task/session/admission timestamps in order.
The optional session `completed_at` may be omitted by GitHub: the consumer uses
the immutable owner-authenticated completed-source enrollment timestamp as a
conservative completion upper bound, while retaining completed task/session
states and ordered authenticated creation evidence. This is owner handoff
certification, not a provider-reported exact completion timestamp. Explicit invalid completion
timestamps fail closed. The producer and consumer both select exactly one GitHub
branch and pull from at most 20 artifacts; unrelated artifacts do not block an
otherwise bound handoff. Accepted exact-head Copilot review suppresses
redundant review requests.
The start-comment ID in the producer command prevents substitution of another
otherwise-valid owner command. It persists verified initial-source identity before
requesting genuine Copilot review through the bounded coordinator action flow.
Issue #92 retires independent reviewer task dispatch; no request publishes a review
or status or satisfies merge gates. Invalid, missing, or changed source blocks
review requests with a deduplicated outcome.
Compact versioned `starter_admission` and `initial_source` provenance are committed
with enrollment. Existing version-1/2 admissions are never completed by searching
the task list for a matching artifact: a version-1 admission is recoverable only
when its authenticated initial-source record was already durably saved, or through
the coordinator's read-only bridge to this owner-private ledger. Its default CLI
uses the same XDG/HOME path described above (empty XDG state home also falls back
to HOME), including installed no-flag invocation; `--starter-state <path>` may
override the input without any ledger writes. The bridge selects exactly one completed handed-off record
bound to the enrolled issue/PR/node/head/branch/base/body and saved comment
high-water, then reauthenticates its exact start command, accepted title/body,
task/session and live GitHub closing authority. Current handoff reservations
persist immutable `source_session_id`; the bridge explicitly compares that saved
binding to the fetched session. Historical canonical-link reservations may supply
their saved `link_intent.session_id` instead, with agreement required when both
are present. Missing saved session identity blocks recovery rather than adopting
the task's currently returned session. Resuming a pre-upgrade handoff reservation likewise
requires an immutable matching saved session binding before enrollment; an
already-existing closing edge is not a substitute for that proof.
It never writes the starter
ledger, re-enrolls, resets budgets, selects task-list candidates or invents a
fixer receipt. A version-2 admission must validate its explicitly recorded task/session and unique
unedited start command. Recovery preserves existing task claims, receipts, and
repair history; it does not fabricate source evidence or reset ledgers.
This is an admission condition, not a lifetime body/linkage pin: later legitimate
body reports and receipt-authorized result heads remain supported. GitHub reads,
local state commit and remote writes are not one transaction; a change after the
final coherent read remains an unavoidable remote race.
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

The owner's exact `/hermes enroll` and `/hermes enroll <40lowerhex>` remain
explicit manual coordinator authorization with their existing timestamp contracts.
The starter never emits either legacy form or accepts it as proof of its own
extended handoff. Malformed extended commands never downgrade to a legacy prefix.
Because both clients use the same owner identity, historical bare or SHA-only
starter comments cannot be distinguished from manual authorization by author ID:
review/reconcile any in-flight legacy starter comments before activation. Install
the paired consumer before enabling this producer; no automatic migration or
legacy fallback is provided.
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
