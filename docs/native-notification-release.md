# Guarded native notification release checks

The ordinary native-controls worker wires `NativeNotificationCallbacks` into the
existing release transaction. The preflight snapshot runs under the deployment
lock and owned admission gate; it does not drain, copy, import, claim, ACK, or
replay notifications. After positive native drain, handoff verifies that every
previously durable outbox record remains bound to the attested native source,
private outbox, ready owner, and current owner route. Newly retained records are
accepted only when their payload identity and owner route are positively proven.

Controller and application state are separate: `Paths.state` owns `deploy.lock`
and `current` (normally `hermes-mobile-deploy`), while the exact `runs.sqlite`
parent owns auth, Inbox, and native outbox databases (normally
`hermes-mobile-live`). The journal parent must match the native probe's private
`state_dir` configuration before deployment starts; there is no default-root
fallback. Symlinked/traversing controller or journal paths are rejected before
normalization. All callback phases retain controller lock/pointer ownership and
use the bound live journal for the admission gate.

After activation, the probe checks the same outbox and source bindings and waits
for each applicable owned record to have the existing native `delivered` state
and its matching owner-scoped bridge receipt, inbox row, and acknowledged flag.
The receipt's `session_id` must exactly match the current owner-route lineage tip
computed with the paged record snapshot; a merely nonempty or different session
is not ownership evidence. Lease tokens may rotate during retry and are excluded
from preservation fingerprints, but final ACK evidence still requires the receipt
token to match the delivered outbox row.
The append-only outbox retains delivered history, so every snapshot reads all
records in bounded rowid pages inside one read-only transaction and requires the
page total to equal the table count; there is no record cap or truncation. Pages
are copied into a private scratch proof rather than resident dictionaries: an
unnamed SQLite temporary database (`sqlite3.connect('')`) that SQLite creates
with private permissions, keeps unlinked, spills to disk beyond a fixed page
cache, and deletes on close or process exit. The worker owns one scratch proof
for exactly one deploy, including rollback verification, and closes it on every
exit path; any later use fails closed. Each record is routed once per snapshot,
in pages, after the outbox read transaction ends, with a bounded per-snapshot
route cache that is cleared rather than grown. Receipts are paged inside one
Inbox read transaction, and preservation, monotonic delivery, and receipt
comparisons are SQL joins between scratch snapshots. A snapshot that fails is
discarded; scratch errors raise and leave the owned gate closed.
Delivery is monotonic: a record proven `delivered` must remain `delivered` with
the same `receipt_id` in every later handoff, probe, and rollback proof, while a
`pending` record may advance to `delivered`.
It also requires zero quarantined/unknown records, conflicts, active notification
workers, shutdown publications, or unpreserved native notifications. The empty
backlog case is accepted only when the bound outbox is positively empty and the
authenticated native status/readiness evidence agrees. Capture, handoff, probe,
and rollback proof callbacks must return the singleton `True` only after all
checks pass. `None`, `False`, numeric or truthy status values are not proof.
Callback failures raise while the release controller still owns its lock and
admission gate; handoff also rejects valid-but-busy readiness.

Each proof binds health PID and start ticks to the identity independently attested
before its reads, then re-attests after its health/status reads. Capture and
handoff use the original baseline identity; activation uses the new listener;
rollback uses the currently attested restored listener (which may have restarted
and must not be confused with the original baseline PID). PID reuse with different
start ticks is an identity change, not continuity.

The source-state vocabulary comes from the attested `NotificationOutbox` default
and `NotificationCapture.transfer`/`claim` in `backend/native_notifications.py`:

- Owned `pending` records allow `unexamined`, `accepted`, `missing`, `queue-only`,
  `dropped`, `delivered`, `busy`, `conflict`, `incomplete`, and `uncertain`.
- Owned `delivered` records allow the first six states, but reject the unresolved
  `busy`, `conflict`, `incomplete`, and `uncertain` markers. A known source state
  alone is never delivery proof: the independent receipt/ACK chain is mandatory.
