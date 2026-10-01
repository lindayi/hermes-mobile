# Accepted guidance presentation contract

Backend contract for frontend worker (runs/native_catalog only; no model-input or native history writes).

## Active/latest overlay

`tool_replay.events` includes `name: "steering"` in journal event-ID order, interleaved with `tool`, `commentary`, and `delta`. Only previously accepted attempts appear; the first acceptance's event ID remains the ordering position even after a later status update. Repeated acknowledgements collapse by attempt ID. `data` is the public attempt shape:

```json
{"id":"attempt-id","run_id":"app-run-id","idempotency_key":"client-key","input":"User guidance","status":"accepted_unconfirmed","steer_id":"native-steer-id","created_at":123.0,"updated_at":124.0}
```

A later `not_delivered` or `unknown` updates that same attempt, not a new message. These statuses are included only with durable prior `accepted_unconfirmed` evidence. Never label acceptance as delivery/consumption. Nonaccepted attempts remain controls/draft state, not conversation messages. SSE retains its existing attempt shape; process it with the same keyed upsert logic, not by appending on every acknowledgement.

## History items

Accepted guidance projects as a normal user message in `items`:

```json
{"id":"journal:app-run-id:steering:attempt-id","role":"user","kind":"guidance","content":"User guidance","tool_calls":null,"timestamp":123.0,"run_id":"app-run-id","source":"journal","steering_id":"attempt-id","idempotency_key":"client-key","steer_id":"native-steer-id","steering_status":"accepted_unconfirmed","turn_boundary":false}
```

Live UI uses the same stable message ID, deduplicating HTTP acknowledgement/SSE/snapshot/history by `(run_id, attempt id)` (and idempotency key while acknowledgement ID is unavailable). `kind: guidance` / `turn_boundary: false` MUST NOT start a new user turn. Render the normal user bubble; any delivery qualification belongs with that message rather than as a separate composer status.

Guidance is a presentation expansion, not a stored raw row: `total`, `offset`, and `count` continue to describe the existing raw/composed paging sequence. `items.length` may exceed `count`; do not derive next-page arithmetic from displayed bubble counts. In overlay mode guidance comes from `tool_replay`, not duplicate `items`. Completed/history guidance is interleaved within its anchored admitted turn, never appended after later turns. Prior owned runs retain guidance after a new run starts. Exact native OOB user envelopes are recognized as non-turn records for boundary detection. Within a proved app-owned guided turn, the journal projection replaces owned native copies without a second duplicate acknowledgement bubble. External native-only OOB rows without app acceptance evidence are outside this projection contract: their native text/shape remains unchanged, and no app acceptance or consumption receipt is inferred. Plain user text merely mentioning an envelope is not treated as one.

Only owner/profile/session-scoped journal events are considered. Public attempt fields are allowlisted; private transport data, analysis/reasoning, arguments, and upstream control evidence are not replayed.

## Backend implementation freeze / independent review handoff

Source frozen for parent review (no further worker source edits). SHA-256:

```text
6475a1ef5359ae5309cb578b35b3b19ee47163acd98c0e91c960f34cf2913b76  backend/runs.py
72c4f2baa88cc7b9182aff3b615ee881f374286d3293d3dc747bdb6d3ead1108  backend/native_catalog.py
8f40bec90eb9671a083b5c38f32f6e60f0a6b48063ac4118923df46f4a960de3  tests/test_guidance_history.py
```

Verification performed with real isolated SQLite fixtures and authenticated test-client routes; no live writes or model calls:

- Strict RED → GREEN slices: acceptance replay; later negative receipts; history after new admission; completed native turn before later CLI turn; OOB non-turn ownership; raw-page expansion count; malformed/conflicting public identity rejection.
- Final bounded suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_guidance_history.py -q` → **15 passed**.
- Earlier integration run (before final count/metadata hardening): guidance + `test_chat_snapshot.py`, `test_latest_tool_replay.py`, `test_turn_pagination.py`, `test_reopen_native_rewrite.py` → **102 passed**. Later OOB/guidance + turn-pagination/rewrite subset → **45 passed**.
- Parent owns the final immutable full release gate and independent review. This directory has no Git metadata; provenance is the source hashes above.

Implementation notes for review:

- A guided turn retains journal authority even if native final text exists, since native OOB text alone is not an individually correlated delivery receipt. Latest turns use existing overlay replay. Completed earlier turns materialize the journal's ordered public activity at the proved native admission boundary, preserving later unrelated native turns.
- History guidance expands immediately before its following raw journal row **after** pagination/turn expansion. Expanded direct-offset pages explicitly return raw `count`, even without `turn_boundary=true`; pages without expansions preserve the existing shape.
- Owner/profile/session filtering precedes all event reads. Updates cannot change an accepted attempt's run/key/input/creation identity. Nonaccepted-only attempts and unrelated control-evidence events never become user messages. Known `not_delivered` is not reversed by a later duplicate acceptance.
- Scope remained `backend/runs.py`, `backend/native_catalog.py`, `tests/test_guidance_history.py`, and this contract. No changes to steering dispatch, native state, routes, frontend, or model input.
