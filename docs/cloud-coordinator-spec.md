# Cloud coordinator specification

This issue implements a durable outbound coordinator for `lindayi/hermes-mobile`
(repository ID `1399942965`). It polls through the existing authenticated GitHub
CLI; it is not a webhook, a public command endpoint, a production Actions runner,
or a deployment controller. The coordinator never checks out, imports, or executes
pull-request code on the host. Code execution remains in GitHub Copilot's cloud
agent.

## Trust and enrollment

The CLI's default invocation is a read-only plan. A write cycle requires both
`--once` and `--apply`. Every cycle verifies the authenticated GitHub account is
owner ID `5164171` and the repository API identity matches the fixed repository
ID. It ignores issue bodies, labels, links, and comments from other authors.

An issue or pull request is enrolled by the exact comment `/hermes enroll` from
that owner. The issue must identify a pull request whose head and base both belong
to this repository and whose base branch is `main`. Existing pull requests are not
enrolled by their age, label, author, or open state. The explicit manual form
`/hermes enroll <40-lowercase-hex-head-sha>` also remains supported; the consumer
requires the value to match the current pull request head and stores it as
immutable `authorized_head`. The paired issue starter instead emits exactly
`/hermes enroll <40lowerhex> issue <N> body-sha256 <64lowerhex> source-task <task-id> source-session <session-id> source-command <start-comment-id>`
from its reserved head, originating issue, raw UTF-8 PR body digest and completed
source identity. `N` is canonical positive
decimal bounded by 2147483647; digests are lowercase and malformed extended forms
never downgrade to a manual prefix. SHA-bound commands require a valid timezone-aware
`created_at` and an identical explicit `updated_at`; an edited or unverifiable
comment is consumed without enrollment, even when its body names the current
head. Only a genuinely new immutable command can authorize that head. The legacy
bare command retains its historical timestamp compatibility.
A stale or mismatched command is consumed without enrollment.

Extended starter admission additionally requires a strict positive integer comment
ID and owner ID, an explicitly non-draft open same-repository PR, matching current
head/body, and exactly one canonical originating-issue closing edge. The shared
leaf `deploy/pull_handoff_binding.py` verifies complete bounded GraphQL pages
(20 pages, 100 nodes/page, 60000 body characters), anchored to REST repository
node identity and the same PR/head/ref/base/body snapshot, then rereads REST.
Description text and injected linkage flags never confer authority. Without the
authenticated API verifier, the extended parser cannot admit a PR.

After all prepared planning and lifecycle source reads, immediately before
`commit_scan`, every effective new extended admission/renewal repeats the unchanged
command identity/body/timestamp and live binding proof. A late mismatch or
incomplete read aborts the entire preparation: no cursor/events, enrollment,
receipts, retirement/export, or external write commits. Compact optional
`starter_admission` metadata is strictly validated on state load. Version 1
contains exactly `version`, `issue_number`, `head_sha`, `body_sha256`, `comment_id`,
and `comment_created_at`; version 2 adds the explicitly recorded
`source_task_id`/`source_session_id`; the current version 3 also records the exact
positive `start_comment_id`. The consumer persists a separately validated
`initial_source` record only after it authenticates the completed source task and
session, task artifacts, exact start command, unchanged source issue, edit/rename
history, ordered timestamps, PR identity/body/head and canonical closing edge.
Version-1 admissions are recoverable from an `initial_source` record already
saved with the enrollment, or the read-only bridge to the existing issue-starter
ledger. The default CLI, including the installed service's no-flag invocation,
reads `$XDG_STATE_HOME/hermes-mobile-issue-starter/state.json`, or
`~/.local/state/hermes-mobile-issue-starter/state.json` when XDG state home is
unset or empty, exactly like the starter. `--starter-state <path>` overrides
only that read-only input. That ledger must pass the starter's
bounded schema and owner-private regular-file/directory checks. Exactly one
`handed_off`/enrollment-`done` record must bind the admitted issue, PR number/node,
head, branch, base and body digest; the immutable enrollment comment must exceed
its saved pre-send comment high-water. The saved task and exact command ID are
then reauthenticated against live GitHub task/session, owner start command,
accepted title/body digest, complete edit/rename history and canonical closing
authority. Its saved task ID and immutable `source_session_id` must match the
fetched task/session. Historical records may instead use the authenticated
canonical-link reservation's saved `link_intent.session_id`; when both session
bindings exist they must both match. A record lacking either saved session
binding cannot recover authority merely because the task now has one session.
No ledger is written, no enrollment is replayed and no repair receipt
is manufactured. Missing, unreadable, ambiguous or changed saved evidence fails
closed. Version-2 admissions can be revalidated only through
their explicitly recorded task/session and a unique unedited owner start command.
The issue-edit GraphQL proof requires an explicitly present nullable
`lastEditedAt` field and a complete, bounded `userContentEdits` connection;
explicit null remains the no-last-edit value, while a missing field is incomplete
evidence. The consumer omits the first-page cursor from GraphQL variables rather
than letting its CLI adapter serialize Python `None` as a string; subsequent
pages use the returned cursor. Transport regressions exercise both real adapters.
Each page is folded through the starter's shared pure validator
(`deploy/issue_starter.py` `_fold_issue_edit_page`) under its 20-page/100-node
bounds: object `data`/repository/issue/connection/node/`pageInfo` shapes,
unchanged `lastEditedAt` across complete pages, null `lastEditedAt` only without
edit nodes, and an exact latest-edit node. Malformed, `errors`-bearing or
inconsistent history yields the provenance blocker rather than aborting the poll;
transport `ApiError` remains distinct. REST `renamed` events use the starter's same
rename-payload and timestamp contract.
The coordinator never selects a task from a task-list search to fill missing
provenance. A missing, edited, ambiguous, changed-head, unknown-task or incomplete
source fails closed with a deduplicated blocker and cannot dispatch first review
work. Ordinary and SHA-only manual enrollments without a supported source or
accepted review receive the same explicit provenance blocker; active source,
fixer and reviewer work and pending receipt/publication/corrective-review
progress, including an authenticated active Copilot dynamic
workflow on the same branch, does not generate that redundant outcome. Unknown
or unverified source state still receives the explicit provenance blocker.
Terminal same-head action history alone does not suppress that blocker.
Absence of starter metadata remains valid for supported identity-bound
manual enrollment; identity-less legacy active state remains fail-closed.
Enrollment `issue` still denotes the PR. Recovery preserves action claims, receipt
proofs and repair budgets; it does not invent provenance or reset ledgers.

The source chronology requires authenticated task/session creation timestamps
at or after the immutable start command. Timestamp ties are accepted as
nondecreasing chronology; they do not establish an order finer than GitHub
provides, while reversed timestamps are rejected. GitHub's session `completed_at` is optional:
when omitted, the immutable owner-authenticated completed-source enrollment
timestamp supplies a conservative owner-handoff-certified completion upper
bound, not a provider-reported exact completion time. Task and session must
still both be completed and uniquely authenticated; creation must precede that
bound. Explicit malformed or out-of-order completion timestamps are rejected,
not replaced. The bound is persisted unchanged on restart. Artifact selection
matches the producer: at most 20 entries, with exactly one GitHub branch and one
GitHub pull matching the current PR; unrelated artifacts confer no authority.
Malformed data on a relevant GitHub branch or pull artifact invalidates the whole
relevant inventory. Session IDs use the starter's `[A-Za-z0-9._:-]{1,256}`
contract at enrollment, admission, source resolution and downstream review/report
boundaries; 257-character IDs are rejected.
The pull artifact's optional `global_id` may be absent only because authenticated
PR detail supplies the bound node identity; explicit null or mismatch is rejected.
An already accepted exact-head independent review suppresses first-review
anchors and dispatch even without a local reviewer action.

This fence is admission-only. Later body reports or changed canonical linkage do
not revoke a durably admitted PR; PR45 receipt-result heads, restart/compaction,
manual renewal and existing repair budgets retain their original contracts.
Provenance does not replace immutable `authorized_head` or sensitive-head gates.
GitHub reads, durable local commit and remote mutations are not a single atomic
transaction. Acceptance records the final coherent admission snapshot; edits after
that read remain a remote race, not a claim of permanent body immutability.
Legacy SHA-only starter output cannot be distinguished from manual authority:
review in-flight old commands and deploy both sides together before activation.

