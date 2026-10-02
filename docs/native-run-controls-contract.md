# Dedicated owner native run controls v1

Process-local owner listener ONLY, launched by new `backend/native_controls_service.py`; the existing `backend/native_api_service.py` remains byte-identical to the deployed baseline, so legacy rollback/member listeners remain unchanged and do not advertise these capabilities. The new entrypoint rejects member configs before native imports. Root-owned installed Hermes files are not modified. Activation/restart is parent-coordinated.

Guarded native notification upgrade preservation and receipt checks are specified
in [native-notification-release.md](native-notification-release.md).

`GET /v1/capabilities` adds top-level `mobile_run_controls: {version:1, steering:true, live_commentary:true}` and `mobile_run_controls_v1: true` (no identity inference or fallback to owner).

`POST /v1/runs/{native_run_id}/steer` accepts exactly `{input: string, idempotency_key: string}`. Input nonblank, at most 32768 characters; key nonblank, at most 128 characters. The bridge resolves native run IDs from owned persisted runs; never trust a browser-supplied native ID.

HTTP 200 receipt: `{object:"hermes.run.steer",run_id,steer_id:<exact key>,status:"accepted_unconfirmed"|"not_delivered"|"unknown",accepted:boolean}`. Same key + exact input returns current receipt without re-enqueue, even after terminal; changed input gives 409 idempotency_conflict. Definitive inactive-run rejection is HTTP 409 with correlated not_delivered receipt. Missing run 404, invalid schema 400. Exception after invoking steer yields HTTP 200 unknown (never safe to automatically resend). Admission checks status again AFTER awaiting body and synchronizes with status updates and worker closure.

Read-only reconciliation uses EXISTING `GET /v1/runs/{id}`: additive `steer_receipts` array with the above receipt shape, plus `pending_steer` when native finalizer/closure/interrupt retained text. No dedicated receipt URL needed. Owner GET run status also adds bounded pending_approvals identities from the actual installed tools.approval.list_gateway_approvals(run-specific approval session), projected to run_id/request_id only. Missing identity, unsupported/error or malformed snapshot omits the field; it is never guessed to be an empty queue. This supports read-only bridge-restart approval notification reconciliation without exposing actions or auto-approving. Receipts are process-local, scoped to native status lifetime; restart/missing receipt means unknown, NOT not_delivered. Bridge must durably retain input and attempt state; no automatic retry. Terminal absence of pending text never proves delivery. Native status and `run.steer_receipts` SSE event carry terminal receipts/pending evidence even for failed/cancelled runs; terminal run outcome and steering outcome are separate.

Live public interim callback emits `message.commentary` with `{event,run_id,timestamp,text,phase:"commentary",channel:"commentary"}`. Uses existing AIAgent safe interim extraction/dedup, skips already-streamed text, does not bind reasoning/analysis, and preserves ordinary text/tool callbacks. Threaded callback delivery uses loop.call_soon_threadsafe plus queue ownership check at execution time.

No model/provider selection changes, no manually injected conversation messages, no native history/cache/role rewrites, no claim of exactly-once delivery.

## Approval timeout recovery (issue #22)

Baseline: `4767372f205b0809d980f47945ecd3ac318107f6`. Native timeout removes
its approval entry but does not restore `waiting_for_approval`; subsequent tool
callbacks preserve that status. Empty pending evidence alone does not prove that
execution can resume.

Both authenticated GET and new steering admission now reconcile under the adapter
lifecycle lock. Recovery requires the same status/control objects, exact native
run/approval-session identity, the callback-bound queue and created agent, an
unchanged active task with `done() is False`, and no closed/stopping/terminal fence.
The public pending snapshot is validated against the native registry while its
lock is held. Keep that lock through status publication, GET serialization or
steering admission: a concurrent new approval cannot slip through an empty read.
Native registry insertion/removal releases its lock before notification takes the
adapter lock; ordering here is controls then registry. Never recursively invoke
`list_gateway_approvals` under the installed non-reentrant registry lock.

Unsupported, unavailable, malformed or changed evidence leaves the wait unresolved.
An approval inserted before its waiting notification also blocks new steering.
Every new admission requires an authoritative empty registry snapshot and the
callback-bound approval identity matching the current run/session; a missing
binding is never a fallback authorization, even if status is already `running`.
Existing idempotent receipts remain readable and no timeout approves an action,
resubmits a prompt, or changes turn identity. Existing bridge reconciliation then
retires only the bound run's obsolete pending approvals and restores guidance;
uncertain approval decisions and stop intent retain their existing fences.

Verification uses synthetic state, real temporary bridge journals/routes, and a
fresh isolated native subprocess exercising the installed approval timeout,
registry, tool callback and GET. No real model or service is invoked. Source pins
match the revised adapter; the complete immediate baseline and older rollback set
remain separately attestable, never accepted as arbitrary per-file mixtures.

Activation is **not** bridge-only: after exact-head review and final integration,
the guarded main-only native rollout must drain active work before replacing the
listener. An already-running old listener does not hot-load this fix. No rollout,
restart, live database repair, dummy approval or active-turn probe was performed.

## Implementation and evidence

- Maximum 256 distinct attempt keys per run; new attempts beyond capacity return 429 `steer_capacity` without invocation. Existing keys remain readable/idempotent. Control records are swept with native status expiry.
- All accepted receipts conservatively become `unknown` on stopping/terminal outcome. `pending_steer` is separate negative-delivery evidence; no unsafe substring matching assigns it to individual keys. `accepted` remains the historical acceptance boolean (unknown after an exception has accepted=false, meaning acceptance was not confirmed).
- Worker finally closes admission under the same adapter lock used for POST/status updates, retaining finalizer output AND any late pending slot. Stop/terminal boundaries retain the slot before native clear; the normal non-clearing interrupt path remains unchanged. No pending text is automatically executed later.
- Every native terminal queue envelope (`run.completed`, `run.failed`, `run.cancelled`) is enriched with receipts/pending evidence BEFORE enqueue, including native branches that enqueue before publishing status. Existing GET status has the same fields. Separate `run.steer_receipts` events are supplementary, not required after terminal.
- Strict RED/GREEN iterations exercised capability isolation, body-await state changes, exact-input dedup, final-drain closure gap, clear-interrupt preservation, all terminal transports, duplicate pending aggregation, TTL cleanup, capacity, and live commentary wiring.
- Isolated test executes the real installed `agent.codex_runtime._consume_codex_event_stream` with fixture events into the adapter-created agent and asyncio queue; exact installed AIAgent methods are AST-compiled to avoid native CLI startup side effects/dependencies. The surrounding adapter/model turn is a stub; no live model request or user steer was performed. Test redaction helper is an identity fixture; production uses the unmodified native safe extractor/redactor. This is not a live-model end-to-end certification.
- Focused command: `.venv/bin/python -m pytest tests/test_native_run_controls.py tests/test_native_api_service.py -q -p no:cacheprovider --tb=short`. Parent must review/refresh launcher AND new-module attestation, then coordinate safe-idle activation and authorized harmless probe. `model_controls.py` was not edited.
