# Sticky Activity spacing acceptance

Scope: CSS-only adjustment of messages / live Activity heading / Stop geometry. Do not change session entry, confirmation, viewport, auth, run actions or rendering code.

## Acceptance (written before implementation)

- In a long-history conversation with sufficiently long live activity, scrolling into live activity pins the Activity heading flush to the session header bottom (within 1 CSS px), at widths 320, 390 and 1280 and normal/reduced viewport heights, in light and dark themes.
- Fix the scroll-container padding origin, not a negative sticky offset or header overlap. Heading retains `position: sticky; top: 0` and its top never covers the session header when pinned.
- Scrolling to the beginning still exposes the first message author and text fully. Retain 12px initial reading breathing room using non-scrollport spacing if necessary.
- Stop retains visible “Stop”, accessible name “Stop run”, red 1px outline and >=44px actual width/height. Its painted button is vertically inset at least 6px on each side of the Activity bar; intended bar minimum is 56px and button 44px.
- Stop remains right-aligned, outside disclosure controls, inside the scroll viewport, and hit-testable at its center and near its top/bottom edges. Real browser pointer click issues exactly one existing Stop request.
- No document horizontal overflow, no browser script errors; existing focused steering and keyboard viewport tests remain green.

## Verification

Use actual headless Chrome via Playwright against local HTTP/SSE fixtures serving the real frontend. Fixtures are explicitly synthetic, not live backend results. First run a failing seam/first-message test, make the minimal padding fix and pass it; then add a failing button-inset test, make the minimal bar-height fix and pass it. Save and inspect final narrow screenshots. Run only these new tests and focused steering/keyboard specs, not full tests or deployment.
