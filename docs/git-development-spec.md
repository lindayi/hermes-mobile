# GitHub migration acceptance

## Scope

Import the deployed application's source into public GitHub without changing its
runtime behavior or interrupting concurrent sessions. Do not import credentials,
account state, generated assets, private operational evidence or redundant archives.
The first README-only bootstrap commit is the sole direct-to-main initialization;
source import and subsequent changes use protected pull requests.

## Required observable outcomes

1. Repository `lindayi/hermes-mobile` exists; remote source readback matches commits.
2. Main requires the exact contexts `source-ci` (Actions app 15368),
   `integration-tests`, and `issue-link` (Actions app 15368), an
   up-to-date branch and resolved review conversations; protections apply to
   administrators. `cloud-review` is advisory and is not a required context.
3. Under owner-authorized issue #92, every PR requires an authenticated exact-head
   Copilot review from reviewer bot ID `175728472`, in state `COMMENTED` or
   `APPROVED`, complete review/thread pagination, resolved threads, and all three
   successful required CI checks. Coding bot ID `198982749` does not qualify.
   Missing, stale, pending, dismissed or rejecting reviews block. No independent
   report or `agent-review` publication is required. Do not generate independent
   review/report-correction tasks; preserve historical records and active occupancy.
   Sensitive changes retain separate exact-SHA owner authorization and targeted review.
4. At least one real PR review is published; genuine findings receive follow-up
   commits and checked thread resolution. Do not invent findings for a demo.
5. Task worktrees isolate edits. Conflict reconciliation preserves both compatible
   intents and is reviewed/tested after updating against main.
6. Production deployment rejects dirty, untracked, stale, wrong-remote or unmerged
   sources before publication. Main commit provenance accompanies staged releases.
7. No production restart or migration is implied by importing identical source.
   If active work prevents safe rollout, report that boundary instead of disrupting it.
8. After merge remove task workspace/branch and disposable artifacts, not active
   sessions, required dependencies, runtime databases or still-referenced releases.

GitHub's automatic Copilot review ruleset requests reviews. Coordinator and manual
operators validate mandatory review evidence before merge; GitHub hard branch
checks enforce only the three CI contexts above. The internal
`copilot-pull-request-reviewer` check is absent from GraphQL `statusCheckRollup`
and cannot be a required branch context. An absent review still blocks merge.
Do not add replacement statuses, wrappers, review services, or human-review gates.

## Supervised manual qualification

This is a narrow operator disposition, not a new reviewer requirement, a parser
exception, or an approval. It applies only when the complete authenticated raw
and rendered review bodies for the latest exact-head Copilot review, and all
complete paginated nested review threads, have been examined. The review must be
current on the exact head and in `COMMENTED` or `APPROVED` state. The bodies must
be unchanged. The review's finding counts, sections, inline findings, body-only
findings, and thread states must agree. A zero-findings headline alone is
insufficient.

For example, the exact overview
“The authorization-sensitive coordinator and review-evidence changes require
final human security review despite extensive regression coverage.”
is a generic request for a non-required reviewer role; it does not itself
identify a source defect, an uncertain safety concern, or a requested source
correction. The operator may record that semantic disposition only when
authenticated evidence positively establishes that this role request is the sole
remaining issue. There may be no unresolved or uncertain source or safety
concern. Any substantive or ambiguous concern, requested correction, rejecting
review, malformed or count-inconsistent evidence, open thread, or missing,
edited, or stale evidence blocks qualification.

Qualification also requires a durable supervised qualification record tied to
the exact head and base, plus complete authenticated task/session evidence
showing no active or unknown task occupancy and certain source receipt and
reservation provenance. Verify fresh-main ancestry, all three current exact-head
required checks, and the same-run complete `source-ci` aggregate. For a
sensitive change, independently read back the actual exact-head sensitive owner
command and verify its binding to the selected review ID and SHA-256 of the exact
raw UTF-8 review body, including author, repository, review identity,
submission/edit chronology, and command time; the command must be created no
earlier than the unchanged review submission. The command mechanics are specified in
[`cloud-coordinator-spec.md`](cloud-coordinator-spec.md).

The record preserves the exact bot prose and the operator's reasoned
disposition. Any head, base, review, or command change invalidates it;
missing, uncertain, or failing evidence blocks. The manual record does not
change `review_valid`, the owner-authorization ledger, or collected evidence.
The parser and collector retain their actual fail-closed result; supervised
eligibility is recorded separately. It does not alter receipts, claims, budgets,
protections, or guarded deployment, and it is not an autonomous-trial success.
A later merge remains a separate, independently verified, normal protected
merge without bypass.

### Manual audit artifact v1

The durable artifact is an append-only PR issue comment authored by the repository
owner, not a bot or a delegated writer. A delegate may prepare evidence; the owner
must publish and independently verify it. This is documented manual validation.
No runtime validator or coordinator qualification command is implemented.
Documentation tests check this contract, not a live artifact or authorization.

