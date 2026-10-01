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

Only current unresolved review-thread comments and completed failed/timed-out
`Source checks` workflow runs are eligible repair evidence. It sends at most
eight findings, clips each finding to 1,000 characters, removes links, and never
fetches check logs. The Copilot request labels all embedded evidence untrusted,
and the text is never interpreted as shell input. A deterministic marker
deduplicates a request. At most three requests are claimed per enrollment.

A durable action claim is written before POSTing a task through
`/agents/repos/{owner}/{repo}/tasks` with the bounded prompt, `base_ref=main`
and the enrolled PR's current same-repository `head_ref`. The returned task ID
is persisted and GET by ID reconciles its state and branch/PR evidence across
head changes. An ambiguous POST (including a crash before ID persistence) is
never resent automatically; it blocks further dispatch for that PR. No second
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
unknown, or incomplete file inventories as sensitive. Backend/authentication,
migrations, deployment and script paths, CI configuration, dependency manifests,
security/deployment-policy documentation, and unknown roots require the separate
owner exact-SHA authorization.

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

The durable outbox posts deduplicated, fixed-text outcome/blocker comments on
public PRs. It carries no logs, credentials, arbitrary issue text, or private
runtime data. The owner mobile Inbox is not implemented by this adapter.

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
