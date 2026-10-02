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
page total to equal the table count; there is no record cap or truncation. Each
record is routed once per snapshot, after the outbox read transaction ends.
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
or other evidence failures are not retried. Exhausted retries fail closed with the
stable diagnostic `Native notification evidence changed during observation`.
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
