# History presentation evidence

## Native evidence (read-only)

Inspected installed sources under `/usr/local/lib/hermes-agent` and only schema/source aggregates in `~/.hermes/state.db` (`mode=ro`; no conversation text or credentials read).

- `tools/delegate_tool.py:1955,2024` creates `platform="subagent"` children and records `model_config._delegate_from`.
- `hermes_state.py` uses `_delegate_from` to distinguish delegation; native tests also use legacy source `delegate`.
- Source aggregate at inspection: 46 subagent sessions, all parented; 6 WhatsApp sessions, **5 parented**. Thus `parent_session_id IS NOT NULL` is not a safe worker filter.
- Full messages schema includes optional `tool_name`, `tool_call_id`, `effect_disposition`, `active`, `compacted`, `api_content`, `display_kind`, `display_metadata`, and private reasoning columns. Presentation selects an allowlist, not `SELECT *`.
- `agent/tool_dispatch_helpers.py:534` stores tool identity separately from content. `tools/process_registry.py:2721` formats the exact async batch envelope, task statuses, truncation warnings, and batch errors; goals may contain newlines.

## API/display contract

- Chat inventory excludes sources `subagent`/`delegate` and optional native `_delegate_from` markers **inside SQL before search/count/pagination**. Ordinary parented continuations remain. Malformed legacy model configuration does not break inventory.
- `GET /hermes/app-api/sessions/{sid}/messages?latest=true&limit=100` returns the last page in ascending message-ID order. Response includes `items`, `total`, `offset`; `offset` is the starting index in the visibility-filtered raw message stream, **before frontend presentation filtering**, not a database ID. Latest mode overrides supplied offset. Default `latest=false` preserves ordinary offset pagination.
- Existing visibility remains: active or compacted rows are displayed, rewound inactive/noncompacted rows are excluded. Legacy schemas without these columns still work.
- Tool `name`: native `tool_name`, otherwise matching preceding assistant call ID within the same session (including calls before the page), otherwise `tool`.
- Tool `status`: `success` only for explicit boolean success, success/ok status, or integer zero exit code; explicit errors/failure/timeout/cancellation/nonzero exit override success. Explicit `completed` remains neutral completion. Missing/malformed evidence is `unknown`; native unknown effect disposition stays unknown. Prose is never keyword-guessed.
- Only user-role text beginning with the exact complete `[ASYNC DELEGATION BATCH COMPLETE — …]` envelope on its own line becomes `role=tool`, `name=delegate_task`, `kind=delegation`. Status is `completed`, `failed`, or `mixed` from native task headers/error format; truncation or uncertain task outcomes are mixed. If individual statuses are unavailable, the completion envelope proves only neutral `completed`, not success. Ordinary mentions/quotes remain user text.
- Content is retained verbatim for expandable actual reports. Native history, execution messages, auth/config, and live services are unchanged; this is only `NativeCatalog` presentation.

## Red → green verification

Each behavior was added in a vertical test-first slice in `tests/test_history_presentation.py`. Observed reds: worker counts 4 instead of 2; marker count 2 instead of 1; missing latest argument; HTTP latest returning the oldest row; missing tool names/statuses; batch reports remaining user messages; unknown effects incorrectly marked success; multiline failed batch marked completed; explicit status aliases reported unknown. Each was run green before proceeding.

- Focused catalog/API/execution-history regression run: **71 passed**.
- Full suite: `(umask 022; .venv/bin/python -m pytest tests/ -q)` → **583 passed, 16 subtests passed**.
- Initial full run under inherited umask `0077` hit the existing backup `open-parent` fixture: `mkdir(mode=0755)` became private and could not test an unsafe directory. Isolated reproduction confirmed this; test-only subshell umask `022` made it pass. No backup code/test changes.
- Synthetic DB byte comparisons prove reads/reclassification do not modify stored sessions/messages. No real model calls, account probes, or live native DB writes.

Changed files: `backend/native_catalog.py`, two route lines in `backend/app.py`, `tests/test_history_presentation.py`, and this document. The working directory has no Git metadata, so verification used file inspection and executed tests, not a Git diff.
