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

Every pull snapshot strictly validates the enrolled numeric ID, number and node
identity, plus repository and base identity. A direct snapshot or a terminal pull
with a changed identity fails closed. During multi-PR scan only, a structurally
valid identity mismatch on an open pull is isolated as unscoped so other enrolled
pulls can progress; that pull receives no writes. Malformed IDs and repository or
base mismatches are not isolated.

The final pre-commit pull-identity fence applies to every planned enrolled pull,
including manual enrollments without starter-source evidence. If a structurally
valid, open pull's identity changes after planning, discard only that pull's
prepared enrollment, actions, outbox, retirement and lifecycle changes, and omit
its commands, recoveries, observations, source proof, revocations and lifecycle
events from the commit. The additional starter-source fence still validates its
head and source proof. Keep the state-store preparation baseline unchanged so the
ordinary compare-and-swap still rejects concurrent durable changes. If an owner
command for that pull was read in the scan, retain the old global cursor and do
not consume that pull's command event IDs; the next cold scan must reread and
revalidate the command rather than silently losing its authority. Consume other
pulls' processed command IDs once, including stale or invalid commands. Other
eligible pulls may still commit and advance.
Direct snapshots and terminal identity mismatches remain strict.

Mixed-root continuation preparation must also skip identity-isolated snapshots.
Acceptance uses a multi-PR apply cycle with multiple retained abandonment roots,
matching retained continuations and a newly scanned continuation command. The
isolated pull keeps its commands pending and unconsumed, with no publication,
history or lifetime-budget changes, while a separate eligible pull advances.
Cold restarts preserve those pending IDs and do not duplicate accepted work.
Restoring the identity permits retry only after fresh authenticated evidence;
missing comment evidence is never replaced with an empty list.

A retained continuation with an explicitly malformed `receipt_proofs` container
(null, mapping or string) never proves a successor, at the shared proof seam used
by planning, reconciliation and preparation. That pull is blocked without task,
review or anchor publication and without history, disposition or budget changes,
while a separate eligible pull advances durably. An absent legacy container still
uses existing authenticated live evidence; an empty or valid list is unchanged.

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
command identity/body/timestamp and live binding proof, after the fresh pull-identity
fence. A structurally valid, open replacement pull identity isolates only that pull
and defers its command as above. Changed commands, body/linkage proof, malformed
identity, repository/base mismatch, or incomplete reads still abort the entire
preparation: no cursor/events, enrollment, receipts, retirement/export, or external
write commits. Compact optional `starter_admission` metadata is strictly validated
on state load. Version 1
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
An already accepted exact-head Copilot review suppresses duplicate review requests
even without a local reviewer action.

This fence is admission-only. Later body reports or changed canonical linkage do
not revoke a durably admitted PR; PR45 receipt-result heads, restart/compaction,
manual renewal and existing repair budgets retain their original contracts.
Provenance does not replace immutable `authorized_head` or authenticated
result-head receipt gates.
GitHub reads, durable local commit and remote mutations are not a single atomic
transaction. Acceptance records the final coherent admission snapshot; edits after
that read remain a remote race, not a claim of permanent body immutability.
Legacy SHA-only starter output cannot be distinguished from manual authority:
review in-flight old commands and deploy both sides together before activation.

Sensitive-path classification is informational only. Owner enrollment and its
immutable `authorized_head` define permitted work; there is no separate
`/hermes authorize-sensitive` prerequisite, grant revalidation, or sensitive-owner
approval outcome in planning, status publication, notification eligibility, or
auto-merge. Legacy authorization fields and lifecycle records remain readable for
compatibility, but they do not block new processing. Pending legacy sensitive
approval notices are marked superseded, and `sensitive_approval` events are omitted
from exports without changing retained history or consumer ACKs. Current exact-head
Copilot review, resolved conversations, all required CI checks, complete source
evidence, task occupancy, and receipt bindings remain mandatory. A bounded source
repair may proceed while findings remain unresolved, but merge remains blocked
until all merge gates pass.
The ordinary review gate requires authenticated unchanged submission metadata.
Missing edit fields or failed metadata enrichment cannot qualify at planning,
publication, final merge fencing, or downstream autonomy validation.

This is a source/first-activation boundary, not an upgrade path for historical
coordinator state. Version-1 records remain readable for inspection, but active
legacy enrollments lacking `pull_id`, `pull_node_id`, or `repository_id` are
unsupported: pull snapshot collection fails closed with
`Unsupported legacy enrollment: missing pull/repository identity; automatic migration is not supported; preserve state and stop activation`.
The coordinator does not backfill legacy identity, reset cursors/budgets, or discard
unresolved claims. A newer exact-SHA command may renew an already identity-bound,
SHA-bound active enrollment: it preserves the initial `authorized_head`, receipt
proofs, unresolved claims and attempt budget, records the new owner-authorized head,
and does not reuse legacy sensitive authorization. It cannot migrate a legacy enrollment.
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

For a canonically marked `COMMENTED` overview only, these two complete sentences
are recognized as gate-status prose:

`The operationally sensitive coordinator gate lacks verified latest-head CI and required review evidence.`

`The coordinator’s authorization and progress seam is security-sensitive, and exact-head owner authorization remains absent.`

This exact whole-summary match classifies prose only; it does not prove review, CI,
or owner authorization. The complete rendered context must equal the exact
entity-decoded live sentence. Added text, including inert blockquote, `code`, or
`pre` context, makes the summary ambiguous. Changed case or punctuation,
optional-word changes, negation, an ASCII apostrophe, a substantive prefix, or
added prose in the same or another sentence—including a no-code-findings
sentence—also remains ambiguous. Quoted-only text remains ambiguous.

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

The current `ccr-overview-v2` renderer also labels linked inventory sections
`N open finding(s)`, `N resolved finding(s)`, and `N resolved since last review`.
The singular/plural form (where present) and
the number of linked list items in the section's single unordered list must
match the count. Duplicate, malformed, inert, partial, or mismatched sections
make the inventory incomplete; a resolved section never becomes active
evidence. Each item must have exactly one supported live discussion anchor;
normalized targets must be unique within and across counted sections, including
absolute/fragment aliases. Nested live lists or disclosures cannot hide extra
items under a counted item. Quoted examples remain inert. Existing `Open (N)`
labels remain supported.

The whole top-level paragraph `<p><strong>0 open findings</strong></p>` is a
zero-open inventory declaration, not arbitrary summary prose. It is recognized
only in marked overviews, and duplicates or any additional open section make the
inventory incomplete. Nonzero bare counts require linked inventory rather than
metadata suppression. Extra prose or markup in that paragraph is not discarded;
free-form summaries still use the existing explicit disposition grammar.

Top-level horizontal rules do not end summary inspection. Only the exact known
rendered `Add a code-review agent skill` footer immediately after a separator is
ignored. Any other content after a separator, including content after that
footer, makes the summary ambiguous and the inventory incomplete; this content
cannot create body-only repair evidence. Multiple separators do not clear this
condition. Quoted and code/pre content remains inert, but its presence after a
separator still makes the inventory incomplete.

In this renderer only, one top-level paragraph immediately after the exact
overview heading and recognized status heading is provider summary context.
It is attached literally to the single count-matched `Previously missed (1)`
finding only when the review also has one count-matched open inventory and no
other ambiguous content. The summary is not discarded or treated as a separate
approval. Extra prose, unknown framing, multiple active findings, and any
unmatched inventory remain incomplete. Actual disclosure markup inside
`code`, `pre`, or `blockquote` is inert and makes the inventory incomplete;
entity-decoded text remains literal.

`body_findings` retains its caller interface. Dispositions remain in the helper's
internal result; no new review-authority protocol or coordinator lifecycle path
is introduced. The formal technical review gate uses genuine exact-head Copilot
COMMENTED or APPROVED review and all three successful CI checks
(`source-ci`, `integration-tests`, and `issue-link`). Ambiguous
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
requires the immutable merge commit's message to retain the complete audit for
every conflict hunk: file/hunk identity, classification, decision, rationale, and
how the resolution preserves both branch intents. The pushed result receipt binds
that audit-bearing merge commit by its exact head SHA. A ready receipt requires
this merge commit; a truthful `conflict_incompatible` or `policy_broken` receipt
does not require or claim one. The audit is not a new receipt field or an automatic
semantic approval. A current-main fence is rechecked before dispatch. Before a new neutral task is
claimed, the coordinator durably reserves one short owner-authored receipt anchor
in the outbox. Its marker binds the exact task key, PR, head, main/base, attempt,
and nonce. A pending-to-sending compare-and-set is required before the comment
POST, so a second writer cannot post the same anchor. The returned ID and exact
body must match; complete comment inventory and direct ID readback must confirm
the owner, PR, unchanged body, and chronology. An uncertain POST is never
repeated. Recovery accepts only one exact authenticated match; incomplete
inventory, edits, deletion, or ambiguity block task dispatch. A final pull,
writer, anchor, and saved-prompt fence runs before the task POST. The prompt
binds the explicit comment ID and anchor body. Only after that verified anchor
does the neutral attempt consume its existing budget.

