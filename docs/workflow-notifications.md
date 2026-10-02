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
duplicate JSON keys, duplicate IDs, stale/future export timestamps, arbitrary
text, URLs, commands, bodies, logs, and secrets are rejected. `events` contains
1–256 records. IDs are stable printable ASCII strings of at most 128 characters;
issue and pull-request numbers are positive integers. A PR event identifies its
exact 40-character lowercase `head_sha`. A merge or deployment also identifies
the exact 40-character `merge_sha`. The `owner_user_id` must match the sole live
`ready` owner with the `default` profile in the existing auth database.
Timestamps are UTC, whole-second ISO-8601 values ending in `Z`. `generated_at`
must be fresh. `occurred_at` is the immutable incident time and may be older than
24 hours when a fresh snapshot retains an unacknowledged event; it cannot postdate
the snapshot or be more than the permitted clock skew in the future. The exporter
must preserve every unacknowledged event within the 256-record bound rather than
silently truncate it.

`reason` maps to `outcome` without inference:

| Reason | Outcome | Additional requirement |
| --- | --- | --- |
| `issue_failed`, `execution_exhausted` | `failed` | Numeric issue identity |
| `task_failed` | `failed` | Numeric issue or PR identity |
| `sensitive_approval` | `approval_required` | PR head plus `approve_production`, `resolve_review`, or `authorize_sensitive_action` |
| `execution_uncertain` | `execution_uncertain` | Numeric issue or PR identity |
| `merged` | `merged` | Exact PR head and merge SHAs |
| `controller_verified` | `deployed` | Exact PR head and merge SHAs plus current controller proof |
| `closed_without_merge` | `closed` | Exact PR and head; no merge SHA or decision |
| `conflict_incompatible`, `policy_broken` | `blocked` | Exact PR and head; no merge SHA or decision |

For a `sensitive_approval` event, the exporter must recheck that the exact PR
head and requested decision are still current and pending before including it
in a fresh snapshot. The Inbox record is informational only: this adapter never
executes or authorizes the requested decision.

The pure contract implementation is `deploy/workflow_events.py`. The issue #30
exporter must use the same schema and canonical JSON digest for each complete
event object. The adapter does not accept a second “success” input or synthesize
an absent event.

## Read-only and apply behavior

Run `python3 scripts/workflow_notifications.py` (or `--plan`) for a read-only
preflight. It validates the explicit config/state path, event export, exactly
one ready default-profile owner, existing compatible notification schema, and
any deployment evidence. It prints a bounded status/count summary plus explicit
per-event deferrals when needed (at most the 256 input events). It does not create
adapter state, write Inbox/outbox rows, call a sender, or write bytecode. Schema,
owner, recipient and digest conflicts anywhere in the complete snapshot remain
whole-batch failures before writes; durable identities are rechecked for the
complete batch again after taking the apply lock. The same export path is re-read
and fully validated with post-lock wall time (both file and envelope freshness).
Its bytes must equal the preflight snapshot, including formatting; a changed
snapshot blocks for a new invocation/replan rather than processing obsolete events.
These checks precede state initialization, recovery, reservations and Inbox writes.
Deployment proof for every
unACKed event is also re-read with fresh current time under that lock before
initialization or reservations. Each unACKed deployment is checked again after
any SQLite reservation-lock wait and immediately before Inbox ingestion: slow
initialization or earlier events must not extend the lifetime of old proof.
Unavailable proof remains an event-local deferral, while recovered proof can
clear a preflight deferral. Exact ACKed replays do not require obsolete proof.
Production uses wall time at each check; Python callers can inject an aware
`clock()` for deterministic advancing-clock tests, or `now` for a fixed clock.

SQLite inputs are opened read-only only when their header identifies
rollback-journal mode and no `-wal`, `-shm`, or `-journal` sidecar exists. WAL
databases and sidecar states fail closed before SQLite is opened, avoiding
SQLite's permitted WAL shared-memory side effects in a writable directory.

Explicit apply has one narrow exception: an established, private, bound adapter
may recover its own exact `workflow-notifications.sqlite-journal` under the
exclusive adapter lock. Plan never recovers it. Both canonical files must be
regular, single-link, owner-private and bounded; WAL/SHM, master journals,
unsupported/torn/corrupt journals and unknown/unbound/foreign databases block.
The adapter validates standalone journal headers, geometry and complete page
checksums, then lets SQLite roll back an isolated private copy. The recovered
schema, repository/owner binding, integrity and complete batch identities must
validate before SQLite may recover the unchanged canonical inputs. Schema/binding
are checked again afterward, before reservations or Inbox writes. The canonical
journal is never blindly deleted. Interrupted recovery copies are ignored (not
promoted or automatically swept), just like interrupted initialization files.
Auth and notification input databases retain the strict no-sidecar policy.

