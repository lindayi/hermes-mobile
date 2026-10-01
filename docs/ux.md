# Frontend UX and integration

## Shipped surface

Plain JavaScript ES modules and static files, no build step, no runtime dependencies or CDN/font requests. Public base `/hermes/`; API base `/hermes/app-api`. Serve `.mjs` as JavaScript. Entry `app.js` mounts `ui.mjs`; parent owns the combined live server.

- Original Hermes wing/H mark; warm parchment, terracotta, quiet borders and editorial Georgia headings derived from the requested Claude design vocabulary. System UI and system monospace fonts; no Claude identity/assets.
- Four mobile bottom tabs: Chats, Inbox, Jobs, Settings. Desktop navigation rail and readable constrained conversation width. Light, dark and system appearance; reduced-motion support.
- Native conversation search/pagination/source labels, new chat and rename requests, paginated retained messages. Multiline draft-preserving composer, explicit send, durable known run ID, real SSE updates, queued/status/error/unknown states and cooperative stop warning.
- Safe Markdown subset: headings, paragraphs, lists, strong text, inline/fenced code, HTTP(S) links. Everything is DOM/text creation, never untrusted HTML. Copy code/text. System/analysis/reasoning messages are hidden; tool history is collapsed without raw logs; assistant commentary is labeled. Rich CommonMark tables/images are intentionally not implemented; literal text is retained safely.
- Inbox actual delivery bodies, read marking and originating conversation links. Action-bound approval card with exact action, target and expiry; approve once invokes fresh passkey verification, deny is explicit. A notification tap only navigates; it never approves or starts work.
- Jobs list and prompt/schedule editor with explicit review/confirm for creation, edit, pause/resume and deletion. Browser cannot supply profile, filesystem/script paths or delivery destinations.
- Real browser WebAuthn invitation/bootstrap registration, discoverable login, fresh verification, replacement enrollment via recovery, second passkey, recovery codes, own device/passkey revocation, owner invitation/member controls. One-time secrets appear only on a transient screen, never in browser storage or URLs. Pending/failed profiles stay in Settings; no owner-profile fallback.
- User-gesture notification opt-in, explicit disable/test controls, iOS Home Screen guidance. Secure-context service worker and manifest, local SVG/PNG icons, public-only offline shell.

## Route agreement

Authoritative request/response shapes: `docs/implementation-contract.md` and `docs/auth-endpoints.md` (read during implementation).

- All requests: same-origin cookies, `cache:no-store`; mutations carry CSRF from `/auth/me` or ceremony responses. HTTP errors and unreadable/proxy responses are visible; no fake production data or optimistic success fallback. API 401 removes the private view and returns to sign-in.
- WebAuthn options response `{options,enrollment_id|challenge_id}`; browser ArrayBuffer fields are converted to/from base64url. Register/login use `/auth/<kind>/verify`; step-up uses `/auth/verify/finish`. Recovery begins `/auth/recovery/options` then uses **registration** with `/auth/recovery/verify`, including the CSRF token captured by the API client. Second passkey: POST `/passkeys {label}`, POST `/passkeys/verify`.
- Messages additionally use the parent catalog's `?limit=100&offset=N` and `total`, so history is reachable beyond the first page.
- Proposed job mutation shape is `{name,prompt,schedule,timezone}` with cron-string schedule; pause/resume uses `{enabled}`. **Parent must retain the explicit unavailable response until its native mutation adapter is verified.** Existing read-only native jobs may have structured schedules, displayed as their actual JSON.
- Run POST `{session_id,input,idempotency_key}`. SSE named events `status,delta,tool,approval,done,error`; delta accepts `{text}` or `{delta}`. Known runs recover through GET `/runs/{id}`, never resubmitting the original prompt. EventSource handles transient same-connection reconnects. Server remains responsible for event replay cursors, persistence, busy locking, ownership revalidation, and authoritative final state.
- Approval fields: `{id,title?,action|payload,target,expires_at}`. No missing transport is bypassed.

## Storage and security

- `sessionStorage`: account-scoped unsent drafts and submission retry records. Draft text remains local to the tab and is not encrypted. Tab closure/storage eviction can lose it; switching views/apps does not.
- `localStorage`: theme and account-scoped **run IDs only**, to find in-progress work after closing/reopening a tab. No transcripts, API keys, bearer tokens, CSRF tokens, passkey secrets or invite/recovery codes.
- Sign-out clears account-local drafts/retry records/run IDs. Clear-local-drafts does not delete server history. Expired auth hides private content.
- Service worker caches a fixed allowlist of public assets only. No `/app-api`, messages, token responses, arbitrary `/hermes/*`, foreign URL, POST or authenticated response caching. Notification payload display is always generic; navigation is clamped to this origin's `/hermes/?inbox=...`.
- Before updating deployed assets, bump the `hermes-public-vN` cache name in `sw.js`; an old installed worker waits for previous clients to close. No forced mid-session worker replacement.
- Same-origin WordPress remains an accepted trust risk; a path prefix is not isolation. Family profiles are explicitly not filesystem sandboxes.

## Verification and limitations

Run instructions and observed RED/GREEN evidence: `docs/tdd-frontend.md`.

Chromium verified 390px navigation, 320px narrow portrait, 390×450 keyboard-sized viewport, 844×390 landscape and 1280×900 desktop; no horizontal overflow, visible composer, all visible chat buttons ≥44px, dark mode, CSP-compatible local assets, no page JavaScript errors, true offline shell reload and no private Cache Storage entries. Screenshots are under `tests/browser/artifacts/`; their authenticated content is explicitly test-only network fixtures, **not a live Hermes result**.

Still requires parent/system acceptance:
- Live auth/backend integration and native run/session/approval/job availability. UI requests the documented routes; absent or blocked routes remain visible failures. No claim that native continuation, WhatsApp coordination, actual tools or cron mutation passed from frontend fixture tests.
- Archive/unread/lineage management has no agreed mutation/metadata contract in the provided API and is not invented in the frontend. Native deletion is intentionally not offered.
- Real iPhone/iPad enrollment/login/recovery, Home Screen install, actual keyboard behavior, locked-phone push, provider delivery, multi-tab session expiry/revocation, and browser/gateway restarts are **MANUAL_PENDING**. Desktop emulation is not handset certification.
- No backend/state/config/profile/cron/WordPress edits or public deployment were performed by this frontend task.
