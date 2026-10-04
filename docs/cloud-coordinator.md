# Cloud coordinator

`scripts/cloud_coordinator.py` is a bounded, outbound GitHub poller for issues
and pull requests explicitly enrolled by the repository owner. GitHub Copilot
performs code work in its cloud environment; this process never checks out or
executes pull-request code on the host.

## Modes

```sh
python3 scripts/cloud_coordinator.py --help
python3 scripts/cloud_coordinator.py --state /private/path/state.json
python3 scripts/cloud_coordinator.py --once --state /private/path/state.json
python3 scripts/cloud_coordinator.py --once --apply --state /private/path/state.json
```

No arguments run one read-only plan and print a bounded JSON summary. `--once`
also performs one read-only cycle. Writes are rejected unless `--apply` and
`--once` are both present. GitHub access uses `gh api` for the fixed
`lindayi/hermes-mobile` repository and requires the authenticated owner account.
The state file and its directory must be private and owned by the running user.
Only IDs, cursor/action state, and public notification metadata are persisted;
credentials are never copied into coordinator state.

The unit templates
`deploy/hermes-mobile-coordinator.service` and
`deploy/hermes-mobile-coordinator.timer` are not installed or enabled by this
change. The timer requires an explicit owner-reviewed activation after external
policy wiring. No production service, GitHub setting, branch protection, or
deployment behavior is changed here.

The service uses the canonical checkout's `.venv/bin/python`, like the workflow
notification consumer. Provision and verify that environment from the repository
lockfile before activation; the unit never installs dependencies or falls back to
system Python. Lifecycle owner/configuration reads import application dependencies,
so a standard-library-only interpreter or a successful CLI `--help` alone is not
sufficient qualification. The focused unit regression imports that lazy dependency
path under a synthetic home without loading live settings or making API calls.

## Issue-to-PR handoff

The issue starter and coordinator are separate workers. As documented in
[`issue-starter.md`](issue-starter.md), only the authenticated repository owner’s
exact `/hermes start` comment on an eligible public issue authorizes the issue
starter to dispatch a cloud task. The issue title and body are untrusted task
context. The starter reserves the task before dispatch, polls only its persisted
task ID, and verifies the resulting task, branch, pull request, and canonical
closing-issue link. It can mark a verified draft PR ready and then submit the
owner-authenticated, head- and body-bound enrollment command. A completed task or
public closing text alone does not prove issue linkage or authorize coordinator
work.

The coordinator accepts only an owner-authored enrollment for an open,
same-repository PR based on `main`. Starter enrollment additionally requires a
non-draft PR at its exact current head and proves the originating issue, unchanged
command, PR identity, body digest, and canonical closing edge. Manual bare and
SHA-bound enrollment can admit draft PRs under the documented owner-command
contract; repair and merge are deferred until the PR is ready. Enrollment is not merge
approval; sensitive changes require separate exact-head owner authorization and
targeted independent review.

## Current coordinator capabilities

- Explicit owner-ID enrollment and exact-current-head sensitive authorization.
- Precollection watermark and overlapping reads, atomic owner-command/cursor
  persistence, and retirement on close/merge (reopening needs a new owner
  enrollment).
- Reserved task-API dispatch with `base_ref=main`, current `head_ref`, durable
  task ID reconciliation, per-PR serialization, three-attempt budget, and an
  ambiguous-write fail-closed path. Public receipt/outcome comments never
  dispatch a second task.
- Generation-bound status transitions and a deduplicated public outcome outbox.
- A 4 MiB state cap checked before replacement (prior state preserved on
  failure) and compaction of positively terminal records into bounded per-PR
  tombstones; unresolved claims and current-head evidence are retained.
- Immutable shared-schema lifecycle events for coordinator outcomes, plus
  receipt-backed issue-starter failure/uncertainty and controller-verified
  deployment outcomes. Export is written to the configured application state
  directory and bound to its sole ready default-profile owner, not the GitHub
  account ID. Source readers never dispatch tasks or modify source state.
