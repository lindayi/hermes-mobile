# Cloud coordinator specification

This issue implements a durable outbound coordinator for `lindayi/hermes-mobile`
(repository ID `1399942965`). It polls through the existing authenticated GitHub
CLI; it is not a webhook, a public command endpoint, a production Actions runner,
or a deployment controller. The coordinator never checks out, imports, or executes
pull-request code on the host. Code execution remains in GitHub Copilot's cloud
agent.

## Trust and enrollment

The CLI's default invocation is a read-only plan. A write cycle requires both
`--once` and `--apply`. Every cycle verifies the authenticated GitHub account is
owner ID `5164171` and the repository API identity matches the fixed repository
ID. It ignores issue bodies, labels, links, and comments from other authors.

An issue or pull request is enrolled by the exact comment `/hermes enroll` from
that owner. The issue must identify a pull request whose head and base both belong
to this repository and whose base branch is `main`. Existing pull requests are not
enrolled by their age, label, author, or open state. The issue starter instead hands
off with `/hermes enroll <40-lowercase-hex-head-sha>`; the consumer requires the
value to match the current pull request head and stores it as immutable
`authorized_head`. A stale or mismatched command is consumed without enrollment.
For a sensitive head, the owner must separately comment
`/hermes authorize-sensitive <40-char-head-sha>`. Authorization is recorded only
if that SHA is still the pull request's current head; it does not carry forward to
a later commit.

This is a source/first-activation boundary, not an upgrade path for historical
coordinator state. Version-1 records remain readable for inspection, but active
legacy enrollments lacking `pull_id`, `pull_node_id`, or `repository_id` are
unsupported: pull snapshot collection fails closed with
`Unsupported legacy enrollment: missing pull/repository identity; automatic migration is not supported; preserve state and stop activation`.
The coordinator does not backfill legacy identity, reset cursors/budgets, or discard
unresolved claims. A newer exact-SHA command may renew an already identity-bound,
SHA-bound active enrollment: it preserves the initial `authorized_head`, receipt
proofs, unresolved claims and attempt budget, records the new owner-authorized head,
and clears sensitive authorization. It cannot migrate a legacy enrollment.
If such state exists, stop activation and preserve it for separately reviewed
operator recovery; do not delete/reset state or use re-enrollment as a migration.
First activation must use the current identity-bound enrollment contract and must
not proceed on unsupported legacy active enrollments. This change installs or
enables no coordinator units.

## Collection and bounded repair

The coordinator captures a precollection watermark, polls issue updates and their
comments with overlapping reads, and atomically persists accepted commands, their
IDs and the watermark. A terminal PR records any observed merged/closed lifecycle
event in durable state before retiring its enrollment. A terminal PR already closed
at the first observation is treated as historical baseline and is not exported;
reopening requires a fresh owner enrollment command. API errors,
rate limits, malformed pagination, and incomplete GraphQL review-thread pages
fail closed; malformed issue/comment rows or IDs also abort the whole scan. The
cursor is advanced only after a complete read.

Only current unresolved review-thread comments and completed failed/timed-out
`Source checks` workflow runs are eligible repair evidence. It sends at most
eight findings, clips each finding to 1,000 characters, removes links, and never
fetches check logs. The Copilot request labels all embedded evidence untrusted,
and the text is never interpreted as shell input. Draft PRs and ordinary pending
review/check/task activity do not dispatch repairs or create owner notices.
Budget exhaustion is reported only for currently scoped, non-draft, idle work
that would otherwise be eligible for a bounded repair or neutral reconciliation.