For a sensitive head, the owner must separately publish a formal `COMMENTED`
independent-agent review, then comment
`/hermes authorize-sensitive <head-sha> review <positive-review-id> <body-sha256>`.
The selected complete authenticated review must be the latest owner review on that
exact head, have a unique positive ID and timezone-aware submission time, and contain
at most 4096 characters of JSON with exactly `schema`,
`reviewed_head_sha`, `review_method`, `verdict`, and `evidence_sha256`. The schema is
`hermes-independent-agent-review-v1`; the method is `independent-agent`, the verdict
is `pass`, and the evidence digest is lowercase SHA-256. The command's digest is over
the exact raw UTF-8 review body. The owner's publication is the trust assertion of
independent execution, not proof of cryptographic/model independence or a distinct
GitHub identity. Arbitrary prose/status, self-assessment, and another account do not
qualify. The previous head-only command remains recognized as no authorization.
Planning and fresh status/merge fences re-read and revalidate the selected review,
body digest, author, state, timestamp, ID uniqueness, and head. Removal, edit,
dismissal, a later negative owner review, or a head change invalidates authorization;
it is not blanket consent for future commits. Complete scan validation clears all
three persisted fields (`sensitive_sha`, `sensitive_authorization`, `targeted_review`)
atomically with commands, observations and lifecycle events before export. This
also invalidates readable head-only authorization without selected-review proof;
it does not migrate unsupported legacy enrollment identities. Plan mode only
projects the change. Incomplete collection, preparation or scan-commit failure
preserves prior durable state, export and consumer ACKs and performs no external
write. Revocation itself consumes no repair attempt and never authorizes merge.

This is a source/first-activation boundary, not an upgrade path for historical
coordinator state. Version-1 records remain readable for inspection, but active
legacy enrollments lacking `pull_id`, `pull_node_id`, or `repository_id` are
unsupported: pull snapshot collection fails closed with
`Unsupported legacy enrollment: missing pull/repository identity; automatic migration is not supported; preserve state and stop activation`.
The coordinator does not backfill legacy identity, reset cursors/budgets, or discard
unresolved claims. A newer exact-SHA command may renew an already identity-bound,
SHA-bound active enrollment: it preserves the initial `authorized_head`, receipt
proofs, unresolved claims and attempt budget, records the new owner-authorized head,
and clears sensitive authorization. It cannot migrate a legacy enrollment.
If such state exists, stop activation and preserve it for separately reviewed
operator recovery; do not delete/reset state or use re-enrollment as a migration.
First activation must use the current identity-bound enrollment contract and must
not proceed on unsupported legacy active enrollments. This change installs or
enables no coordinator units.

## Collection and bounded repair

The coordinator captures a precollection watermark, polls issue updates and their
comments with overlapping reads, and atomically persists accepted commands, their
IDs and the watermark. A terminal PR records any observed merged/closed lifecycle
event in durable state before retiring its enrollment. A terminal PR already closed
at the first observation is treated as historical baseline and is not exported;
reopening requires a fresh owner enrollment command. API errors,
rate limits, malformed pagination, and incomplete GraphQL review-thread pages
fail closed; malformed issue/comment rows or IDs also abort the whole scan. The
cursor is advanced only after a complete read.

Only current unresolved review-thread comments, body-only findings of the latest
authenticated exact-head Copilot review, and completed failed/timed-out
`Source checks` workflow runs are eligible repair evidence. It sends at most
eight findings in one combined budget (threads first, then body findings, with one
slot reserved for a workflow failure), clips each finding to 1,000 characters,
removes links, and never fetches check logs. Body findings are already decoded
plaintext: the coordinator preserves literal angle-bracket text such as
`<details>` and `<summary>` in repair evidence, with the same credential/link
redaction and character/item caps. Thread and PR-intent sanitization is unchanged.
A literal edit within the retained evidence changes the final request and its
deterministic marker, so the existing fresh-evidence fence suppresses stale dispatch.

Body-only findings are read by `deploy/review_evidence.py` from the complete
review collection. Every record, author, and present record ID is validated
before author filtering; a malformed, tied, pending, stale-head, or foreign-author
latest review yields no body evidence. Only a single latest Copilot review on the
current head with a positive record ID is read, and only when it is `COMMENTED` or
`CHANGES_REQUESTED`. Review list and detail GETs use
`Accept: application/vnd.github.full+json`; the real adapter retains complete
pagination and the same authenticated record's raw `body`, rendered `body_html`,
author, head and submission time. Missing, empty, malformed or oversized rendered
content blocks body dispatch; there is no raw-Markdown fallback.
From a `ccr-overview-v2` body, each item of a
`Previously missed (N)` section is forwarded (including under a `Findings: None`
headline). One stdlib HTML parser builds bounded disclosure structure, respecting
nested markup, quoted attributes (including `>`), case, and closing whitespace.
Only GitHub-rendered HTML is parsed. The exact raw marker
`<!-- ccr-overview-v2 -->` must be the first complete line (no indentation or
trailing text), solely as format identity; GitHub omits this comment from rendered
HTML. Rendered comments never establish format identity or approval. Overview
section classification runs only for canonically marked raw bodies, including
when the state is `CHANGES_REQUESTED`. An unmarked disclosure never substitutes
for the separate bare explicit-correction path; its labels also cannot suppress
an independently eligible bare correction outside disclosures.
HTML `code`, `pre`, and `blockquote` subtrees are inert for disclosure scope,
section labels and summary actionability. Literal decoded text remains context
inside independently genuine findings, including nested quotation, horizontal
rules and validation sentences. Entity callbacks append decoded text directly:
escaped disclosure/overview text is never reparsed as markup. Incomplete raw
markup and unclosed/crossed elements fail closed. No Markdown engine or lexical
shielding is used. Ordinary rendered list corrections and code identifiers in
genuine correction context remain supported. Overview framing removes only known
metadata labels/values, never an entire paragraph containing subsequent correction
text; quoted context is not framing.
All disclosure traversal is iterative. There are no repeated HTML-removal passes. Limits are
64 Ki characters, 4,096 parser events, 32 nested elements and 256 disclosures;
exceeding any limit or unclosed/crossed markup yields `ambiguous`, not a truncated
repair request. An unknown but balanced section is locally ambiguous: it never
becomes fixer evidence and never erases a separately bounded active section.

The helper explicitly classifies `active`, `resolved`, `history`, `validation-only`,
`no-findings`, `inline`, and `ambiguous` content. Resolved/history ancestors exclude
their entire subtree, even a nested active-looking label; they never leak into
summary evidence. Structurally intact, explicitly labelled `Previously missed`
sections may still be forwarded whole when the count or child-item shape cannot
be proven (including unknown counts), rather than dropping known active evidence.
This fallback requires live nonhistorical content beyond the outer summary and
the known `In code that hasn't changed since last review` introduction. Empty,
intro-only and history-only sections never emit their label as a finding, even
with a positive or unknown count; they consume no repair attempt and confer no
approval. Populated fallback sections and independently genuine findings remain
eligible under the existing review gates. Count-matched items use the same live
content predicate as fallback sections: each item must contain nonempty unquoted,
nonhistorical text beyond the known introduction before its literal context is
retained. Quoted-only matched items are omitted individually, without dropping a
genuine sibling or its quoted context. A matched section with no eligible items
is `no-findings`, not a repair request. Both intro comparisons normalize whitespace,
including rendered line breaks. Matched-item and fallback live eligibility reuse
the existing complete-negative/status-only grammar after excluding the known intro,
preserving sentence/block boundaries even when status sentences lack punctuation.
For this eligibility classification only, ordinary unquoted text-node HTML whitespace
collapses to spaces; source soft newlines are not sentence boundaries. Inserted block
and `br` boundaries remain separate, and forwarded plain/quoted context stays literal.
Neutral-only content cannot supply a finding; genuine corrections, independent
siblings and their literal context remain eligible.
Unsupported top-level disclosures remain ambiguous, not implicitly resolved.

