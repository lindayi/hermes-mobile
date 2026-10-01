# Next-turn model controls — frontend integration contract

## Frozen public interface

`createModelControls({doc, win, api, sessionId, userId, isCurrent})` is a synchronous export from `frontend/model-controls.mjs`. `api.request(path)` performs one read-only GET to `/sessions/${encodeURIComponent(sessionId)}/model-options`. `isCurrent()` is the caller's route/account lifetime fence.

Returns `{element, selection(), setLocked(bool), destroy(), canSubmit()}` immediately:
- `canSubmit()` is false when a previously saved explicit model choice has not been validated by a successful current catalogue response, or the view is stale/destroyed. A legacy Default with no saved selection stays usable. Caller checks this before a NEW attempt, not before retrying an existing immutable attempt; failure/pending catalogue must not silently turn a saved choice into Default.
- Append `element` (a compact button, accessible name/title **Model settings**) to the composer. It starts hidden and becomes visible only after a valid available catalog. Default visible text is **Model**, selected text is the model ID. Parent owns truncation/layout CSS. It is `type=button`, not a submit control.
- `selection()` returns a fresh `{model, provider, reasoning_effort?}` snapshot, or `null` for Default, unavailable, destroyed, or stale route. Parent omits `selection` from run requests when null. Lock does not erase an already applied selection.
- `setLocked(true)` disables the button and dismisses an open dialog without applying its draft. Unlock does not fetch or mutate the choice. Parent locks during active/uncertain runs.
- `destroy()` removes the modal and listeners, fences late requests, and disables/hides the element. Parent must call on navigation/account cleanup. Cleanup does not steal focus on a stale route or destroy.

## Catalog and choices

Expected response: `{available:true, models:[{id,provider,label,reasoning_efforts:[]}], default:{model,provider}}`. Require a nonempty list whose every record has nonempty string ID/provider/label and a unique pair, plus an array of unique nonempty string efforts. Reject partial/malformed catalogs as a whole; no fake options. `default` is descriptive metadata, not an explicit selection. Failures/unavailable catalogs leave the control hidden. Backend is the authority for owner-only capabilities and per-admission validation.

The modal uses existing `.dialog-overlay`, `.dialog`, `.field`, `.actions` styles, title **Model settings**, and caption **Applies to the next message. Provider fallback may still occur.** Model choices are Default plus exact advertised model/provider pairs. Reasoning is hidden for Default or an empty effort array; otherwise Default plus exactly advertised strings. Default effort omits the field. Changing model resets the draft effort. Apply validates DOM values against the catalog; invalid drafts never become selections. Cancel/Escape discard drafts. Tab wraps within the modal; opening focuses the model select; normal close returns focus to the opener.

## Persistence and caller obligations

Applied selections persist in `sessionStorage` under `hermes:${userId}:model-controls:${JSON.stringify([userId,sessionId])}`; this fits existing per-user logout cleanup while the JSON tuple prevents account/session collisions. Restore only after validating against the fetched catalog. Stale/invalid saved selections are removed; no storage or network failure fabricates a selection. Storage being unavailable is nonfatal. Default removes the saved value.

Caller captures `selection()` once into each immutable submission attempt, including retries, and includes the helper in service-worker precache. The helper never posts global configuration, starts runs, or modifies observed transcript model metadata. Provider fallback can still occur. No backend/live/deployment operations are part of this module.

## Verification

Focused command: `node --test tests/browser/model-controls.test.mjs`. Tests use the repository's existing JSDOM harness convention. Public interface and semantics above are **frozen for parent integration**.

Verified implementation evidence:
- Incremental RED→GREEN cycles exercised export/loading, strict catalog validation, advertised model/effort selection, persistence validation, keyboard/cancel behavior, locking, stale-route/destroy fencing, and injected DOM choice rejection.
- Focused suite: **9 passed, 0 failed**. Syntax checks of helper and focused test module passed.
- Full browser unit suite at handoff: **165 passed, 1 failed**; only failure was the concurrently added public-worker precache assertion for `/hermes/model-controls.mjs`. Service-worker integration is parent-owned and intentionally untouched here. The four parent next-turn submission/invariant integration tests already passed in that run.
- Existing global `[hidden]{display:none!important}` styling was checked, so the empty-effort field remains genuinely hidden despite `.field` display rules.

Only the helper, its focused tests, and this contract were authored in this workstream. No backend, live model call, or deployment was performed.
