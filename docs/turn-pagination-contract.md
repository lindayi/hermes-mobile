# User-turn history pagination contract

## Scope and acceptance (written before implementation)

Backend-owned files: `backend/native_catalog.py`, `backend/chat_snapshot.py`,
`tests/test_turn_pagination.py`. Parent owns HTTP/UI integration and full-suite verification.
All tests use temporary SQLite databases; native reads must not mutate history.

Root cause: `NativeCatalog.messages` currently slices a fixed raw-row range before
presentation classification. A tool-heavy turn is cut at an arbitrary row; a
synthetic native user-role delegation envelope is not a genuine user boundary.

Ranked hypotheses/probes:
1. Fixed row slicing: real SQLite latest/older requests inside a long tool turn
   start with a tool instead of its user. Expansion should fix this directly.
2. Native-role classification: a backward role=user search alone would stop on
   an anchored delegation completion envelope. Match presentation semantics.
3. Composed history: native visibility, compaction rewrites and journal overlays
   change positional offsets. Align within the composed filtered stream, not IDs.

## Explicit opt-in, preserving raw clients

Add keyword-only `turn_boundary=False` to catalog and snapshot calls. Existing
raw `offset` and `latest` calls retain exact behavior and response shape. UI calls
must opt in on BOTH initial latest and older requests. Parent must expose an
HTTP boolean query parameter `turn_boundary` and pass it through; this backend
change alone cannot enable the UI.

For opted-in pages:
- Clamp requested limit to 1..500 as before. It is a target raw-row range, NOT
  a ceiling on the expanded response. A normal 600+ row tool turn stays whole.
- Determine the original exclusive right edge using latest or offset+limit.
  Expand only the left edge, back to and including the nearest genuine user at
  or before the original left edge. Never advance the right edge or overlap an
  already loaded newer range. No preceding user means include the visible prefix.
- Boundary means role=user excluding the same anchored delegation completion
  envelopes used in presentation. Assistant text, tools, system rows and ordinary
  agent activity never start a user turn; ordinary user mentions still do.
- `offset` is the ACTUAL expanded start in the filtered/composed chronological
  stream, never a message ID. `count` is len(items). `total` retains its existing
  composed-stream meaning. For nonempty pages, offset+count is the original
  requested right edge. Rows remain chronological, contiguous and duplicate-free.
- Include `turn_boundary` metadata: `requested_offset`, `expanded`,
  `complete`, `truncated`, `reason`, `max_rows`, `max_extra_bytes`.
  Complete means left edge is a genuine user or the available stream beginning;
  it does not imply an active turn has finished or the right edge is a turn end.
- Safety: at most 10,000 returned rows (20 times the existing maximum target),
  and at most 8 MiB of additional stored public content/tool-call bytes beyond
  the original target. These limits deliberately accommodate multi-hundred tool
  sequences. If either expansion budget is exhausted, return a contiguous suffix
  ending at the requested right edge, `complete=false`, `truncated=true`, and
  reason `row_cap` or `byte_cap`. Do not silently claim this is a whole turn.
  Existing requested-row body size semantics are unchanged; this is an expansion
  bound, not a new body-truncation policy. Never alter or drop message bodies.
- Preserve tool IDs/names/statuses, safe public summaries, source/filter/compaction
  behavior, run/snapshot/replay metadata, and exclusion of private reasoning.

## Exact frontend/route handoff

1. Initial: `?latest=true&limit=100&turn_boundary=true`.
2. Older: let O be the oldest response offset. If O>0, request
   `limit=min(100,O)`, `offset=O-limit`, `turn_boundary=true` (NOT latest).
3. Prepend rows once; set oldest offset to **response.offset**, NOT requested
   offset and NOT offset minus rendered bubble count. A response may exceed 500.
4. Render the expanded history as a single chronological sequence; pair tool
   calls/results by IDs across requests. A partial-turn safety flag must not
   become a fake user boundary; show continuation and permit another older read.
5. Preserve scroll anchoring and abandon stale-route responses (parent UI scope).
6. Native appends after loading do not shift older offsets. Reconciliation or
   compaction can change the underlying sequence between requests: these are
   coherent per-read snapshots, not a cross-request immutable cursor. Refresh
   rather than claim cursor consistency across arbitrary history rewrites.

## Acceptance matrix

- Actual SQLite latest and older load pages, >100 and >500-row tool turns;
  return to preceding real user, preserve call/result IDs and contiguous ranges.
- Multiple older reads reach all history without duplicates or gaps.
- Synthetic delegation envelope in native user role does not split a turn;
  ordinary quoted/mentioned envelopes remain genuine user input.
- No user prefix; empty history; legacy schema; hidden rows and compaction copies.
- Raw offset/latest compatibility; no private reasoning; unchanged native DB bytes.
- Explicit row/byte cap flags and further progress after capped pages.
- Composed journal-only turns, active latest overlay and later external CLI turn:
  preserve ownership, run/replay and conservative-union metadata while paging.
- Focused new and adjacent regressions only in worker; parent runs full suites,
  route/UI integration and browser geometry. No services/deploy/model POSTs.

## Worker verification receipt / freeze

Observed RED before each production slice:
- Real SQLite latest tool turns: offsets 44/524 instead of the preceding user at 2.
- Pathological turn: 10,202 returned rows before the row budget was added.
- Oversized expansion: offset 0 instead of bounded suffix offset 2.
- Journal-only composed answer: offset 2 instead of its user at 1.
- Snapshot with active web overlay and later external CLI: offset 665 instead of 143.
Temporary RED signature-fallback shims were removed after the explicit option
existed; final tests call the real option directly.

Final worker execution:
- `tests/test_turn_pagination.py`: **18 passed**.
- Focused command below: **192 passed in 31.90s**, exit 0.
  This is NOT the full repository suite; parent owns that gate.

```sh
.venv/bin/python -m pytest tests/test_turn_pagination.py tests/test_native_catalog.py tests/test_history_presentation.py tests/test_chat_snapshot.py tests/test_snapshot_loss_regressions.py tests/test_latest_tool_replay.py tests/test_reopen_native_rewrite.py tests/test_tool_presentation.py -q
```

Frozen worker-owned code/test SHA-256 (before parent commentary projection):

```text
0f4a28af50e744d0a27abd51fbd2309bdc7f217e843652b7d8d48750273ca51a  backend/native_catalog.py
f46435b77435deb6eaf419c46b5175247ae6f507ad2dd9d9f8c6f3c89ba9d99e  backend/chat_snapshot.py
3005981606c6ba24803b98bdb31e54adca677aab6fa009d43fdd00ef789e9565  tests/test_turn_pagination.py
```

No native history writes, service operations, deployment or model POSTs were
performed. The supplied directory has no Git repository metadata, so Git
history/diff inspection was unavailable; exact file hashes identify this candidate.
Parent has coordinated ownership of HTTP/UI integration and subsequent public
commentary projection. Worker code is frozen for that handoff.