Validation-only requires complete recognized status prose (optionally accompanied
by no-code-issues sentences), not the presence of `pending` anywhere. Examples
include `The exact-head verification remains pending.` and `Exact-head verification
is still pending.`. Separate required corrections survive validation status or a
`Looks good`/`Findings: None` headline; explicit requests such as
`Required correction: reject pending receipts.` remain active.
Mixed summaries exclude only complete validation-status
sentences, retaining the correction. Complete negative verdict sentences such as
`No bugs found.` and `No code defects were found in the reviewed changes.` are
recognized before correction classification, including beside validation status.
The bounded grammar permits issues, bugs, defects, problems, vulnerabilities or
findings, optional `code`/`were`, and found/identified/detected verdicts. Optional
`in` scopes are finite code/change/diff/patch/implementation phrases, not arbitrary
trailing prose that could hide a correction. In mixed summaries the recognized
negative sentence remains quoted context but cannot itself supply affirmative
defect evidence; separate or compound required corrections remain actionable.
Both bare `CHANGES_REQUESTED` prose and overview summaries require a nonempty
explicit `Required correction:`, `Required change:`, `Requested correction:` or
`Requested change:` clause at a sentence/line boundary or after `, but`/`, however`.
Generic modal/bug/defect words, formatting and file scopes do not establish a
correction. This deliberately narrows the prior broad-English heuristic: even a
bare requested-changes verdict without explicit correction evidence stays
ambiguous/non-dispatching, never approved. Negative or status-only prose does not
consume a fixer attempt; separate explicit corrections remain eligible.
Positive `Open (N)` counts suppress
summary duplication because those findings are already represented by threads;
explicit `Previously missed` sections still forward body-only items.
`Open (0)` never suppresses an actionable summary. COMMENTED summaries alone do
not authorize repairs; explicitly active disclosures do.

`body_findings` retains its caller interface. Dispositions remain in the helper's
internal result; no new review-authority protocol or coordinator lifecycle path
is introduced. The formal technical review gate uses the current exact-head owner-published
structured independent-agent COMMENT review, not Copilot APPROVED state. Ambiguous
body text is never passed blindly to a fixer; independent authenticated
thread/check evidence remains eligible. Each body finding records the genuine review ID, head SHA, and
submission time; it never carries a thread ID. Body text is untrusted evidence:
it is never approval, and approval is never inferred from prose.

This scoped issue #39/PR #40 architectural revision starts at
`b1cd397d4378c7300a5de2f527bbe4f3eb69da8d` and addresses review `5392186494`.
Acceptance includes real RED/GREEN entity-text, formatted-negative and modal-only
regressions using public synthetic raw/rendered API pairs, full-media paginated
adapter and missing-rendered negatives,
structural classification/bounds cases, active body-only forwarding, ambiguity
without repair/approval, and the existing fresh-evidence dispatch fence. Candidate
helper pins are refreshed without clearing the unconditional source-contract hold.
No activation, protection, lifecycle redesign, or production change is included.

The bounded follow-up at `0f34afa7f6aeead6af73016464d342099d86b991` addresses
review `5392482450`, comments `4166255676` and `4166255756`, only: decoded literal
preservation through the request/marker/dispatch fence, and nonempty live fallback
content. Finite RED/GREEN cases cover literal edits and sanitization plus empty,
intro-only, resolved/history-only and populated sections with counts 1, 0 and
unknown. Only the helper's pending digest and independent fixture are refreshed;
the coordinator baseline, other pins and unconditional source-contract hold stay
unchanged.

The final bounded structural follow-up at
`b27fa36e17c25df02232324e49d1e5e15dc0bac0` addresses review `5392668833` only:
canonical raw-marker gating of overview sections and live-content eligibility
before forwarding matched items. Finite managed RED/GREEN cases cover both
review states, missing/noncanonical and canonical LF/CRLF raw markers, bare
explicit corrections, quoted-only code/pre/blockquote items at matched and
unknown counts, and mixed genuine/quoted items. No-dispatch cases consume no
attempt or approval; independent marked findings and genuine literal context
remain eligible. Only the helper candidate digest and independent fixture change;
the coordinator baseline, other pins and unconditional source-contract hold stay
unchanged. This is not activation or full integration evidence.

The bounded follow-up at `c8bb08d0c47ac1d49a33f0c2cc962ebb9be2d641` addresses
review `5392848133`, comment `4166553619`, only: whitespace-equivalent introductions
and complete negative/status-only matched-item and fallback eligibility. Finite
managed RED/GREEN regressions cover newline/`br` intros, negative/status mixtures,
unpunctuated status blocks, no dispatch/attempt/approval, and preservation of real
findings, independent siblings and quoted literal context. No new language grammar,
Markdown architecture or authority is added. Only the helper candidate digest and
independent fixture are refreshed; other pins, the coordinator baseline and the
unconditional source-contract hold remain unchanged. No activation or full
integration evidence is claimed.

The second same-class status-normalization follow-up at
`aa3ee4e45d03b24024567ead485c0da9788d41c3` addresses review `5393153136`, comment
`4166809900`, only: soft text-node newlines in `No bugs\nfound.` and
`Exact-head verification remains\npending.` must not create matched or fallback
findings. Finite managed RED/GREEN cases cover both review states, matched and
zero/unknown-count fallback shapes, no dispatch/attempt/approval, genuine mixed
corrections and siblings, and exact plain/code/pre/blockquote context. Existing
unpunctuated block-boundary behavior and section-label quote detection are unchanged.
Only the helper candidate digest and independent fixture are refreshed; retention
main, other pins and the unconditional source-contract hold remain unchanged.
No broader grammar, parser architecture, activation or full integration is included.

The Copilot request labels all embedded evidence untrusted,
and the text is never interpreted as shell input. A deterministic marker
deduplicates a request. At most three requests are claimed per enrollment.
Draft PRs and ordinary pending
review/check/task activity do not dispatch repairs or create owner notices.
Budget exhaustion is reported only for currently scoped, non-draft, idle work
that would otherwise be eligible for a bounded repair or neutral reconciliation.

Behind or genuinely conflicted enrolled PRs receive a neutral task through the same
durable reservation, exact task-ID reconciliation, serialization lock, and three
attempt budget as ordinary repair tasks. Its bounded prompt identifies both exact
branch SHAs and the PR's sanitized title/description as untrusted intent; the task
must preserve both sides, merge main into the PR branch (never rebase or force-push),
and test the combined behavior. Before returning a `ready` receipt, the prompt
requires a PR comment recording each conflict hunk's file/hunk identity,
classification, decision, and rationale explaining preservation of both branch
intents (or genuine incompatibility). This is a deliverable under the existing
task receipt and independent-review contract, not a new receipt schema or an
automatic semantic approval. A current-main fence is rechecked before dispatch.
The prompt directs genuinely incompatible requirements or broken required policy
to a fixed typed result; these stop further repair for that exact head. Waiting or
uncertain tasks retain the shared lock. Ordinary technical conflicts are repaired
within the shared budget; exhaustion or ambiguous execution becomes a meaningful
owner blocker rather than an unbounded retry.

A durable action claim is written before POSTing a task through
`/agents/repos/{owner}/{repo}/tasks` with the bounded prompt, `base_ref=main`
and the enrolled PR's current same-repository `head_ref`. The returned task ID
is persisted and GET by ID reconciles its state and branch/PR evidence across
head changes. An ambiguous POST (including a crash before ID persistence) is
never resent automatically; it blocks further dispatch for that PR. Unresolved
fixer claims survive PR closure, reopening, and fresh owner enrollment, even when
the repository task list is empty. Their attempt counter is retained as well so
attempt-derived request keys cannot collide; re-enrollment resets that counter
only when no unresolved fixer claim remains. A fresh enrollment is authorization,
not evidence that an earlier task stopped. No second
`@copilot` dispatch comment is posted. Queued, in-progress, waiting-for-user,
idle and unknown task states do not release the fixer lock. For SHA-bound starter
enrollments, a completed coordinator-dispatched task releases the lock only with
the exact task/session/nonce receipt defined in `deploy/task_receipts.py`. Its
unchanged, authenticated `ready` receipt may extend authorization from a previously
authorized head to that exact result head. The initial `authorized_head` never
changes; each later result head needs its own receipt rooted in an already
authorized head. Missing, edited, copied, stale, mismatched, or blocker receipts
do not authorize continuation. Sensitive authorization remains bound to its
original SHA, and review/check evidence must be independently fresh for each result
head. Validated task/session/nonce and unchanged-comment proof is persisted with
completion in the enrollment before lifecycle compaction can retire its action.
Restart and final dispatch revalidate the retained proof against the remote comment.
Deferred ready/review handoffs recheck it after the scan commit; a new observed bound
head clears stale sensitive authorization. A typed blocker remains `task-result-blocked`,
not an unrelated push. A receipt is not review or CI success. Bare manual enrollments
retain PR33 lifecycle and receipt handoff behavior.

