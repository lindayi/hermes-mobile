# Mobile clarification bridge contract

## Scope

The owner-only native controls listener supports the pinned Hermes runtime ABI:
`clarify_tool(question, choices=None, multi_select=False, callback=None)` calls
`callback(question, choices, multi_select=False)` synchronously and uses its
returned string or list as the tool answer. The API-server-created agent currently
has no callback, so the tool reports that clarification is unavailable. The
dedicated mobile listener supplies a callback only to agents bound to its native
run. CLI and messaging adapters remain unchanged. This contract deliberately
supports the pinned single-question ABI, not a newer batched-question schema.

## Transport and lifecycle

- The native callback creates a bounded, unpredictable question ID scoped to the
  existing native run, emits the actual question, choices, `multi_select` flag,
  and `pending` state on that run's SSE stream, then blocks the same tool call.
  In the composed owner listener, run-controls and maintenance state use one
  reentrant lifecycle lock so clarification and status publication cannot invert
  lock order or expose a torn maintenance snapshot.
- `GET /v1/runs/{run_id}` and the run event stream expose the bounded clarification
  records for rehydration. `POST /v1/runs/{run_id}/clarifications/{question_id}`
  is authenticated by the existing native API key and accepts one validated
  answer. A question accepts its first answer only; an identical retry returns
  the saved receipt, a changed retry conflicts, and stale or foreign IDs fail.
- Single-select answers are the exact offered choice or explicitly selected
  Other text. Multi-select answers are a list of exact offered choices with
  optional explicitly selected Other text. Open-ended answers are nonblank text.
  The native clarification tool remains responsible for stripping its
  presentation-only Recommended suffix from its returned answer.
- The callback has a finite timeout bounded by the runtime's configured
  clarification timeout. Native stopping/cancelled releases a pending waiter as
  cancelled; completed/failed and timeout release it as expired. A local Stop
  intent alone makes an unanswered mobile row unknown, not confirmed native
  cancellation. None is an approval or changes a permission decision.
- The authenticated mobile bridge binds each request and answer to the local
  owner, profile, session, run, and native run ID. It persists bounded request,
  answer, and lifecycle evidence in the run journal. Reopen/refresh reconciles
  with the native run snapshot. Recovery may restore an unanswered row to
  pending only when an authenticated snapshot confirms the same live waiter;
  failed or missing snapshots do not erase the last known state. If the native
  process has lost its waiter, the bridge reports unknown/expired and never
  claims that the question can resume.
- Acknowledgement loss may be retried with the same question ID and exact answer,
  but cannot replace the first accepted answer or invoke a new user run. Unknown
  outcomes do not cause automatic retries. Question and answer text are rendered
  as plain text and remain in the session's chronological activity history.

## Issue #77 reliability correction

The supervised correction baseline is PR #78 head
`a4ddbb9296b659afea161ab4cca239a81dfe9a34` over `main`
`f718cc3ec2f227184d2875a1e7c94047af54c574`. Scope is limited to safe Unicode
answer retries, exact native confirmation after an uncertain send, and tracking
an initially unknown run after authenticated clarification rehydration. It does
not change native activation, unrelated controls or orchestration behavior
outside this correction, or terminal unknown outcomes.

Snapshot reconciliation, clarification SSE updates, answer acknowledgements, and
terminal observations for one owned run are serialized by a lock bound to the
owner, profile, local run, and native run identity. The lock may span a bounded
native request, but no network wait is held inside a SQLite write transaction.
This prevents a stale snapshot or late answer acknowledgement from replacing a
newer question or run state; journal stop and terminal fences remain authoritative.

Acceptance cases: (1) A multi-select answer containing literal Unicode remains
idempotent when the answered native event arrives before the POST acknowledgement,
including rows written with legacy default JSON encoding; a changed answer or
Other flag still conflicts. (2) Recovery may promote an attempted answer from
unknown to answered only when the authenticated snapshot matches its owned
profile, local/native run, question, exact answer, and Other flag. The observation
time remains monotonic and no answer is resent; contradictory or unavailable
evidence leaves the attempt unknown. (3) A reopened unknown run waits for verified
clarification reconciliation before choosing terminal return versus live tracking.
The same-run answer can complete through the existing event/status path, while
navigation, account changes, teardown, unavailable snapshots, and terminal
unknown runs cannot be reopened or tracked as live.
(4) While a native GET is held, a newer clarification event remains pending and
answerable after a stale empty/running snapshot returns. (5) While an answer POST
acknowledgement is held, the next clarification remains pending and answerable,
the first accepted answer stays immutable, and the same native run continues
without duplicate answer or run dispatch. Overlapping snapshots serialize, and
stale waiting evidence cannot reopen a stopping or terminal run.

