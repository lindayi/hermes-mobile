# Native steering, live public commentary and approval push acceptance

User explicitly approved the native changes and requests fixing steering/live commentary. An approval request should have triggered push; inspect actual state and repair the pipeline without replaying the already-approved action.

## Native scope and capability

Dedicated mobile request listener only. Root-owned installed Hermes code, WhatsApp gateway, cron scheduler, provider credentials and family homes remain untouched. A tested process-local adapter compatibility layer is allowed in the owner launcher. Update model-selection runtime attestation for the reviewed launcher/module binding, never disable verification. Capabilities must identify the new protocol: mobile_run_controls version1, steering=true, live_commentary=true. Old/unverified listeners cannot enable steering UI.

## Public commentary

Wire the actual public interim callback to typed message.commentary on the same run event queue, before later tools/final. Native Responses/Codex public phase-commentary goes through this callback, not private reasoning fallback. Preserve text/tool chronology, existing streaming, callback deduplication, native persistence, thread-safe queue handoff and terminal fencing. No analysis/reasoning.available/private sidecars exposed. Bridge writes public commentary into its durable journal; reconnect and terminal reentry recover it once. Native-created agent integration tests must prove callback wiring rather than merely injecting a fabricated downstream event. Safe native restart only after all existing work drains; no current-turn interruption.

## Steering semantics and safety

Steering changes guidance at the next native safe checkpoint, not an interrupt/undo or a new turn. Do not modify old conversation roles, prompt caches or tool output manually. Use native steering mechanics. API accepts only positively running known owned runs, with bounded plain input and idempotency key. Recheck state after body awaits; never overwrite stopping/terminal/approval state. Close admission at run termination, preserve accepted-but-undelivered guidance through finalization/cancellation/failure and avoid the previous silently-lost final-drain race.

Bridge persists and atomically claims each (owner,profile,run,upstream,key,input) before external I/O. Exact duplicate returns recorded outcome, changed payload conflicts; process restart and ambiguous transport never trigger redispatch. Native request-key dedup and correlated receipts prevent repeat guidance within listener lifetime; bridge remains the durable cross-restart guard. Accepted is not proof of model consumption: UI says accepted_unconfirmed unless a precisely defined checkpoint receipt exists. Expose not_delivered/unknown honestly; never infer delivery from absence of pending text. Steering outcome does not falsify a separately confirmed run outcome.

Required state/race cases: queued/approval/stopping/unknown/terminal rejection; running eligible; concurrent duplicates; changed key binding; timeout/500/mismatched ACK; stop while body parsing; finish while in-flight; no-tool final; final-drain closure; native restart; bridge restart before/after I/O; cancellation; external state changes; cross-account/profile/run denial; CSRF/auth-session revocation. Do not steer real user work for tests.

## Mobile UI

Feature-gated actual running run: explicit Steer current run composer action plus separate labelled red-outline Stop (minimum 44px touch target), right-aligned beside Activity and its status. Stop is outside the tool disclosure and model/composer controls, including before the first tool, while waiting for approval and while stopping. Show contextual plain guidance, “Send another message to guide this run.” only when running and supported; never promise delivery or permanently show checkpoint jargon. Next-turn Send remains idle-only. Keep Stop usable through pending/unknown steering; allow the model picker for next-turn selection during admitted runs, locking only pending/uncertain submission attempts to preserve retry identity. Active runs are never retargeted. Preserve draft on rejection/unknown, clear only exact accepted draft, never clear later edits. Show accepted guidance once as a normal user-message bubble in conversation chronology with a small honest delivery-unconfirmed status, not duplicate plaintext under the textarea. Deduplicate keyed acknowledgement/SSE/replay/history and retain bubbles across terminal reentry and later turns. No automatic retry, queue-next-turn, or implicit new-run fallback. Ignore late results after account/route/run changes; unsupported listeners retain cooperative Stop in the activity header, with the composer action disabled rather than falling back to a new run.

## Approval notifications

Every new native approval produces an owned durable Inbox record and eligible push outbox entry once; deep link opens approval review but never approves. Respect actual subscriptions, revoked sessions, expiry and resolved actions. Push failures remain visible/retryable rather than falsely reporting delivery. Diagnose the already-approved request with read-only records; do not resend it or create synthetic production approvals. Browser permission cannot be granted by the server.

## Verification and rollout

Acceptance before code and observed RED→GREEN. Isolated actual native adapter/consumer tests, FastAPI+SQLite+mock upstream integration, browser HTTP/SSE journey and real geometry, independent review and full immutable generated pipeline. Native rollout requires durable standalone worker, shared deployment/admission gate, native+bridge idle confirmation, immutable listener binding, exact rollback and post-restart capability/health/auth checks. Never manually clear gate or restore user DBs. User has authorized safe native restart, not WhatsApp/gateway restarts. Report staged/scheduled/live distinctly; verified outcome goes to Inbox/push as available. A harmless tool-free dedicated verification run may exercise the new adapter after activation; no fabricated live evidence or real-job test executions.