If main advances while a completed SHA-bound `ready` receipt is awaiting its review
handoff, including a handoff blocked only by its bounded review-wait limit, a stale
PR base may be used only for the bounded neutral reconciliation path. The live PR
must still be the enrolled same-repository pull request on `main`,
with `mergeable: true`, `mergeable_state: behind`, and the exact unchanged receipt
result head. The coordinator reads the authenticated compare endpoint for the
recorded base against both current main and that result head. It requires the real
compare fields `status`, `ahead_by`, `behind_by`, `base_commit.sha`, and
`merge_base_commit.sha`, with the recorded base as both base and merge base,
non-boolean nonnegative counts, zero `behind_by`, and `ahead` (or `identical` only
for equal input SHAs); `ahead` requires unequal input SHAs. Missing, malformed,
unknown, diverged, or inconsistent compare evidence fails closed. Before releasing
the old handoff lock, the coordinator re-fetches the exact task by its durable ID
and revalidates its terminal state, identity, session, nonce, and complete unchanged
remote receipt. The recorded dispatch base and immutable initial authorization are
never rewritten. Only a positively authorized result head can use this exception,
and the neutral task consumes the existing combined three-attempt budget. Once that
neutral task is positively accepted, its task ID and the superseded predecessor
handoff are committed in one StateStore mutation. An ambiguous neutral POST instead
leaves an uncertain claim as the lock and atomically supersedes the predecessor.
Restart recovery resolves pending sending/uncertain ownership before advancing any
predecessor, irrespective of action iteration order, then reads the updated prepared
state rather than replaying stale scan records. This also holds when the accepted
remote task advanced the PR head before its acceptance metadata was persisted:
no predecessor wait is charged and neither claim nor predecessor can trigger a
duplicate POST or a false `execution_exhausted` lifecycle event. Superseded completed
handoffs are terminal for retirement,
while their validated receipt proofs remain in the enrollment across compaction and
re-enrollment. Replay does not dispatch an accepted task twice. Historical-base
scope is not current-main eligibility: stale PRs receive no status action or merge
eligibility, and exact current-main, fresh review/check, status, and merge fences
remain mandatory after reconciliation.

Polling a claimed task requires positive integer creator, owner, and repository
identities matching the fixed owner/repository before any terminal failure can
release the lock or emit `task_failed`. Missing or malformed identity evidence
retains the sent claim, then reaches bounded `execution_uncertain`; it never
blindly starts another task. Task and session IDs remain opaque strings.
Live REST pull numbers, pull IDs, and head/base repository IDs must be positive
integers at collection and every dispatch, ready/review handoff, and merge fence;
booleans, floats, strings, and missing values are not identity proof.
Unidentifiable nonterminal repository tasks conservatively block new dispatch
until GitHub exposes enough branch, session, or PR evidence to scope them.
Only a positively pre-send superseded reservation can advance to a distinct
bounded attempt on unchanged head/base; sent or uncertain reservations are never
retried. Only confirmed `dirty` or `behind` pull requests receive the neutral
reconciliation task described above, not an ordinary repair task. Uncomputed or
unknown mergeability (including `mergeable: null`, missing fields, or a negative
mergeability result without an explicit `dirty`/`behind` state) defers both task
kinds. It claims/posts no task, consumes no attempt, and produces no budget
exhaustion notice or lifecycle event. Planning and both dispatch fences enforce
this boundary; a dispatch-time deferral clears `repair_requested` and reports
`mergeability-unknown` rather than a stale conflict/behind reason. A later poll
may resume ordinary repair or confirmed neutral reconciliation once computed.
Immediately before claiming a repair, the coordinator re-reads its bounded
thread/review/check evidence and fences the planned head, branch, main SHA, base binding,
mergeability and active tasks. Changed or incomplete evidence suppresses that
planned request without consuming an attempt; it does not substitute another
repair or fall back to auto-merge in the same cycle.

### Lifetime repair budget and verified progress

Each enrolled pull request has a hard lifetime ceiling of 20 source-repair
reservations. A reservation consumes one attempt before the task API POST; failed
tasks and ambiguous POST outcomes remain consumed and are never replayed. The
cumulative count, unresolved reservation, authorization, accepted source heads,
and receipt proofs survive restart, head/base changes, main advances, close/reopen,
duplicate events, and later owner enrollment commands. A new job or enrollment
cannot reset the count. Independent review, its separately bounded report
correction, polling, CI/review waiting, and infrastructure backoff do not consume
source-repair attempts. Neutral main reconciliation has its own three-reservation
bound and does not consume the source-repair budget.

Three consecutive completed source repairs without verified forward progress stop
dispatch before the lifetime ceiling. Progress is decided only once for a
coordinator-reserved source task after the authenticated task is terminal and its
unchanged ready receipt binds the task, session, starting head, result head, PR and
dispatch base. The result head must be the current authorized PR head; the recorded
dispatch base must still match the receipt, but need not remain current after main
advances. The complete current GitHub collections must also prove successful
required checks and a complete review-conversation inventory. A positive exact-head review can
confirm progress as before. A complete authenticated, owner-published
`changes_requested` report for the current head can also prove which tracked
independent-review findings remain or were resolved; the negative disposition is
progress evidence only and never grants review approval, merge eligibility, or
deployment authority. Task state, agent prose, a
changed SHA, code churn, unrelated green checks, elapsed time, changed issue text,
cosmetic wording, or new review IDs alone cannot count as progress.

Evidence collection is independent of dispatch caps and usable repair prose.
An unresolved thread with missing comments is not an empty inventory. Historical
main advancement does not invalidate a bound source receipt or its exact-head
completed review/check evaluation; it still cannot authorize current-main dispatch.
Terminal source tasks await a verified evaluation before another source reservation.
Pre-upgrade unscored receipts remain explicitly unknown rather than blocking forever.
Legacy shared counts are split only after authenticating every unique retained
task/session, prompt, and receipt chain, without inventing ordinals or task types.
Fully observed terminal failed verification allows only a nonprogress decision.
Completed required check runs with `failure`, `cancelled`, `timed_out`,
`action_required`, `neutral`, `skipped`, `stale`, or `startup_failure` are terminal
observations, not successful verification. They permit a no-progress evaluation
when the exact-head task and independent review are complete, but never resolution
credit or merge eligibility. Pending, null, unknown or malformed conclusions,
wrong app/head bindings and incomplete pagination keep evaluation pending.
A bound negative report supplies the review-stage disposition, not a successful
`agent-review` check; all non-review checks must be observed before scoring.
An incomplete or older-encoding reservation baseline receives an explicitly
unknown decision only after fresh complete review/check evidence; it cannot clear
targets or alter the streak. Neutral advancement can leave an older source result
unscored; this remains explicit unknown history, not a current-head pending lock.

At reservation, the coordinator records at most 32 bounded fingerprints of the
actual eligible repair targets: stable review-thread identity, normalized body
finding, or source-workflow failure class. The persisted resolved-history bound
is 640 fingerprints per enrollment (20 reservations at that per-task limit), so
the progress ledger and retained receipt proofs stay bounded within coordinator
state capacity. A result is progress only when at least one previous target is absent from the
complete current-head evidence and independently verified as resolved. Negative
reports may include optional `progress_disposition` with exactly `version: 1` and
`resolved`: up to 32 unique lowercase SHA-256 target fingerprints from the saved
source reservation. The reviewer receives that inventory and source evidence,
must inspect the exact source delta, and must not infer resolution from wording,
new IDs, or a moved finding. The authenticated report envelope binds this assertion
to source receipt/session/head/base; the owner publication digest includes it.
Absent disposition on a negative report earns no resolution credit. Positive
independent review retains its existing resolution semantics. Finding text uses
one canonical namespace across body sections and independent paths; thread IDs
and workflow failure classes retain their stable namespaces. All independently
observed cleared targets enter history even when regression withholds credit.
The ready receipt's original dispatch base remains
bound as historical provenance even after main advances; it is not replaced with
the current base or treated as a fresh eligibility requirement. A previously
cleared fingerprint that reappears is a regression and prevents progress credit;
thus alternating A-to-B-to-A findings cannot repeatedly reset the streak.
Fingerprints ignore review IDs and cosmetic case, whitespace, and punctuation
changes. Encoding version 2 is persisted on reservations, proofs and progress.
Unversioned hashes/history remain retained but are never treated as cleared by
canonical version-2 observations. Incomparable resolved history cannot grant
progress credit; lifetime counts and prior stagnation remain unchanged.
The rendered-body parser reports the total extracted finding count, truncation,
and inventory completeness separately from its bounded repair findings. A capped,
malformed, ambiguous, or count-inconsistent inventory is unknown, even with a
positive owner review and green checks; reordering cannot prove resolution.
Later complete trustworthy evidence resumes evaluation without spending an
additional source attempt while evidence is pending.