Preclaim publication has a separate lifetime bound of three neutral receipt-anchor
reservations per enrollment, charged atomically when a new outbox entry is added.
The durable `neutral_anchor_reservations` count survives restart, main/head changes,
retirement, renewal, and re-enrollment. It never resets the source or neutral task
attempt counts. Older state initializes it conservatively from the greater of the
known neutral attempt count and retained neutral receipt-anchor count; this is
not a reconstruction or deletion of historical publication evidence. Sent and
uncertain anchors remain unchanged. Recovery of the same anchor consumes no new
reservation and never repeats its POST. Once the bound is reached, no new anchor
entry or public comment is admitted, even if main changes before every action
claim. Acceptance exercises successive main/base changes with a fixed PR head
across cold restart, finite entries/comments, retained evidence and counters, plus
normal dispatch and lost-POST readback without duplicates.

Inactive retirement preserves the complete binding for each sent neutral
receipt-anchor within that same three-reservation bound, including its nonce,
body, comment ID and exact task/PR/head/main/base scope. It does not tombstone a
preserved anchor key. After a cold restart and a fresh owner enrollment at the
same scope, the coordinator must authenticate and reuse that exact public anchor,
then dispatch at most one neutral task with its original causal binding. It must
not repost or infer missing binding data. A changed scope, altered/deleted/
ambiguous anchor or incomplete remote read cannot reuse the old binding. Source
and neutral attempt counts, claims, receipts and uncertainty history remain
unchanged by retirement and re-enrollment. Active main churn retains complete
sent neutral receipt-anchor bindings on the current PR head within the same
lifetime reservation bound; a main change does not make those bindings eligible
for compaction. Obsolete unclaimed review-correction anchors retain their separate
existing compaction behavior.

Publish exactly one receipt-only PR reply. Its payload contains only the canonical
v2 header and seven bound fields, with no decision, report, URL, prose, or extra
fields. The finite anchored envelope has zero or one empty leading line, exactly
one `> ` line containing the complete short reserved anchor body, zero or one
quoted blank line before and after that anchor (each either `>` or `> `), then
exactly one ASCII separator line (empty, one space, or one tab). These 54 shapes
support blank formatting differences, not arbitrary excerpts or semantic quote
text. The anchor cannot be missing, changed, truncated, or duplicated. Extra
quoted prose, headers or receipts, missing separators, other whitespace, CR,
fences, extra fields and tails fail closed. The unchanged whole comment still
must fit 8192 UTF-8 bytes and 64 LF-delimited lines.

Before its sole publication, the producer prevalidates all 54 complete carriers
with actual bound values and the strict parser, including worst-case overhead.
This finite support is not a guarantee of provider serialization: public immutable
comment 6134321641 establishes an observed quoted excerpt plus separator, not a
universal prefix bound or live acceptance of these synthetic shapes. Unknown
shapes remain rejected. The tool receives only the eight-line payload. Never
send and retry, edit the reply, or post a
second comment. The receipt is completion evidence only; it is not CI, review,
merge, or deployment approval.

Acceptance must demonstrate managed RED then GREEN at initial
`validate_task_receipt`, retained-proof validation, cold budget reconstruction,
and real synthetic Coordinator acceptance/handoff for alternate blank envelopes
at the same exact anchor. Verify payload-only tool input, one publication per
anchor, immutable comment readback, durable completion and restart without replay.
Keep identity, session/prompt/body, nonce/PR/head/base/task, chronology,
edit/duplicate and byte/line rejection gates. Preserve all existing historical
producer prompts, including the superseded anchored exact-prefix prompt, through
explicit byte-exact constructors; do not rewrite reservations or the frozen
`coordinator-budget-v1-requests.json` fixture.
The prompt directs genuinely incompatible requirements or broken required policy
to a fixed typed result; these stop further repair for that exact head. Waiting or
uncertain tasks retain the shared lock. Ordinary technical conflicts are repaired
within the shared budget; exhaustion or ambiguous execution becomes a meaningful
owner blocker rather than an unbounded retry.

Cold compatibility reconstruction recognizes only exact saved neutral prompts
from known released producers, including the previous two-comment instruction,
the later combined-comment instructions, and the anchored exact-prefix v1
instruction. Each historical prompt must
reconstruct byte-for-byte from its saved reservation; no broad suffix or
comment-based opt-in is allowed. For the already-dispatched legacy reservation
that asked for conflict decisions in a PR comment, the comment must be from the
immutable Copilot bot author, unchanged, within the ordinary v2 byte and line
limits, and contain exactly one final canonical v2 receipt. Before it, the
parser accepts only the observed legacy decision-envelope shape: its provider
quote is inert and must match the saved starting head. The quoted outcome marker
may be either complete or exactly provider-truncated as
`<20 lowercase hex characters> -...`; neither marker form is authority. It is
followed by one unquoted reconciliation assertion matching the reserved PR,
starting head, dispatch base, and receipt result head, the conflict-decision
section, and a bounded test summary. The existing separated summary and the
single observed combined backticked `deploy/autonomy_policy.py` summary are the
only supported summary forms. Quoted/fenced/HTML assertions, arbitrary prose,
ambiguous headers, extra fields, trailing content, or any mismatch remain
invalid. Receipt fields and all ordinary v1/v2 parsing rules stay strict.

When main advances before the original receipt is adopted, recovery is bounded
across two cold scans. The first scan validates the original dispatch-base receipt
and persists its unchanged proof, extending the authorized head chain without
making its already-collected stale scope current. The next scan can establish
historical-base eligibility from that retained proof and positive ancestry, then
reserve the necessary next neutral task against fresh main. Accepting that task
supersedes the original pending handoff; normal retirement may remove the original
action, but never its receipt proof, source count, or lifecycle history. Cold replay
does not dispatch the original task again or reset its receipt waits. After the
next neutral completes, fresh independent review may authorize genuine source
repair 4; neutral reservations remain separately counted.

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
do not authorize continuation. Review/check evidence must be independently fresh
for each result head. Validated task/session/nonce and unchanged-comment proof is persisted with
completion in the enrollment before lifecycle compaction can retire its action.
Restart and final dispatch revalidate the retained proof against the remote comment.
Deferred ready/review handoffs recheck it after the scan commit; a new observed bound
head does not replace owner enrollment or receipt provenance. A typed blocker remains `task-result-blocked`,
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
re-enrollment. Validated owner-abandonment records also survive inactive
re-enrollment as deep-copied audit bindings; this does not copy legacy sensitive
authorization or reset the existing counters, progress, receipt proofs, or claims.
Replay does not dispatch an accepted task twice. Historical-base
scope is not current-main eligibility: stale PRs receive no status action or merge
eligibility, and exact current-main, fresh review/check, status, and merge fences
remain mandatory after reconciliation.

Polling a claimed task requires positive integer creator, owner, and repository
identities matching the fixed owner/repository before any terminal failure can
release the lock or emit `task_failed`. Missing or malformed identity evidence
retains the sent claim, then reaches bounded `execution_uncertain`; it never
blindly starts another task. Task and session IDs remain opaque strings.
After the bounded receipt-poll limit, an uncertain neutral reservation with a
known persisted task ID and creation time receives at most one additional
read-only reconciliation attempt. It can resolve only from a fresh GET of that
exact task with complete original task/session/branch/repository identity,
positive terminal completion, and the unchanged authenticated receipt contract
above. Preserve its existing waits, uncertainty event, counters, and reservation.
An absent, nonterminal, malformed, or incomplete read remains uncertain and
cannot be polled again by this recovery path. No recovery path reads or replays a
lost-create reservation with no known task ID, posts another task, or re-executes
the neutral task. If that bounded read was already consumed by the prior receipt
codec, one additional read may be reserved only for the new legacy-neutral carrier
codec revision on that same known-ID uncertain neutral action. Planning only
collects recovery candidates; read-only planning neither claims nor GETs them.
After the complete scan and its commit fences succeed, recheck the live pull,
main, and original pull identity under the existing apply execution lock. A
no-GET preflight abort leaves the revision unclaimed for a later valid scan.
Atomically claim the exact unchanged original reservation through the existing
state transaction before GET; prepared-state updates are not durable claims.
Preserve the prior attempted flag and all history, and never repeat that codec
read after an interruption, cold restart, later failure, or main change. Failed
claim persistence prevents GET. It still resolves only
from complete positive unchanged task/session/receipt proof; every other response
remains uncertain without another read. Successful recovery refreshes downstream
planning without repeating reconciliation; newly derived lifecycle events are
canonicalized against active/retained history and the same verified ACK snapshot,
then durably recorded before their outcomes are published. Acknowledged incidents
remain retired rather than being reinserted with conflicting replay payloads.