Behind or genuinely conflicted enrolled PRs receive a neutral task through the same
durable reservation, exact task-ID reconciliation, serialization lock, and three
attempt budget as ordinary repair tasks. Its bounded prompt identifies both exact
branch SHAs and the PR's sanitized title/description as untrusted intent; the task
must preserve both sides, merge main into the PR branch (never rebase or force-push),
and test the combined behavior. Before returning a `ready` receipt, the prompt
requires a PR comment recording each conflict hunk's file/hunk identity,
classification, decision, and rationale explaining preservation of both branch
intents (or genuine incompatibility). This is a deliverable under the existing
task receipt and independent-review contract, not a new receipt schema or an
automatic semantic approval. A current-main fence is rechecked before dispatch.
The prompt directs genuinely incompatible requirements or broken required policy
to a fixed typed result; these stop further repair for that exact head. Waiting or
uncertain tasks retain the shared lock. Ordinary technical conflicts are repaired
within the shared budget; exhaustion or ambiguous execution becomes a meaningful
owner blocker rather than an unbounded retry.

A durable action claim is written before POSTing a task through
`/agents/repos/{owner}/{repo}/tasks` with the bounded prompt, `base_ref=main`
and the enrolled PR's current same-repository `head_ref`. The returned task ID
is persisted and GET by ID reconciles its state and branch/PR evidence across
head changes. An ambiguous POST (including a crash before ID persistence) is
never resent automatically; it blocks further dispatch for that PR. Unresolved
fixer claims survive PR closure, reopening, and fresh owner enrollment, even when
the repository task list is empty. Their attempt counter is retained as well so
attempt-derived request keys cannot collide; re-enrollment resets that counter
only when no unresolved fixer claim remains. A fresh enrollment is authorization,
not evidence that an earlier task stopped. No second
`@copilot` dispatch comment is posted. Queued, in-progress, waiting-for-user,
idle and unknown task states do not release the fixer lock. For SHA-bound starter
enrollments, a completed coordinator-dispatched task releases the lock only with
the exact task/session/nonce receipt defined in `deploy/task_receipts.py`. Its
unchanged, authenticated `ready` receipt may extend authorization from a previously
authorized head to that exact result head. The initial `authorized_head` never
changes; each later result head needs its own receipt rooted in an already
authorized head. Missing, edited, copied, stale, mismatched, or blocker receipts
do not authorize continuation. Sensitive authorization remains bound to its
original SHA, and review/check evidence must be independently fresh for each result
head. Validated task/session/nonce and unchanged-comment proof is persisted with
completion in the enrollment before lifecycle compaction can retire its action.
Restart and final dispatch revalidate the retained proof against the remote comment.
Deferred ready/review handoffs recheck it after the scan commit; a new observed bound
head clears stale sensitive authorization. A typed blocker remains `task-result-blocked`,
not an unrelated push. A receipt is not review or CI success. Bare manual enrollments
retain PR33 lifecycle and receipt handoff behavior.
Unidentifiable nonterminal repository tasks conservatively block new dispatch
until GitHub exposes enough branch, session, or PR evidence to scope them.
Only a positively pre-send superseded reservation can advance to a distinct
bounded attempt on unchanged head/base; sent or uncertain reservations are never
retried. Only confirmed `dirty` or `behind` pull requests receive the neutral
reconciliation task described above, not an ordinary repair task. Uncomputed or
unknown mergeability (including `mergeable: null`, missing fields, or a negative
mergeability result without an explicit `dirty`/`behind` state) defers both task
kinds. It claims/posts no task, consumes no attempt, and produces no budget
exhaustion notice or lifecycle event. Planning and both dispatch fences enforce
this boundary; a dispatch-time deferral clears `repair_requested` and reports
`mergeability-unknown` rather than a stale conflict/behind reason. A later poll
may resume ordinary repair or confirmed neutral reconciliation once computed.
Immediately before claiming a repair, the coordinator re-reads its bounded
thread/check evidence and fences the planned head, branch, main SHA, base binding,
mergeability and active tasks. Changed or incomplete evidence suppresses that
planned request without consuming an attempt; it does not substitute another
repair or fall back to auto-merge in the same cycle.

## Review, checks, and merge

