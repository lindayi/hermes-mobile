# Message timestamps and runtime-reminder reconciliation

## Goal

Show the original human question once when native compression appends a preserved task list and skill-reload notice, and show recorded local times on human, assistant, and visible system/process messages. Raw native history and model input remain unchanged.

## Reconciliation

The app journal retains the original submitted question while native history may persist the same question with a generated suffix. Strict content comparison previously caused both the human question and final answer to be displayed twice.

Use a bounded proof of the owning admission and completed native turn, exact supported producer grammar, clean public provenance, recorded timing, and matching final/tool evidence. Verify the original admission before following unique archived copies to a relocated boundary. Retained pre-admission assistant/tool carriers may be ignored only with positive compression and timestamp evidence; unexplained human turns must never be skipped. Repeated genuine questions are not globally deduplicated.

Project the original human content once. Keep the generated suffix in a folded `runtime_reminders` process child with stable identity and recorded compression time, without changing native pagination coordinates. Materialized journal replay and background origin use the same proven identity. Do not edit or delete stored messages, inspect private reasoning to infer authorship, or strip arbitrary marker-like text.

Active, failed, unknown, incomplete and ambiguous turns remain unchanged until sufficient completed-turn evidence exists. Public search retains readable reminder detail when it lacks owned journal evidence. Unknown producer variants, copied markers, trailing genuine requests, conflicting provenance and exhausted work budgets fail closed.

## Recorded times

Use recorded native seconds or explicit timezone-qualified ISO timestamps. Journal inputs use creation time; terminal assistant output uses recorded completion time. A streaming assistant start is labeled Started and is not passed off as its final sent time. Public commentary, process notices and background receipts retain their own evidence. Tool durations are unchanged.

Render quiet local date/time with an HTML `time` element, exact machine-readable ISO, and a full local date/time/timezone title and accessible label. Localized display never participates in message ordering. Missing, invalid or unsupported numeric timestamps are omitted rather than invented. Raw system prompts and private analysis remain hidden.

Keep author/actions aligned and process headers reachable at narrow and tablet widths. Preserve compact no-timestamp headers; a timestamp must not cause a long receipt title or Completed caption to wrap into an oversized header. Reload, live completion, older pagination, background placement, disclosure state and selection remain intact.

## Acceptance

Synthetic native SQLite plus journal snapshots verify one human/final, a separate folded reminder, unchanged stored bytes, original-admission contradictions, repeated questions, optional schemas, tool replay, origin binding and shared work/byte budgets. Generated browser tests verify actual projection, recorded times, DST folds, missing evidence, live completion/reload, private-system exclusion, intact captions and responsive layout. Full managed integration and exact-head independent GitHub review precede merge; deployment is from reviewed main only.