An owner may explicitly abandon receipt acceptance for one known-ID task with the
immutable issue comment `/hermes abandon-unverified <task-id> <current-head-sha>`.
The command is valid only for the enrolled PR's exact current head and the exact
uncertain source or neutral reservation named by its persisted task ID. Apply must
revalidate the unchanged command, pull/node/branch/base/main identity, reservation,
and a complete fresh remote task inventory immediately before commit. A fresh task
detail must authenticate the original creator, owner, repository, task creation,
PR and branch artifacts, unique session identity, exact saved prompt, chronology,
and positive completed task/session state. Any missing, malformed, active,
reactivated, unknown, or API-error evidence blocks recovery. A lost-create claim
without a known task ID is never eligible.

Recovery means only that the owner abandons acceptance of this result. It is not a
receipt, source-progress credit, successful handoff, review, merge approval, or
deployment authorization. Persist a bounded, schema-validated supersession record
bound to the immutable command and a digest of the original reservation. Keep the
original reservation, authorized head, receipt history, attempt counts, progress
state, uncertainty and lifecycle history unchanged. The command's exact current
head may authorize only a distinct bounded source, review-followup, or neutral fix
reserved after recovery commits. It is not result provenance or merge authority.
Only that later task's valid ready receipt may add its result head to general
provenance; an older task or the abandoned task cannot do so. Normal current-main,
Copilot-review, and required-check fences still apply. Before every later dispatch, collect
complete fresh remote task occupancy and recheck the recovered task's exact identity
and terminal state. A neutral continuation that reconciles after `main` advances
must keep the recovery record's historical base in `recorded_base_sha` while its
v2 receipt base and current pull base continue to match that continuation's
current `main_sha`. After a valid continuation receipt, bind the recovered task's
terminal check to that exact receipt lineage even when the PR head has advanced.
The terminal check uses both live actions and the enrollment's authenticated
`receipt_proofs`; compaction must retain the neutral continuation's
`recorded_base_sha` and exact `head_ref` as well as its receipt and
owner-abandonment bindings. Missing or mismatched branch identity is never inferred
from the current pull.
For bare enrollments with valid retained abandonment claims, persist accepted receipt
proofs atomically with completion too, so every lineage edge survives compaction and
cold restart. These occupancy proofs do not create SHA-bound source authorization.

Pre-upgrade retained proofs omit `head_ref`. A read-only compatibility view may
obtain it only from the original authenticated task session. It requires a fresh
complete task inventory, positive task and session completion, the same owner,
creator, repository, pull artifact and branch artifact, and exact task/session
IDs. Summary rows and rows that retain `sessions` use the same strict artifact
validation before task detail is used. The original summary and exact detail must
agree, including their complete type-exact artifact lists; each must contain
exactly one valid GitHub pull and branch artifact. The pull artifact's numeric ID
and node ID must both match the identities stored in the proof. Missing, foreign,
malformed, duplicate, or contradictory artifacts deny the view. The original
prompt must match
the supported reservation type, exact pull/start head and reservation marker, and
end with a supported canonical receipt instruction bound to the stored nonce and
base. If a live action remains, its original prompt must also agree; a missing body
is a mismatch. Anchored
neutral prompts require the immutable authenticated original owner anchor and its
exact receipt carrier. Revalidate the stored receipt against the original task's
chronology and the unchanged receipt comment; require the immutable abandonment
root and fresh scoped occupancy. Unknown, reactivated, mismatched, incomplete or
unavailable original evidence denies the view. An explicitly present branch value
is never replaced. No proof, claim or counter is migrated, and no current pull or
enrollment supplies missing historical identity. Repeat these checks at the final
source and reviewer write fences; planning evidence is not a write fence.

A different current head is accepted only through a connected chain that starts with
one distinct, abandonment-bound ready receipt and follows each later ready receipt's
exact `receipt_start_head` to the preceding `receipt_head`. The chain is bounded by
the source and neutral lifetime ceilings combined. Every edge must pass immutable
receipt validation and match the current repository, pull request and branch; the
live pull's numeric and node IDs must also match the retained abandoned-task
reservation; never bind a pull identity using the live pull's own IDs. The current
base must match each receipt's original dispatch base. Historical dispatch bases
may precede the current pull base only with authenticated ancestry to that base
and the edge's result head. A later bound edge must match its own unique retained
valid abandonment root and unchanged original owner command, including its causal
binding and attestation. Sequential roots followed by a main advance must survive
actual apply, retirement and cold restart without duplicate dispatches, changed
history or reset allowances. Missing or contradictory ancestry must deny progress.
Missing, ambiguous, disconnected,
malformed, non-ready, wrong-identity or cyclic history fails closed.
Before eligibility filtering, correlate live and retained representations by the
durable action key, then reconcile each task relevant to this pull. A missing,
non-string or substituted task ID cannot hide a duplicate. All receipt,
reservation, identity, starting
`head` and `dispatch_nonce` fields must agree. An ineligible malformed duplicate
cannot disappear from this check. Include any live action sharing a retained task
ID before filtering by its issue identity, so a missing, malformed or mismatched
issue cannot hide the duplicate. Identical copies count once; distinct tasks on
unrelated pulls do not block this lineage.
Receipt-like live actions with a numeric-equal malformed issue identity block the
affected pull even with a fresh action key and task ID and no retained links.
Both source-authority indexing and final remote lineage validation check this
before filtering candidates. Boolean aliases of issue 1 fail the same way.
Exact integer receipts for genuinely unrelated pulls remain isolated.
Apply the same bounded receipt-like identity check before the no-current-proof
fallback, including first-successor dispatch. Correlate embedded action keys against
retained proof, abandonment, and continuation keys before issue or task filtering,
including final source and reviewer publication fences. Retirement and lineage
reconciliation must preserve live/proof authority-field presence and canonical-value
conflicts, including continuation bindings, attestations, and companions, not just
compare fields present in the proof. A legacy proof without `head_ref` cannot erase a
live branch value unless authenticated retained evidence establishes that exact
branch; retirement must retain uncertainty rather than infer it from the current
pull. Preserve supported authentic legacy omissions and unrelated terminal
compaction.
An unrepresented competing same-pull outgoing edge remains evidence by its
`receipt_start_head`, even with a fresh key/task and old dispatch head. Repeated
apply, explicit retirement and cold restart must retain those exact conflicting
bytes and deny source/reviewer writes; unrelated terminal actions still compact.
Every persisted abandonment record must resolve to one matching retained root
action before any receipt, dispatch, reviewer, or terminal authority is granted.
Missing, mismatched, or duplicate root claims block the affected enrollment. The
root action keys and abandoned task IDs must each be unique; distinct matching
actions do not make two claims for the same abandoned task valid. The
record's issue, command-comment and actor IDs, and the root action's issue, owner,
repository and pull IDs must be exact positive integers; the enrolled repository ID
is validated the same way at pull snapshot admission.
Each individual successor proof's issue must also be an exact positive integer
before identity comparison; matching duplicates and receipt bindings do not replace
this per-proof validation.
The immutable abandonment command is revalidated for both current and pre-upgrade
receipt proofs at every applicable successor and final write fence; a current
`head_ref` never replaces that check.
Each candidate root binding is checked against the expected binding with canonical
JSON equality before lineage traversal; numeric aliases such as `1.0` for version
`1` fail closed even when only one retained proof exists.
A source-task publication fence repeats exact pull/head and source-authority checks
after its final task, workflow, comment and root reads. It binds the enrollment's
retained receipt proofs, abandonment records and related actions to the fresh final
state before posting; unrelated PR state does not widen that fence. The commit-time
pull-identity fence permits isolation only for a structurally valid, open PR with
a changed pull identity, so that PR receives no state or writes while other enrolled
PRs continue. It applies regardless of optional starter-source proof. Direct
snapshots, malformed identities and terminal pulls remain strict.

For source and reviewer publication, the coordinator re-fetches the exact pull after
final authorization and projection checks, then rechecks the local proof projection
with canonical JSON equality before publication, so numeric aliases in the retained
proof projection cannot pass ordinary Python dictionary equality. This finite
ordering closes changes observed during legacy root/task reads; it is not a remote
compare-and-swap and does not claim network atomicity. Task occupancy and workflow
reads are completed before this final pull; no later remote read is treated as part
of that fence. A changed head, identity, or base blocks publication. The source
reservation remains truthful and is superseded; a reviewer request is not claimed.
Repeated polls do not replay either write.
Malformed retained `receipt_proofs` that are not lists deny owner-abandonment head
authority for that PR without aborting unrelated PR work. An empty list and valid
legacy proofs retain their existing meanings.
Legacy receipt actions may omit their embedded `key`; an omitted key remains
distinct from an explicit null, malformed, or mismatched key. Conflicting receipt
representations cannot erase a separately authenticated `policy_broken` proof:
its result head stays blocked, and conflicting ready records do not restore source
or reviewer publication authority. Authenticate non-ready receipts from both live
actions and retained proofs before filtering representation conflicts, including
live/live conflicts. This does not promote conflicting ready evidence or trust
unauthenticated blockers, and it leaves stored receipts and histories unchanged.