New source reservations also persist a hash-to-canonical-target map derived only
from the exact serialized repair targets, not reconstructed from task prose.
Each entry has exactly `kind` (`finding`, `thread`, or `source-failure`) and
`target` (the normalized identity text), whose hash must equal its key. The map
is limited to 32 entries, 1,000 characters per target, and 16,000 ASCII-serialized
bytes. Clipped or redacted targets that cannot retain their original identity,
and omitted targets beyond those bounds, are explicitly unresolvable.
Normal and report-correction reviewer prompts carry the same saved map.
Nonempty resolution claims must select mapped hashes, with the map and target
inventory authenticated against the original ready receipt's reservation or
retained proof. Swapped targets, missing maps on legacy reservations, and guessed
associations grant no resolution credit and never reset history. The optional
version-1 report envelope remains compatible; an omitted or empty disposition
requires no map and grants no negative-review resolution credit.
If task, receipt, original dispatch-base binding, CI, review evidence,
pagination, or target identity is absent, malformed, partial, stale, or otherwise
unverified, the attempt remains unevaluated: it neither earns progress nor
increments the no-progress streak. Three positively observed no-progress decisions
stop further source repairs. The 20-reservation ceiling remains the ultimate bound; this
bounded text/identity heuristic cannot prove semantic equivalence of arbitrary
paraphrases.

Existing records retain their cumulative attempts and receipt proofs. Missing
pre-upgrade progress history is explicitly unknown; it is not converted to
zero-progress or retroactively scored. A valid retained ready-receipt chain may
continue to provide current source provenance and prove an authenticated
historical-base handoff after its completed action is compacted, but cannot by
itself establish a historical no-progress streak or replace fresh checks and
independent review. Source acceptance remains distinct from merge approval and
deployment.

Legacy records that predate the separate neutral counter retain a conservative
unknown marker until every shared reservation is accounted for by retained task
claims or authenticated receipt proofs. Reservation type comes from the exact
controller prompt of the uniquely authenticated terminal task/session (`completed`,
`failed`, `timed_out`, or `cancelled`), not saved
`task_type` or policy-version labels; this applies to both retained v1 and v2
receipts. Ordinal-less reservations still require an authenticated retained receipt
with its session chronology and nonce-bound instruction; failure cannot manufacture
a missing receipt. A receipt alone never implies a source reservation. Missing, conflicting,
or incomplete history keeps both repair paths unavailable rather than granting a
new budget. The shared count is split atomically into source and neutral
reservations without resetting either budget, starter provenance, or receipt
history. New
`execution_exhausted` lifecycle events carry a closed typed stop cause and exact
used/remaining counts for the source ceiling, no-progress streak, neutral
ceiling, or review-handoff wait. Older events without this optional detail remain
readable and preserve their original PR identity. New typed stop events use a
separate identity when they project a linked issue; the Inbox fallback for events
without typed detail explicitly says the exact cause and counts are unavailable.

The 20-attempt ceiling is grounded in a small, nonrandom sample of merged cloud
pull requests, not a percentile or guarantee. The audit used authenticated
repository task lists, task-to-PR artifact IDs, and actual dispatch/session
prompts, counted each dispatched source correction once, and excluded initial
implementation, independent review, report-only correction/evidence, and
transport-only tasks. The observed source-repair counts were PR82: 0, PR76: 0,
PR74: 1, PR70: 2, PR72: 4, PR84: 8 (including one failed repair task), PR80: 14,
and PR78: 14 plus one separately excluded neutral integration. PR84's eight
verified repair task IDs were c0b66de0, 42b11cbe, 002f40ba, c8cdae09, e23eb1f6,
fa8ec9e1, c4d04ca9, and 1385eae4. PR80's 14 repair tasks were interleaved with
14 independent reviews; PR78's neutral integration task was 7cd68547. The sample
supports headroom beyond the former three-attempt cap but cannot predict future
workloads.

## Review, checks, and merge

Technical review acceptance requires the latest authenticated owner-published
structured independent-agent formal COMMENT review on the exact current head.
The existing `hermes-independent-agent-review-v1` contract requires a positive
`pass` verdict and a bound lowercase evidence SHA-256. The selected record's ID and
raw-body SHA-256 are checked against the current complete review collection using
`selected_independent_agent_review`; edited, removed, stale, malformed, or
superseded evidence cannot reuse an earlier review. Every review record must be an
object with positive integer user and review IDs (not booleans or strings), and
review IDs must be unique. These are validated across the complete collection
before author filtering. All authenticated reviews must have valid timezone-aware
submission times. Missing, malformed, or naive timestamps fail closed; this
includes an unsubmitted `PENDING` review, for which GitHub omits `submitted_at`.
Times are compared as instants, not strings; tied latest owner reviews fail closed.
Because REST `application/vnd.github.full+json` review payloads do not reliably
expose edit metadata, the authenticated transport also re-reads the exact REST
review's GraphQL `PullRequestReview` node and binds node ID, database ID,
repository, pull number, head commit, author login, body, submission time,
`updatedAt`, `lastEditedAt`, and `includesCreatedEdit` before treating the review
as an unedited positive proof. Missing GraphQL metadata, mismatches, or any edit
signal fail closed.
Review pagination must be complete and every thread resolved, with complete thread
pagination. A status, Copilot review, overview, or arbitrary comment is not
independent review. Copilot feedback is supplemental; COMMENTED or missing APPROVED
alone does not block acceptance, trigger repeated review requests, or consume fixer
budget. Actual open findings must still be resolved, and a definite rejection is
not acceptance.
Task receipt handoff and review acceptance are separate. After the exact result
head, repository, base and authorization fences pass, the coordinator completes
the source-writing task handoff only when the existing independent-review gate
accepts a current owner-published review on that head, with complete review and
thread collection and all threads resolved. Otherwise the task stays in
`waiting_review`, retaining its lock without consuming fixer or handoff-wait
budgets. Unresolved findings and definite rejection are not treated as acceptance.
The coordinator never requests or waits for an advisory Copilot review. Completing
a handoff is not merge evidence: merge still requires the valid independent-agent
review, resolved threads, required checks and every other existing guard.

The existing task API exposes task identity, GitHub pull/branch artifacts and
authenticated session metadata. After a source task reaches a verified `ready`
receipt, the coordinator publishes one owner-authored anchor issue comment for
that exact head and dispatches one distinct read-only reviewer task on the same
frozen PR head/base. The reviewer must reply to the saved anchor with
`engine-tools-reply_to_comment`; the observed transport prepends a quoted
blockquote excerpt of the anchor, a blank separator line, and then one compact
JSON `hermes-independent-review-report-v1` object outside the quote. The report
binds the saved nonce, reviewer session, repository, PR, reviewed head/base,
source start head, source session, source receipt comment, bounded verdict,
bounded findings, reviewed file hashes, and bounded narrative report. The child
does not echo its review-task UUID; the parent authenticates the saved task ID
and session through the task API.

Normal first-review anchor markers and outbox keys bind the current-main SHA.
If main advances before reviewer dispatch, a distinct anchor may be reserved
without rewriting or replaying the old sent or uncertain anchor, including legacy
anchors whose identities omitted main. Dispatch still requires the admitted exact
head and the existing current-main/base fences. A sent or uncertain reviewer
creation remains occupied across a base change and is never replayed; an existing
normal report retains its saved base binding and exact-current-head acceptance
semantics. Corrective anchor identities and their separate retry budget are unchanged.

The parser accepts only that bounded quoted-reply envelope, with a limit of
65,536 UTF-8 bytes and 64 LF-delimited lines. The byte budget accommodates the
declared maximum inventory of 64 paths of up to 200 characters plus the quoted
anchor and compact report. Edited, oversized,
ambiguous, foreign, stale, wrong-session, wrong-binding, or duplicate reports
fail closed, and a report inside the quote is never evidence. A verified report
triggers one owner-published formal COMMENT review on the exact head using the
existing `hermes-independent-agent-review-v1` body schema, with its
`evidence_sha256` bound to the canonical compact report JSON. Positive reports
produce `verdict:"pass"`; bounded findings produce `verdict:"changes_requested"`
and feed one bounded fixer follow-up for that exact head. The coordinator never
infers review acceptance from task completion, a digest alone, a Copilot review
request, or a status, and never invents a task-result API field.

