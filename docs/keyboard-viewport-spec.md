# Keyboard-aware viewport acceptance (pre-implementation)

Scope: public frontend viewport observer, app entry, viewport-only CSS, public service-worker graph, isolated tests. No native service changes, deployment, authenticated live probes, or conversation renderer changes.

## Contract and acceptance cases

1. At scale 1, size the app shell and fixed overlays to `visualViewport.height`, position them at `visualViewport.offsetTop`, and use window inner height when VisualViewport is absent. Retain CSS dynamic-viewport fallback before JS starts. Real Chromium geometry must prove header, message viewport, composer Send/Expand, rename dialog, expanded editor and Done fit the visible rectangle.
2. A substantial height loss while an editable text control is focused identifies a likely text keyboard. Compact only nonessential bottom navigation/help and composer spacing. Never hide security notices, approval status/actions, or Stop. URL-bar changes alone, buttons/selects and pinch zoom are not text keyboards. Zoom stays enabled; during non-unit scale keep the last unzoomed layout rather than repeatedly resizing it to the magnified viewport.
3. Focus/blur never programmatically focus, blur, scroll the page, submit, or rebuild the editor. Draft text, exact editor node, caret/selection survive changes. Resize preserves a reader's message scroll offset, while a previously bottom-following reader stays at the bottom.
4. Repeated keyboard show/hide, fractional event jitter, visual viewport scroll, window resize fallback, URL-bar expansion/collapse, orientation and keyboard dismissal restore usable full layout. Coalesce events in animation frames; ignore invalid/transient zero dimensions.
5. A disposable observer attaches to the real stable `#app` root in the entrypoint. Pagehide suspends observation and cancels pending work; pageshow remeasures/reactivates once (including back-forward cache). Explicit disposal removes listeners, cancels pending work and restores owned style/data state. No duplicate observers on repeated lifecycle events.
6. `viewport.mjs` is imported by the app and included in the public service-worker precache. Existing public asset builder includes this `.mjs`; no authenticated endpoints enter caches.

## Evidence boundaries

- Unit fixtures supply deterministic VisualViewport dimensions/events (Safari-like visual-only resize and offsets), lifecycle, focus, zoom and jitter. They are not an iOS keyboard simulation claim.
- Headless Chromium tests use actual window viewport resizing plus deterministic VisualViewport fixtures driving real DOM/CSS geometry, long histories, overlays and screenshots. Network fixtures are explicitly synthetic and test-only.
- Physical iOS Safari/Android keyboard, browser-chrome animation, installed-PWA safe-area behavior and hardware keyboard remain manual device gates. No physical keyboard result is claimed.
- Run focused new and adjacent tests only; parent owns immutable release/full-suite/deploy gates and master acceptance record.

## Planned RED → GREEN seams

Observer basic geometry → keyboard/zoom classification → lifecycle → reader scroll; real CSS geometry and overlay/editor fit → app entry integration → offline public asset graph. Each changed seam requires an observed failing assertion before implementation and a passing rerun.

## Execution record

Observed RED → GREEN:
- Missing observer export → real helper geometry/fallback.
- Missing keyboard state → focused-text substantial-loss detection, URL-bar distinction, zoom exclusion, orientation baseline.
- Missing managed state → coalesced rounded geometry, invalid zero suppression, pagehide/pageshow, disposal restoration.
- Chromium shell top `0` instead of visual offset `36` → viewport-only shell/overlay CSS.
- Expand click lost after focusout changed layout → keyboard-open state retained until actual viewport recovery; explicit unit regression plus successful real pointer click.
- Actual app root lacked managed state → entrypoint integration; startup-error observer stayed active → disposal on failed mount.
- Real Chromium resize lost bottom-following → preserve message scroll intent while applying geometry.
- Narrowing width left Stop at bottom `1338.5625`, outside visual bottom `404` → capture pre-reflow bottom intent across message line wrapping; existing older-history reading remains stable.
- New public dependency absent from cache → precache viewport module and source cache v4; actual public-tree builder includes it.

Final focused command (no full suite or deployment):

```sh
node --test tests/browser/keyboard-viewport.test.mjs tests/browser/keyboard-viewport.spec.mjs tests/browser/pwa.test.mjs tests/browser/technical-ui.spec.mjs tests/browser/steering-controls.spec.mjs
```

Result: **17 passed, 0 failed**. Seven new real Chromium cases plus four new unit/public-graph cases; adjacent PWA and composer/editor/approval cases also pass. All browser page-error assertions empty. Actual resizes include portrait, short keyboard-sized, and landscape windows; deterministic VisualViewport uses height/offset/scale and lifecycle events. Screenshots `tests/browser/artifacts/keyboard-viewport-{320,390}-{light,dark}.png` and `keyboard-viewport-editor.png` were produced. Visually inspected the 320px light approval/security/Stop/composer screenshot and dark expanded editor: no overlap, Expand and Done reachable; empty area below the synthetic visual rectangle is not an actual keyboard screenshot.

Physical Safari/Android keyboards and installed PWA safe areas remain untested manual gates. No live authenticated tests, service changes, deploy, or `ui.mjs` edits were made by this worker. Parent owns full immutable candidate verification.
