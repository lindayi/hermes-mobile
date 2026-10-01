# Conversation snapshot contract (backend/frontend handoff)

`GET /hermes/app-api/sessions/{sid}/messages?latest=true&limit=100`
returns `{items, total, offset, run, last_run, snapshot}`.

- `run`: nullable **owned/profile-scoped journal run** with existing run fields (`id`, `session_id`, `input`, `status`, `output`, `error`, `upstream_id`, timestamps). Discover it from this response, never browser storage. Includes queued/running/stopping/waiting_for_approval/unknown and terminal runs whose turn is not yet fully in native history.
- When `run != null`, `items` excludes only the first matched post-anchor native turn (up to the next real user row), NOT the whole native tail. Unrelated/later native turns remain visible; a mismatched first row or canonical-session mismatch retains the native tail conservatively. Render `items` + ONE run overlay. Seed the overlay input from `run.input`; use `run.output` as the authoritative terminal answer. Replay that run's SSE from cursor **0** to recover its deltas/tools; merge terminal output, do not append another answer. No actions are resubmitted.
- When the completed run's full turn is verified in native history, `run = null`; `items` is authoritative history, including that turn. Clear any stale local overlay/run ID.
- `last_run`: nullable latest owned journal run, even when `run=null`; use for status/error context, NOT another message bubble.
- `snapshot`: `{mode: 'overlay'|'history'|'legacy-unanchored', anchored: boolean}`. Informational only. `items`, `run`, and `last_run` come from the same backend composition; do not combine with a separately fetched run to decide exclusions.
- `total`/`offset` describe the composed historical sequence: retained native rows plus prior journal-only user/assistant entries, excluding the current overlay's matched native turn.
- `latest` defaults to **false**. Both latest and ordinary offset pages use the SAME owned journal composition and overlay exclusion; `latest=true` selects the last page. Older-page consumers render `items` only, not another `run` bubble. Offset pagination is no longer native-only.
- Earlier owned journal runs are bounded by their own and the next admission's message-ID anchors. Only the first turn within that interval can prove input/output persistence. Unproven prior inputs/outputs become ordinary `items` with stable IDs `journal:<run-id>:user|assistant`, `source:'journal'`, `run_id`, `run_status`, `run_error`, and `reconciliation:'unverified'`. They are never written to native SQLite. Equal-text prior admissions with the same watermark stay distinct.
- `snapshot.reconciliation:'conservative-union'` flags synthetic journal recovery or unrelated retained native tails. Ambiguous attribution can retain duplicate-looking text; no global matching/deduplication is performed. A current overlay can appear after later external history under the existing renderer; order ambiguity is preferable to hidden messages.

## Storage and safety

Admission captures a native SQLite message-ID high-watermark BEFORE dispatch. It is stored atomically with the run in a separate app-journal metadata table; no native state edits or changes to the existing `runs` columns. Repeated identical inputs are separate turns: exclusion is by watermark, never global text matching. Native reads are read-only and do not invoke the model or resubmit a run.

Legacy runs without a reliable watermark: nonempty native history remains authoritative (`run:null`, latest status in `last_run`, `mode:'legacy-unanchored'`); do not guess a text boundary or discard historical turns. Empty native history can safely show the journal overlay. This intentionally prefers no fabricated attribution/duplicate turns over claiming perfect legacy recovery.

Security remains under the messages route's ready-user/runtime-binding validation. Journal discovery filters user, profile, and requested session. No foreign journal data is returned.

## Implementation details and conservative limits

