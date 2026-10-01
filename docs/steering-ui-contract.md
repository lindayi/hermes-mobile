# Steering UI contract

Only a successful owned-local-run `GET /runs/{id}/controls` with `steering: true` enables steering. Unsupported/error responses retain the existing Stop-replaces-Send UI. Only actual `running` enables **Steer current run**; queued, approval, stopping and unknown do not dispatch guidance. Supported active runs have a separate visible 44px Stop control. The helper says “At next safe checkpoint; current tools continue”. Models remain locked throughout active/unresolved runs.

An explicit click posts `{input,idempotency_key}` to `/runs/{local-id}/steer`, never `/runs`. Persist the attempt before the request, scoped to owner/session/run. An uncertain retry of identical input reuses its key, without automatic POST retries. Accepted results mean **accepted, delivery unconfirmed**, never delivered. Only `accepted_unconfirmed` may clear an unchanged current draft. Submitted copies remain visible. Unknown/rejected/not-delivered retain drafts; changed/newer drafts and navigated views are never overwritten. Completion never automatically turns a pending steering request into a new turn.

Controls are refreshed read-only on entry, focus and steering SSE events. GET attempts use `idempotency_key` for correlation (also allow record id/steer_id for display identity); event payloads are not delivery receipts. Stale GET responses and pending POST completion may not change another route/account/run or restore obsolete running state. Terminal completion requires another explicit user action to send a new turn.

Tests use the real mounted composer in JSDOM and an isolated HTTP/SSE Chromium fixture. No native gateway/model calls.