- Current-head owner-published independent-agent review and resolved-thread
  validation, fail-closed check collection, and protected auto-merge eligibility.
  Copilot feedback is supplemental; the coordinator does not request Copilot review
  or treat its owned advisory `cloud-review` status as required review evidence.
- Failure to read the required-check policy or to prove complete/current evidence
  blocks auto-merge. The coordinator never reports tests as successful.

For an enrolled, non-draft PR with computed GitHub mergeability, ordinary
technical review/check findings can receive a bounded fixer task. A confirmed
`dirty` conflict or `behind` PR instead receives a neutral reconciliation task
through the same durable reservation, serialized task lock, persisted task-ID
polling, and combined three-attempt budget. The task must preserve both branch
intents, merge current `main` into the PR branch (never rebase or force-push),
record conflict-hunk decisions, and test the combined behavior. Unknown or
uncomputed mergeability defers dispatch without consuming an attempt. Exhausted
budget, incompatible product requirements, or broken required policy blocks for
owner attention; this is bounded task dispatch, not automatic conflict resolution
or merge.

Task completion is accepted only against the durably saved task ID and
authenticated task/session evidence. The receipt must bind that task to its
unique session, dispatch nonce, repository, PR, branch, start head, dispatch-time
main, and result head, and remain an unchanged authenticated PR comment. A valid
`ready` receipt can authorize continuation to that result head; it is not evidence
that tests passed, review was approved, required checks passed, a PR merged, or a
release was deployed. The parent continues to require fresh evidence on the
result head.

Accepted task IDs are reconciled by GET, including when the PR head advances.
Queued, running, waiting, uncertain, and completed tasks awaiting independent
review retain the serialization lock; review waits consume neither handoff nor
fixer budgets. An ambiguous task-creation response is never blindly resent;
preserve the durable claim and state for verified recovery rather than resetting
state or assuming a new enrollment stopped the earlier task. A receipt, task
comment, or completed session never triggers a second dispatch by itself.

## Review, merge, delivery, and Inbox are separate

The coordinator requires the latest authenticated owner-published structured
independent-agent formal `COMMENT` review on the exact current head and complete
review/thread evidence with every thread resolved. Copilot feedback is
supplemental; missing approval alone never delays task handoff or consumes a fixer
attempt. Actual unresolved findings and definite rejection still block acceptance.
After a source task reaches a verified `ready` receipt without current
independent evidence, the coordinator posts one owner-authored review anchor
comment and dispatches one reserved read-only reviewer task on that exact
head/base. The reviewer replies to the saved anchor with
`engine-tools-reply_to_comment`; the accepted transport is the observed quoted
blockquote preamble plus one compact JSON
`hermes-independent-review-report-v1` object outside the quote. The parent
binds that report to the saved nonce, distinct reviewer session, repository,
PR, head/base and source receipt evidence, then publishes the owner COMMENT
review on the exact head. Positive reports publish `verdict:"pass"`; bounded
findings publish `verdict:"changes_requested"` and feed one bounded fixer
follow-up for that exact head. The completed source task lock is retained until
that verified reviewer lifecycle completes, and every configured required check
must still independently succeed.

The reviewer prompt names every required report field and the exact finding
shape: each finding has only `path` and a nonblank `comment` of at most 1,000
characters. It includes the complete changed-path inventory. Every non-deleted
file hash is independently computed from the exact Git-blob bytes; deleted paths
use JSON `null`. The coordinator fetches and hashes the full inventory again and
does not accept a partial or report-supplied substitute. If a positively terminal
task has no usable report, the private action record keeps a bounded validation
error and the outbox posts one deduplicated blocker. At most one separately
reserved correction can follow, with a new anchor, task, session, and nonce; the
original report is left unchanged and source-fixer attempts/authority are
preserved. Active, unknown, or insufficiently authenticated task/session evidence
does not authorize a correction. An ambiguous creation is never replayed, and
failure of the single correction remains a blocker. A corrected negative verdict
uses the existing bounded fixer flow; a pass status is still gated by the full
strict review proof. Recovery session IDs are stored and considered for retry only
when they are nonempty strings no longer than 128 characters; unsafe IDs remain
out of state while the bounded diagnosis and deduplicated blocker are retained.
After a corrected report is durably published, reconciliation repairs a still-
reserved parent to `recovered` only from the positively bound completed correction;
for a pass, the `agent-review` status must also already be durably complete. This
restart repair does not repeat publication or dispatch.