A same-head ready receipt is still a valid direct continuation, but it cannot hide a
cycle in a later edge. At the abandonment root, one bound `A -> A` receipt may be
followed by exactly one authenticated ready source receipt reserved later from `A`
to a different head. The self-edge is consumed once; any additional competing edge
or later cycle still fails closed. A second `A -> A` receipt does not advance the
lineage and is rejected.
Live and retained copies must agree on reservation `created_at`, including field
presence and value, before that timestamp can establish same-head root ordering.
This only proves terminal occupancy; ordinary
source authorization, fresh review/check, current-main and owner-authorized
result-head fences remain separate. The recovery head remains continuation-only only while it lacks
general receipt provenance. A distinct, bound ready receipt that returns the same
head releases this restriction, including after a cold restart; recovery alone
never does.
Reactivation, duplicate commands, restart, or uncertain persistence must not cause
a repeated POST, notice, or accepted result.

A later owner-enrolled supervised task is not a coordinator receipt. Its enrollment
or terminal task state alone grants no result authority. Any separate support after
a neutral main merge must require an unchanged exact-head owner enrollment and
positive authenticated ancestry from the abandonment-rooted ready result, with the
same repository, pull request and branch. Until that contract is implemented and
verified, this separate supervised-descendant case remains unsupported and is
tracked separately in issue #110. PR #109 closes only issue #108's receipt-lineage
slice; it does not recover PR #99 or implement the issue #110 contract.

Neutral task publication uses one immutable receipt-only PR reply, not a decision
comment followed by a receipt comment. The complete per-hunk decision audit stays
in the merge commit message. The existing strict v2 parser accepts only supported
quoted context before the exact final receipt block; the producer validates the
whole carrier before publication and the consumer parser remains unchanged. An
edited comment remains invalid; there is no retry or edited-receipt fallback.

Finite acceptance for this recovery:

- After the last neutral-anchor readback (or source action claim), the actual
  dispatch fence refreshes complete task occupancy, comments, and each exact
  recovered task and immutable owner command. Completed tasks with changed
  creator, artifacts, session, or prompt, reactivation, missing detail, API errors,
  incomplete inventory, and command edits/deletion must block the successor POST.
  The original reservation and recovery record must still match at this fence.
- A synthetic completed known-ID uncertain task with an unusable receipt can be
  abandoned by one exact immutable owner command, then the coordinator can plan and
  dispatch a distinct bounded task without reposting the old task.
- A valid distinct continuation may return a different result head; that head is
  accepted only from its immutable ready receipt after a genuinely new durable
  reservation records an explicit attested binding to the committed recovery;
  same-second older reservations or copied/malformed bindings fail closed.
  Copilot review handoff then proceeds across cold restart without duplicate work.
- Source and review-followup fix reservations are eligible; independent review and
  report-correction reservations are not.
- A current-head owner command that names a fully observed local nonmatch (for
  example an ineligible review/report-correction reservation, pull/ref mismatch,
  or unsupported local receipt/identity metadata) is consumed once through the
  ordinary durable scan path, grants no recovery or continuation, preserves the
  original reservation bytes/history, and cannot block unrelated PR work. Remote
  ambiguity, malformed snapshots, pagination gaps, API errors, or post-admission
  changes still fail closed with no recovery and no network write.
- Duplicate command delivery, process restart, and a second poll preserve the same
  recovery record, source/neutral counters, uncertainty and lifecycle history, and
  produce no duplicate POST or notice.
- Reject active, reactivated, absent, unknown, malformed, or API-error tasks; task
  or command ID mismatch; wrong creator/owner/repository/PR/node/branch artifacts;
  mismatched prompt/session/timestamps; edited/deleted commands; changed head,
  main, or reservation; incomplete task pagination; and persistence failure.
- Prove that a failed or incomplete final fence makes no durable recovery change or
  network write, and that neutral instructions, serialized synthetic carrier, and
  strict immutable receipt validation use the same one-comment contract.

#### Append-only continuation disposition after supervised advancement

New SOURCE successors positively bound to an `owner_continuation` disposition
reserve one create-once owner receipt anchor before task publication. Its durable
identity binds the disposition, action, PR identity, branch, exact head/main/base,
attempt, nonce, and original request. Authenticate the immutable owner comment by
complete inventory and direct readback. Uncertain POSTs are never retried; only
authenticated readback of the original reservation can recover them. Retirement,
restart, and re-enrollment must not renew this publication allowance.

The exact outgoing task prompt names `engine-tools-reply_to_comment`, the reserved
`commentId`, and the exact anchor body. It requires exactly one call with only the
eight canonical v2 receipt lines, never an edit, retry, or second reply. Validate
the existing complete bounded quote carriers before task publication and bind the
returned task session to that exact prompt. SOURCE `ready` means the actual pushed
result, not a neutral audit-bearing merge commit. Acceptance and retained proof
require the same immutable owner anchor and exact bot reply envelope. For a
SOURCE continuation proof, anchor validation is mandatory even if retained state
omits both optional anchor fields; generic v2 receipt parsing cannot replace it.
The observed anchor comment ID must be an exact positive integer at initial
receipt acceptance and retained-proof validation; equal float or boolean aliases
are not identity evidence.
Before the first receipt is accepted, a renewed owner enrollment may authorize
the successor result head. The committed continuation candidate can bridge that
head change only to the existing full receipt validator; it does not itself prove
or accept a receipt.
An inactive close/reopen may produce a fresh exact-head enrollment with
`authorized_head` but no `owner_authorized_head`. Admission, commit, and publication
use `owner_authorized_head` when that field exists; only when it is absent may the
exact `authorized_head` match the continuation's frozen current head. An explicit
stale owner-authorized head never falls back. The historical bindings and spent
budgets remain unchanged.
Ordinary historical SOURCE and all neutral transport semantics remain unchanged.

A positively confirmed `sent` Copilot review-request action is settled and is not
a source writer during continuation admission. `sending`, `uncertain`, malformed
review-request claims, source actions, and uncertain outbox publications remain
blocking.

After the final ordinary authority reads, the disposition fence authenticates the
immutable command and enrollment, then re-reads the exact PR and main and reloads
canonical StateStore data from disk. It validates exactly one immutable owner
disposition for the same historical action, task, and reservation from the complete
PR comment inventory. The reserved command must match the exact head, main, PR,
owner, and chronology; competing owner commands for that reservation reject the
continuation. Non-owner commands and unrelated owner chatter are not authority and
do not count as competing dispositions. This inventory check runs at admission and
again at publication, so commands consumed on an earlier poll are included.
An exact pending-comment lookup only discovers its PR. For a PR with pending hints,
fetch the complete bounded comment inventory before accepting any command; do not
restore cached comment bytes absent from that inventory, and leave their IDs pending.

After the last historical-task and successor-anchor reads, independently
reauthenticate all three immutable owner records: the original abandonment
command against its retained historical binding, the fresh disposition, and the
fresh exact-head enrollment. Do not fill missing historical fields from the new
enrollment. Admission and publication both enforce this finite last-read fence.
Then read the final pull and main. The final pull must have `draft` exactly
`false`; absent or malformed values reject admission and publication.

The fence compares the complete affected enrollment, historical reservation,
continuation list, same-PR action claims, and outbox claims using canonical
type-preserving JSON. SOURCE anchor inventory and direct readback remain mandatory.
A continuation record must match the active enrollment's frozen comment ID, head,
base, owner-authorized head, and immutable pull/repository identity. Every member
of a multi-root cohort must carry that same enrollment-comment ID; close/reopen
cannot combine records from different enrollment epochs or renew a spent allowance.
Newly planned dispositions for one PR form one admission unit. If any member's
final fence fails, defer every newly planned disposition for that PR, remove all
their processed IDs and staged continuation records, and suppress dependent
publication plans together. Preserve previously committed history and claims, and
continue independent reconciliation for other PRs.
After the final historical-task reads, reauthenticate every disposition, original
abandonment command, and enrollment in that planned cohort. An earlier member
changing while a later member is read must defer the whole cohort before event
consumption or publication.
A positively confirmed ancestry mismatch rejects the affected PR's continuation.
An API failure during either initial ancestry comparison is unavailable evidence,
not a mismatch; leave the immutable disposition unconsumed for a later poll. If
durable state changed during preparation, discard both the prepared mutations and
all dependent scan plans, then rebuild the complete scan once from fresh durable
state.
Quarantine the failed continuation's PR for the rest of that poll: do not consume
its commands, reconcile its claims, publish, or apply stale observations to it.
Across a prepared-state conflict, carry only positively identified unconsumed command
IDs; candidates include existing pending IDs and command IDs processed by the failed
scan. New candidates must be unconsumed at the preparation baseline. Previously
carried IDs remain eligible unless current durable state explicitly records their
consumption; a newer watermark alone does not consume them. A continuation-fence
conflict carries all baseline pending IDs, including other PRs, discarded processed
IDs, existing retry exceptions, and every disposition ID in the failing cohort.
Every retry ID must be a canonical positive signed-64-bit integer. The retry list
is bounded and overflow fails closed. Do not carry authority or publication plans.
After the first conflict, reload and persist carried IDs as pending under the
StateStore writer lock before making any network request in the rebuilt scan.
Reauthenticate receipts, source authority, commands, and
lifecycle evidence during the rebuild. Compare the durable preparation baseline using
type-exact canonical JSON again under the commit lock. Before propagating a second
conflict, reload fresh durable state under the writer lock and atomically preserve
only bounded retryable IDs, excluding explicit event or disposition consumption.
Capacity or validation failure leaves that fresh state unchanged. Never replace
newer progress, proofs, actions, attempts, or cursor with discarded preparation.
A second conflict stops the run without committing stale prepared state. This retry is allowed only before scan
commit and external publication; it never replays an unknown external write.

