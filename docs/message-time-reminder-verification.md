# Verification notes

## Development evidence

This task began before the Git migration. Changes were developed against the deployed baseline with isolated managed tests and byte-pinned independent backend/frontend reviews, then ported as a diff into a dedicated Git worktree. The migration's Git guard, workflow and public-source cleanup are preserved; private session records, screenshots of real conversations, local operational reports and deployment artifacts are not included here.

Observed red/green cases covered the duplicate journal/native turn, nested runtime-reminder rendering, null timestamps incorrectly interpreted as the epoch, scientific-notation fractional dates, original-admission contradictions, and repeated compression-timestamp query work. The real affected conversation was checked read-only in the private environment: one original question, one final answer and one separate runtime child, with unchanged stored text. Public regression fixtures use synthetic content only.

Before the Git port, 467 managed JavaScript tests passed. Backend review-fix adjacency passed 476 tests; a separate parent selection passed 201. These are historical source-level checks, not a claimed green status for a later Git commit.

## Release-gate findings retained

A pre-port frozen Python gate stopped with 3,732 passes and one failure in a mocked self-deploy command-recorder test. Its process-free fixture was observing unrelated host process-scan uncertainty. The fix isolates only the recorder's own scan and preserves real marker, lock, process-group and unrelated-workspace guards. A deterministic injected-uncertainty regression went red then green. The complete self-deploy/workspace selection subsequently passed 169 tests without weakening the real-process tests; two transient failures in those unchanged tests occurred on its first attempt.

A complete generated browser run passed 299 and failed two cases. One was an asynchronous fixture readiness race: document load was mistaken for session-list readiness. Explicit response latency reproduced the failure; awaiting the actual session button fixed it with all original assertions retained. The other was a genuine compact-header height regression introduced by timestamp flex wrapping. The correction scopes wrapping to headers with timestamps and lets timed receipt titles shrink beside their labels using the existing ellipsis. It preserves the unchanged compact-height assertion and adds timed, legacy-time, explicit-null-time and untimed header geometry coverage. Six focused generated-browser cases passed after the correction.

## Final gates

The PR records exact-head independent review, follow-up commits and managed integration evidence. Passing historical checks does not set the current commit status. Required GitHub checks must pass and the prerequisite source migration must be merged before this task merges into main. No private candidate deployment bypass is permitted. Physical Safari/iPad testing is distinct from generated Chromium verification.