- Foreign records must have state `foreign` and source state `foreign-retained`.
  Unknown/malformed states and mismatched owner/foreign combinations fail closed.

`unexamined` is the durable outbox default, not a claim of SDK acceptance.
Recognized pending records can be captured and retained without manufacturing
source transitions or delivery; a successful activation still requires their
existing delivery receipts.

Capture runs before drain, so a legitimate append may race the native status
reads. Capture retries only that evidence change, a bounded number of times, and
re-proves gate ownership and baseline PID/start ticks before each retry; identity
or other evidence failures are not retried. Native ACK marks an outbox record
`delivered` before the bridge sets the receipt's `acknowledged` flag, so capture
also retries, under the same bound and rechecks, a delivered record whose receipt
is otherwise positively bound (owner, profile, inbox row, session lineage, token,
and `receipt_id`) and only awaits that flag. Missing, malformed, wrong-owner,
wrong-session, or wrong-token receipts remain immediate failures. Exhausted
retries fail closed with the stable diagnostic `Native notification evidence
changed during observation` or `Owned notification receipt acknowledgement is
still pending`; no baseline is recorded and the gate stays closed.
Rollback before handoff accepts positively validated records appended by the
undrained old listener; after handoff the record set must match exactly.

Before either abort path reopens admission, the controller requires a separate
read-only rollback proof against the captured source, database identities, durable
records, and previously existing owner receipts. A candidate failure or receipt
timeout may reopen only when that proof succeeds; missing, changed, or unknown
evidence leaves the owned gate closed for operator recovery.

These checks only inspect existing records; they never create a notification or
invoke a model. Delivery evidence is the existing native outbox ACK linked to the
bridge's owner-scoped durable Inbox receipt, not a claim that an external push
provider displayed a message.

Fail closed when the pre-release native source does not attest
`backend/native_notifications.py`, the outbox/owner/database bindings are
unavailable, any record cannot be routed, or matching receipt/ACK evidence is
missing. Earlier native sources have no exact per-record durable-outbox contract;
aggregate queue counts cannot prove preservation of opaque work. The historical
migration-only operator and its positive-count manifest are not substitutes for
ordinary release evidence or for an empty-backlog proof. Such upgrades require a
separately approved maintenance/migration path; this worker does not bypass it.

## Native maintenance validation mode

Normal native-controls maintenance uses the verified hosted release bundle:

```text
python -m deploy.native_controls_release --schedule --hosted-run-id ID
```

The scheduler captures the fresh source SHA and forwards it with that exact positive
run ID to the separate systemd worker. Before staging or admission, the worker
requires its own freshly verified SHA to equal the scheduled SHA. It independently
calls `acquire_verified_bundle`; current-main identity, complete same-attempt CI
jobs, transport and signer provenance must all verify against that SHA and run ID.
It stages the authenticated source/public maps and exact generated bytes,
then runs the complete installed-runtime host partition once through the managed
runner. It does not rebuild frontend assets or run portable `suite=all` on the
server. Missing, stale, malformed, expired or mismatched evidence fails before
native capture, admission, publication or restart. There is no local fallback.

`--local-full-checks` explicitly selects the conservative local diagnostic path
and must be supplied to both `--schedule` and `--worker`; it rebuilds assets and
runs the full managed suite. This is not the normal hosted deployment path. The
two validation selectors are mutually exclusive and one is required before any
worker is scheduled.

Native controller status adds `git_sha` and `validation_mode`. Hosted status adds
the run ID, run attempt, artifact ID and bundle digest only after the independent
verifier succeeds; failure before verification cannot claim hosted evidence.
`git-provenance.json` binds the stage SHA for existing installed-base consumers.
These local records do not create GitHub deployment provenance. The observer,
approved native/source pins, owned admission gate, drain, notification capture,
handoff/receipt proofs, readiness, and verified rollback remain mandatory in
either mode.