Only an explicit `--apply` may create the private
`workflow-notifications.sqlite` and its lock file, after batch security checks
validate. An event without sufficient deployment proof may be reserved as pending,
but cannot be ingested or ACKed. The database pins the fixed repository, owner,
event digest, recipient, and acknowledgement. Applying calls the existing `NotificationService.ingest`
implementation through a constructor-free, mode-`rw` adapter: it never runs the
service constructor or migrates the app notification database. It creates one
Inbox item per stable event ID with `category='operational'`, default profile,
and no session. Existing master/category preferences, hide-details previews,
device rules, retention, and sender dispatch remain authoritative. A disabled
category or master switch suppresses the outbox entry; a successful ingest is
not evidence that a push was physically received.

First initialization builds a uniquely named private temporary database in the
same directory under the held apply lock. Its schema and owner/repository binding
are committed, validated and fsynced within the same main-file capacity bound
before Linux `renameat2(RENAME_NOREPLACE)` atomically installs it; the directory
is then fsynced. Installation fails closed if this no-overwrite primitive is
unavailable or a canonical name/sidecar has appeared. No incomplete canonical
file is published, and existing malformed/unknown state is never blindly repaired,
replaced or deleted. Normal failure removes only this invocation's temporary
files. Interrupted temporary files are ignored, never reused/promoted, and are
not automatically swept; repeated interruption can consume additional disk and
requires operator inspection. A crash after installation leaves a complete,
unaliased database that normal validation can accept on retry.

The event digest and recipient are checked before each ingestion. A process
crash after the Inbox commit but before adapter acknowledgement safely retries
the same delivery ID through the existing `(user_id, delivery_id)` uniqueness
constraint. Altered content or a different owner using the same event ID is a
hard conflict. Acknowledged identities are retained so Inbox retention does
not cause replay.

### Deferred results and bounded capacity

A valid snapshot can make partial progress. Optional `deferred` entries have only
`event_id`, `status: "deferred"`, and a fixed adapter diagnostic `reason`:

* `deployment_evidence_unavailable`: the event is correctly typed, but its exact
  current deployment proof is missing, stale, superseded, unsafe or inconsistent.
  No deployment Inbox item is created and no ACK is issued for this event.
  Apply retains its digest/recipient as pending when reservation capacity permits;
  an existing pending row (and any Inbox item committed before an earlier crash)
  is unchanged. Other verified events still progress.
