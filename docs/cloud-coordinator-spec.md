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

An issue or pull request is enrolled only by the exact comment `/hermes enroll`
from that owner. The issue must identify a pull request whose head and base both
belong to this repository and whose base branch is `main`. Existing pull requests
are not enrolled by their age, label, author, or open state. For a sensitive
head, the owner must separately comment `/hermes authorize-sensitive <40-char-head-sha>`.
Authorization is recorded only if that SHA is still the pull request's current
head; it does not carry forward to a later commit.

## Collection and bounded repair

The coordinator captures a precollection watermark, polls issue updates and their
comments with overlapping reads, and atomically persists accepted commands, their
IDs and the watermark. A terminal PR records any observed merged/closed lifecycle
event in durable state before retiring its enrollment. A terminal PR already closed
at the first observation is treated as historical baseline and is not exported;
reopening requires a fresh owner enrollment command. API errors,
rate limits, malformed pagination, and incomplete GraphQL review-thread pages
fail closed; the cursor is advanced only after a complete read.

Only current unresolved review-thread comments and completed failed/timed-out
`Source checks` workflow runs are eligible repair evidence. It sends at most
eight findings, clips each finding to 1,000 characters, removes links, and never
fetches check logs. The Copilot request labels all embedded evidence untrusted,
and the text is never interpreted as shell input. Draft PRs and ordinary pending
review/check/task activity do not dispatch repairs or create owner notices.

Behind or genuinely conflicted enrolled PRs receive a neutral task through the same
durable reservation, exact task-ID reconciliation, serialization lock, and three
attempt budget as ordinary repair tasks. Its bounded prompt identifies both exact
branch SHAs and the PR's sanitized title/description as untrusted intent; the task
must preserve both sides, merge main into the PR branch (never rebase or force-push),
and test the combined behavior. A current-main fence is rechecked before dispatch.
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
idle and unknown task states do not release the fixer lock. Completion only
releases the fixer lock; it is not review or CI success.
Unidentifiable nonterminal repository tasks conservatively block new dispatch
until GitHub exposes enough branch, session, or PR evidence to scope them.
Conflicted or unmergeable pull requests are not sent to a fixer; a neutral
reconciler must be assigned separately.

## Review, checks, and merge

The `cloud-review` gate accepts only a latest `APPROVED` review authored by the
authenticated Copilot review identity (GitHub ID `175728472`) on the exact current
head SHA, plus fully paginated review threads that are all resolved. A `COMMENTED`
review, arbitrary comment, stale approval, author assertion, or truncated thread
list does not pass. If the authenticated reviewer identity differs, the check
fails closed and requires policy review rather than inferring approval.

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
pending, failed, or incomplete checks are not green.

Auto-merge is requested through GitHub's protected `enablePullRequestAutoMerge`
operation only when the same-repository main base is current, the PR is not a
draft or conflict, all required checks including `cloud-review` are green, the
review gate passes, no fixer may be running, and sensitive authorization is
current. Both the PR head and the current `main` SHA are re-read before enabling
auto-merge; the mutation supplies `expectedHeadOid` as the server-side head
fence. The owner-managed branch rule must also require the PR branch to be
up to date (strict required checks) and require conversation resolution; the
coordinator cannot alter branch protection. Current review, thread, check, and policy evidence is fetched again
immediately before the merge request. A new head has no inherited
`cloud-review` success, so required branch protection must keep it blocked until
the new head is evaluated. Ambiguous writes are reconciled from GitHub state and
are never blindly repeated.

The durable outbox posts only fixed-text actionable owner blockers; missing review
approval, pending checks, draft state, and ordinary task activity are not notices.
Public comments are deduplicated by the issue, current head, and blocker.

Apply mode atomically writes a private `<state_dir>/workflow-events.json` snapshot
for the owner-bound mobile consumer. It uses the versioned closed envelope, fixed
reason/outcome mapping, canonical event fields, stable incident IDs, numeric issue
and PR identities, exact lowercase SHAs, and UTC timestamps. It includes no raw
GitHub text, check logs, commands, credentials, or private runtime data. The file
is owner-only, regular, unaliased, atomically replaced, and bounded to 256 recent
events no older than 24 hours, and 1 MiB; event timestamps cannot be later than the
export timestamp. Plan mode never creates the file. Stable event IDs deduplicate
polling replays and persistent policy/budget incidents; exact-head authorization
and neutral-task outcomes remain bound to that head. Terminal enrollment retirement
and event persistence share one state transaction.

The fixed lifecycle mapping is `merged` → `merged` (exact merge SHA),
`closed_without_merge` → `closed`, `conflict_incompatible` and `policy_broken` →
`blocked`, `sensitive_approval` → `approval_required`, `execution_uncertain` →
`execution_uncertain`, and `execution_exhausted`/`task_failed` → `failed`.
Closed and blocked PR events carry the exact PR/head with null merge SHA and
decision; incompatible requirements are never mislabeled as task failures.

The producer implements the agreed `closed_without_merge`, `conflict_incompatible`,
and `policy_broken` contract additions without editing the consumer/schema. It is
not ready for activation until the actual completed consumer schema is exercised
against producer output on the final integrated revision. The owner mobile Inbox
adapter remains a separate component; this file alone does not claim delivery.

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