The comment contains exactly `<!-- hermes-supervised-qualification/v1 -->`, a
newline, one fenced `json` object, and a final newline. No other prose is allowed.
The whole comment is at most 60 KiB of UTF-8. The following table is the exact
top-level field inventory; the named object types below also have exact key sets.
Missing, extra, duplicate, mistyped, or malformed fields block, as do unknown
versions, nulls except where allowed, truncation, and any exceeded bound. JSON
booleans are not integers. This schema is an audit format, not proof of acceptance.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | Literal `hermes-supervised-qualification/v1` | Fixed version. |
| `kind` | Enum `qualification`, `revalidation`, `invalidation` | Lifecycle event. |
| `record_key` | Key | Unique create-once publication reservation. |
| `prior_comment_id` | ID or null | Null only for the first qualification; otherwise the superseded event. |
| `repository` | Repository | Immutable repository identity. |
| `pull` | Pull | Immutable PR identity. |
| `operator` | Operator | Owner publisher and preparation authority. |
| `head_sha` | SHA | Frozen PR head. |
| `base_sha` | SHA | Freshly fetched main SHA, not just the PR merge base. |
| `reserved_at` | Time | Local publication reservation time. |
| `observed_at` | Time | Completion time of the coherent evidence snapshot. |
| `review` | Review | Selected latest exact-head Copilot review. |
| `review_inventory` | EvidenceRef | Complete reviews and nested threads/comments manifest. |
| `source_proof` | EvidenceRef | Source, receipt, reservation, ancestry, and diff proof. |
| `occupancy_proof` | EvidenceRef | Complete task/session and publication occupancy proof. |
| `checks` | Array of exactly three Check objects | One successful check per required context. |
| `source_run` | SourceRun | Complete hosted Source run and attempt. |
| `sensitive` | Sensitive or null | Separate immutable owner command; null only for proven nonsensitive scope. |
| `role_request` | Text | Exact bot quotation, without rewriting or paraphrase. |
| `reasoning` | Text | Why that quotation is solely a non-required role request. |
| `machine_outcome` | MachineOutcome | Actual parser, collector, and authorization observations. |
| `status` | Enum `supervised-only`, `blocked`, `invalidated` | Manual outcome, never automatic approval. |
| `reason` | Text | Outcome explanation, including every blocker or invalidation. |
| `evidence` | Array of 1–128 Evidence objects | Retained authenticated public evidence inventory. |

Primitive types: ID is an integer from 1 through 2^63−1. SHA is exactly 40 lowercase
hex characters; Digest is exactly 64 lowercase hex characters. Time is a UTC RFC3339
string `YYYY-MM-DDTHH:MM:SSZ`, with a valid calendar date, no future value, and no
invented precision. Key matches `[A-Za-z0-9._:-]{1,128}`. Text is a nonempty UTF-8
string of at most 8192 bytes. URL is an HTTPS GitHub API or github.com URL of at
most 2048 bytes for this repository (the owner user endpoint is also allowed).
EvidenceRef is a Key naming exactly one `evidence` entry; keys and paths are unique.
Digest calculation never trims, normalizes, decodes HTML entities, or reserializes
the body being hashed.

Named object types, with all keys mandatory:

- Repository: `name` (literal `lindayi/hermes-mobile`), `id` (ID).
- Pull: `number` (ID), `node_id` (Text), `url` (URL).
- Operator: `login` (Text), `id` (ID), `delegation` (EvidenceRef). Authenticate
  owner identity from GitHub, not a login string or this object's assertion.
  The reference retains the unchanged owner scope/authority and delegate identity,
  or the owner's direct preparation statement.
- Review: `id` (ID), `node_id` (Text), `author_id` (literal integer 175728472),
  `state` (enum `COMMENTED`, `APPROVED`), `commit_id` (SHA equal to `head_sha`),
  `submitted_at` (Time), `last_edited_at` (Time or null), `raw_sha256` (Digest),
  `rendered_sha256` (Digest), `metadata` (EvidenceRef). Require sha256 over the
  exact raw UTF-8 `body` and sha256 over the exact rendered UTF-8 `body_html`.
  Metadata retains authenticated full-media bodies and complete edit history;
  explicit null means proven no edit, not absent metadata. An edited review blocks.
- Check: `name` (enum `source-ci`, `integration-tests`, `issue-link`), `id` (ID),
  `app_id` (ID or null), `head_sha` (SHA), `conclusion` (literal `success`),
  `completed_at` (Time), `proof` (EvidenceRef). Require Actions app 15368 for
  `source-ci` and `issue-link`; `integration-tests` remains app-unbound but the
  observed app ID or explicit null must be retained. All heads equal `head_sha`.