Immediately before a continuation task POST, the coordinator acquires the existing
StateStore write lock, reloads and compares the fenced affected-state projection,
revalidates active exact-enrollment identity and writer occupancy, and validates
the exact successor claim, continuation, and anchor. The projection is scoped to
the affected PR, so unrelated PR state changes do not block publication. The lock
is held through the POST. This prevents stale affected local authority from being
published or overwritten while the request is in flight. GitHub offers no
transaction spanning the final pull/main reads and task POST, so a remote change
after those reads remains an unavoidable race; these checks do not claim remote
atomicity.

Neutral continuation successors use the existing neutral anchor, role, and
receipt instruction at both the outer fence and locked publisher. SOURCE
continuations keep their distinct SOURCE contract. Neither path renews an
anchor publication allowance or the source/neutral lifetime counters.

The multiple-root contract is a direct, frozen cohort, not transitive acceptance.
Each distinct historical action retains its original reservation, task, command,
uncertainty, and separately authenticated disposition. A single successor may
serve those dispositions only when its reservation explicitly binds each one at
the same exact enrolled head, main, PR, and branch. Authenticate every member's
own original command and latest task detail before publication, including
non-selected roots whose inventory rows still report completion. Missing,
ambiguous, changed, or active roots block the cohort. Never substitute one root's
authority for another or consume a disposition outside that frozen cohort.
Do not commit or journal new dispositions while a required historical root lacks
its own valid disposition. Read the complete bounded comment inventory for
retained abandoned claims, so a deferred immutable command does not fall outside
the ordinary polling cursor before the other disposition arrives. Independently of
the issue-update cursor, discover active enrolled PRs with retained valid
abandonment claims by reading their exact issue and pull identities; discovery is
bounded to 100 claims per poll. Closed or non-PR issues are not eligible, and
malformed identities, incomplete inventories, and API failures fail closed.
Preparation conflicts preserve unconsumed command IDs in the bounded pending-ID
list. On later polls, retrieve each ID through the exact issue-comment endpoint,
independent of both issue and comment update cursors. Accept the returned comment
only when its integer ID matches, its issue URL is the exact canonical URL in this
repository, and a fresh open issue response links to the exact canonical pull URL.
The ID is only a discovery hint; existing owner, body, edit, head, and identity
checks remain authoritative. Each ID causes at most one exact comment lookup per
poll; the scan reuses an issue already in its authenticated inventory and performs
at most one issue lookup per missing PR, caching 404, closed, and non-PR results
only for that scan. A 404 remains retryable, temporary API errors abort the scan
without consuming the ID, and malformed or mismatched responses fail closed.
The later comment inventory takes precedence over pending lookup hints; append
only absent IDs after validating inventory identities, including duplicates.
These IDs remain retryable even if the consumed-event watermark advances past them;
overflow aborts the scan before cursor or event state commits. Pending IDs do not
waive immutable owner-command revalidation and are removed only when consumed.
Edited or deleted pending commands cannot authorize a continuation.
One task and one receipt anchor spend one source or neutral lifetime attempt;
each included disposition permanently spends its one-successor allowance.
Only that task's authentic immutable ready receipt can directly satisfy the
included roots. Restart, compaction, and inactive close/reopen must preserve
all per-root bindings and spent allowances. A retained authenticated same-head
successor proof prevents later ordinary work from rebinding a spent disposition.
Malformed retained evidence remains blocked, not a fresh allowance.
After receipt acceptance, both the original abandonment command and its continuation
command must still authenticate unchanged before granting terminal/head authority,
including merge replanning and final publication.

A SOURCE or neutral successor cannot combine an ordinary
`owner_abandonment_intent` with an `owner_continuation_intent`. If a planned
successor would carry both, defer every new disposition for that PR before scan
commit or receipt-anchor publication, retain the immutable command IDs as pending,
and suppress that PR's dependent publication plans for the poll. Preserve unrelated
accepted scan progress and all existing claims, receipts, counters, and budgets.
This is a source-safety boundary only; it does not prove an operational recovery
path for such a mixed cohort. Combined abandonment and continuation successors
remain unsupported.

An existing owner-abandonment record whose recovery head no longer equals the
current PR head cannot be rebound by replaying its original command or by ordinary
re-enrollment. The only supported exception is this finite, append-only owner
disposition, consumed after the owner has disclosed and supervised the source
advancement:

```
/hermes continue-unverified <abandonment-comment-id> <task-id> <reservation-sha256> <current-head-sha> <current-main-sha>
```

The command is one immutable owner comment on the same PR issue. Its positive
decimal comment ID, task ID, and lowercase digests are exact values, not aliases.
It refers to exactly one already-persisted abandonment by that record's original
command-comment ID, task ID, and digest of the complete original reservation.
Those historical values must be verified from the original command, reservation,
and task evidence; later enrollment or task evidence must never fill a missing
historical identity.

Accept the disposition only for the same numeric issue and repository, pull request
REST and node IDs, head repository and branch, and a fresh active exact-head owner
enrollment. The command's head and main must match the live PR head and base, the
fresh enrollment, and the freshly observed main. Complete task inventory and task
detail, session and artifact identity, original prompt and reservation, immutable
original command, and positive latest terminality must all be revalidated. Require
complete pagination and writer occupancy, no other active writer for the PR, and
positive ancestry from the historical recovery head to the current head and from
the historical base to the current main. Any uncertain publication, incomplete
read, identity mismatch, ambiguous or competing disposition, or post-plan change
rejects the affected write.
When a fresh enrollment and disposition are discovered in one poll, the admission
candidate carries forward retained receipt proofs, abandonment records, and
continuation records before cohort validation; scan commit is not the first point
at which the validator may see that history.

A temporary or uncertain task-detail API failure is not a positive rejection.
Do not consume its disposition comment ID; retry the original immutable command
from the complete bounded comment inventory on a later or cold poll. A definitive
missing-task response and positively invalid, stale, edited, or ambiguous evidence
remain fail-closed and cannot authorize dispatch.

Persist a bounded record separately from the historical abandonment records. The
record binds the exact historical action and abandonment, immutable disposition
comment, current enrollment comment, current head and main, pull identity, and
acceptance time. It is append-only, unique for both the abandoned action and
disposition comment, and may authorize at most one distinct successor reservation.
The successor uses existing per-PR execution locks and lifetime accounting. This
authorization does not accept either task's result, create a ready receipt, grant
source provenance, qualify review, or authorize merge. A source or review-followup
successor must provide an authenticated immutable ready receipt through the new
single-publication SOURCE owner anchor; a neutral successor must use the existing
single-publication neutral owner receipt anchor.
Only that ready receipt can authorize later source or review advancement. The
disposition command is not itself a receipt or anchor. Preserve the original
abandonment, command, receipt bytes or absence, uncertainty, action reservation,
attempts, and spent budgets.

The disposition command is not accepted by the automatic enrollment or abandonment
anchors. A repeat, edit, deletion, competing command, close/reopen, or cold restart
must not create another disposition, successor task, anchor, command, reservation,
or budget renewal. It does not change the legacy parser, manual qualification,
review protection, production ledger, or deployment contract.

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
required checks and a complete review-conversation inventory. A positive exact-head
review with no actionable findings and resolved conversations can confirm progress
as before. A complete authenticated, owner-published
`changes_requested` report for the current head can also prove which tracked
independent-review findings remain or were resolved; the negative disposition is
progress evidence only and never grants review approval, merge eligibility, or
deployment authority. Task state, agent prose, a
changed SHA, code churn, unrelated green checks, elapsed time, changed issue text,
cosmetic wording, or new review IDs alone cannot count as progress.

