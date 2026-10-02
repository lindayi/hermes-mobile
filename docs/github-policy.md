# GitHub policy and operating modes

Tracks #5, #7 and #8. `AGENTS.md` is the portable development/product preference
contract for both cloud agents and local sessions. Copilot-specific instructions
must stay aligned; private owner memories are not a source to upload.

## Issue → PR → merge → release

Create/search an issue before implementing an approved requirement. Each delivery
PR targets `main` and contains an actionable closing reference (`Closes #N`).
Split partial work into bounded child issues; reference the parent epic without
closing it early. The `Issue-first policy` workflow validates real open same-repo
issues, not PR numbers or links hidden in inline, fenced or indented code examples,
comments or quoted lines. Use a plain prose `Closes #N` or
`Closes lindayi/hermes-mobile#N` reference; this is a conservative metadata policy,
not a general Markdown renderer.

The workflow runs with `pull_request_target` from the trusted base/default branch,
never the PR's workflow definition. It does not fetch or execute PR source, load
artifacts, interpolate PR text into JavaScript, or reference deployment secrets.
The SHA-pinned `github-script` validator has only `issues: read`. Default workflow
permissions are empty. Separate metadata-only publisher jobs receive only
`statuses: write` (and `pull-requests: read` for the final freshness check); that
narrow write permission is necessary to report an enforceable PR-head result.

The publishers set commit-status context `issue-link` on the event's exact PR head
SHA, first `pending`, then `success` only for successful validation and an unchanged
current open PR (head, body and base). Failure, cancellation or skipped validation
cannot produce success. Once pending is written, a publication/API failure leaves
it blocking rather than reusing an earlier success. Runs serialize per PR instead
of cancelling an in-flight publisher. Actions job names are deliberately different
from `issue-link`: a `pull_request_target` job check is associated with the trusted
base/default SHA, not evidence for the PR head.

Enable required `issue-link` only after the workflow is on main and actual PR runs
prove both passing and failing statuses on their exact heads, including a body edit
and a fork PR. Bind the required status to GitHub Actions, not any status producer.
This context does not distinguish different workflows using that same app; retain
independent `agent-review` and protection for workflow/policy changes. Metadata
changes and status updates are asynchronous, not an atomic merge-time transaction;
an unscheduled run or a failed initial pending write needs operator attention.
Merge closes the implementation issue; it does not assert production deployment.

Substantial work can use `gh agent-task create --repo lindayi/hermes-mobile
--from-file task.txt`. Include acceptance, ownership and non-goals, and require
reading repository instructions. Inspect the resulting PR and any workflow
approval request before approving cloud-originated CI. Never give coding agents
production credentials or automatically approve arbitrary external PR execution.
Small local changes remain supported: dedicated task worktree, focused managed
RED/GREEN, commit/push, linked PR, hosted final checks, review, merge. Neither route
bypasses protection. Missing cloud entitlement/quota uses this explicit local path.

## Review

The repository ruleset `Copilot cloud review` targets `refs/heads/main`, requests
Copilot on ready PRs and new pushes, and excludes draft reviews to avoid needless
review churn. Push coherent changes rather than one commit per keystroke. Request
`@copilot` explicitly for a PR already open when the ruleset was introduced.

Copilot is independent cloud feedback, not automatic acceptance of its suggestions.
Address real findings with follow-up commits and evidence; explain false positives.
A completed review, no comments, a resolved thread or prose saying "approved" is
not an approving review. Existing required `agent-review` remains an explicit
exact-head assessment and disposition of findings. Auth/security, migrations,
deployment, CI/review-policy and agent-instruction changes retain targeted
independent review. Review the complete paginated diff under trusted policy.

GitHub currently documents optional Copilot native approvals in public preview.
They are controlled through Settings → Copilot → Code review → Auto-approval;
the public REST/GraphQL APIs audited here do not expose these toggles. Do not
invent API settings or remove an existing gate on the assumption they are on.
If enabled later, first observe an actual undismissed `APPROVED` review for the
current head and prove merge-rule recognition. Retain high-risk independent
review and a usable local fallback. New commits invalidate prior-head evidence.

Main protection remains strict/up-to-date, owner-enforced and thread-resolution
required, with pre-cutover contexts `source-ci`, `integration-tests`, `agent-review`,
and `issue-link`. Report
`integration-tests` only after matching complete hosted and residual host results.
Do not let an untrusted PR publish these privileged verdicts. Auto-merge can wait
for these gates; use an exact-head merge precondition for operator-driven merges.
No admin bypass. A native merge queue would require organization ownership;
this rollout does not transfer the personal repository.

## Production authorization

The `production` environment permits only branch `main` and requires approval by
the repository owner. Self-review is permitted because this is a sole-owner
repository; do not lock out the only approver. The pull worker independently
requires positive approval-history evidence for the exact workflow attempt and
revision, so merely bypassing an environment gate cannot authorize deployment.

Disabling "Allow administrators to bypass configured protection rules" is
additional recommended UI hardening (Settings → Environments → production).
This property is readable but was not present in the documented environment
update API. Never claim an unsupported API request changed it. No deployment
credential is available in the ordinary PR workflow. No production Actions
runner or public command webhook is installed.

See `guarded-delivery-spec.md`, `verified-release-artifacts.md` and
`pull-delivery.md` for artifact trust, installation, status and disable procedures.
The initial production policy remains per-release owner approval; the agent must
not approve its own deployment request on the owner's behalf. Busy or unresolved
sessions defer activation. Dependency/native/data maintenance remains an explicit
operator task, not an automatic-restart exception.