The `cloud-review` gate accepts only a latest `APPROVED` review authored by the
authenticated Copilot review identity (GitHub ID `175728472`) on the exact current
head SHA, plus fully paginated review threads that are all resolved. A `COMMENTED`
review, arbitrary comment, stale approval, author assertion, or truncated thread
list does not pass. All authenticated reviews must have valid timezone-aware
submission times. Missing, malformed, or naive timestamps fail closed; this
includes an unsubmitted `PENDING` review, for which GitHub omits `submitted_at`.
Times are compared as instants, not strings. Every review tied at the latest
instant must approve the current head; conflicting states or heads fail closed
regardless of response order. This same gate controls planning, status publication
and revocation, and the final merge recheck. If the authenticated reviewer
identity differs, the check fails closed and requires policy review rather than
inferring approval.
For task handoff, an authenticated submitted review on the exact result head
completes the review request only when its timezone-aware submission instant is
strictly after the independently validated task session completion and no later
than the current clock. This also applies when the task leaves the head unchanged;
an earlier approval cannot shortcut the handoff. The validated completion time,
session ID and receipt comment ID are persisted with the receipt head/base and
dispatch claim for restart. The authentic session completion is retained as
`receipt_session_completed_at` in both the action and the SHA-bound enrollment's
`receipt_proofs` projection before compaction, alongside the supported
`receipt_completed_at` metadata. Observation time or mutable task update time is
not a substitute. Missing or invalid completion proof fails closed. A fresh submitted
review completes handoff even if unresolved threads keep the approval gate false;
those threads then remain eligible for the next bounded repair.
Preparation stages verified receipt/handoff state in memory. Controller evidence
is collected against durable lifecycle history plus all newly observed merge
events in the complete plan. The scan commits those source events, receipt state,
commands, cursor, observations and retirements atomically before any external
handoff, status or merge write. Capacity, validation or scan failure preserves the
entire prior state and export. Only a successful commit refreshes the export,
before deferred external work; preparation is discarded on failure.
After receipt acceptance, handoff uses the freshly scanned main SHA and rechecks
live main, exact result head and repository/PR identity. The stored receipt base
remains provenance, not a requirement that main never advance. This does not
change receipt schema or first-receipt validation.
A PR that becomes draft at the final dispatch fence is reported as a suppressed
repair with the `draft` reason; no task is claimed or posted.

Path classification includes both sides of renames and treats malformed,
unknown, empty, oversized, or incomplete file inventories as sensitive. Only the
seven explicitly audited presentation files (`frontend/styles.css`,
`frontend/viewport.mjs`, `frontend/session-swipe.mjs`,
`frontend/disclosure-reachability.mjs`, and three named PNG icons), bounded
portable test patterns, and non-operational top-level documentation are routine.
Authentication-bearing `frontend/ui.mjs`, bootstrap `frontend/app.js`,
`frontend/index.html`, `frontend/api.mjs`, and every unknown or new frontend
module require an owner exact-SHA decision. Operational, native, security and
deployment-policy docs are sensitive too. This PR classification concerns merge
authorization; the separate release policy still classifies the complete diff
from the deployed base before deployment.

The coordinator publishes only its own `cloud-review` commit status, and only
when that context is already required by branch protection/rules. Its success
means current Copilot approval, resolved threads, and any required sensitive
authorization were verified; it does **not** mean tests passed. It never writes
`integration-tests`, `source-ci`, or `agent-review` statuses. A status transition
has a durable generation bound to the PR and head SHA; an ambiguous write is
reconciled against a newer owned status on that same SHA, not assumed successful
from a previous matching state. A later change in review evidence may revoke
and restore success on the same SHA. All configured
required checks must independently report success; skipped, cancelled, missing,
pending, failed, or incomplete checks are not green. Branch rules are collected
with explicit `per_page=100` and `page` pagination, bounded to 100 pages; only a
short final page proves completion. Errors (including an unavailable rules
endpoint), malformed pages/policy fields, or exhaustion of the bound fail closed.
The policy retains the union of classic and all ruleset requirements, including
separate app bindings for the same check context.