The bounded race follow-up starts from PR #78 head
`71dd092fcd55cbcba76b75e740933b6c70167316` over main
`f718cc3ec2f227184d2875a1e7c94047af54c574`. It retains the preceding correction
and only adds owned-run serialization for native snapshots, answer acknowledgements,
clarification events, terminal events, and refresh reconciliation.

The Stop/snapshot follow-up starts from head
`0bcc6cf5a40ec473294638e4b6536158c7754896` over the same main baseline.
An actual local Stop intent fences delayed pending snapshots and events inside
the short journal write transaction, without waiting for the clarification
network lock. Stored pending rows become unknown before Stop transport; attempted
answers and their possible native consumption remain intact. Reopen and replay
cannot revive an answerable row after Stop. A later positive matching native
answer receipt remains valid, and known native terminal mappings are unchanged.
Acceptance includes a held pending GET across successful or transport-uncertain
Stop, delayed pending events, replay/reopen, no stale answer POST or new run/tool
dispatch, immutable first answers, and independent other-session progress.

The bounded persistence follow-up starts from head
`a98a6fcd56f6a1bb9d8319789cc96f6cdb8a3fe7` over the same main baseline.
Repeated stop-fenced pending snapshots and SSE observations retain the saved
question timestamp and emit no duplicate clarification or run completion events,
even when the snapshot observation timestamp changes or the store is reopened.
They preserve the recorded Stop outcome; genuine native terminal evidence and
positive matching receipts for attempted answers still advance the saved state.

The bounded lifecycle follow-up starts from head
`097722a114f08808713bc455ccc368073ce69334` over the same main baseline.
A positively authenticated GET for the bound native run returning 404 preserves
question/attempt history as unknown and fences the owning local run as unknown
using the existing recovery semantics, never completion or resumption. Transport
timeouts, 5xx, and unavailable capabilities do not establish native loss.
The owner/profile/native identity, Stop intent, and actual terminal progress
remain authoritative. Repeated GET, reconciliation, store reopen, and fresh
Orchestrator replay must stabilize clarification/done events without redispatch.
The persisted definitive-loss reason also fences delayed pending SSE events and
prevents a later rehydration from reviving the lost waiter; ordinary restart
unknowns remain restorable only by an authenticated live snapshot.

The answer-rejection correction starts from PR #78 head
`45ba7773bf7988951eef6e3cdd3e6e66694fb1eb` over main
`f718cc3ec2f227184d2875a1e7c94047af54c574`. A definitive stale/conflicting
answer rejection reconciles the authenticated owned run and current question:
accepted, expired, cancelled, terminal, and replacement-question state is rendered
from that authoritative result, and a rejected draft never replaces an accepted
answer. If the same question is still pending and the run permits answers, retain
the draft and require an explicit retry. Capability failure proven before the
answer claim/dispatch uses the bounded `clarification_not_sent` response marker;
arbitrary 503 responses and post-dispatch acknowledgement loss remain unknown.
Late answer failures and reconciliation are fenced by route, owner, session, run,
question, Stop, terminal state, and newer clarification observations.

Run-state derivation under the existing clarification lock uses the aggregate
persisted question state, not the arriving event or incidental history order.
The newest creation time identifies the current question; ties are conservative.
Pending, sending, and unresolved unknown current questions prevent resumption
from an older answered/expired observation. Only resolution of all current
questions allows same-run continuation; the first accepted answer remains
immutable. Replayed older pending history cannot reopen a newer resolved waiter.
Acceptance covers authenticated route/event flows for late answered and expired
history, duplicate/reordered observations and snapshots, legitimate answers to
the newer waiter, fresh-store reconciliation, concurrent answer/event sequencing,
and independent other-session progress. Synthetic native loss tests also cover
transient uncertainty and Stop/terminal races without altering prior assertions.

## Acceptance cases