- SourceRun: `id` (ID), `attempt` (integer 1–100), `head_sha` (SHA),
  `conclusion` (literal `success`), `proof` (EvidenceRef). Retain workflow identity,
  run URL, complete jobs/pages, and every hosted suite's success; its head must
  equal the root `head_sha`. Bind `source-ci`
  and `integration-tests` check IDs to the same run ID and run attempt; a rerun,
  partial pass, or another attempt's success is not this proof.
- Sensitive: `comment_id` (ID), `author_id` (ID equal to owner), `body_sha256`
  (Digest), `created_at` (Time), `updated_at` (Time equal to `created_at`),
  `review_id` (ID equal to `review.id`), `review_raw_sha256` (Digest equal to
  `review.raw_sha256`), `head_sha` (SHA equal to root head), `proof` (EvidenceRef).
  Read the actual `/hermes authorize-sensitive <head-sha> review
  <positive-review-id> <body-sha256>` command separately. Retain its exact body,
  GitHub identity, repository/PR binding, and complete edit history. Require command
  creation at or after review submission and at or before the observation.
- MachineOutcome: `review_valid` (boolean, actual observed value),
  `parser` (EvidenceRef), `collector` (EvidenceRef), `authorization` (EvidenceRef),
  `authorization_result` (enum `accepted`, `rejected`, `missing`, `unknown`,
  `not-required`), `blocked_reason` (Text). Record actual machine acceptance or
  rejection, including the blocked parser/collector disposition and the observed
  source revision and time. `not-required` needs nonsensitive diff proof.
  An unavailable observation is `unknown`, never inferred acceptance.
- Evidence: `key` (Key), `path` (relative POSIX path of at most 256 bytes, without
  absolute, empty, `.` or `..` components), `sha256` (Digest), `bytes` (integer
  1–2097152), `url` (URL), `retrieved_at` (Time). These are hashes of retained
  exact response bytes, not redacted replacements. An inventory entry may point
  to a manifest of responses from that endpoint; its member paths, hashes, sizes,
  retrieval times, and request/page/cursor identities must also be retained.

Each collection allows at most 20 pages of 100 nodes, including nested connections;
the bundle allows at most 20 MiB in total. A larger collection blocks this bounded
path; never sample, omit history, or treat a bound as end-of-pagination. Evidence
must include complete review/thread/comment pagination, totals and terminal
`hasNextPage=false`/REST last-page proof, finding counts/sections and disposition
for each finding. It must include complete source task/session, receipt, reservation,
and prompt bindings, immutable owner source command and issue chronology, starting
and result head/base, certain terminal completion, and retained failed/unknown
attempts and counters. Verify full repository task inventory against every known
reservation, then hydrate relevant task/session details. Active or unknown writers,
review workflows, claims, sent/uncertain publications, or unmatched reservations
block; a terminal task alone is not absence-of-occupancy proof.

### Publication and independent manual validation

Keep the retained public evidence bundle outside the checkout at
`${XDG_STATE_HOME:-$HOME/.local/state}/hermes-mobile/manual-qualification/PR/record_key/`,
with directory mode 0700 and files 0600. The PR comment is the durable public index;
retain exact comment/request/readback bytes, response manifest, reservation, and all
historical events locally. Another manual operator must independently repeat every
read from the referenced authenticated public endpoints, recompute the hashes, and
verify identities and chronology; possession of this local bundle is not authority.
Private evidence must not be uploaded or placed in the public bundle. If necessary
proof cannot be reproduced from public authenticated evidence, this path blocks.
Never store credentials in the bundle.

Use supported `gh api` commands as the authenticated owner. The variables below
are manually checked values: REPO is `lindayi/hermes-mobile`, PR is the target PR
number, BUNDLE is the absolute directory above, and COMMENT_ID is the returned
positive ID, not a guessed value. These are operator examples, not coding-agent
actions and not a new validator CLI.

```sh
gh api user > "$BUNDLE/owner.json"
gh api "repos/$REPO" > "$BUNDLE/repository.json"
gh api "repos/$REPO/pulls/$PR" > "$BUNDLE/pull.json"
gh api --paginate --slurp -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments?per_page=100" > "$BUNDLE/comments-before.json"
```

Before POST, prepare `comment.txt` in the exact format and persist a create-once
reservation using exclusive creation, for example
`( set -C; printf '%s\n' "$RECORD_KEY" > "$BUNDLE/reservation" )`.
Retain the frozen request digest, owner/repo/PR/head/base/review/command identities,
reserved time, and pre-send comment high-water ID with that reservation. Flush the
files durably before sending. Complete the final evidence reads after reservation,
then freeze the final comment/request bytes and digest before POST.
A reservation is for one POST only; never reuse its
key or reset it after failure. The event's `observed_at` must be no earlier than
`reserved_at` and review/command/check completion, and no later than GitHub's
comment `created_at`. Comment `updated_at` must equal `created_at`, with complete
GitHub edit history proving no edits. Server author/time/repo/issue URL and ID,
not self-declared JSON, supply the immutable publication binding.

