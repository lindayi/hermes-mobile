# Acceptance cases — written before production code

Status: original pre-implementation inventory. Execution evidence and remaining manual gates are recorded in `docs/release-status.md` and the `docs/tdd-*.md` records. This inventory is not a blanket claim that every case has passed.
Source: /home/lindayi/.hermes/plans/2026-09-27_185338-hermes-mobile-webapp-spec.md v0.4.
Deployment requested: https://lindayi.me/hermes. Public frontend /var/www/html/hermes only; secrets/state/backend outside docroot. Uploads remain deferred.

## Method
For each behavioral slice, add an executable test, run and record expected failure (RED), implement minimally, rerun (GREEN), then regression-test. Record real commands/results in docs/tdd-evidence.md. Fakes are allowed only at external side-effect boundaries; the delivered runtime must never fabricate model/tool responses. PASS, FAIL, BLOCKED, MANUAL_PENDING remain distinct.

## Auth and invitations
AUTH-01 Anonymous API calls fail; frontend carries no credentials.
AUTH-02 Owner bootstrap is unique, expiring, single-use and cannot be bypassed by first-public-signup.
AUTH-03 Real WebAuthn registration verifies origin, RP ID, challenge, signature and user verification.
AUTH-04 Wrong challenge/origin/RP/UV, reused challenges and forged assertions fail.
AUTH-05 Valid passkey authenticates; private keys/biometrics never reach server.
AUTH-06 Opaque session cookies are Secure/HttpOnly/SameSite; tokens are hashed at rest.
AUTH-07 30-day inactivity/90-day absolute limits enforce server-side; background polling doesn't keep a session alive indefinitely.
AUTH-08 Logout/device revoke invalidates API/event access; owner can revoke all devices.
AUTH-09 Sensitive authenticator/invite/recovery actions require fresh user verification.
AUTH-10 Recovery codes are hashed, one-use and limited to recovery; older sessions revoked.
INV-01 Only owner creates/revokes invites; token is random, hashed, single-use with expiry.
INV-02 Missing/incorrect/expired/revoked/reused invite cannot register.
INV-03 Concurrent redeem creates one account; interrupted ceremony doesn't create unprotected account.
INV-04 User cannot choose owner role/default profile/path; failed provisioning never falls back to default.
INV-05 Separate member profile has distinct native memory/session/job state, without owner's credentials copied.

## Security
SEC-01 All operations enforce authenticated record ownership, including guessed session/run/job/event/subscription IDs.
SEC-02 Origin + CSRF checks protect mutations; malicious remote origins fail.
SEC-03 Markdown/scripts/unsafe links cannot execute; CSP, no framing, no private caching.
SEC-04 Proxy exposes only /hermes public app and approved API; no arbitrary Hermes admin proxy.
SEC-05 Auth/register/recover/submission rate limits and bounded payloads.
SEC-06 No secrets/auth headers/invite codes/model message bodies in logs.
SEC-07 Same-origin existing WordPress trust risk documented; path prefix is not a browser security boundary.

## Native sessions and runtime
SES-01 Enumerate all default-profile sessions across CLI/WhatsApp/cron with pagination/search and source labels.
SES-02 Read native history without changing source sessions or replaying historical tools.
SES-03 Web resumes CLI session with tool context; CLI sees/resumes resulting native history.
SES-04 WhatsApp → web → WhatsApp includes intervening turns and tool results.
SES-05 New web session appears in CLI; native memories remain shared, not copied.
SES-06 One active turn per session across channels; busy conflicts do not create stale agents.
SES-07 /new and compression lineage do not silently redirect an older web tab.
RUN-01 Real upstream streamed turn persists status/results; no synthetic success fallback.
RUN-02 Repeated request idempotency key doesn't dispatch duplicate work.
RUN-03 Phone disconnect doesn't cancel run; reconnect/restart restores state.
RUN-04 Ambiguous upstream dispatch after crash is marked unknown, not replayed.
RUN-05 Stop requests cooperative cancellation; no assertion of reversing prior side effects.
RUN-06 Up to four distinct sessions execute concurrently per profile; fifth is explicitly capacity-blocked, while duplicate canonical sessions/aliases remain serialized. Switching A/B preserves drafts, run controls and results; reconnect never resubmits.
RUN-07 Native capacity configuration activation is gated, idle-only and rollback-verified; WhatsApp/cron remain running.
APR-01 Pending approvals show exact action/target; correct owner can approve once/deny.
APR-02 Changed/expired/foreign/replayed decisions rejected.
APR-03 Both interfaces resolve one shared decision; never run action twice.
APR-04 Missing native approval/clarify transport blocks sensitive execution, never auto-approves.

