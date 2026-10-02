# Owner-authorized issue starter

Issue #28 adds a separate, opt-in worker for starting cloud agent tasks from an
authorized public issue. It does not activate a service, alter GitHub settings,
merge changes, or deploy code. The implementation uses GitHub's public-preview
agent-task REST API (`POST` and `GET
/agents/repos/{owner}/{repo}/tasks[/{task_id}]`; see the
[official API reference](https://docs.github.com/en/rest/agent-tasks/agent-tasks).
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
stale commands fail closed. Reopening requires a newer owner command and verified
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
is read or persisted by the worker. Apply one bounded poll at a time; repeated
invocations may be needed while a task is running. Poll failures, uncertain
responses, and unverified identities are escalated rather than used to select
or dispatch arbitrary tasks.

## Dispatch and handoff

Before the task POST, state durably reserves the issue/comment identity and the
current trusted `main` SHA. The request uses `create_pull_request: true` and
`base_ref: "main"`. Its fixed instructions require reading `AGENTS.md`, a
`Closes #N` PR, managed strict TDD, exact test/review evidence, cloud-only
execution, and no merge, production access, settings, or permissions changes.

The worker polls only the persisted task ID. It requires the task's repository
and owner/creator identity, a completed task and successful bound session, and
the task's unique GitHub branch/PR artifacts. It checks the PR's repository,
main target, branch, open state, issue-closing reference, and exact current head
before any handoff. Closing references are accepted only in a conservative plain
Markdown subset. Raw HTML, links/images, code, lists/quotes (including lazy
continuations), escapes, and other unsupported markup fail closed for the whole
body, even if a separate valid directive exists. Complete same-line inline HTML
comments within a text paragraph are ignored without joining surrounding text;
line-start comments are HTML blocks, not inline text (CommonMark §4.6). Use a
plain `Closes #N` description if a rich description is rejected. No renderer or
additional runtime dependency is introduced.

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
using the reserved verified head SHA and owner-authenticated API client. The
paired cloud coordinator recognizes this command in its complete scan, requires
the SHA to match the current PR head, and persists the bound head. A mismatch is
consumed without enrollment and cannot authorize a later head or be replayed.
Bound enrollment cannot inherit authority on a subsequent push; an explicit
new owner command is needed. A new owner command retains unresolved task claims
and the repair budget. This deliberately does not infer that a later fixer commit
was authorized merely because a task completed.

The owner's legacy exact `/hermes enroll` remains broad/manual coordinator
authorization. The starter never emits it or accepts it as proof of its own
SHA-bound handoff. Because both clients use the same owner identity, historical
bare comments cannot be distinguished from manual authorization by GitHub author
ID: review/reconcile any legacy starter comments before activation. Install the
paired consumer before enabling this producer; no fallback to bare commands.
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
they expect the checkout at `%h/hermes-mobile` and a configured owner `gh`
login in that user environment. They contain no install target and are not
installed or enabled here. A parent operator must review this code and perform
a read-only live API probe before any activation. No authenticated live probe,
task dispatch, enrollment, service installation, settings change, or production
action is performed by this implementation task. Tests use only synthetic
GitHub API fixtures through the managed test runner. The handoff integration test
loads the actual consumer source (not a replacement acceptance predicate). Until
both changes share a checkout, set `HERMES_COORDINATOR_SOURCE` to the paired
checkout's `deploy/cloud_coordinator.py`; absent both an integrated source and
that explicit path, the paired cases are reported as skipped, not as coverage:

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py python -- tests/test_issue_starter.py tests/test_issue_starter_handoff.py
```
