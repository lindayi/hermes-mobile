# Session deletion and keyboard-aware layout acceptance

## User scope and safeguards

User requests deleting a session and adapting the mobile UI while the software keyboard is visible. Implement features, not deletion of any existing live conversation. All destructive testing uses disposable databases/transports. No memory/artifact/calendar/job/account/certificate changes.

## Session deletion

Chosen scope after native discovery: standard deletion of stored native session history, not permanent erasure of every copy. The confirmation warns to close other clients: an already-open CLI/gateway can later recreate native history. No core/global storage fencing or gateway reload is included. A content-free mobile tombstone blocks that identity from resurfacing through this app. Existing WhatsApp messages, previously delivered notifications, log/transcript files, independent memories/artifacts and backups are outside deletion scope and must not be promised erased. Refuse unsafe active/unknown/routed/lineage cases; never force deletion. See session-deletion-discovery.md for installed-contract limitations.

- A discoverable, accessible >=44px trash action in conversation history, separate from the row's open action and meaningful run status; never nested buttons. Do not crowd the compact conversation header.
- A named-session confirmation with Cancel/Escape default focus and explicitly destructive Delete action. State that the selected shared Hermes stored session/history is removed from shared Hermes storage, not merely hidden. It does not erase messages already sent in WhatsApp; close other clients first because an open client can recreate native history. Clearly state actual cascade/retention scope after native contract discovery. Memories, created files, independent conversations and backup copies are not deleted. Confirmed UI body requires literal confirm=true and exact session identity.
- Native-only/server runtime capability gate: unavailable or unverified deletion must not be silently presented as reliable. Existing backends remain compatible by omitting the new control until supported.
- Preserve current user/profile ownership, authentication and CSRF. Prohibit active, queued, stopping, awaiting-approval, unresolved or otherwise unsafe session deletion. Safety must hold against new admissions and native CLI/WhatsApp work racing a confirmation—not just a stale UI check.
- Read actual native deletion/lease/lineage contract before implementing a route. Catalogue read-only code must not become an ad-hoc destructive SQLite writer. Do not widen to arbitrary filesystem/profile paths or unconfirmed descendants. If safe native support needs a dedicated compatibility method, limit to that adapter and attest/version it; never bypass deployment protection.
- Account/view/session fences cover confirmation, pending request, successful/failed/ambiguous response and navigation. DELETE is sent once per confirmed attempt, never automatically retried after network ambiguity. Report unknown outcome honestly and refresh/read-check without resubmitting; success requires a verified response for the target.
- Successful deletion cannot reappear in this app through bridge journal overlays, stale local drafts/run hints/idempotency attempts, pagination/search, native recreation of the same ID, or an old Inbox link. Other sessions, drafts and user data remain unchanged. Preserve selected filter/query and repair last-page pagination after removal. Do not fake a successful removal on a failed backend response.
- Backend tests cover actual isolated route/native boundary plus admission/lease races, canonical lineage/alias cases, persistence/restart/retry, user/profile isolation, body/CSRF validation and truthful unsupported outcomes. Browser tests cover confirmation/cancel/keyboard, double click, pending/error/404/ambiguous responses, account/navigation fences and 320px geometry. Observe RED then GREEN before production changes.

## Keyboard-aware viewport

- Use actual visual viewport dimensions/offsets where supported, with a sensible window/CSS fallback; the existing interactive-widget meta alone does not establish iOS Safari behavior.
- Keep the composer, Send/guide action and top-right Expand reachable above the software keyboard. Adapt full editor and dialogs too; compact nonessential navigation/spacing when appropriate without hiding approval/security/Stop information. No horizontal overflow or page-jump/focus loop.
- Restore normal layout after dismissal, orientation change and page resume. Preserve draft text/caret, editor/dialog state, bottom-follow for an already-bottom reader and stable position for someone reading older messages.
- Do not confuse pinch zoom with keyboard opening; do not disable browser zoom. Test URL-bar motion, hardware keyboard/no viewport shrink, repeated transitions, missing VisualViewport support and cleanup.
- Deterministic viewport-state tests plus real Chromium geometry use isolated fixtures/generated assets. Physical iPhone/iPad keyboard/Safari validation remains manual; emulation is not proof of actual device behavior.

## Delivery

Update master spec in place. Review identified candidate hashes independently, run the full immutable generated-asset pipeline, deploy only the services actually required with active-run guards, and distinguish publication from later verification failure. No unmarked model/probe sessions.
