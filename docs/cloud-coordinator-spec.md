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
IDs and the watermark. A closed or merged PR retires its enrollment; reopening
requires a fresh owner enrollment command. API errors,
rate limits, malformed pagination, and incomplete GraphQL review-thread pages
fail closed; the cursor is advanced only after a complete read.

Only current unresolved review-thread comments, body-only findings of the latest
authenticated exact-head Copilot review, and completed failed/timed-out
`Source checks` workflow runs are eligible repair evidence. It sends at most
eight findings in one combined budget (threads first, then body findings, with one
slot reserved for a workflow failure), clips each finding to 1,000 characters,
removes links, and never fetches check logs.

Body-only findings are read by `deploy/review_evidence.py` from the complete
review collection. Every record, author, and present record ID is validated
before author filtering; a malformed, tied, pending, stale-head, or foreign-author
latest review yields no body evidence. Only a single latest Copilot review on the
current head with a positive record ID is read, and only when it is `COMMENTED` or
`CHANGES_REQUESTED`. From a `ccr-overview-v2` body, each item of a
`Previously missed (N)` section is forwarded (including under a `Findings: None`
headline); disclosure boundaries are balanced, and a section whose item structure
or count cannot be proven is forwarded whole as one item. Section headings allow
summary attributes; unsupported counts such as `(unknown)` take that whole-section
fallback. Positive `Open (N)` counts suppress summary fallback because those
findings are carried by their review threads, not duplicated. `Open (0)` alone
does not suppress an actionable summary. A `Looks good` disposition with
`Findings: None`, or an explicit validation-status clause such as `exact-head
verification remains pending`, yields no summary evidence. Domain words such as
`pending receipts` alone do not declare validation pending. `CHANGES_REQUESTED`
prose without structured items (a plain body, or an actionable overview with no
positive `Open` count and no `Previously missed` items) is forwarded as one summary.
Each body finding records
the genuine review ID, head SHA, and submission time; it never carries a thread ID.
Body text is untrusted evidence: it is never approval, and approval is never
inferred from prose. At most 64 KiB of a body is parsed.

The Copilot request labels all embedded evidence untrusted,
and the text is never interpreted as shell input. A deterministic marker
deduplicates a request. At most three requests are claimed per enrollment.

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
Conflicted, unmergeable, or `behind` pull requests are not sent to a fixer;
they are reported as needing a separately assigned neutral reconciler.
Immediately before claiming a repair, the coordinator re-reads its bounded
thread/review/check evidence and fences the planned head, branch, main SHA, base binding,
mergeability and active tasks. Changed or incomplete evidence suppresses that
planned request without consuming an attempt; it does not substitute another
repair or fall back to auto-merge in the same cycle.

## Review, checks, and merge

The `cloud-review` gate accepts only a latest `APPROVED` review authored by the
authenticated Copilot review identity (GitHub ID `175728472`) on the exact current
head SHA, plus fully paginated review threads that are all resolved. A `COMMENTED`
review, arbitrary comment, stale approval, author assertion, or truncated thread
list does not pass. Every review record must be an object whose user ID is a positive integer
(not a boolean or string), and any present record ID must be a unique positive
integer; these are validated across the complete collection before author
filtering, so a malformed later record cannot be skipped to reuse an earlier
approval. All authenticated reviews must have valid timezone-aware
submission times. Missing, malformed, or naive timestamps fail closed; this
includes an unsubmitted `PENDING` review, for which GitHub omits `submitted_at`.
Times are compared as instants, not strings. Every review tied at the latest
instant must approve the current head; conflicting states or heads fail closed
regardless of response order. This same gate controls planning, status publication
and revocation, and the final merge recheck. If the authenticated reviewer
identity differs, the check fails closed and requires policy review rather than
inferring approval.

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
