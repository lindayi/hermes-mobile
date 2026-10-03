# GitHub policy and operating modes

Tracks #5, #7 and #8. `AGENTS.md` is the portable development/product preference
contract for both cloud agents and local sessions. Copilot-specific instructions
must stay aligned; private owner memories are not a source to upload.

## Issue → PR → merge → release

Create/search an issue before implementing an approved requirement. Each delivery
PR targets `main` and includes an actionable closing reference (`Closes #N`) for
readability. Split partial work into bounded child issues; reference the parent epic
without closing it early. The `Issue-first policy` workflow validates the complete
authenticated `PullRequest.closingIssuesReferences` GraphQL connection, not text
guesses from the PR description. Every reference must be a distinct open issue in
this repository; PR references, foreign repositories, closed issues, malformed or
incomplete pages, and more than ten links fail closed.

The workflow runs with `pull_request_target` from the trusted base/default branch,
never the PR's workflow definition. It does not fetch or execute PR source, load
artifacts, interpolate PR text into JavaScript, or reference deployment secrets.
Default workflow permissions are empty. Its single metadata-only job receives only
`checks: write`, `statuses: write`, `issues: read`, and `pull-requests: read`. It
creates a genuine GitHub Actions check run named `issue-link` for the exact PR head,
then updates that same run and retains the matching legacy commit status. The
workflow's Actions identity is the app-bound producer; app identity is not inferred
from or attached to a commit status.

For authenticated PR events, the compatibility status and check run are invalidated
on the event-bound head before any fallible PR metadata read. Both writes are
attempted independently; a failure prevents validation from succeeding. Dispatch
must first authenticate its supplied PR/head against the current open PR on main;
untrusted refs, mismatched heads, or unavailable authorization reads permit no writes.

The `always()` publisher can finalize failure without a snapshot digest or create a
failure check when no check ID was returned. Missing pending outputs use only the
authenticated event head, or a freshly authenticated dispatch PR/head, never arbitrary
inputs. Check publication failure still attempts the blocking compatibility status.
Success still requires all pending outputs, complete canonical-reference validation
and fresh REST/GraphQL checks
that the PR identity, head, base, body, issue set, and open issue states remain
unchanged. Failed, cancelled, skipped, malformed, incomplete, or stale validation
cannot produce success. Successful finalization publishes compatibility status
success first and authoritative app-check success last. Any publication failure
attempts a blocking check and then a blocking compatibility status independently;
a failed cleanup write does not suppress the other attempt. Runs serialize per PR
and do not cancel an in-flight publisher.

The documented pull-request workflow events do not include a dedicated action for
manually adding or removing an issue link through the Development panel. The workflow therefore
supports `workflow_dispatch` from trusted `main` with a canonical positive PR number
and expected head SHA; it rereads the open PR and rejects a different head or base.
Use this bounded recheck after adding a canonical link to an already-ready PR rather
than assuming a readiness or edit event will occur. Ordinary PR events still cover
open, edit, synchronize, reopen, and ready-for-review actions. Neither manual
dispatch nor a passing check changes branch protection. After the workflow reaches
protected main, verify real passing and failing exact-head check runs, including
manual links, a body edit, and a fork PR, before treating live publication as proven.
Metadata reads and check/status writes are not an atomic merge-time transaction;
an absent event, unavailable check/status APIs, dispatch authorization that cannot
be read, or an update racing after the final read still needs attention. In particular,
if both publication APIs remain unavailable no workflow can guarantee overwriting
prior success; API errors are not proof that invalidation reached GitHub. Merge
closes the implementation issue; it does not assert deployment.

The workflow uses documented GitHub inputs only: the
[PullRequest GraphQL schema](https://docs.github.com/en/graphql/reference/objects#pullrequest)
defines `closingIssuesReferences` and its `userLinkedOnly` filter. The pinned
Octokit `github.graphql` resolves direct data (not an HTTP `data` envelope) and
throws on GraphQL errors, including partial-data errors. The checks
[create](https://docs.github.com/en/rest/checks/runs#create-a-check-run) and
[update](https://docs.github.com/en/rest/checks/runs#update-a-check-run) endpoints
publish the exact-head check run. Manual rechecks use the documented
[`workflow_dispatch`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
event and are rejected unless they run from trusted `main`.

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
