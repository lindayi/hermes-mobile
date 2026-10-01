# Guarded native notification release checks

The ordinary native-controls worker wires `NativeNotificationCallbacks` into the
existing release transaction. The preflight snapshot runs under the deployment
lock and owned admission gate; it does not drain, copy, import, claim, ACK, or
replay notifications. After positive native drain, handoff verifies that every
previously durable outbox record remains bound to the attested native source,
private outbox, ready owner, and current owner route. Newly retained records are
accepted only when their payload identity and owner route are positively proven.

After activation, the probe checks the same outbox and source bindings and waits
for each applicable owned record to have the existing native `delivered` state
and its matching owner-scoped bridge receipt, inbox row, and acknowledged flag.
It also requires zero quarantined/unknown records, conflicts, active notification
workers, shutdown publications, or unpreserved native notifications. The empty
backlog case is accepted only when the bound outbox is positively empty and the
authenticated native status/readiness evidence agrees. Callback failures raise
while the release controller still owns its lock and admission gate.

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
