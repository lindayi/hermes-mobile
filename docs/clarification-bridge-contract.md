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
  clarification timeout. Stop and terminal run states release a pending waiter
  as cancelled; timeout releases it as expired. None is an approval or changes
  a permission decision.
- The authenticated mobile bridge binds each request and answer to the local
  owner, profile, session, run, and native run ID. It persists bounded request,
  answer, and lifecycle evidence in the run journal. Reopen/refresh reconciles
  with the native run snapshot; if the native process has lost its waiter, the
  bridge reports unknown/expired and never claims that the question can resume.
- Acknowledgement loss may be retried with the same question ID and exact answer,
  but cannot replace the first accepted answer or invoke a new user run. Unknown
  outcomes do not cause automatic retries. Question and answer text are rendered
  as plain text and remain in the session's chronological activity history.

## Acceptance cases

1. A synthetic API-server agent calls the pinned clarification tool on a worker
   thread; the run emits a pending question, remains blocked, receives an
   authenticated answer for that exact question, and returns the actual answer
   from the same tool call and run.
2. Single-select, multi-select, Other, and open-ended inputs require explicit
   submission; no option is preselected. Invalid, empty, excessive, or
   unrecognized answers are rejected.
3. Reopen/retry preserves a pending question and the first accepted answer;
   duplicate, stale, cross-run, cross-profile, and cross-owner requests cannot
   alter another waiter.
4. Timeout, Stop, terminal status, or native process loss leaves a truthful
   non-pending status and releases any live waiter without fabricating a tool
   result or starting another run.
5. Small touch layouts and keyboard interaction can reach every choice, Other,
   and Submit control. Existing drafts, Stop, parallel sessions, scroll position,
   and permission approval behavior are unchanged.

Native activation remains a separate guarded deployment step: this change does
not restart a listener, access a production runtime, or claim installed-host
compatibility or deployment approval.