## Jobs/inbox/push
JOB-01 List existing native jobs; changes require confirmation and preserve native job IDs/schedules/state.
JOB-02 Arbitrary cross-profile destinations/script paths cannot be supplied by browser.
JOB-03 Existing production WhatsApp jobs/destinations remain unchanged during development.
DEL-01 One cron execution yields one inbox item and one intended delivery per destination.
DEL-02 Retry only failed destination; no duplicate successful delivery, run or brief injection.
DEL-03 Silent tick creates no notification; historical import creates no new push.
DEL-04 Script-only and agent jobs preserve actual outputs and source correlation.
DEL-05 Recipient ownership prevents family results sent to owner's WhatsApp.
PUSH-01 Authenticated subscriptions bound to user/device; revoke on logout/device removal.
PUSH-02 Actual Web Push encrypted/signed using library; expired endpoints removed.
PUSH-03 Real iPhone Home Screen permission and locked-phone notification work.
PUSH-04 Notification tap opens correct authorized inbox item; never grants action consent.
PUSH-05 Inbox survives failed push and offline device; retries don't duplicate inbox.

## UX/PWA
UX-01 Familiar conversation list/composer; mobile bottom navigation Chats/Inbox/Jobs/Settings.
UX-02 390px viewport, safe areas, dynamic keyboard, no horizontal overflow, usable 44px touch targets.
UX-03 Light/dark system theme, readable typography, keyboard/focus and accessible controls.
UX-04 Draft survives navigation; active stream doesn't steal scroll when reading earlier messages.
UX-05 Code blocks/copy, safe Markdown, collapsible tool progress, final response distinct from commentary.
UX-06 Offline shell caches public assets only, private API/transcripts excluded.
UX-07 PWA works under /hermes/ including manifest/scope/icons/notification deep links.
UX-08 Enrollment/recovery/invitation/device management are usable without terminal after owner bootstrap.

## Operations and release
OPS-01 Python bridge idle RSS and active peaks measured; target <100 MiB incremental idle.
OPS-02 No Docker/Redis/local models/frontend watcher in production.
OPS-03 Deployment templates validate without touching WordPress or production routing during build.
OPS-04 HTTPS host, proxy paths, streaming and secure cookies verified at requested URL.
OPS-05 Backup/restore and routing-only rollback preserve new native state.
OPS-06 Real-phone tests explicitly MANUAL_PENDING until user performs them.
OPS-07 Family profile concurrency and cron operation measured; no hidden resident-runtime assumptions.
OPS-08 No public deployment if critical auth/native-approval/shared-session tests fail.

## Known prerequisites at kickoff
- /var/www/html is www-data-owned and not writable by lindayi; Apache config is root-owned; sudo needs authentication. Deployment requires administrator action.
- Existing origin hosts WordPress. Other same-origin scripts can access the app with the user's cookies; /hermes is not origin isolation. Deployment requires acknowledgement or a dedicated subdomain.
- Permanent URL is known; real device passkey/push cannot be certified by server-only tests.
- Upstream shared-session execution coordination/approval routing must be proven before claiming full feature completion.