* `state_capacity`: SQLite could not reserve a new event or commit an ACK within
  its capacity (SQLite's disk-full result is also treated conservatively). A new
  event without a durable reservation is not ingested. An ACK failure may follow
  an already committed Inbox item; its durable pending identity remains for
  same-delivery-ID reconciliation, without duplicate Inbox insertion while that
  item is retained. Other existing pending and ACKed records remain readable and
  are processed even when earlier new reservations cannot fit.

These are adapter diagnostics, **not** lifecycle `reason`/`outcome` values. No
export field, event ID, timestamp or canonical digest is rewritten, and no event
is converted to a fabricated failure, merge or success. When both proof and
reservation are unavailable, the deployment-proof diagnostic remains primary.
The producer must retain every unACKed event, including events not yet reserved
locally. The existing fresh-envelope/immutable-incident recovery contract and
shared schema 1 are unchanged.

`status: "applied"` means the validated batch was processed, not that every event
was ACKed; consumers/operators must inspect `deferred`. The CLI exits zero for a
processed partial batch, as for an ordinary apply. `events` counts input records;
`inbox_items` counts ingestions that completed adapter ACK during this run (not
physical pushes, and not necessarily newly inserted Inbox rows). Read-only plan
reports proof deferrals but does not attempt reservations or predict their exact
SQLite space requirements.

The adapter database has a hard **4 MiB main-file limit** (`MAX_STATE_BYTES`).
Every adapter write connection, including initialization, reservation and ACK,
sets SQLite `max_page_count` to `floor(MAX_STATE_BYTES / page_size)` before
writing. A transaction requiring more pages fails before committing an oversized
database; failed reservation/ACK writes are rolled back. Previous replay records
and readable state survive. Rollback journal/disk space is additional to this
main-file bound; this is not a total filesystem quota. The application Inbox
remains governed by its existing service, not the adapter's 4 MiB limit.

This is a finite capacity boundary, **not infinite history retention**. ACKs are
never deleted to make room. An existing pending ACK can itself require more pages
and remain deferred even though it can be read and retried. A previously oversized
database is still rejected and needs operator recovery, not automatic destructive
truncation.

Issue #41 adds a separate producer-side retirement contract; it does not change
this consumer, its SQLite schema, Inbox records, or replay ledger. Under the
coordinator execution lock, the producer reads the existing adapter database
read-only in one SQLite snapshot. It requires the sole ready default-profile
application owner, schema version 1, repository ID `1399942965`, and exact
`event_id`, canonical event digest, owner recipient, `status='acked'`, and
nonempty retained `inbox_id`. At original ACK creation, the consumer verifies the
Inbox record's owner, delivery ID, and Inbox ID before committing the ACK. The
producer validates this retained consumer assertion and detectable consistency
constraints; it does not require the Inbox row to remain present. An `inbox_id`
shared by distinct ACKed event IDs anywhere in the snapshot (including
active/context and context/context pairs) blocks preparation before any retirement.
The database remains subject to the existing private canonical path, 4 MiB
main-file, rollback-journal, and no-sidecar rules. The producer never initializes,
recovers, or modifies it. Missing or pending rows do not authorize retirement;
malformed, foreign, conflicting, or incomplete rows block the complete coordinator
preparation before state commit or external writes.

The unchanged consumer-owned ledger remains ACK authority after physical Inbox
retention: a genuine retained ACK still authorizes retirement and deduplicates
replay, while absence of an Inbox row never ACKs a pending event. This is not a
tamper-proof receipt system. Once Inbox evidence is removed, a unique well-formed
substituted historical Inbox ID or an arbitrary internally consistent ledger
rewrite is not detectable from the retained assertion alone. The producer does
not claim comprehensive tamper detection or introduce new authority or indefinite
Inbox retention.

Only exact ACKed lifecycle events are removed from the producer's active 256-event
export set. Their canonical payloads remain in a strictly validated, owner- and
repository-bound lifecycle context in coordinator state; that context preserves
replay identity and merged-event anchors but is never ACK authority. Source
collection filters exact ACKed receipt replays before its own 256-event cap and
uses retained merged anchors to correlate a later exact controller proof.
Retirement and the prepared scan are committed atomically before export or remote
writes. A crash before that commit replays the same Inbox delivery; a crash after
commit rebuilds the export from durable state. Coordinator plan mode reads no ACK
ledger and writes neither producer nor consumer state.

Producer context remains subject to the coordinator's existing total 4 MiB state
limit. It is finite, is not age-pruned, and can itself reach capacity; exhaustion
continues to fail closed. Operators must monitor capacity rather than interpret
ACK-backed retirement as unlimited history storage or unattended activation
approval.

## Deployment evidence paths

The adapter reads only these fixed, existing private records:

* `/home/lindayi/.local/share/hermes-mobile-delivery/state.json`: version 1;
  the matching durable terminal `records["<merge_sha>:<approval_run_id>"]`
  entry must be `deployed` and bind the exact SHA and positive source, approval,
  and deployment run IDs. The terminal record is either the exact legacy six-key
  result (`status`, `reason`, `sha`, `approval_run_id`, `source_run_id`,
  `deployment_id`) or the worker's exact nine-key version-2 result adding
  integer `version: 2`, `risk: "routine" | "sensitive"`, and `base_sha`.
  Routine records require a lowercase 40-character base SHA. Sensitive records
  may use either that SHA format or null, including but not limited to bootstrap.
  Unknown or incomplete fields and untyped metadata are rejected. The outer
  ledger remains version 1. These metadata do not replace any current deployment
  proof gate. Its deployment ID must equal `latest_id`. The volatile
  `last` poll result may be that terminal record or the producer's exact
  `duplicate` / `Consumed intent: deployed` result; it cannot contradict the
  latest intent. An already-acknowledged event is deduplicated by its durable
  digest and recipient before any now-obsolete deployment proof is rechecked.
* `/home/lindayi/.local/share/hermes-mobile-deploy/status.json`: fresh
  `succeeded`, same `git_sha`, and a release ID.
* `/home/lindayi/.local/share/hermes-mobile-deploy/current` and
  `releases/<release>/git-provenance.json`: the symlink must resolve to that
  release, whose provenance must contain exactly the same `git_sha`.

Each file must be canonical, private, current-user-owned, bounded, and
unaliased. Both status/ledger timestamps must be within 24 hours; the ledger
must not predate controller success, and controller success cannot postdate a
new deployed event beyond clock-skew tolerance. Missing records, a queued
or uncertain delivery intent, stale controller state, or any mismatch defers
that unACKed deployment without weakening any deployment proof check. No network
query or subprocess is used to reconstruct missing proof. A superseded or irrecoverably
missing proof may leave this event pending indefinitely; it does not poison other
valid events in the snapshot.

The delivery worker's existing `deploy/pull_delivery.py` contract records terminal
`deployed` only after a successful controller invocation and a changed success
status for the exact approved SHA (`_poll`, `finish`). A retained terminal record
can therefore attest that worker-observed historical success. It does **not** store
a release ID or controller-success timestamp, and does not establish the adapter's
current-release/provenance/freshness claim for an old exported event. The adapter
neither pretends that historical record is current proof nor fabricates a new
historical-deployment schema/message. Previously ACKed exact digests/recipients
continue to deduplicate without obsolete proof, even after Inbox retention.

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
