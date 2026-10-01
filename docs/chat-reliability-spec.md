# Conversation delivery/display reliability acceptance

Scope: full send/open/leave/reopen/reload/reconnect/finish lifecycle. Preserve real native history and durable journal; never replay a model action to repair display. Tests first, one defect at a time. Production still needs one-time scoped deployment setup; do not restart an active web run inline.

Root-cause predictions to test:
1. History reads only native DB while accepted input resides in app journal; reopening renders live tools but not journal input. Server-owned snapshot must expose accepted in-flight input/run without dependence on browser storage.
2. EventSource error sets a global notice with no successful-open/event clear path. Recovery should clear only its own warning, never unrelated action/run errors.
3. History plus saved local run pointer can render completed or partially persisted messages twice. A stable snapshot/anchor contract must give each turn one display owner, not global text deduplication.
4. Async submit/resume or queued old stream callbacks may act after route change and close a new chat's stream. Every callback must be scoped to its view/run; navigation invalidates its updates.
5. Dead or suspended streams can leave send locked indefinitely or reconnect after a terminal state. Read-only status reconciliation on errors, bounded backoff, focus/pageshow/visibility recovery and cleanup must recover terminal output without resubmission.

Acceptance inventory:
A. Reopen queued/running/approval turn: own accepted message appears exactly once before live assistant activity. Works after refresh, missing/disabled storage, and another browser with same account.
B. Reopen when native input/tools/output are partially or fully saved: no duplicate turn, tool replay or final answer. Repeated identical user prompts remain distinct messages.
C. Completion while away/opening: show final answer exactly once. Journal/native snapshot ordering covers reads crossing completion; older-page pagination does not duplicate overlay.
D. Interrupted then open/valid event: connection warning clears; unrelated failed-action or approval warnings remain. Tool success alone never finalizes run.
E. Network-only errors reconcile owned run status without POST; terminal-only output and unknown state become visible. Retry with bounded frequency; no permanent misleading warning after successful recovery. Expired auth returns to login; run 404 unlocks/removes stale pointer with honest notice.
F. Leave/reenter/switch chats/sign out during delayed history, run lookup, submit or status poll: stale callbacks never attach/close streams, clear another draft, change send controls, or reveal data in another view/account.
G. Double-click, form submit, retry after lost POST response and duplicate/replayed SSE IDs cannot create two actions or duplicate display. Preserve idempotency key until known outcome.
H. Terminal callbacks cannot regress completed state, duplicate answer, or append stale tools. Stop remains cooperative; approval/unknown states remain honest.
I. Fallback when EventSource unsupported and page wake-up after suspension uses read-only status recovery. Timers/listeners/streams clean up on navigation/destroy.
J. Real mobile browser journey verifies own-message persistence across reopen, warning clearing, long-tool/final viewport geometry, and no JS errors. Unit/ASGI suites cover deterministic races, scope/auth/privacy and failure paths. Physical Safari suspension still needs device confirmation.

Delivery: record observed RED/GREEN cases, full frozen Python/browser suite and independent review hashes. Distinguish implemented/tested from publicly deployed; retain previous final-stream/atomic-commit fixes.