Recovery after main advances preserves the original report's saved base as audit
evidence. It requires current exact-head owner authority, the original source
receipt, and independently proven ancestry from that saved base through the PR
base, current main, and reviewed head. The correction records the historical parent
base separately from its current-main report binding; if either changes after
reservation, the correction report cannot be published or grant `agent-review`.
An inventory that cannot be represented within the strict 64-file contract is
blocked before reviewer dispatch, recorded on the source handoff, and reported
once through the deduplicated outcome path. The blocked handoff does not keep a
generic agent lock or create a fixer task.

Sensitive changes additionally need owner authorization bound to that exact SHA
and the documented targeted independent review; neither approval nor authorization
carries to a later head.

The active required contexts are exactly `source-ci` (Actions app 15368),
`integration-tests`, `agent-review`, and `issue-link` (Actions app 15368), with
strict/up-to-date checks and resolved review conversations. The coordinator may
publish an owned advisory `cloud-review` status, but it is not required and never
replaces the structured independent review. No repository settings or protections
are changed by this source behavior. See [`autonomy-policy.md`](autonomy-policy.md)
for the owner-controlled deployment boundary.

When eligible, the coordinator requests GitHub protected auto-merge; branch
protection and fresh current-head review, thread, check, and policy fences remain
authoritative. This is not deployment. After merge, ordinary delivery remains a
separate guarded operation from verified exact `main`, reusing the matching
hosted artifact and running the installed-runtime host partition. Existing
admission, drain, health, and rollback protections remain in force; already
admitted sessions are allowed to finish rather than being interrupted. See
[`verified-release-artifacts.md`](verified-release-artifacts.md) and
[`self-deploy.md`](self-deploy.md).

Coordinator export is also separate from owner mobile Inbox ingestion and push
delivery. Export does not invoke or activate the Inbox consumer or sender; those
remain separate explicit owner-controlled operations.

## Activation boundary and historical scope

The repository unit and timer files are templates, not proof of installation or
activation. Recurring operation requires a separately reviewed owner activation
after the canonical checkout’s locked application environment is provisioned and
verified, a separately authorized read-only API qualification is complete, owner
authentication and private state are qualified, and external reviewer,
required-check, strict-up-to-date, and conversation-resolution policy is
verified. Unsupported legacy active state must be preserved and resolved through
reviewed operator recovery; it must not be reset or migrated by re-enrollment.
This documentation makes no claim that a particular operator has completed
these steps or activated a service.

Earlier source-slice descriptions that deferred conflicted or behind PRs to a
separately assigned reconciler are historical, not the assembled behavior
described above. The historical boundary does not imply that current review,
check, merge, deployment, or Inbox gates are automatically satisfied.

Separate owner/policy work still required:

- Keep the active required contexts and protections owner-controlled. The
  coordinator does not configure Copilot reviewer requirements, publish
  `cloud-review` as a required status, or change repository settings. Its owned
  `cloud-review` status remains advisory and never substitutes for independent
  review. Exact current required contexts and independent-review evidence are documented in
  [`AGENTS.md`](../AGENTS.md) and
  [`cloud-coordinator-spec.md`](cloud-coordinator-spec.md).
- Install and enable the service/timer only after exact-head review and policy
  wiring. They remain disabled templates in this repository change.
- Keep Inbox ingestion as a separate explicit apply operation; coordinator
  export does not activate or invoke the consumer or push sender.
- Roll out native compatibility gating and routine deployment policy under #5.
  This coordinator does not deploy, modify deployment/controller/artifact code,
  or replace the current global `AGENTS.md` requirements.
