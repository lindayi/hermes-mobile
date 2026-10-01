# Sessions, notifications, and parallel work acceptance

Requested by owner on 2026-09-30. Candidate starts from active immutable bridge release, not older source workspace. No change to WordPress, public TLS, WhatsApp, cron destinations, member profiles, or approval authorization.

## Session opening
- An ordinary mouse click or real touch tap on a session opens that session, including repeated return/open cycles and small finger movement.
- Horizontal swipe reveals actions but never accidentally opens/deletes. Vertical scrolling remains native. Cancellation, lost capture, and synthesized clicks are covered.
- Pending history loads have feedback; failures remain visible and retryable; stale responses cannot replace a newer view.

## Sessions list
- Heading is `Sessions`; no redundant `Showing chats page 1` text under it.
- Toolbar visual and DOM order: Search, Filter, New. Existing compact search/filter, pagination, accessibility, and destructive-action guards remain.

## Notification settings
- Exactly one effective device master control, visually separated above preferences, displays actual enabled/disabled status.
- Disabled master hides individual category/privacy choices. Browser permission/subscription failures cannot be shown as success.
- While enabled, category and privacy edits remain local until explicit Save; save feedback, failures/retry, and revision conflicts stay truthful.
- Device opt-out and category/privacy protections remain; Inbox and WhatsApp unaffected. No automatic permission prompt on page load.

## Parallel sessions
- Four distinct sessions per profile can execute concurrently. A fifth receives an explicit capacity response; no silent resubmission.
- Same canonical session stays serialized, including native aliases, unknown runs, repeated idempotency keys, and racing submissions.
- Switching A -> B allows submitting B while A runs. Returning to A reconnects to A; stream/results/Stop/approvals/drafts never cross session boundaries.
- Terminal/error/stop recovery frees only the relevant slot. Deployment admission gate blocks all new runs and drains existing work.
- Native listener configured to capacity four through supported Hermes config command; preserve same-session native lease and isolated listener service boundaries.

## Verification and release gates
- Observed failing regression before changed production behavior, then passing targeted and integrated Python/JS/browser checks on exact generated assets.
- Browser fixtures prove input/render contracts; real native concurrent harmless model runs separately prove actual execution and persistence.
- Independent review of candidate and configuration-activation operator. Activation must retain live work/backlog, gate and prove idle before dedicated-listener restart, and verify/rollback on failure. Never restart WhatsApp gateway.
- Canonical/public assets and anonymous API rejection verified after guarded bridge deployment. Physical iPad/Safari touch and push remain device checks, not claims from Chromium tests.
- Test manager from source workspace is ported to live-baseline candidate because active release predates it; tests use private bounded scratch and cleanup.