- Completion proof is restricted to the **first visible turn after the captured message ID**: exact input, then the final no-tool-calls assistant row matching journal output before the next user turn. Native delegation completion envelopes are tool activity, not new user turns. Earlier equal text never participates in matching.
- History count, completion proof, and returned page use one read-only native SQLite transaction. Owned journal state brackets that read; racing admission/completion retries at most three times, then returns 503 rather than mixed state. Changes after the validated snapshot remain normal SSE/refresh work.
- Existing terminal finish/event atomicity and SSE draining are untouched. `last_run` preserves all journal status/output/error fields even when there is no overlay.
- If the server resolved a different canonical continuation session, the source-session anchor still isolates its prefix, but native completion is not inferred from an unrelated source tail; retain the overlay conservatively.
- This does not introduce a global cross-channel writer lock. Watermarks assume append-only native message IDs during the admitted turn; destructive database replacement/ID reuse and unrelated simultaneous external writers are not newly solved.

## Backend review handoff — FROZEN

Production files: `backend/app.py` (messages route only), `backend/runs.py`, `backend/orchestration.py` (admission only), `backend/native_catalog.py`.
New helper: **`backend/chat_snapshot.py`** (`conversation_snapshot`). New catalog helpers: `NativeCatalog.history_anchor`, `_completed_turn`; new journal lookup: `RunJournal.latest`.
New tests: **`tests/test_chat_snapshot.py`**, **`tests/test_chat_snapshot_binding.py`**.

Observed RED → GREEN for missing discovery, atomic admission watermark, partial native-turn exclusion, completed-turn retirement/repeated identical turns, and journal/native races. Latest focused regression run: **197 passed in 30.75s**:

```
.venv/bin/python -m pytest tests/test_chat_snapshot.py tests/test_chat_snapshot_binding.py tests/test_native_catalog.py tests/test_history_presentation.py tests/test_orchestration.py tests/test_runs.py tests/test_canonical_history.py tests/test_app.py tests/test_runtime_binding.py tests/test_run_finish_atomic.py tests/test_sse_terminal_drain.py -q
```

No full-suite run, live model call, native live-state write, service restart, or deployment performed. Frontend remains parent-owned.

## Loss-regression handoff to parent / reviewer sa-0-5c47a199 — FROZEN

The previous whole-tail exclusion and latest-only discovery both reproduced with real temporary native+journal databases: **9 expected failures**, plus **1 expected pagination failure**, before production edits. All ten new regressions now pass. Targeted backend run: **204 passed, 3 deselected in 28.06s** (same focused list above plus `tests/test_snapshot_loss_regressions.py`). No full suite run.

Changed by this follow-up: `backend/chat_snapshot.py`, `backend/native_catalog.py`, `backend/runs.py`, new `tests/test_snapshot_loss_regressions.py`, and this document. Existing tests and frontend were left untouched. `RunJournal.latest` remains backward-compatible; new `snapshot_state` reads all owned admissions in one query, and the retry bracket compares that complete state. Synthetic pagination merges native IDs only, fetching native content/tools only for the requested page; presentation logic is unchanged.

**Parent must update these three obsolete assertions before the full suite:**
1. `test_default_and_offset_pagination_are_native_only_latest_pages_prefix`: ordinary total is now 3, not 4; ordinary pages carry the same run/last_run/overlay metadata as latest. This is the explicitly requested composed-pagination contract change.
2. `test_completion_proof_requires_this_anchored_turn_and_final_answer[suffix2]`: retain `['Retained answer', 'Other', 'Answer']`, not only the prefix. Run remains an overlay.
3. The same test `[suffix5]`: retain `['Retained answer', 'Another input', 'Answer']`, not only the prefix. Run remains an overlay.
These were the only failures in the initial focused run (**50 passed, 3 failed**); the later 204-pass run explicitly deselected those three, not silently claimed them green.

Limits: native append-only/first-post-anchor attribution assumptions remain; no ownership inference from arbitrary later text. Journal recovery uses conservative union when persistence cannot be established, so duplicate-looking messages and approximate ordering are possible. Synthetic rows include safe owned input/output/status/error only, never reconstructed tool activity. Stable totals/offsets refer to a single read snapshot; concurrent future native/journal changes can still move offset pages, as before.