The three-seam correction starts from PR #78 head
`f2787f997a998803d36f91256a633b6ea85f868f` over main
`f718cc3ec2f227184d2875a1e7c94047af54c574`, preserving the independently accepted
native-loss fence. A clarification GET observing native running uses the existing
locked reconciliation path: restart-unknown runs with unresolved approval rows
cannot resume without authoritative empty pending approvals. Ordinary live-waiter
recovery remains supported.

HTTP answered and unknown acknowledgements retain the journal's authoritative
`updated_at`; older buffered sending frames cannot regress them, including when
the stream disconnects before its receipt. Same-question pending/sending/unknown
frames during an active submission do not replace its draft or invalidate its
rejection reconciliation. A verified still-pending reconciliation records a
timestamp fence without replacing the form, so buffered pre-reconciliation frames
cannot erase selected choices, Other text, or open-ended text. Retry requires an
explicit submission with the original question and answer/Other semantics.
Newer accepted answers, questions, terminal events, Stop, owner/session/run changes,
and navigation remain authoritative; a draft never replaces another accepted answer.
Synthetic generated-browser coverage relays actual bridge journal frames for both
acknowledgement states and before/after rejection reconciliation, alongside newer
question/terminal/accepted-answer observations.

The supervised native-seam correction starts from PR #78 head
`709ecdcea72b28b1314ee3cd9f6564cd33fd70dd`, retaining the accepted rejection UI,
Stop, first-answer, uncertainty, and replay behavior. Native answer conflicts now
carry `object: hermes.run.clarification`, the exact native run and question IDs,
`status: rejected`, and the bounded `clarification_conflict` or
`clarification_stale` error code. Only this positively bound HTTP 409 is a typed
dispatch rejection. Bare legacy errors, mismatched identities, answer-bearing
conflicts, network loss, and 5xx remain unresolved; they never authorize acceptance
or automatic resend. Rejection clears only that still-sending claim, preserves
its historical event, and reconciles the authenticated snapshot before returning
bridge 409. A different first accepted answer may then be restored without
overwriting it with the rejected draft; a replacement question remains current.

Reopen applies a validated bound approval, stopping, or terminal snapshot through
the existing reconciliation path while holding the owned clarification lock,
without reacquiring that lock or issuing a second GET. Terminal output is retained;
Stop, owner/profile/session/native-run fences and newer terminal authority remain
effective. Repeated snapshots and fresh-store replay do not repeat durable
lifecycle events. Unavailable transport remains unresolved.

The composed native adapter releases pending waiters before serializing an early
terminal queue frame: completed/failed expire them; cancelled cancels them.
The first terminal frame fences late status publication and duplicate frames.
Later status publication and callback cleanup cannot change the release reason,
release again, acknowledge a late answer, or resurrect a question. The bridge
can stop at that terminal frame with honest same-run clarification history.
These are supervised source repairs, not pristine autonomous workflow completion,
independent review acceptance, or native activation evidence.

1. A synthetic API-server agent calls the pinned clarification tool on a worker
   thread; the run emits a pending question, remains blocked, receives an
   authenticated answer for that exact question, and returns the actual answer
   from the same tool call and run.
2. Single-select, multi-select, Other, and open-ended inputs require explicit
   submission; no option is preselected. Invalid, empty, excessive, or
   unrecognized answers are rejected before claiming the waiter. A definitive
   validation rejection is explained inline and leaves the form and draft
   correctable.
3. Reopen/retry restores only an authenticated unchanged pending waiter and
   preserves the first accepted answer. Identical accepted retries return the
   receipt without another dispatch; changed, stale, cross-run, cross-profile,
   and cross-owner requests cannot alter another waiter.
4. Timeout, Stop, terminal status, or native process loss leaves a truthful
   non-pending status and releases any live waiter without fabricating a tool
   result or starting another run.
5. Small touch layouts and keyboard interaction can reach every choice, Other,
   and Submit control. Existing drafts, Stop, parallel sessions, scroll position,
   and permission approval behavior are unchanged.
6. A definitive answer conflict reconciles the first accepted answer or the
   current question; a still-pending question keeps its draft and only retries
   after an explicit user action. Verified pre-dispatch capability failure says
   the answer was not sent, while ambiguous post-dispatch outcomes remain
   nonretrying unknown.
7. Delayed answer failures and conflict reconciliation cannot overwrite newer
   question, Stop, terminal, route, owner, session, or run state.

Native activation remains a separate guarded deployment step: this change does
not restart a listener, access a production runtime, or claim installed-host
compatibility or deployment approval.
