# Cloud coordinator

`scripts/cloud_coordinator.py` is a small outbound GitHub poller for pull requests
the repository owner explicitly enrolls. GitHub Copilot does code work in its cloud
environment; the coordinator never checks out or runs pull-request code on the host.

```sh
python3 scripts/cloud_coordinator.py --state /private/path/state.json          # read-only plan
python3 scripts/cloud_coordinator.py --once --apply --state /private/path/state.json
```

Writes need both `--once` and `--apply`. The authenticated `gh` account must be the
owner (user ID `5164171`) and the repository must be `lindayi/hermes-mobile`. The
state file is owner-private JSON holding only enrollments, reserved action keys and
lifecycle events.

## Enrollment

An owner comment `/hermes enroll` (optionally `/hermes enroll <head-sha> ...`, as
posted by the issue starter) on an open same-repository PR targeting `main`
enrolls it. A SHA-bound command only enrolls when it matches the current head.
Edited comments and comments from anyone else are ignored. Enrollment is not merge
approval.

## Merge gates

A PR is merged only when all of these hold on its exact current head:

- the latest submitted review by the Copilot reviewer bot (`175728472`) is
  `COMMENTED` or `APPROVED` and was made on that head;
- every review thread is resolved;
- the required checks `source-ci` (Actions app 15368), `integration-tests`, and
  `issue-link` (Actions app 15368) succeeded on that head;
- GitHub reports the PR as non-draft and mergeable.

The merge call passes the head SHA (`PUT .../merge` with `sha`), so GitHub rejects
it if the head moved. Branch protection remains authoritative. There is no
independent-review, `agent-review`, or owner sensitive-change authorization gate,
and review prose is not parsed.

## Actions

Per enrolled PR and cycle the coordinator does at most one of:

- request a Copilot review once per head when none exists for that head;
- dispatch a fix task (unresolved review threads or failed required checks),
  at most 5 per enrollment;
- dispatch a reconciliation task for a `dirty` or `behind` PR (merge `main` into
  the branch, never rebase or force-push), at most 3 per enrollment;
- merge, as above.

It waits while any agent task touches the PR branch, and while mergeability or
checks are pending. Every request is keyed (`kind:pr:enrollment-comment:head[:main]`)
and the key is saved before the API call, so a key is never dispatched twice. An
ambiguous task-creation response blocks the PR for owner attention instead of
retrying. Exhausted budgets also block. Installed version-1 state migrates in place:
active enrollments keep their identity with spent budgets, and every unresolved
(uncertain or claimed) v1 dispatch is carried as a PR-level fence. A new owner
`/hermes enroll` comment is the explicit release of such a fence. Re-enrolling (a new owner comment) resets
budgets.

## Lifecycle events

Merged, closed, exhausted and uncertain outcomes are recorded as lifecycle events
and exported to the application's `workflow-events.json` for the separate Inbox
consumer (see [`workflow-notifications.md`](workflow-notifications.md)), together
with receipt-backed issue-starter failures and controller-verified deployments.
Events acknowledged by the consumer are dropped.

## Units

`deploy/hermes-mobile-coordinator.service` and `.timer` are templates; installing
or enabling them is a separate owner operation. The coordinator never deploys.