Auto-merge is requested through GitHub's protected `enablePullRequestAutoMerge`
operation only when the same-repository main base is current, the PR is not a
draft or conflict, all required checks including `cloud-review` are green, the
review gate passes, no fixer may be running, and sensitive authorization is
current. Both the PR head and the current `main` SHA are re-read before enabling
auto-merge; the mutation supplies `expectedHeadOid` as the server-side head
fence. The owner-managed branch rule must also require the PR branch to be
up to date (strict required checks) and require conversation resolution, either
through classic protection or a ruleset `pull_request` rule's
`parameters.required_review_thread_resolution`; a malformed `pull_request` rule
leaves the policy unproven and blocks status publication and auto-merge. The
coordinator cannot alter branch protection. Current review, thread, check, and policy evidence is fetched again
immediately before the merge request. A new head has no inherited
`cloud-review` success, so required branch protection must keep it blocked until
the new head is evaluated. Ambiguous writes are reconciled from GitHub state and
are never blindly repeated. An auto-merge request is recorded as sent only when
the mutation returns the exact pull-request node ID and a valid, nonempty
`autoMergeRequest.enabledAt`; any other response remains uncertain until
GitHub's pull-request state proves auto-merge was enabled.

The durable outbox posts deduplicated, fixed-text outcome/blocker comments on
public PRs. It carries no logs, credentials, arbitrary issue text, or private
runtime data. The owner mobile Inbox is not implemented by this adapter.
Before posting, an existing marker in the fully read PR comments proves
publication only when the numeric author ID matches the authenticated owner used
for writes and the body exactly matches the planned notification. Copied markers
from other users, missing identity metadata, and edited notification bodies do
not count. POST responses require the same owner/body proof plus a comment ID;
unproven responses stay uncertain and are never automatically retried.

Apply mode atomically writes a private `<state_dir>/workflow-events.json` snapshot
using the closed, versioned schema and fixed lifecycle reason/outcome mapping.
It includes only sanitized identifiers, exact lowercase SHAs, and UTC times.
Raw GitHub text, check logs, commands, credentials, and private runtime data are
excluded. Export persistence is bounded to 256 events and 1 MiB; plan mode never
writes it. Stable event IDs preserve polling replay identity, and terminal
enrollment retirement is committed with event persistence.

Before each export, sensitive approval events are filtered against durable
active enrollment, the exact last observed open head, and still-pending
`authorize_sensitive_action` authorization. Apply commits the complete scan's
head observations, owner commands, and retirements before exporting; an old
enrollment head is not a fallback for missing current-head observation. Authorized,
head-superseded, retired, unobserved, or unsupported decisions are omitted from
the exported view only. Durable incident payloads/IDs/times and all other outcomes
remain unchanged; consumer ACK history is neither read nor rewritten, and export
never grants or revokes an approval. If filtering leaves no events, the previous
export is removed rather than refreshing an obsolete request. This is a polling
snapshot of observed state, not a live authorization endpoint.

The export is written to the existing state directory selected by application
configuration, not the coordinator's private state directory. The coordinator
binds the envelope to the sole ready default-profile application owner read from
the configured auth database. This opaque application user ID is independent of
GitHub's numeric repository-owner ID.

Issue-starter failure and uncertainty events are read from its durable v1
command state only when a blocked receipt is persisted as sent and the exact,
unchanged owner-authored receipt comment is present; that comment's creation
time supplies the incident timestamp. Deployed events are emitted only for an
existing exact merged event when the current delivery ledger, successful
controller status, current release, and git provenance validate for that merge
SHA. A merge alone is never treated as a deployment.

Task receipt v2 is the default generated dispatch contract. The host supplies
literal nonce, PR number, start head and **dispatch-time main SHA** in the prompt;
the child copies those values, reads `COPILOT_AGENT_SESSION_ID` from its exposed
environment, and reports the pushed result head. Missing session environment is an
honest blocker: never guess an ID, emit a ready receipt, obtain extra credentials,
or request an owner-comment handshake. The child is not asked to discover or echo
the separate Task UUID. The read-only cloud probe established session-environment
identity, not a source for the Task UUID (and not proof that such a source cannot
exist).

Post exactly one unchanged authenticated Copilot issue comment, with these exact
ordered lines, no fences, extra fields, surrounding text or trailing newline:

