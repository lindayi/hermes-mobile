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

## What is and is not enforced

Implemented in this slice:

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
- Current-head Copilot review and resolved-thread validation, fail-closed check
  collection, owned `cloud-review` status updates, and protected auto-merge
  eligibility.
- Failure to read the required-check policy or to prove complete/current evidence
  blocks auto-merge. The coordinator never reports tests as successful.

Separate owner/policy work still required:

- Verify the configured Copilot reviewer identity and require `cloud-review` in
  repository branch protection/rules and require branches to be up to date.
  Require resolved review conversations as a protected merge rule as well.
  This PR intentionally makes no settings or protection changes and preserves
  `agent-review` unchanged.
- Install and enable the service/timer only after exact-head review and policy
  wiring. They remain disabled templates in this repository change.
- Keep Inbox ingestion as a separate explicit apply operation; coordinator
  export does not activate or invoke the consumer or push sender.
- Roll out native compatibility gating and routine deployment policy under #5.
  This coordinator does not deploy, modify deployment/controller/artifact code,
  or replace the current global `AGENTS.md` requirements.

Conflicts and `behind` pull requests are not automatically resolved. They are reported for a separately
assigned neutral reconciler. A completed cloud-agent run is not a successful
review or test result, and this coordinator does not modify `source-ci`,
`integration-tests`, or `agent-review` status contexts.
