# Compact mobile controls and process history

## Requested UX

1. Conversations header puts New chat, filter and search in the same compact action row. Filter is an accessible icon-triggered dropdown with Chats/All/Cron/Tests and a visible active-state cue, not a permanent segmented second toolbar. Search stays collapsed until opened; query/filter/pagination behavior remains intact.
2. No permanently visible per-row trash icon. A deliberate horizontal left swipe reveals a red Delete action; swipe alone never deletes. Vertical scrolling must remain natural, accidental taps after a drag must not open a chat, only one row is open, and tap-away/right-swipe/Escape closes it. Preserve existing named confirmation, auth/CSRF/capability/busy/deletion-uncertainty safeguards. Provide keyboard/screen-reader access and a non-touch fallback without permanent trash clutter.
3. Model selection is a direct native select, not a button launching a dialog. Selection applies to the next message without an Apply step. Default, exact model/provider identity, session-specific persistence, runtime capability validation, run lock and stale/account guards remain. The actual backend advertises default reasoning only; no unsupported reasoning override is sent. Retain any genuinely supported effort selection without a modal if needed.
4. Background results in chat are collapsed disclosures by default, with a compact clear label and safe full report available on expansion. Preserve open state/DOM identity on refresh, full body, result identities, newest-result retention, omission notices and reader position. No auto-execution, fake delivery labels or user bubbles.
5. Native context-compression envelopes are process history, not user-authored turns. Investigate actual native persistence/provenance; positively recognize supported native envelopes with available provenance, not keyword matches. Preserve genuine human lookalikes, native rows and raw model-input history. Public projection contract: role=tool, kind=context_compression, name=Context compression, status=completed, original content retained. Render a compact collapsed process disclosure, never You/user-bubble. Test compaction generations, pagination/turn boundaries and journal overlay interactions.

## Delivery gates

- Observed RED→GREEN tests, isolated fixtures, independent reviews and freshly generated assets.
- Existing safety assertions/timeouts remain; assertions encoding superseded visual contracts may be updated only to the explicit new UX, retaining their behavior/safety coverage.
- 320px/390px mobile, tablet and desktop; light/dark; keyboard/focus and pointer/touch cancellation; no overflow or accidental destructive gesture.
- Main/integration workspaces and native SDK remain untouched. Bridge-only update only if public history projection needs it; no native/WhatsApp/cron restarts and no user sessions deleted.
- Freeze full source/asset hashes, run full suites with short private Chrome scratch, then guarded publish and post-verify. Physical iOS gesture behavior remains a device check unless observed.

## Ownership

- UI worker: frontend/ui.mjs, frontend/styles.css, UI/list/history tests excluding model-controls tests.
- Model worker: frontend/model-controls.mjs and model-controls/model-selection tests, no ui.mjs/styles.css.
- Compression worker: backend public history projection/native catalogue and new Python regression tests; no native SDK, app routes or frontend.
- Parent: specification, integration, release wrapper, live read-only evidence and release/verification.

At preparation: isolated candidate, not yet deployed.
