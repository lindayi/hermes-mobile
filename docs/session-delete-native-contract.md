# Owner native stored-history deletion v1 — acceptance contract

This supplements `session-delete-contract.md`. Authorized scope is standard stored native history deletion, not all-copy erasure, filesystem cleanup, gateway purge or permanent native-ID tombstoning. Close all other clients first: they may recreate native IDs later. Mobile admission/tombstones are a separate bridge responsibility. No installed Hermes files, native schema, member listener, gateway or runtime are modified; no live deletion/restart is authorized here.

## Wire contract

- Owner-only composition, fixed owner home and default profile. Reject profile-prefixed mutation/receipt paths. Authenticate with the existing native token before any receipt/data access.
- Capability: `features.mobile_session_delete_version: 1`.
- `DELETE /api/mobile/sessions/{session_id}`, body exactly `{confirm:true,operation_id:<32 lowercase uuid hex>}`. Exact bounded safe ID; no title/prefix/canonical substitutions or client cascade lists.
- Committed success: `{id,deleted:true,operation_id,status:"deleted",scope:"native_session_db",files_deleted:false}`. Completed internal delegate session/message rows may be included; return their exact IDs. This does NOT claim durable async task/result records or transcript/debug/export/backup removal.
- `GET /api/mobile/session-deletions/{operation_id}` returns the same committed receipt after response loss. Receipt persists in a private owner-home `mobile-delete-records/receipts.sqlite`, separate from native schema. Receipt identity includes exact root and operation; no message/title/task content.
- Definitive pre-mutation safety refusal persists `{id,operation_id,status:"refused",deleted:false,reason:<content-free code>}` and returns that receipt (normally HTTP 409). GET returns the exact same receipt with HTTP 200. The bridge may release only the exact matching claim on this durable evidence. A fresh explicit request may use a NEW operation ID; the refused operation itself is never replayed.
- Invalid body/ID: 400; conflicting operation binding, busy/unsafe sessions: 409; missing initial session: 404; unsupported/unknown evidence: 503. No false deletion response counts as success.
- Persist `unknown` before native deletion. Only the owning live attempt may transition it to a durable pre-mutation refusal or committed deletion; a restarted/repeated unknown operation can never be upgraded. Commit receipt only after native primitive returns true. A crash in the native-commit/receipt gap stays `unknown`, including after restart: NEVER infer success from absence, replay mutation, or upgrade unknown automatically. Repeating the same operation returns its existing receipt without touching native state; mismatched root is a conflict.

## Safety / implementation acceptance

1. Resolve exact native row and enumerate native delegate targets; bound size, validate every ID, and acquire all turn leases and compression locks with unique holder, nonwaiting admission, holder-qualified release only. Check owned, unexpired leases inside deletion transaction.
2. Require positive process-local quiescence: all maintenance sources known, no pending admissions, active tasks/agents/reservations/workers/subprocesses/delegations, no unpreserved or pending completion queue. Terminal status is not proof of worker exit. A local route admission fence excludes concurrent listener mutations while deletion runs; queued work fails rather than waits.
3. Use installed `SessionDB.delete_session(expected_delete_ids=...)`, with a per-invocation delegating facade over its `_execute_write` seam and the installed connection-level `_collect_delegate_child_ids` helper (the public enumerator holds a non-reentrant lock and must not be nested). Guard reads execute inside the SAME native `BEGIN IMMEDIATE` callback, immediately before native deletion. No bridge SQL writes to native session tables, no global monkeypatch, no two-call preflight pretending to be atomic.
4. In that transaction reject any root parent/compression/delegate marker, unexpected target membership, independent children, malformed provenance, cross-profile/source mismatch, gateway session key/routing reference, and associated async delegation records (even terminal). Allow only completed internal delegate rows whose exact marker ancestry terminates at the confirmed root and has no independent children.
5. Fail closed on unsupported native/schema/local evidence, failed locks, lease loss/expiry, unknown receipt or native exceptions. Serialization prevents newly inserted branch/routing/delegate metadata from slipping between checks and deletion; future external recreation is explicitly out of scope.
6. Tests run actual installed SessionDB only in a subprocess with a fresh temporary HOME/HERMES_HOME. RED/GREEN targeted tests cover deletion, durable receipts/idempotency, refusals/races, active locks/workers, identity/profile validation and composition/auth. Never open production state or run the full suite/deploy/pin changes from this worker.

## Limits

Private receipts are not native global tombstones. SQLite logical deletion is not forensic erasure. Refusing associated async records means some completed delegate histories remain unavailable; no direct async-record cleanup is invented. Target set is current internal worker scope at confirmation, never independent branch history. Local service admission exclusion does not drain other processes; shared leases plus atomic metadata checks provide the supported stored-history safety boundary.


## Release coordination / lifecycle

The owner service composition now includes `native_session_deletion.py`. The minimal `native_maintenance.py` extension adds `work.session_deletion_workers` (zero on adapters without this feature) to its existing version-1 evidence. Counts include off-loop deletion and receipt jobs; reservation is made before scheduling, and the actual worker releases it in `finally`, never the cancelled HTTP task. A cancelled request retains its admission fence and tracked task until the worker exits. Graceful disconnect waits for tracked jobs.

**Parent release prerequisite:** `deploy/native_readiness.py` currently requires an exact `WORK` key set. Its consumer must explicitly support and validate the new worker field before rollout (including compatibility with pre-feature evidence for capture). Do not silently drop the field or conclude zero from an unsupported schema. This worker does not edit deployment/readiness/pins.

All unsafe checks are conservative: any unknown maintenance source, any process-local active work, or any queued completion notification refuses deletion. Async delegation records referring to the root or any target refuse even if terminal. This may reject some otherwise completed histories; no background-delivery cleanup or worker liveness inference is invented.
