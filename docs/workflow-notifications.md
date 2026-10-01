# Workflow outcome notifications

Issue #31 adds a local, fixed-purpose consumer for the future issue #30 lifecycle
export. It does not implement that exporter, read GitHub, send push requests,
install units, or activate notifications. A missing export or any uncertain
binding blocks; no source success is inferred.

## Export contract (version 1)

The future exporter writes one private UTF-8 JSON file at
`<state_dir>/workflow-events.json`, where `state_dir` is explicitly set in the
existing private Hermes configuration. The event file is an atomic, bounded
snapshot of new durable lifecycle transitions, not a log of polling ticks. It
must be owned by the service user, regular, unaliased, mode `0600`, no larger
than 1 MiB, and no more than 24 hours old. The enclosing configured state
directory must already exist, be canonical, owned by that user, and private.
No directory, app database, or exporter file is created by plan mode.

The closed envelope is:

```json
{
  "version": 1,
  "repository_id": 1399942965,
  "repository": "lindayi/hermes-mobile",
  "owner_user_id": "the-current-ready-default-owner-id",
  "generated_at": "2026-10-01T20:59:00Z",
  "events": [{
    "event_id": "pr:32:merged:exact-merge-sha",
    "outcome": "merged",
    "reason": "merged",
    "issue_number": 31,
    "pr_number": 32,
    "head_sha": "0123456789abcdef0123456789abcdef01234567",
    "merge_sha": "89abcdef0123456789abcdef0123456789abcdef",
    "decision": null,
    "occurred_at": "2026-10-01T20:58:00Z"
  }]
}
```

The envelope and each event have exact, versioned fields; unknown fields,
duplicate JSON keys, duplicate IDs, stale/future timestamps, arbitrary text,
URLs, commands, bodies, logs, and secrets are rejected. `events` contains 1–256
records. IDs are stable printable ASCII strings of at most 128 characters;
issue and pull-request numbers are positive integers. A PR event identifies its
exact 40-character lowercase `head_sha`. A merge or deployment also identifies
the exact 40-character `merge_sha`. The `owner_user_id` must match the sole live
`ready` owner with the `default` profile in the existing auth database.
Timestamps are UTC, whole-second ISO-8601 values ending in `Z`.

`reason` maps to `outcome` without inference:

| Reason | Outcome | Additional requirement |
| --- | --- | --- |
| `issue_failed`, `execution_exhausted` | `failed` | Numeric issue identity |
| `task_failed` | `failed` | Numeric issue or PR identity |
| `sensitive_approval` | `approval_required` | PR head plus `approve_production`, `resolve_review`, or `authorize_sensitive_action` |
| `execution_uncertain` | `execution_uncertain` | Numeric issue or PR identity |
| `merged` | `merged` | Exact PR head and merge SHAs |
| `controller_verified` | `deployed` | Exact PR head and merge SHAs plus current controller proof |

The pure contract implementation is `deploy/workflow_events.py`. The issue #30
exporter must use the same schema and canonical JSON digest for each complete
event object. The adapter does not accept a second “success” input or synthesize
an absent event.

## Read-only and apply behavior

Run `python3 scripts/workflow_notifications.py` (or `--plan`) for a read-only
preflight. It validates the explicit config/state path, event export, exactly
one ready default-profile owner, existing compatible notification schema, and
any deployment evidence. It prints only a bounded status/count summary. It does
not create adapter state, write Inbox/outbox rows, call a sender, or write
bytecode.

Only an explicit `--apply` may create the private
`workflow-notifications.sqlite` and its lock file, after all evidence validates.
The database pins the fixed repository, owner, event digest, recipient, and
acknowledgement. Applying calls the existing `NotificationService.ingest`
implementation through a constructor-free, mode-`rw` adapter: it never runs the
service constructor or migrates the app notification database. It creates one
Inbox item per stable event ID with `category='operational'`, default profile,
and no session. Existing master/category preferences, hide-details previews,
device rules, retention, and sender dispatch remain authoritative. A disabled
category or master switch suppresses the outbox entry; a successful ingest is
not evidence that a push was physically received.

The event digest and recipient are checked before each ingestion. A process
crash after the Inbox commit but before adapter acknowledgement safely retries
the same delivery ID through the existing `(user_id, delivery_id)` uniqueness
constraint. Altered content or a different owner using the same event ID is a
hard conflict. Acknowledged identities are retained so Inbox retention does
not cause replay.

## Deployment evidence paths

The adapter reads only these fixed, existing private records:

* `/home/lindayi/.local/share/hermes-mobile-delivery/state.json`: version 1;
  `last` and the matching `records["<merge_sha>:<approval_run_id>"]` entry must
  both be `deployed` and agree on the exact SHA and positive source,
  approval, and deployment run IDs. The record must be the latest deployment.
* `/home/lindayi/.local/share/hermes-mobile-deploy/status.json`: fresh
  `succeeded`, same `git_sha`, and a release ID.
* `/home/lindayi/.local/share/hermes-mobile-deploy/current` and
  `releases/<release>/git-provenance.json`: the symlink must resolve to that
  release, whose provenance must contain exactly the same `git_sha`.

Each file must be canonical, private, current-user-owned, bounded, and
unaliased. Both status/ledger timestamps must be within 24 hours; the ledger
must not predate controller success, and controller success cannot postdate the
exported deployed event beyond clock-skew tolerance. Missing records, a queued
or uncertain delivery intent, stale controller state, or any mismatch blocks
the entire apply before adapter state is created. No network query or
subprocess is used to reconstruct missing proof.

A `merged` event needs no deployment proof and is described distinctly from a
verified `deployed` outcome.

## Unit templates and activation boundary

`deploy/hermes-workflow-notifications.service` is a fixed local `--apply`
entrypoint with no command, network, or user-selection inputs. Its timer is
provided as `deploy/hermes-workflow-notifications.timer`. Neither template is
installed, enabled, or started by this change. The service has a private
network namespace because ingestion only writes local SQLite records; the
existing push sender remains a separate component.

The #30 exporter is not present in this branch. Before any operator installs
or activates these templates, the parent must independently verify the schema
parity and read-only producer/owner/path binding against the exact reviewed
head. Until then, the fixed export is absent and the adapter remains unavailable.

All tests use isolated synthetic files and SQLite databases. They do not probe
live notification devices, use production credentials, or access production
state.