The task prompt is generated from this exact report contract. Top-level keys are
`schema`, `nonce`, `session_id`, `repository`, `repository_id`, `pr`,
`anchor_comment_id`, `role`, `head`, `base`, `source_start_head`,
`source_session_id`, `source_comment_id`, `verdict`, `summary`, `findings`,
`files`, and `report`. Each finding has exactly `path` and `comment`; paths must
be reviewed changed paths and comments must be nonblank and at most 1,000
characters. A `pass` has no findings; `changes_requested` has one through eight.
Summary and report text are nonblank and at most 1,000 characters. The prompt
includes the complete changed-path inventory, including deleted paths. `files`
must map every inventory path exactly once: for each non-deleted path the reviewer
independently computes lowercase SHA-256 over the exact Git-blob file bytes, and
for a deleted path supplies JSON `null`. The coordinator independently fetches
and hashes every non-deleted blob and accepts only an exact map; it never trusts
the prompt or report to prove completeness.
The report must use canonical compact ASCII-escaped JSON, exactly as produced by
Python `json.dumps(report, ensure_ascii=True, separators=(',', ':'))`. Non-ASCII
characters are represented by JSON Unicode escapes; parsing those escapes retains
the original report text. Raw non-ASCII JSON is rejected by the canonical transport
check.
GitHub blob envelopes may contain ASCII LF line wrapping in base64 content.
Only those LF characters are removed before strict base64 decoding; other
whitespace, non-ASCII characters, malformed alphabet or padding, and mismatched
blob identity, encoding or declared byte size remain rejected.

Before dispatch, the complete inventory must also be representable within the
64-file bound and have unique bounded paths, nonempty statuses, and valid blob
identities for non-deleted files. An unrepresentable inventory is never sent as
a partial map: apply mode records a bounded diagnosis on the source handoff,
marks that handoff `inventory_blocked`, and posts one deduplicated blocker. This
terminal blocker does not retain generic agent occupancy or dispatch a fixer;
repeated scans reuse the same outcome for the exact head.

If a task returned by the saved task ID is positively terminal but its report is
missing, malformed, or incomplete, apply mode durably records a bounded diagnostic
and posts a deduplicated blocked outcome. A single corrective review may be
reserved for that source report only after task ID/time, task and repository
identities, scope, the unique terminal session, session owner/repository/user,
prompt, branch, and session chronology are authenticated. The correction has a
separate action, anchor, task, session, and nonce; both its authenticated task ID
and session ID must differ from the saved parent report's task and session IDs.
Session IDs used for recovery must be strings of 1 through 128 characters before
they are persisted or made retry-eligible. Missing, non-string, empty, or oversized
IDs are not copied into state and block correction, while the bounded report
diagnostic and deduplicated blocker remain durable.
Missing or reused identities fail closed before report acceptance or publication.
It cannot replace or edit the old report and does not reset or consume the
source-fixer budget. The durable
reservation is made before task creation. An ambiguous creation, active or unknown
task/session, or missing authentication metadata is never retried; terminal correction
failure exhausts this separate one-attempt budget and remains a clear blocker.
If the task lookup itself returns a non-object, the task remains sent and unresolved:
the bounded observation diagnostic and deduplicated blocker do not establish task
identity, terminality, or availability for retry. Reconciliation waits for an
authentic task response and does not dispatch a correction or publish review evidence.
Non-list `artifacts` or `sessions` containers likewise retain the sent task and its
occupancy, with a bounded deduplicated diagnostic; authentic container metadata can
then resume reconciliation without resetting the task or repair history. Repeated
polls reuse the same deduplicated outcome. A valid corrected
`changes_requested` report is published through the existing formal COMMENT path
and can feed the existing bounded fixer. A valid `pass` can publish `agent-review`
only after the full report, exact-head bindings, and independently verified file
inventory pass the same strict checks. If the process stops after a completed
correction publication but before the parent audit transition, reconciliation
idempotently changes `report_retry_state` from `reserved` to `recovered` only when
the completed correction, parent, distinct task/session/nonce/anchor, exact head,
source bindings, and persisted owner publication all match, including a positive
non-boolean review ID and the exact generated body. A missing or invalid ID leaves
publication uncertain until an authenticated exact matching review readback proves
it. A pass also requires its `agent-review` publication to be durably complete; a
`changes_requested`
correction is complete with its durably recorded formal COMMENT publication and
does not require or publish an `agent-review` success status. This parent-only
repair does not repeat task dispatch, formal review publication, status
publication, or fixer work; stale or incomplete publication is not successful
recovery, and uncertain publication and unbound or mismatched parents remain
unresolved.

If main advances after the original malformed report is durably recorded, its
saved `main_sha` remains unchanged as audit evidence. A retry is eligible only
when the exact head remains owner-authorized, the original source receipt still
authenticates, and ancestry from the saved report base is independently proven
through the current PR base, current main, and exact head. The new correction
stores that historical parent base separately and binds its report to the
current-main observation. Dispatch and report acceptance are fenced to that
reserved head and main; a later head or main advance invalidates the correction
without publishing its report or an `agent-review` success status.

The corrective anchor marker, outbox key, and reviewer nonce include the
reservation's current-main SHA as well as the immutable parent failure identity.
If main advances before an anchor is claimed, an uncertain anchor remains
unchanged, while a positively sent obsolete preclaim anchor may be compacted into
the existing bounded outbox tombstones; its public marker remains the replay
evidence. A distinct anchor may be reserved for the new main. Claimed/current-needed
anchors remain retained. Once a correction task is claimed, including an uncertain
creation, a later main advance does not authorize another correction task.

Every pending correction publication replay freshly checks the live PR identity,
head, current main, and existing correction authority immediately before a formal
COMMENT write, an `agent-review` status write, and a parent `recovered` transition.
Existing publication readback remains idempotent. A detected advance records a
bounded stale disposition, exhausts that correction reservation, and releases the
generic busy lock without retrying it. These client-side reads cannot make a later
GitHub write atomic with the read; no server-side compare-and-swap guarantee is
claimed. Crash repair may update the parent without new writes only when the
completed correction and all required publications were already durably recorded
before restart and the complete original task, report, source, and parent bindings
still match.

A stale correction's remote COMMENT and success status remain historical
publications, not rollback targets. Review eligibility, source handoff, and fresh
status/merge fences reject the selected COMMENT only when its PR, head, positive
review ID, and exact generated-body digest match that stale correction's durable
publication. Before a correction COMMENT write, the exact generated body is
persisted as publication intent. If the response is uncertain and a main/head
advance makes the action stale before readback, a later authenticated owner
COMMENT is also rejected only when its exact body matches that intent and its
submission follows the authenticated completed reviewer session. A different
later independent review remains eligible. Ambiguous publication is never
reposted. Exhaustion remains recorded and deduplicated; it does not revoke an
unrelated legacy review or a later authentic independent review. No new task,
COMMENT, or success status is emitted to repair this stale publication.

For a positively terminal malformed parent report, the authenticated review
session completion time is retained with the bounded error and session ID. A
later positive independent owner COMMENT on the same head supersedes recovery
only when it is current, unedited, fully authenticated and submitted after that
session completed. This suppresses the obsolete correction retry, its busy
state, and its report blocker without deleting or resetting the parent record.
The same review is rechecked immediately before claiming a correction task. A
correction already sending, uncertain, sent, or otherwise remotely active keeps
its separate occupancy lock; later review evidence does not cancel or release
that task.

The validated completion time, session ID and receipt comment ID remain persisted
with the receipt head/base and dispatch claim for restart. The canonical supported
validator metadata is `receipt_completed_at`, retained in both the action and the
SHA-bound enrollment's `receipt_proofs` before compaction. The optional redundant
`receipt_session_completed_at` projection must equal that canonical timestamp when
present; its absence does not invalidate an older genuinely validated proof.
Observation time or mutable task update time is never substituted for authenticated
completion. Missing, invalid or conflicting completion proof fails closed.
Unresolved findings and threads remain eligible for the existing bounded repair
path; no fixer attempt is consumed just for awaiting advisory Copilot feedback.
Preparation stages verified receipt/handoff state in memory. Controller evidence
is collected against durable lifecycle history plus all newly observed merge
events in the complete plan. The scan commits those source events, receipt state,
commands, cursor, observations and retirements atomically before any external
handoff, status or merge write. Capacity, validation or scan failure preserves the
entire prior state and export. Only a successful commit refreshes the export,
before deferred external work; preparation is discarded on failure.
After receipt acceptance, handoff uses the freshly scanned main SHA and rechecks
live main, exact result head and repository/PR identity. The stored receipt base
remains provenance, not a requirement that main never advance. This does not
change receipt schema or first-receipt validation.
A PR that becomes draft at the final dispatch fence is reported as a suppressed
repair with the `draft` reason; no task is claimed or posted.

