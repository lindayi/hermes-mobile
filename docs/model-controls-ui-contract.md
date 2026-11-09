# Next-turn model controls — frontend integration contract

## Frozen public interface

`createModelControls({doc, win, api, sessionId, userId, isCurrent})` is a synchronous export from `frontend/model-controls.mjs`. It calls `api.request(path)` once with a read-only GET to `/sessions/${encodeURIComponent(sessionId)}/model-options`. `isCurrent()` fences route/account lifetime.

It returns `{element, reasoningElement, selection(), setLocked(bool), destroy(), canSubmit()}` immediately:
- `element` is a native Model `<select>`; `reasoningElement` is a separate native Reasoning effort `<select>`. The parent appends both beside the composer.
- Both controls start hidden and disabled. They appear only after a valid available catalog. Model uses accessible name **Model**. Reasoning uses **Reasoning effort** and is hidden when the selected or default model has no exact supported efforts.
- `canSubmit()` is false while a saved explicit choice awaits validation or the view is stale/destroyed. A legacy Default with no saved selection stays usable. The caller checks this before a new attempt, not before retrying an immutable attempt.
- `selection()` returns a fresh `{model, provider, reasoning_effort?}` snapshot or `null` for Default, unavailable, destroyed, or stale route. Default effort omits the field. If effort is selected while Model remains Default, the snapshot binds it to the currently validated catalog default model/provider.
- `setLocked(true)` disables both selects while a submission's model choice is unresolved. A known active run does not lock the next-turn choice. Locking does not erase the applied selection.
- `destroy()` removes listeners, fences late requests, and hides/disables both controls. Parent calls it on route/account cleanup; stale cleanup does not steal focus.

## Catalog and choices

Expected response: `{available:true, models:[{id,provider,label,reasoning_efforts:[]}], default:{model,provider}}`; `default` may be null. Require a nonempty list of complete records, unique model/provider pairs, unique nonempty efforts, and a default that exactly matches a catalog record. Reject partial or malformed catalogs as a whole; do not invent options. The backend validates ownership, current catalog support, and every new admission.

Model options are Default plus exactly the advertised model/provider pairs. Its label may name the validated catalog default; this does not create an override. Reasoning options are Default plus exactly the current model's advertised efforts. Default effort omits `reasoning_effort`; changing Model clears the effort. Neither control opens a modal, posts on change, traps keyboard focus, nor changes active work or steering payloads.

The pinned app transport does not enforce Copilot CLI context tiers. Do not render a Context select or send a context override. Existing context and compression behavior stays unchanged.

## Persistence and caller obligations

Applied selections persist in `sessionStorage` under `hermes:${userId}:model-controls:${JSON.stringify([userId,sessionId])}`. Restore only after validating the saved pair and effort against the fetched catalog. Keep stale or invalid saved selections blocked across reentry. Show a disabled “Saved choice unavailable — choose model” placeholder and hide Reasoning until the user explicitly selects a model or Default; only that choice replaces the stale override. Storage failure is nonfatal. Default removes the saved override.

The caller snapshots `selection()` once into each immutable submission attempt, including retry identity, and includes the helper in service-worker precache. Model changes apply to the next message and cannot retarget active work. The helper does not post global configuration, start runs, or alter observed transcript model data. The UI reports a requested next-turn model; only trusted runtime evidence may identify observed execution.

## Verification

Focused unit command: `python3 scripts/test.py js -- tests/browser/model-controls.test.mjs`. Generated-browser command: `python3 scripts/test.py browser -- tests/browser/model-selection.spec.mjs`. Tests use synthetic catalogs and requests only; no live model call or deployment is part of this module.