For an owner-continuation successor, the historical dispatch head need not itself be
authorized when the exact continuation record, historical action, successor binding,
and immutable ready receipt are valid and the receipt result is the current
authorized head. A later descendant remains connected only through an unbroken chain
of valid ready receipts from that bound successor; a blocked or disconnected result
cannot release the abandoned reservation or advance progress. Recheck this chain
after compaction and restart before releasing historical occupancy or requesting
review.
Revalidate the immutable disposition command even when a retained ready receipt
establishes the successor chain. Edited or deleted commands deny the continuation
fallback at planning and final source/reviewer publication fences.

Prove this reachability from the matching direct successor receipt results. Global
authorization from a later owner enrollment, another continuation, or another
receipt root does not connect a head to this successor. Every traversed receipt must
be valid and `ready`; do not traverse a blocked intermediate. Accept any valid ready
path rooted at the matching successor, without requiring a single linear history.

Evidence collection is independent of dispatch caps and usable repair prose.
An unresolved thread with missing comments is not an empty inventory. Historical
main advancement does not invalidate a bound source receipt or its exact-head
completed review/check evaluation; it still cannot authorize current-main dispatch.
Terminal source tasks await a verified evaluation before another source reservation.
Pre-upgrade unscored receipts remain explicitly unknown rather than blocking forever.
Legacy shared counts are split only after authenticating every unique retained
task/session, prompt, and receipt chain, without inventing ordinals or task types.
For every receipt-backed recovered reservation, inline or hydrated and with or
without a saved attempt ordinal, the existing receipt validator's timezone-aware
chronology applies: task creation must be at or before session creation, the
authenticated receipt must fall within the session interval, and session completion
must not be in the future. Ordinal-bearing terminal reservations without receipts
still require valid task, session, and completion chronology. Hydrated task
`created_at` must match the authenticated task-list value exactly.
Missing, malformed, out-of-order, or list/detail-inconsistent timestamps preserve
unknown history and block dispatch; no timestamp or ledger value is synthesized.
New source and neutral follow-up request hashes include the
literal `budget-v2:` discriminator so split ordinals cannot collide with retained
legacy completed keys. Historical keys, bodies, nonces and receipts are unchanged;
both prompt forms remain authenticated under the same task/session bindings.
The namespace does not bypass local sent/uncertain claims or remote active tasks,
and never authorizes replay of a lost task POST.
Fully observed terminal failed verification allows only a nonprogress decision.
Completed required check runs with `failure`, `cancelled`, `timed_out`,
`action_required`, `neutral`, `skipped`, `stale`, or `startup_failure` are terminal
observations, not successful verification. They permit a no-progress evaluation
when the exact-head task and Copilot review are complete, but never resolution
credit or merge eligibility. Pending, null, unknown or malformed conclusions,
wrong app/head bindings and incomplete pagination keep evaluation pending.
A rejecting Copilot review supplies a negative review-stage disposition, not a
successful CI result; all three CI checks must be observed before scoring. A
complete exact-head Copilot COMMENTED review may also admit evaluation only when
its rendered full-media body proves a complete, unambiguous actionable finding
inventory. It is negative evaluation evidence, not review approval, and cannot
enable merge. For a linked `N open finding(s)` inventory, every live overview
target must match an unresolved thread comment
from that exact review and head in the authenticated, fully paginated thread
collection. The numeric comment/review/author identities, nonempty finding text,
and ordered comment/submission timestamps must agree. Absolute targets must
name this pull request. Discussion IDs require canonical positive ASCII digits
within the body budget; failed integer conversion denies admission without
aborting other pull requests. Duplicate, missing, extra current-review, resolved,
malformed, or mismatched targets do not admit evaluation. An `inline` label or
an unrelated unresolved thread alone is not proof. Positive open targets also
prevent a generic COMMENTED review state from supplying successful progress
or merge evidence. Unknown summary prose remains ambiguous.
Every COMMENTED admission also requires a structurally complete thread inventory
with unique bounded thread IDs and unique positive comment IDs, even when all
actionable findings are body-only. Each returned thread must contain at least one
fully collected comment; a complete empty inventory is valid. When inline targets
exist, exact target-to-comment, author, review-head, and timestamp matching remains
an additional requirement. This inventory prerequisite does not change the
existing CHANGES_REQUESTED path.
This admission is distinct from the owner-published proof required
to consume `progress_disposition.resolved`; a rejected, edited, superseded, or
otherwise unproven owner follow-up cannot earn resolution credit. Findings present
in the COMMENTED inventory remain current repair targets; COMMENTED state without
proved actionable findings has no negative authority. The existing
verified-resolution rules still govern progress credit.
An incomplete or older-encoding reservation baseline receives an explicitly
unknown decision only after fresh complete review/check evidence; it cannot clear
targets or alter the streak. Neutral advancement can leave an older source result
unscored; this remains explicit unknown history, not a current-head pending lock.

GraphQL review-thread IDs are validated as nonempty strings of at most 256
characters before entering the inventory. A malformed ID aborts the scan before
state, cursor, outbox, or external writes; it is never stringified or dropped to
make a partial page look complete.

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
Resolution credit additionally requires an authentic `ready` receipt whose result
head differs from its reserved starting head. A terminal failed task or same-head
ready receipt may be evaluated as a no-progress decision, but cannot clear targets
or reset stagnation when later review evidence drops a finding.
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
Copilot review. Source acceptance remains distinct from merge approval and
deployment.

When a paginated task-list item for a saved retained receipt has `session_count: 1`
but omits `sessions`, the coordinator may hydrate only that exact, uniquely listed
task ID with an authenticated task-detail GET. The list record must be terminal and
bound to the expected owner and repository; the detail must repeat its exact task
ID and terminal state, match those identities, and contain exactly one authenticated
terminal session with the saved receipt's task/session/nonce chronology and prompt
binding. Hydrated details are used only for that retained receipt's migration
decision; they never replace list inventory used for occupancy or duplicate
detection. Missing, duplicate, malformed, mismatched, or unauthenticated list/detail
evidence leaves history unknown and dispatch blocked. Hydration is bounded by the
saved reservation history and never creates task records or infers missing sessions.

An authenticated historical ready handoff may qualify for neutral reconciliation
when its recorded base is an ancestor of both the freshly read main and exact PR
head, and the PR's mergeability fields are a consistent confirmed pair: `true` with
`behind`, or `false` with `dirty`. The `false`/`dirty` case is available only to
neutral reconciliation; report-correction work retains the existing `true`/`behind`
historical rule. All other, unknown, or inconsistent combinations remain ineligible.
This exception authorizes only the existing bounded neutral reservation path; it
never treats the historical PR as current-base eligible or enables merging. Fresh
identity, receipt, ancestry, dispatch, and pre-send fences remain mandatory.

Before a legacy neutral result has an adopted receipt proof, only its exact
one-shot recovery read may cross the `false`/`dirty` fence without a ready proof.
This requires the already-reserved uncertain action's known task ID and creation
time, unchanged legacy-neutral dispatch prompt/nonce, owner/repository/PR identity,
original recorded PR base as captured in that prompt, exhausted receipt-wait count,
and unconsumed codec recovery revision. Under the execution lock, the fresh pull's
base must still equal the authenticated task checkout base, and both ancestry
comparisons must positively prove that checkout base is an ancestor of current
main and the exact result head. The ordinary task-detail, session, dispatch,
timestamp, unique actual receipt, and immutable comment checks still govern
adoption after GET. This recovery-only allowance does not make the result head
generally authorized and is not passed to source dispatch, report
correction/publication, review/status publication, or merge fences; failure or
unknown evidence remains blocked without repeating the claimed read.

A completed authenticated malformed review with an unused correction does not
occupy the historical `false`/`dirty` neutral path: planning emits neither a
correction action nor a correction anchor, and the available report retry is
retained. Active or uncertain correction reservations still occupy the workflow.
Correction anchors recheck their frozen main and behind-only historical eligibility
before enqueueing and publication; a newly dirty base suppresses publication and
yields to neutral reconciliation on the next scan. An unpublished anchor remains
pending while mergeability or ancestry eligibility is unavailable, so recovery to
the same confirmed behind binding can resume without replaying a publication.
Proven head or main changes still supersede it. Correction task dispatch and
review/status publication retain their independent fresh fences. Lost task POST
responses remain reserved and cannot cause a second neutral dispatch.

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
Unknown retained history is recoverable waiting, not budget exhaustion: neither
source nor neutral work is dispatched, and no exhaustion notice or lifecycle event
is created. The enrollment, counts, receipt proofs, historical events, and consumer
acknowledgements remain intact. When complete authenticated task/session history
returns, the coordinator resumes once using the same counters and proofs.

Hydrated task details are checked against each matched retained receipt even when
the record already carries an attempt ordinal: session completion time and the
nonce-bound prompt instruction must match. Ordinal-less reservations additionally
require that authenticated receipt binding and cannot infer a missing receipt.

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