Path classification includes both sides of renames and treats malformed,
unknown, empty, oversized, or incomplete file inventories as sensitive. Only the
seven explicitly audited presentation files (`frontend/styles.css`,
`frontend/viewport.mjs`, `frontend/session-swipe.mjs`,
`frontend/disclosure-reachability.mjs`, and three named PNG icons), bounded
portable test patterns, and non-operational top-level documentation are routine.
Authentication-bearing `frontend/ui.mjs`, bootstrap `frontend/app.js`,
`frontend/index.html`, `frontend/api.mjs`, and every unknown or new frontend
module require an owner exact-SHA decision. Operational, native, security and
deployment-policy docs are sensitive too. This PR classification concerns merge
authorization; the separate release policy still classifies the complete diff
from the deployed base before deployment.

The coordinator may publish its existing owned advisory `cloud-review` status,
but does not require it; a status is not a substitute for reading the structured
independent review. The current protected
policy is exactly `source-ci` (Actions app 15368), `integration-tests`,
`agent-review`, and `issue-link` (Actions app 15368), with strict/up-to-date checks
and required conversation resolution. Missing, extra, or differently app-bound
required contexts fail closed. All four required checks must independently report
success; skipped, cancelled, missing, pending, failed, or incomplete checks are not
green. The coordinator never writes `integration-tests` or `source-ci` statuses.
It may write `agent-review` success only after an independent-review report
passes strict exact-head and authenticated-report validation plus complete,
independently verified file-inventory checks; that status is not a substitute for
independent review or any other required context. Branch rules are collected
with explicit `per_page=100` and `page` pagination, bounded to 100 pages; only a
short final page proves completion. Errors (including an unavailable rules
endpoint), malformed pages/policy fields, or exhaustion of the bound fail closed.
The policy retains the union of classic and all ruleset requirements, including
separate app bindings for the same check context, and validates that union against
the four active required contexts. GitHub's classic-protection response may include
both app-bound `checks` and a legacy `contexts` array that is only their name
projection. Its entries must be strictly strings, not context objects whose app
bindings could be discarded. When both arrays contain the same distinct context names, the
coordinator counts the projection once and retains the stronger `checks` app
bindings. The comparison includes empty arrays: an empty projection beside
nonempty checks (or the reverse) is inconsistent and fails closed. Consistent
empty classic arrays remain valid when the required union is supplied by rulesets.
Extra, missing, duplicate, malformed, or otherwise inconsistent names
are not normalized away: all available requirements are retained and the policy
fails closed rather than weakening protection.

Auto-merge is requested through GitHub's protected `enablePullRequestAutoMerge`
operation only when the same-repository main base is current, the PR is not a
draft or conflict, all four required checks are green, the independent review gate
passes, no fixer may be running, and any sensitive authorization is current. Both
the PR head and the current `main` SHA are re-read before enabling
auto-merge; the mutation supplies `expectedHeadOid` as the server-side head
fence. The owner-managed branch rule must also require the PR branch to be
up to date (strict required checks) and require conversation resolution, either
through classic protection or a ruleset `pull_request` rule's
`parameters.required_review_thread_resolution`; a malformed `pull_request` rule
leaves the policy unproven and blocks status publication and auto-merge. The
coordinator cannot alter branch protection. Current review, thread, check, and policy evidence is fetched again
immediately before the merge request. A new head has no inherited review or check
acceptance; all evidence is freshly bound to that head. Ambiguous writes are reconciled from GitHub state and
are never blindly repeated. An auto-merge request is recorded as sent only when
the mutation returns the exact pull-request node ID and a valid, nonempty
`autoMergeRequest.enabledAt`; any other response remains uncertain until
GitHub's pull-request state proves auto-merge was enabled.

The durable outbox posts deduplicated, fixed-text outcome/blocker comments on
public PRs. It carries no logs, credentials, arbitrary issue text, or private
runtime data. The owner mobile Inbox is not implemented by this adapter.
Independent-review report observations and terminal unusable-report diagnoses use
separate stable issue/head keys (`review-report-observation` and
`review-report-terminal`), so one lifecycle stage cannot consume the other's
notice. Historical `review-report` entries are classified by their fixed message:
a pending observation follows normal delivery, sent observations remain deduplicated,
and sending/uncertain observations are left unchanged and are not retried. A matching
legacy terminal entry retains the prior terminal deduplication.
Before posting, an existing marker in the fully read PR comments proves
publication only when the numeric author ID matches the authenticated owner used
for writes and the body exactly matches the planned notification. Copied markers
from other users, missing identity metadata, and edited notification bodies do
not count. POST responses require the same owner/body proof plus a comment ID;
unproven responses stay uncertain and are never automatically retried.

Apply mode atomically writes a private `<state_dir>/workflow-events.json` snapshot
using the closed, versioned schema and fixed lifecycle reason/outcome mapping.
It includes only sanitized identifiers, exact lowercase SHAs, and UTC times.
Raw GitHub text, check logs, commands, credentials, and private runtime data are
excluded. Export persistence is bounded to 256 events and 1 MiB; plan mode never
writes it. Stable event IDs preserve polling replay identity, and terminal
enrollment retirement is committed with event persistence.

Before `_build_plan` can add lifecycle events, apply mode holds the coordinator
execution lock and prepares state. When the existing notification consumer is
configured, preparation resolves the sole ready default-profile application
owner and reads `workflow-notifications.sqlite` through the consumer's existing
read-only, private-path, size, rollback-journal, and no-sidecar boundary. One
read transaction validates the adapter schema/binding (version 1 and repository
ID `1399942965`) and every stored event identity against active lifecycle events
or retained context. A row authorizes producer retirement only when its
`event_id`, canonical event digest, recipient owner, `acked` status, and nonempty
retained `inbox_id` satisfy the consumer-ledger checks. The consumer verifies the
Inbox record's owner, delivery ID, and Inbox ID before committing the original
ACK; the producer validates that retained assertion and its consistency, not a
currently present Inbox row. No two ACKed event IDs may share an `inbox_id`, across
all active and retained-context rows; any duplicate blocks the whole preparation
before retirement. Missing and pending rows retain events; malformed, foreign,
unbound, or detectably inconsistent adapter state blocks preparation. The
coordinator does not initialize, recover, or write the consumer database.

The unchanged consumer-owned ACK ledger remains authoritative after physical
Inbox retention, so a valid historical ACK still permits retirement and prevents
replay. These checks are not tamper-proof: after the Inbox row is removed, a
unique well-formed substituted Inbox ID or an arbitrary internally consistent
historical ledger rewrite cannot be distinguished from the trusted retained
assertion. No new receipt authority or indefinite Inbox retention is implied.

Exact ACKed outcomes move from the active export list into optional
`lifecycle_context`, which has a closed schema, fixed repository binding, exact
owner binding, and a bounded set of validated canonical events. It is replay and
correlation context only, never an ACK source. The existing 256-event lifecycle
and 1 MiB export limits remain unchanged. Receipt-source replays are filtered
against the same ACK snapshot before their cap; exact canonical payloads and
timestamps are preserved when persistent incidents are regenerated. Retained
merged outcomes continue to anchor later exact `controller_verified` evidence.
The prepared retirement and scan commit together before export or external
mutation, followed by a fresh owner binding check immediately before commit.
Plan mode neither reads ACK state nor changes producer or consumer state.

Before each export, sensitive approval events are filtered against durable
active enrollment, the exact last observed open head, and still-pending
`authorize_sensitive_action` authorization. Apply commits the complete scan's
head observations, owner commands, and retirements before exporting; an old
enrollment head is not a fallback for missing current-head observation. Authorized,
head-superseded, retired, unobserved, or unsupported decisions are omitted from
the exported view only. Durable incident payloads/IDs/times and all other outcomes
remain unchanged; consumer ACK history is neither read nor rewritten, and export
never grants or revokes an approval. If filtering leaves no events, the previous
export is removed rather than refreshing an obsolete request. This is a polling
snapshot of observed state, not a live authorization endpoint.