```sh
jq -Rs '{body: .}' "$BUNDLE/comment.txt" > "$BUNDLE/request.json"
gh api --method POST -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments" --input "$BUNDLE/request.json" \
  > "$BUNDLE/create-response.json"
gh api -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/comments/$COMMENT_ID" > "$BUNDLE/readback.json"
gh api --paginate --slurp -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments?per_page=100" > "$BUNDLE/comments-after.json"
```

Read back the returned ID and the complete comment inventory. Verify exact raw
body bytes, marker/schema, immutable owner/time/repo binding, unique record key,
and absent edit history. No PATCH or DELETE is permitted. Never replay an uncertain
POST: reconcile by fresh complete authenticated comment pages after the saved
high-water, match exactly one owner/body/key/repo/PR publication, then hydrate its
ID and edit history. Zero matches, duplicate matches, incomplete pages, or a timeout
remain blocked/uncertain, even if a later list is empty. Preserve the reservation;
do not publish a second qualification to work around uncertainty.

The independent manual check repeats all bound reads, verifies fresh-main ancestry
and complete changed-file inventory, unchanged review bodies/identity/chronology,
all finding counts and resolved threads, source/provenance/occupancy, each required
check and the complete same-attempt Source run, and the separate sensitive command.
Compare raw and rendered prose semantically without changing the parser. Preserve
the exact quotation and explain why no substantive or ambiguous concern remains;
a headline or this schema is not sufficient. Retain validation time, validating
operator identity, request URLs/pages, and digests in a new owner's `revalidation`
event (`operator` identifies its publisher). This is a second manual read, not an
independent-review task or an extra required reviewer role.

`supervised-only` records only that narrow manual semantic disposition; it is not
merge acceptance, authorization, or autonomous-trial success. Any other failing,
missing, or uncertain gate requires `blocked`. Preserve the actual machine outcome;
never set `review_valid` or edit a ledger. A runtime rejection still blocks that
runtime path. In particular, if the existing sensitive command is rejected because
the selected review remains ambiguous, a manual record cannot authorize it:
record that exact contract conflict as `blocked`. Do not invent a command, positive
collector outcome, new status, or approval to bridge the conflict.

Immediately before any merge, perform and publish fresh complete revalidation,
including readback of every prior event and absence of competing successors.
Any head/base/review/command/check/run-attempt/occupancy change or edited/deleted
record invalidates the old snapshot immediately, whether or not an invalidation
has yet been published. On change, append a new `invalidation` record with a new
key, `prior_comment_id`, `status=invalidated`, the old bound snapshot, current
observation time, and a precise reason plus fresh change evidence. Old references
are historical, not asserted current by an invalidation. A replacement qualification
must reference that invalidation, gather entirely fresh proof, and preserve all old
records, failures, reservations, and counters. A revalidation references the latest
qualification/revalidation and repeats the full schema; it never revives an
invalidated snapshot. Missing history, uncertain publication, or competing
successors blocks until independently reconciled.

The eventual normal protected merge is separate: re-read current head and main at
the merge fence, use an expected-head SHA fence, require every ordinary protection
and sensitive authorization, use no bypass, and independently read back the merged
PR/main result. If these conditions cannot be satisfied without runtime override,
stop and report the conflict. This contract does not execute merge or deployment.

## Migration baseline

The baseline is a separately verified deployed release, not the older project
working directory. Runtime application source remains byte-identical except for
the push-contact default, which uses the public HTTPS URI https://lindayi.me
instead of personal email. Publication privacy changes also replace one test's
human-session fixture with synthetic data and sanitize specifications. This PR
also changes workflow/deployment controls. Pre-existing issues such as placeholder
naming are separate PRs after migration.

## Test environments

Hosted GitHub Actions verifies syntax, secrets, JavaScript/browser tests and the
portable Python partition without credentials or production access. `source-ci`
requires the complete hosted native suite and exact generated-artifact evidence.
The automatically emitted exact-head `integration-tests` check succeeds only when
the same-run complete `source-ci` aggregate succeeds; this hosted result gates
merging in every phase. The residual host-only compatibility partition is not
premerge PR evidence. The full `.github/host-tests.json` suite remains required
inside guarded exact-main deployment against the verified staged source and
artifact, before activation; a compatibility failure blocks activation. Never
execute public PR code on a production host. See
`hosted-ci-spec.md` and `autonomy-policy.md`. No production-host self-hosted
Actions runner executes public PR code automatically. Main merges trigger CI, not
deployment. Release staging retains its conservative full test gate until
exact-source/asset CI evidence reuse is separately implemented and reviewed.
