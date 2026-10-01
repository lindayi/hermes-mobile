# Expanded tool detail integration contract

Current contract: see `activity-detail-spec.md` and `tool-summary-presentation.md`. The worker receipt below is historical evidence, not the current size limit or test status. Both current preview layers use a 360-character bound after full-value privacy screening; named collapsed previews and recorded durations use the shared helpers.

## Findings / release implication

- Saved native assistant `tool_calls[].function.arguments` survive catalog mapping, but `ui.mjs` currently drops them while building rows. Tool result `content` also survives but is ignored. Catalog attaches a summary by call ID even across pagination.
- Existing summaries deliberately reduce terminal commands to `Run <program>` and ignore wrapper arguments/goals. Therefore terminal result rows often cannot recover meaningful arguments even though native history recorded them.
- Installed native API callback emits started `{event,run_id,timestamp,tool,preview}` and completed `{event,run_id,timestamp,tool,duration,error}`. It does NOT emit arguments, results or call IDs. Mobile normalization drops preview after converting to the limited summary. Frontend-only cannot recover this lost live data.
- Backend presentation-only change IS required to retain a bounded safe primary preview in `summary`. Parent must use full guarded release, not frontend-only. No native/API modifications, model commands, DB writes or service actions by this worker.

## Stable planned integration API

New `frontend/tool-details.mjs` exports `toolDetail(data)` returning ONE bounded plain-text string (max 360 characters). Use as `.tool-summary` text via existing DOM text nodes, never HTML. Empty/unknown data returns exactly `Arguments/output not recorded`; recognized but unsafe data returns `Details omitted for privacy` rather than falsely asserting it was not recorded.

1. In `toolPreview`, render `toolDetail(data)` instead of directly slicing summary.
2. Preserve assistant call data: map `tool=>({...tool,name:tool.function?.name || tool.name})` rather than discarding function/arguments. Tool result rows already retain content.
3. Existing live completion summary retention is sufficient: enhanced backend summary is the canonical safe primary preview. Optional live fixture argument fields should be retained when replacing pending rows if tests cover them; do not invent output missing from native events.
4. Helper priority: allowlisted useful arguments (function wrappers / bounded nested `multi_tool_use`) > non-bare safe summary > bounded safe public tool result snippet > explicit missing/privacy state. Never stringify unknown args, arbitrary objects/config, private reasoning, tool code/body/patch content.
5. Keep folded defaults/tool identity/status/call-ID matching unchanged. Wrapper traversal and input/output length are bounded. Existing conservative sensitive-value guard remains fail-closed.

Owned by detail worker: `backend/tool_presentation.py`, related backend tests, new helper + `tests/browser/tool-detail*.test.mjs`, this contract. Worker will not edit ui.mjs/styles.css.

## Frozen worker receipt

Implementation/tests frozen on parent request. Parent owns integration, staged verification, independent re-review and full release. No ui.mjs/styles.css edits by this worker.

### Changes and actual evidence

- Read-only native DB shape check: 80 recent rows / 139 calls, all sampled call objects used `{function,id,type}`, functions used `{arguments,name}`, and arguments were strings. Printed only allowlisted key names/types/counts, never values or secrets.
- Existing opaque-string guard included `/`, misclassifying ordinary long absolute paths as credentials. Guard now treats slash-separated path segments separately while retaining sensitive labels/prefixes and long opaque-segment rejection.
- Backend summaries retain screened useful command text (still 120 characters), bounded nested wrapper children; native started previews share this path. Completion still cannot and does not invent arguments/results. Native input/output/source untouched.
- Frontend details recover preserved function arguments, allowlisted path/query/search/goal fields, bounded wrapper children/delegation goals, and screened public result strings / only output/content/text/message keys. Maximum text 360, argument JSON 10,000, wrapper depth 3 / fanout 4. Missing args stay explicit. Arbitrary objects and private reasoning are never serialized.
- Observed RED→GREEN for each behavior slice. Independent-review B1/B2 fixed under RED→GREEN: inline interpreter eval/code forms suppressed; canonicalize bounded percent decoding BEFORE stripping URL userinfo/query/fragment. Credential/body-bearing curl flags suppressed, including compact forms. Literal native truncation ellipsis remains useful. Malformed names/null data fail closed.

### Test results / blockers requiring parent attention

- `.venv/bin/python -m pytest -q tests/test_tool_detail_presentation.py tests/test_tool_presentation.py`: **52 passed**.
- `node --test tests/browser/tool-detail.test.mjs`: **7 passed** (worker file; parent may have additional differently named tests).
- Full `node --test tests/browser/*.test.mjs`: **107 passed, 1 failed, 2 cancelled**, timeout 240s. Concrete compatibility failure at `tests/browser/ui.test.mjs:454`: legacy fixture summary **`Run: pytest tests`** becomes `Details omitted for privacy` because it is not the canonical backend `terminal: pytest tests`/program form. Worker did NOT edit after freeze; parent must resolve/update test contract before claiming full green. Timers from failure left suite pending until timeout.
- Unscoped `.venv/bin/python -m pytest -q`: collection blocked by unrelated `spikes/test_native_fixes.py` native import missing `dotenv`. No installation attempted.
- Scoped `.venv/bin/python -m pytest -q tests`: **896 passed, 1 failed, 16 subtests passed**. Failure `test_backup.py::test_create_rejects_unsafe_paths_before_writing[open-parent]` is environment umask: shell reports `0077`, which makes fixture `mkdir(mode=0755)` become private. Re-run `umask 022; .venv/bin/python -m pytest -q tests/test_backup.py -k open-parent`: **1 passed**. Parent full suite should use `umask 022`.
- Directory has no Git metadata, so hashes rather than git diff used. No native/API edits, DB writes, service actions or model commands performed.

### SHA-256 (frozen owned files)

```
203a1940770d7ef66cdc35a78afbcf037dbb87a301755c4bbde29b9576d0c53a  backend/tool_presentation.py
89ad32304e5ee3769bd1bfe0b820c0d3f8a654df06ee826bd79dc7e790730d9d  frontend/tool-details.mjs
87ac4895ef7e25288c70a7cd0e8576c389be69f94babf135caeeafc414564ce7  tests/test_tool_presentation.py
16f59be44d301ae5f7573d959e637d924004e4ee39bb3c5c4a0100fe03355ce5  tests/test_tool_detail_presentation.py
e2ad706664c98cc252310b2fd6292712390759c9e6d0b83265eef641f47e6c75  tests/browser/tool-detail.test.mjs
```

Privacy remains a conservative presentation minimization policy, not general-purpose natural-language DLP. Oversized/malformed structured output and safe-looking non-ASCII text other than truncation ellipsis can still be omitted; no raw-JSON fallback was introduced.