Each enrollment has an optional bounded `sensitive_generation` (absent means zero,
maximum `2**31 - 1`). Invalidating a persisted grant advances it exactly once in
the same scan commit; repeated invalid polls/restarts do not advance it. It feeds
the existing lifecycle `incident` identity, so a renewed approval request reaches
the unchanged consumer even if the prior request was ACKed and retired. A new
owner command may select the same restored review ID/body, but revoking that new
grant advances the generation again. Export includes only the current enrollment,
head and generation's approval request, excluding older unACKed requests without
rewriting canonical events or ACK history. A fresh valid selected review/command
suppresses the current request. Head changes clear old-head authority and use the
head-bound event identity; new enrollment commands reset the generation under
their distinct enrollment identity. Invalid or exhausted generation fails closed.
No event schema, consumer protocol, credential or notification framework changes.

The export is written to the existing state directory selected by application
configuration, not the coordinator's private state directory. The coordinator
binds the envelope to the sole ready default-profile application owner read from
the configured auth database. This opaque application user ID is independent of
GitHub's numeric repository-owner ID.

Issue-starter failure and uncertainty events are read from its durable v1
command state only when a blocked receipt is persisted as sent and the exact,
unchanged owner-authored receipt comment is present; that comment's creation
time supplies the incident timestamp. Deployed events are emitted only for an
existing exact merged event when the current delivery ledger, successful
controller status, current release, and git provenance validate for that merge
SHA. A merge alone is never treated as a deployment.

Task receipt v2 is the default generated dispatch contract. The host supplies the
literal nonce, PR number, start head and **dispatch-time main SHA** together at the
start of the receipt template. The prompt is placed in the JSON task-create request
as its `prompt` string. Source and synthetic JSON-round-trip tests can verify that
producer/request representation, but cannot establish the bytes ultimately supplied
to a model. A reported missing field is evidence of a delivery/interpretation
failure, not proof of downstream transformation; provider transformation and
agent-side misreading/context loss remain hypotheses until an owner-controlled real
task trial verifies the boundary.

Before any source edits, the child must verify that all four fixed bindings appear
complete and read a nonblank `COPILOT_AGENT_SESSION_ID` from its exposed environment.
If any required context is absent, it must stop without source edits, report an
explicit blocker, and not guess, substitute a value, or emit a receipt. Never obtain
extra credentials or request an owner-comment handshake. The child is not asked to
discover or echo the separate Task UUID. The read-only cloud probe established
session-environment identity, not a source for the Task UUID (and not proof that
such a source cannot exist).

Post exactly one unchanged authenticated Copilot issue comment containing one
contiguous v2 receipt block as its final unquoted lines. The posting transport may
prepend an unchanged Markdown blockquote and reorder the receipt fields. Only ASCII
blank lines (empty or containing only spaces/tabs) or blockquote lines may precede
the block. A quoted prefix must end with an ASCII blank line immediately before
the receipt header: without it, raw lines can be lazy continuation inside the
quote. NBSP, controls and other Unicode whitespace are not blank separators.
No fences, unquoted prose, extra fields, duplicate fields, or trailing newline
are accepted. Quoted or code-formatted content is never receipt evidence.

The entire unchanged v2 comment is limited to **8192 UTF-8 bytes and 64 LF-delimited
lines**, inclusive of the quoted prefix, blank lines, header and seven fields.
These finite limits leave room for a short quoted report and the eight-line receipt
without admitting arbitrarily large stored proofs. Reject character lengths over
8192 before encoding, reject malformed Unicode, then enforce the byte limit before
counting or splitting; enforce the line limit before splitting. The same limits
apply at first acceptance and persisted-proof revalidation. Plain v2 remains valid
within these limits; the exact-body v1 contract is unchanged.

```text
Hermes-Task-Receipt: v2
nonce=FIXED_DISPATCH_NONCE
pr=FIXED_PULL_NUMBER
start_head=FIXED_DISPATCHED_HEAD_SHA
base=FIXED_DISPATCH_TIME_MAIN_SHA
session=SESSION_ID_REPLACE_ME
head=PUSHED_PULL_HEAD_SHA_REPLACE_ME
result=ready|conflict_incompatible|policy_broken
```

The uppercase replacement labels are plain ASCII instructional tokens, never valid
receipt values; replace both dynamic tokens with the exact session ID and pushed
pull-head SHA before posting. Each field appears exactly once; field order may vary
under the documented transport. Choose exactly one closed result value. After
pushing and focused checks, the coding task must not idle waiting for CI/review: the
parent controller handles those phases. `ready` is not passing CI, approval, merge
or deployment success.

Task identity still comes solely from the durable saved task ID and authenticated
Task API response, never from a comment. The host validates exact returned task ID,
`session.task_id`, task/session owner and repository, creator/user, nonce, exact
PR and branch artifacts, and task/session/comment chronology. Receipt author and
comment ID must be strict numeric GitHub identities (positive comment ID, no
float/string/bool coercion). Missing, edited, duplicate, mixed-version, mixed-field, copied or otherwise
noncanonical receipts fail closed. For v2 only, the consumer accepts the bounded
transport above and stores the complete unchanged comment body; restart validation
requires the remote author, comment ID, exact body, and timestamps to remain
identical. All receipt identity, result-head, dispatch-base, chronology and immutable
authorization bindings remain unchanged.

The strict v1 reader remains for existing receipts and proofs: its body includes
`task=<authenticated-task-id>` immediately after nonce, and its base must match
main at initial receipt validation. No historical dispatch binding is invented.
For v2, base instead must equal trusted `action.main_sha`, even when main advances
before the first receipt poll. The receipt version and authentic dispatch main
are retained with the proof before compaction. Revalidation requires canonical
body/metadata consistency, v2 dispatch-base equality and exactly one unchanged
remote receipt for that authenticated author/nonce.

The paired PR29 consumer uses this receipt to prove result-head continuation
without inheriting review/check or sensitive authorization. Proven HEAD authority
survives unrelated main advances; the recorded base remains provenance, not a
fresh eligibility predicate. Handoff mutations fence against freshly scanned and
live current main, not historical receipt base. A behind authorized result head
must take bounded neutral reconciliation, never merge behind. Repository/base/ref,
strict up-to-date policy, mergeability, exact head and pre-send main fences remain
required; fresh review/check evidence is still required for the repaired head.

The owner mobile Inbox consumer is a separate adapter and must validate this
producer's exact event schema and bind the export to the actual ready application
owner. A GitHub account ID is not an application owner ID. Exported merged
outcomes do not imply deployment. The coordinator neither invokes the consumer
nor activates notification delivery.

PR33 reuses the consumer's read-only owner, private-file, and deployment-evidence
validators without editing its files. During current-main assembly, preserve
those validators or expose an equivalent supported shared API, and rerun the
producer-to-Inbox integration tests against the assembled schema and consumer.

## Bounded state

The serialized state is limited to 4 MiB. The cap is checked before the
temporary file replaces the state; a write that would exceed it fails the cycle
with a clear error and leaves the prior valid state unchanged. Only records with
positive terminal proof are compacted: verified-completed fixer tasks and
sent/superseded/blocked status, auto-merge and outbox records of a PR observed
closed or merged, plus those records on non-current heads of an open PR. They
are replaced by a per-PR tombstone (a status-generation watermark and bounded
lists of retired auto-merge and outbox key digests). Pending, sending, uncertain
and sent fixer claims, every record on the current head, enrollments with their
command fence and attempt budget, and consumed command IDs are never dropped;
the oldest command IDs fold into a numeric watermark that still fences replays.
Positively completed `inventory_blocked` fixer handoffs are terminal too and are
retired by the same head-change/inactive contract; active, current-head, and
uncertain work and pending outbox posts remain retained. Their retirement comparison
uses the authenticated `receipt_head` when available, not the dispatch head.

ACK-backed lifecycle retirement does not erase history. The optional
`lifecycle_context` preserves each retired event's canonical payload and exact
identity for stable replay and merge-to-deployment correlation; it is validated
against a closed schema and owner/repository binding. Context is not independent
ACK authority, is never age-evicted, and remains inside the same 4 MiB total state
bound. If it fills the state bound, preparation fails before export or external
writes. This is finite retention, not unlimited history storage or unattended
activation approval.

## External policy boundary

The current source policy requires the exact four protected contexts above and
independent structured review evidence; advisory `cloud-review` is neither required
nor synthesized. Any later protection change requires separate owner authorization
and new verification. The coordinator cannot alter branch protection.

Native compatibility gating and routine deployment policy remain follow-ups under
issue #5. This slice does not change the current global `AGENTS.md` review,
integration, merge, or deployment instructions. Any future transition should
follow verified coordinator and branch-protection rollout, preserve exact-head
evidence and sensitive owner decisions, and keep guarded deployment separate.