```text
Hermes-Task-Receipt: v2
nonce=<fixed-dispatch-nonce>
session=<COPILOT_AGENT_SESSION_ID>
pr=<fixed-pull-number>
start_head=<fixed-dispatched-head-sha>
head=<pushed-current-pull-head-sha>
base=<fixed-dispatch-time-main-sha>
result=<ready|conflict_incompatible|policy_broken>
```

Choose exactly one closed result value. After pushing and focused checks, the
coding task must not idle waiting for CI/review: the parent controller handles
those phases. `ready` is not passing CI, approval, merge or deployment success.

Task identity still comes solely from the durable saved task ID and authenticated
Task API response, never from a comment. The host validates exact returned task ID,
`session.task_id`, task/session owner and repository, creator/user, nonce, exact
PR and branch artifacts, and task/session/comment chronology. Receipt author and
comment ID must be strict numeric GitHub identities (positive comment ID, no
float/string/bool coercion). Missing, edited, duplicate, mixed-version, mixed-field,
copied or otherwise noncanonical receipts fail closed.

The strict v1 reader remains for existing receipts and proofs: its body includes
`task=<authenticated-task-id>` immediately after nonce, and its base must match
main at initial receipt validation. No historical dispatch binding is invented.
For v2, base instead must equal trusted `action.main_sha`, even when main advances
before the first receipt poll. The receipt version and authentic dispatch main
are retained with the proof before compaction. Revalidation requires canonical
body/metadata consistency, v2 dispatch-base equality and exactly one unchanged
remote receipt for that authenticated author/nonce.

The paired PR29 consumer uses this receipt to prove result-head continuation
without inheriting review/check or sensitive authorization. Proven HEAD authority
survives unrelated main advances; the recorded base remains provenance, not a
fresh eligibility predicate. Handoff mutations fence against freshly scanned and
live current main, not historical receipt base. A behind authorized result head
must take bounded neutral reconciliation, never merge behind. Repository/base/ref,
strict up-to-date policy, mergeability, exact head and pre-send main fences remain
required; fresh review/check evidence is still required for the repaired head.

The owner mobile Inbox consumer is a separate adapter and must validate this
producer's exact event schema and bind the export to the actual ready application
owner. A GitHub account ID is not an application owner ID. Exported merged
outcomes do not imply deployment. The coordinator neither invokes the consumer
nor activates notification delivery.

PR33 reuses the consumer's read-only owner, private-file, and deployment-evidence
validators without editing its files. During current-main assembly, preserve
those validators or expose an equivalent supported shared API, and rerun the
producer-to-Inbox integration tests against the assembled schema and consumer.

## Bounded state

The serialized state is limited to 4 MiB. The cap is checked before the
temporary file replaces the state; a write that would exceed it fails the cycle
with a clear error and leaves the prior valid state unchanged. Only records with
positive terminal proof are compacted: verified-completed fixer tasks and
sent/superseded/blocked status, auto-merge and outbox records of a PR observed
closed or merged, plus those records on non-current heads of an open PR. They
are replaced by a per-PR tombstone (a status-generation watermark and bounded
lists of retired auto-merge and outbox key digests). Pending, sending, uncertain
and sent fixer claims, every record on the current head, enrollments with their
command fence and attempt budget, and consumed command IDs are never dropped;
the oldest command IDs fold into a numeric watermark that still fences replays.

## External policy boundary

This change does not edit repository settings, required-check protection, Actions
permissions, deployment workflows, the deployment controller, or running
services. Before unattended use, an owner must separately review the numeric
Copilot reviewer identity and configure `cloud-review` as a required status while
preserving existing required checks (including `integration-tests` when required),
`agent-review`, strict up-to-date checks, and required conversation resolution.
If this policy is absent or unreadable, auto-merge remains disabled.

Native compatibility gating and routine deployment policy remain follow-ups under
issue #5. This slice does not change the current global `AGENTS.md` review,
integration, merge, or deployment instructions. Any future transition should
follow verified coordinator and branch-protection rollout, preserve exact-head
evidence and sensitive owner decisions, and keep guarded deployment separate.
