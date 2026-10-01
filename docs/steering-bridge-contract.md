# Durable mobile STEER acceptance contract

## Wire contract (frontend/native coordination)

- `GET /hermes/app-api/runs/{LOCAL_ID}/controls` → `{steering: boolean, attempts: [...], pending_steer?: string, steer_receipts?: [...]}`. The boolean means eligible now, not a delivery promise. Requires ready, runtime-bound owner; no arbitrary profile/native ID accepted.
- `POST /hermes/app-api/runs/{LOCAL_ID}/steer` strict JSON `{input: string (1..32000, nonblank), idempotency_key: string (1..128, nonblank)}` → attempt `{id, run_id: LOCAL_ID, idempotency_key, input, status, steer_id: string|null, created_at, updated_at}`. Status is `sending`, `accepted_unconfirmed`, `unknown`, `rejected`, or native receipt `not_delivered`. No state claims delivered. Preserve the draft for unknown/rejection; retain the durable submitted copy on acceptance. No automatic resend.
- `steering` journal/SSE events notify the UI to refresh controls; GET restores attempts after reconnect even after terminal SSE ends.
- Native capability: top-level `mobile_run_controls: {version: 1, steering: true, live_commentary: true}` on `/v1/capabilities`. Legacy run_steer advertising alone is insufficient.
- Native POST `/v1/runs/{UPSTREAM_ID}/steer` body `{input,idempotency_key}`. Acknowledgement must contain `{run_id: UPSTREAM_ID, steer_id: submitted_idempotency_key, accepted: true, status: 'accepted_unconfirmed'}`. `object` must equal `hermes.run.steer`; an echoed `idempotency_key` is optional (if echoed, must match). Correlated `accepted:false,status:not_delivered` on HTTP 200/409 is definitive rejection. Acceptance never guarantees consumption.
- Native read-only reconciliation uses existing `GET /v1/runs/{UPSTREAM_ID}` with exact `run_id`, optional `pending_steer` and `steer_receipts` list of correlated `{run_id, steer_id: submitted_key, status, accepted}` records (no separate idempotency_key required). Native 404, timeout or missing records never proves rejection or delivery.

## Acceptance criteria before implementation

1. Real FastAPI routes + temporary SQLite + httpx MockTransport exercise authorization, exact Origin/CSRF, ready-user/profile/runtime binding, strict body limits and ownership before native I/O.
2. Durable table shares the run journal DB; unique `(user_id,idempotency_key)` binds exact input/profile/local/upstream run. Identical retries return stored state (including after terminal/restart), changed binding conflicts. Atomic claim precedes POST, concurrent duplicates dispatch once. Persist state/event together.
3. Only positively running local run with known upstream and execution-ready newly capable adapter can claim. queued/approval/stopping/unknown/terminal reject before native I/O. Stop and steer serialize per run locally; native remains final lifecycle gate.
4. Restart changes `sending` to `unknown`, never redispatches. Timeout/cancellation/500/malformed or mismatched ACK → durable unknown, draft retained, independent of run outcome. Native definitive 409 may be rejected; 404 remains unknown.
5. Preserve provided `pending_steer` and correlated `steer_receipts` on every completed/failed/cancelled SSE and poll branch before publishing terminal run outcome. Missing pending text is not delivery evidence. GET refresh is read-only native I/O; it may reconcile receipts but never submit.
6. Normal start payload/model locks remain unchanged. No live state mutations, model calls, service/config changes during implementation/tests.
