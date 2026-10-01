# Inbox read cleanup contract

Scope: notification receipts only, not conversations, native history, jobs or the separate pending-approval API/cards. No live cleanup or push tests; all verification uses disposable fixtures.

## API (under existing authenticated API prefix)

- `GET /inbox?limit=100&offset=0`: existing `items`, plus `read_count` counting **all** owned, non-dismissed read receipts, independent of page. Hidden receipts are excluded from pagination and this count.
- `POST /inbox/{id}/dismiss` with `{}`: authenticated user's read receipt only. Returns `{ok:true,dismissed:1}` on first dismissal and `{ok:true,dismissed:0}` on repeat. Missing/foreign ID returns 404 without leaking ownership; unread owned receipt returns 409. No passkey step-up.
- `POST /inbox/clear-read` with `{}`: atomically tombstones **all** owned read receipts, not just visible items; returns `{ok:true,dismissed:N}` (newly hidden count). Idempotent, includes older pages, never marks unread receipts read. No passkey step-up.
- Cleanup POSTs use existing `require_user` and `require_mutation` (Origin/CSRF) guards. Existing direct `POST /inbox/{id}/read` is unchanged; hidden IDs behave as missing there.
- `POST /inbox/read-all` with `{}`: marks all owned attention-visible notifications read, including older pages; returns `{ok:true,updated:N}` for newly read rows. Auth/Origin/CSRF plus bearer revalidation through commit; idempotent. Uses the same attention predicate as listing/count/clear-read, excludes background-category and same-owner background-receipt storage, and suppresses unclaimed pending/retry pushes in the same transaction. Does not dismiss records, decide approvals, replay delivery, or change background ACK state.

## Durability, migration and delivery

Additive `dismissed_inbox` table keyed by receipt ID, with owner and timestamp. Do not ALTER or delete Inbox rows; preserve positional legacy INSERT compatibility and ingest `(user_id,delivery_id)` deduplication. Redelivery cannot recreate a hidden receipt. Migration is idempotent. Code rollback needs no DB rewind: old code can use the schema but ignores tombstones (hidden receipts may reappear in old UI); preserve the table for roll-forward.

Cleanup suppresses pending/retry outbox work transactionally; the push claim also excludes tombstones to handle stale candidate snapshots. Already claimed/in-flight and sent deliveries cannot be recalled. A failed in-flight delivery must not be retried after dismissal. Do not claim external cancellation.

## UI

Notification bodies start folded behind native keyboard/touch-accessible summaries showing title, time and read state. Explicitly opening an unread summary marks that notification read; fetching, scrolling, programmatic toggles, body links and child actions do not. Per-item Mark as read buttons are removed. Successful single and top-level **Mark all as read** operations update rows in place, preserving disclosure, scroll and drafts; failed operations retain unread state and inline retries. Concurrent opening during a bulk request retains its own read intent; a confirmed bulk success dominates a late single-read failure. Duplicate in-flight requests for the same action are blocked.

Read receipt rows expose a separate **Dismiss** button outside the disclosure summary. **Clear read** remains separate from bulk reading, uses global `read_count`, and confirms that unread notifications, pending approvals and conversation history are preserved. Cancel does nothing; no cleanup passkey ceremony. Only successful cleanup reloads Inbox, and only while route/account still match. Stale success, error and confirmation must never mutate or navigate a new view/account.

Approval payloads also start folded, with visible NEEDS YOUR REVIEW, title and expiry. Decision buttons live inside the disclosure for explicit review. Reading notifications (individually or in bulk) never approves, denies or resolves these independent actions. **Review approval** opens its separate pending-action disclosure.

**Open conversation**, including from a notification deep link, selects Chats and reads exact authorized `/sessions/{id}` metadata for the real title. It never uses the notification heading, guesses via list search, creates a conversation or submits a message. Missing metadata stays truthful and retryable; stale title results cannot overwrite another route/account. See `chat-inbox-iteration.md` for current acceptance.

## Written acceptance cases (before implementation)

1. Real authenticated HTTP + temporary SQLite: dismissal survives reopen/redelivery, hides only owned read receipts, and retains underlying native session link/receipt.
2. Auth/CSRF/origin, foreign/missing/unread, idempotency and hidden mark-read boundaries.
3. Bulk >200 receipts, unread first page, global owned read count, preservation of unrelated data and legacy positional inserts.
4. Pending/retry push suppression, stale claim race and in-flight failure suppression using a fake network only.
5. DOM UI: row/bulk actions, confirmation/cancel, off-page count, busy/error handling, no step-up, separate approvals, stale action/navigation/account guards.