The exceptional supervised semantic disposition for a generic request for a
non-required reviewer role is defined by
[`Supervised manual qualification`](git-development-spec.md#supervised-manual-qualification)
in `docs/git-development-spec.md`.
It does not change this collector's fail-closed result or any durable machine
authorization.

Owner-authorized issue #92 replaces independent-review merge gating. Technical
acceptance requires the latest authenticated Copilot review from reviewer bot ID
`175728472` on the exact current head, in state `COMMENTED` or `APPROVED`. Coding
bot ID `198982749` does not qualify. Review and thread pagination must be complete,
every thread resolved, and all three required CI checks completed successfully.
Missing, stale, pending, dismissed, rejecting, wrong-identity or malformed review
evidence blocks. A check alone is not a review. COMMENTED is eligible; do not
demand APPROVED-only or dispatch duplicate reviews after acceptance.
The automatic Copilot review ruleset requests reviews. The coordinator and manual
operator validate review evidence separately from GitHub's three hard branch CI
contexts. The internal `copilot-pull-request-reviewer` check is absent from GraphQL
`statusCheckRollup` and cannot serve as a required branch context. A missing review
still blocks merge; no replacement status, workflow wrapper, review service, or
extra human-review gate is required.
Task receipt handoff and review acceptance are separate. After the exact result
head, repository, base and authorization fences pass, the coordinator completes
the source-writing task handoff only when a complete review collection contains
a current authenticated Copilot COMMENTED or APPROVED review on that head.
Otherwise the task stays in
`waiting_review`, retaining its lock without consuming fixer or handoff-wait
budgets. This receipt handoff does not accept unresolved findings for merge.
Repair-progress evaluation uses completed review identity separately from merge
acceptance, so actionable findings can remain repair targets without blocking
the bounded repair budget. Unknown or incomplete finding inventories still defer
progress evaluation. Definite rejection never qualifies as completed acceptance.
The coordinator requests or re-requests the real Copilot review through the
existing bounded action flow. Completing a handoff is not merge evidence: merge
still requires the genuine Copilot review, resolved threads, required checks and
every other existing guard. Actionable, ambiguous or incomplete rendered body
evidence still blocks merge. Invalid starter-source provenance blocks new review
requests and retains its explicit diagnostic; it is not independent-review
authority. New merge decisions never dispatch independent review,
report-correction or owner independent-review publication actions, and never
publish `agent-review`. Historical records and active tasks remain intact; they
are not cancelled or repurposed and still occupy their existing task slots.

Review requests use one durable reservation per PR/head. Check authenticated
review and requested-reviewer readback before requesting the genuine reviewer.
Repeat events and cold restarts cannot duplicate that request. A lost or ambiguous
POST response stays reserved and is never blindly replayed. A new head needs fresh
review and a new head-bound reservation; no old success is copied.

### Historical independent-report transport (retired by issue #92)

The following report/publication contract records the former implementation.
It does not authorize new reviewer dispatch or publication and is not a current
merge requirement. Sensitive-path classification does not add an owner-approval guard.

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
path; no fixer attempt is consumed just for awaiting required Copilot review.
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
but does not require it; a status is not a substitute for reading the genuine
Copilot review. The current protected
policy is exactly `source-ci` (Actions app 15368), `integration-tests`,
and `issue-link` (Actions app 15368), with strict/up-to-date checks
and required conversation resolution. Missing, extra, or differently app-bound
required contexts fail closed. All three required checks must independently report
success; skipped, cancelled, missing, pending, failed, or incomplete checks are not
green. The coordinator never writes `integration-tests` or `source-ci` statuses.
It never synthesizes the Copilot review check or writes `agent-review` for new merge
decisions. Branch rules are collected
with explicit `per_page=100` and `page` pagination, bounded to 100 pages; only a
short final page proves completion. Errors (including an unavailable rules
endpoint), malformed pages/policy fields, or exhaustion of the bound fail closed.
The policy retains the union of classic and all ruleset requirements, including
separate app bindings for the same check context, and validates that union against
the three active required contexts. GitHub's classic-protection response may include
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
draft or conflict, all three required checks are green, the Copilot review gate
passes, and no fixer may be running. Both
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

Sensitive approval lifecycle events and outbox outcomes are legacy history only.
They are not generated for current processing, and pending legacy approval outcomes
are marked superseded rather than replaced with a new request. Lifecycle export
omits legacy `sensitive_approval` events without deleting durable records or changing
consumer ACK history. Legacy `sensitive_generation` and grant fields remain readable
for compatibility but are not revalidated, advanced, or used as a processing gate.
If filtering leaves no events, the previous export is removed rather than refreshing
an obsolete request. This is a polling snapshot of observed state, not a live
authorization endpoint.

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
These finite limits leave room for a short quoted anchor and the eight-line receipt
without admitting arbitrarily large stored proofs. For the neutral producer this
is the unchanged reply anchor, not decision audit or report text. Reject character
lengths over 8192 before encoding, reject malformed Unicode, then enforce the byte
limit before counting or splitting; enforce the line limit before splitting. The same limits
apply at first acceptance and persisted-proof revalidation. Newly anchored neutral
producers prevalidate the 54 complete carriers specified above, with actual bound
values, before their sole publication. The largest supported prefix is 92 bytes
and five LF characters at the 80-byte anchor bound; it yields at most 13 total lines.
This is a finite contract bound, not a universal provider bound. If any supported
carrier fails validation or exceeds these limits, stop before publication. Plain v2 remains
valid within these limits; the exact-body v1 contract is unchanged.

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
without inheriting review/check evidence. Proven HEAD authority
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
Compaction preserves every action correlated by map key, embedded reservation key,
or task ID to a retained abandonment root, and conflicting copies of retained
receipt proofs. Missing or foreign issue fields never sever that correlation.
Before the first successor, alternate representations of an abandoned reservation
or task deny admission too. Repeated apply polls, explicit retirement, and cold
restart must retain the original conflicting bytes, histories, and attempt counts
with no source or reviewer publication. Unrelated terminal actions still compact.

Malformed receipt-proof containers, entries, identities, or same-issue live
receipt aliases cannot preserve initial or separate owner-authorized head
permission while skipping blocker evidence. An absent or empty legacy proof list
remains valid; explicit null and malformed members fail closed before fallback or
bare-enrollment publication. Acceptance covers both proof orders beside an
authenticated same-head `policy_broken` receipt, real apply and final dispatch
denial, retirement, cold restart, and unchanged budgets and history.
Bare-enrollment reconciliation uses the complete shared receipt projection,
including attempt and repair metadata.
Canonical value or presence conflicts deny source and reviewer publication and
remain retained; identical or reordered canonical copies and authentic legacy
omissions remain valid.
An unauthenticated live copy that asserts a well-formed typed blocker may be
ignored only when it differs from a valid retained proof solely in `receipt_result`
and `receipt_body`. It is not blocker or receipt authority; the retained proof
remains authoritative. Any mismatch in identity, attempt, binding, head, or another
projection field still fails closed. Typed blockers prevent progress only when
their remote receipt is authenticated.
An authenticated live v1 projection may supply omitted legacy values to a
read-only candidate only after the receipt pair matches. Conflicting copies block
their affected start and result heads, including initial or separately authorized
heads and continuation fallbacks; unrelated authorized history remains usable.
Authenticated typed blockers remain visible during planning.
An authenticated retained receipt may omit its legacy reservation key. Reconcile
that proof by its exact task ID, without adding a key to persisted history.
Explicit null, empty, or malformed keys remain invalid. Keyless successor proofs
must match all other receipt fields in live copies; conflicting task or keyed
aliases deny source and reviewer publication after compaction and restart.
Bare final publication authorization uses this same canonical key/task
reconciliation before its snapshot fence; individually valid retained proofs
cannot hide a pre-existing conflicting live copy. This does not create SHA source
authority or add an authorized head to bare enrollment.

For a retained-only direct successor with an omitted key, authenticate the
original task, session, prompt and unique reservation marker before recovering
the original key into a read-only view. The same view proves an omitted branch
and supplies the original abandonment attestation key to source authorization.
Never infer that key from current enrollment or write it into retained history.
Missing or ambiguous original evidence and explicit malformed keys fail closed.
A live action may omit its embedded key when its action-map key proves it;
explicit key mismatches and task aliases still conflict. Key and task joins use
the existing v1 omission rules for receipt version, main SHA and task type, while
retaining authenticated supplied values in a read-only candidate for downstream
checks. Never backfill the retained proof. Require receipt base equality for v2
or an explicitly supplied main SHA, not an omitted v1 field.
The lineage and handoff checks use the same narrow exception for an
unauthenticated typed-blocker copy: only differences in `receipt_result` and
`receipt_body` may be ignored in favor of the authenticated proof. Authenticated
blockers and every key, task, storage-identity, attempt, binding, head, or other
projection conflict still fail closed. A read-only proof view never backfills or
promotes the retained proof or live action. Historical-base ancestry checks and
active-writer exclusion remain mandatory.
For an accepted anchored SOURCE continuation, the waiting-review handoff joins
the full live action to its retained proof, including receipt-anchor fields and
the original prompt and task request body. Cold restart must permit one genuine
Copilot review request for the authenticated ready receipt without changing either
stored receipt copy or replaying that request on the next poll. Missing or changed
remote evidence and conflicts outside the two receipt result/body fields still
deny publication.

ACK-backed lifecycle retirement does not erase history. The optional
`lifecycle_context` preserves each retired event's canonical payload and exact
identity for stable replay and merge-to-deployment correlation; it is validated
against a closed schema and owner/repository binding. Context is not independent
ACK authority, is never age-evicted, and remains inside the same 4 MiB total state
bound. If it fills the state bound, preparation fails before export or external
writes. This is finite retention, not unlimited history storage or unattended
activation approval.

## External policy boundary

The current source policy requires the exact three protected contexts above and
genuine exact-head Copilot review evidence; advisory `cloud-review` is neither required
nor synthesized. Any later protection change requires separate owner authorization
and new verification. The coordinator cannot alter branch protection.

Issue #92 synchronizes the current contributor review contract without changing
native compatibility, CI or deployment guards. Any future transition should
follow verified coordinator and branch-protection rollout, preserve exact-head
evidence and owner-enrollment/receipt provenance, and keep guarded deployment separate.

### Issue #92 coordinator test migration

The prior coordinator suite had 60 test functions renamed with the `_legacy_`
prefix. Their former names are inventoried below. Many asserted independent-review
report, correction-task, or `agent-review` status publication, which issue #92
retires. The collected replacements instead exercise the retained behavior,
including active and uncertain historical occupancy and malformed task identity:

| Formerly collected test names | Collected replacement coverage |
| --- | --- |
| `ambiguous_corrective_review_creation_is_never_reposted`, `completed_partial_review_report_persists_error_and_retries_once`, `completed_report_correction_repairs_parent_after_publication_crash`, `correction_anchor_identity_changes_if_main_advances_before_claim`, `failed_correction_child_write_recovers_reserved_parent_after_reload`, `historical_correction_publication_stops_when_dirty`, `historical_dirty_keeps_active_correction_occupied`, `historical_dirty_plan_ignores_existing_correction_anchor`, `historical_dirty_report_failure_plans_only_one_neutral`, `historical_report_correction_never_dispatches_on_dirty_base`, `historical_report_correction_yields_if_dirty_before_plan_writes`, `historical_report_failure_rejects_unconfirmed_reconciliation`, `historical_report_recovery_uses_fresh_main_and_keeps_retry_separate`, `invalid_formal_review_id_stays_uncertain_until_authenticated_readback`, `later_bound_review_supersedes_malformed_parent_without_correction`, `later_review_before_malformed_parent_anchor_skips_correction`, `later_review_does_not_release_active_or_uncertain_correction`, `lost_correction_post_is_rejected_after_delayed_readback_and_reload`, `malformed_terminal_task_containers_are_diagnosed_and_recover`, `new_bound_review_is_rechecked_before_correction_task_claim`, `nonobject_review_task_response_stays_unresolved_until_terminal_evidence`, `obsolete_sent_preclaim_correction_anchors_remain_bounded`, `pending_correction_publication_rechecks_live_reservation`, `pending_correction_replay_rechecks_fresh_main_before_publication`, `pending_historical_correction_anchor_never_publishes_when_dirty`, `pending_historical_correction_anchor_resumes_after_transient_fence`, `report_correction_is_not_published_after_its_snapshot_advances`, `review_report_correction_rejects_reused_task_id_without_reposting`, `review_report_correction_requires_authenticated_terminal_task_metadata`, `review_report_dispatch_and_publication_complete_handoff`, `review_report_findings_publish_and_trigger_bounded_followup`, `review_report_observation_does_not_suppress_terminal_diagnosis`, `review_report_recovery_bounds_external_session_metadata`, `review_report_rejects_findings_outside_complete_changed_inventory`, `review_report_rejects_forged_file_sha256_inventory`, `stale_correction_is_rejected_at_fresh_merge_fences`, `stale_correction_rejection_binds_exact_selected_publication`, `stale_negative_correction_cannot_dispatch_fixer_after_reload`, `stale_pass_cannot_complete_handoff_after_reload`, `terminal_review_report_outcome_remains_deduplicated`, `uncertain_correction_review_publication_reads_back_without_reposting`, `unrepresentable_review_inventory_persists_one_deduplicated_blocker` | `test_later_review_does_not_release_active_or_uncertain_correction` checks persisted sent/uncertain records (including malformed task identity), no task/status publication, and no occupancy release. `test_waiting_copilot_review_handoff_survives_restart_without_budget` and `test_completed_task_handoff_waits_for_copilot_without_independent_dispatch` cover retained handoff locks and only the genuine Copilot request. |
| `clipped_or_redacted_target_is_unresolvable_in_real_review`, `completed_negative_review_evaluates_only_complete_terminal_checks`, `current_independent_review_completes_handoff_without_copilot`, `invalid_review_timestamp_revokes_owned_success_on_same_head`, `mapped_partial_resolution_through_real_report_and_restart`, `negative_review_cannot_clear_incomplete_inventory`, `real_negative_review_source_evaluation`, `resolution_disposition_is_bound_to_real_report_envelope`, `terminal_source_waits_for_evaluation_then_resumes_once` | `test_current_copilot_rejection_advances_progress_but_cannot_merge`, `test_commented_copilot_findings_are_negative_progress_not_review_approval`, `test_commented_findings_evaluate_completed_receipt_and_resume_once`, `test_rejecting_review_progress_uses_non_review_checks_in_the_planner`, and the completed/current-head Copilot handoff tests cover bounded evaluation separately from review approval and merge acceptance. |
| `incomplete_preclaim_status_inventory_cannot_claim_or_post`, `new_head_publishes_status_in_first_cycle`, `rejected_status_generation_fails_cycle_instead_of_reporting_transition`, `returning_head_revokes_success_in_first_cycle`, `status_claim_never_invents_a_watermark`, `status_reconciliation_requires_durable_preclaim_id_watermark`, `status_transitions_can_repeat_on_same_head`, `uncertain_status_is_bound_to_generation_and_head`, `uncertain_status_reconciles_only_new_owned_remote_generation` | `test_foreign_retired_review_status_is_not_merge_authority`, `test_auto_merge_uses_current_three_checks_without_cloud_review`, and `test_required_policy_accepts_only_current_three_contexts_and_apps` cover the active check contract and reject retired review-status authority. No coordinator status is emitted for the retired context. |
| `current_independent_review_completes_handoff_without_copilot`, `invalid_review_timestamp_revokes_owned_success_on_same_head`, `same_head_handoff_uses_independent_review_not_copilot`, `structured_owner_comment_accepts_review_without_copilot_approval`, `waiting_independent_review_handoff_survives_restart_without_budget` | `test_completed_copilot_review_completes_handoff_without_independent_report`, `test_completed_copilot_comment_accepts_review_without_independent_report`, `test_waiting_copilot_review_handoff_survives_restart_without_budget`, and `test_completed_task_handoff_waits_for_copilot_without_independent_dispatch` cover current Copilot identity, state, head, and durable handoff behavior. |

The remaining retired-path cases are `test_auto_merge_uses_current_four_checks_without_cloud_review`,
`test_copilot_review_timing_does_not_control_handoff`,
`test_foreign_cloud_review_status_cannot_authorize_auto_merge`,
`test_independent_review_completes_handoff_without_copilot`,
`test_owner_comment_only_four_check_gate_replays_without_duplicate_dispatch`,
`test_required_policy_accepts_only_current_four_contexts_and_apps`, and
`test_waiting_independent_review_handoff_survives_restart_without_budget`; their
collected replacements are the corresponding current three-check and Copilot-only
tests in the preceding rows. The coordinator module currently collects 1,355
pytest cases. The source-level inventory compares 275 baseline coordinator test
functions with 219 current test functions; name changes and the explicit
retirement map above account for the difference, rather than an assertion that
the historical suite still runs unchanged.

Two read-only policy tests were replaced as well: former
`test_copilot_comment_or_missing_approval_is_advisory` maps to
`test_copilot_comment_or_approval_is_required` and
`test_ordinary_review_needs_no_independent_report_or_selected_review`; former
`test_review_requires_authenticated_exact_head_independent_evidence_and_complete_threads`
maps to `test_review_requires_authenticated_exact_head_copilot_evidence_and_complete_threads`.
The documentation test formerly named
`test_policy_documents_bounded_four_check_maps_and_independent_review` is now
`test_policy_documents_bounded_three_check_maps_and_copilot_review`. These remain
collected under their current names.
