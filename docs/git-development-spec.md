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

The durable artifact is an append-only PR issue comment published by the
authenticated owner or an explicitly delegated operator. Disclose the unchanged
owner authority, exact delegate identity, scope, and publication permission.
Bot authorship or an assertion of delegation is insufficient. Publication and a
distinct fresh verification pass may use the same authorized operator: this is
not a second person, human security review, self-approval, or independent-review
gate. Sensitive owner commands still require the actual owner.
No runtime validator or coordinator qualification command is implemented.
Documentation tests check this contract, not a live artifact or authorization.

The comment contains exactly `<!-- hermes-supervised-qualification/v1 -->`, a
newline, one fenced `json` object, and a final newline. No other prose is allowed.
The whole comment is at most 60 KiB of UTF-8. The following is the exact top-level
field inventory for a `supervised-only` qualification or revalidation. Its named
object types also have exact key sets. Missing, extra, duplicate, mistyped, or
malformed fields block, as do unknown versions, nulls except where allowed,
truncation, and any exceeded bound. JSON booleans are not integers. This strict
schema is an audit format, not proof of acceptance.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | Literal `hermes-supervised-qualification/v1` | Fixed version. |
| `kind` | Enum `qualification`, `revalidation` | Lifecycle event. |
| `record_key` | Key | Unique create-once publication reservation. |
| `prior_comment_id` | ID or null | Null only for the first qualification; otherwise the superseded event. |
| `repository` | Repository | Immutable repository identity. |
| `pull` | Pull | Immutable PR identity. |
| `operator` | Operator | Authenticated publisher and disclosed owner authority. |
| `head_sha` | SHA | Frozen PR head. |
| `base_sha` | SHA | Freshly fetched main SHA, not just the PR merge base. |
| `reserved_at` | Time | Local publication reservation time. |
| `observed_at` | Time | Completion time of the coherent evidence snapshot. |
| `review` | Review | Selected latest exact-head Copilot review. |
| `review_inventory` | EvidenceRef | Complete reviews and nested threads/comments manifest. |
| `source_proof` | EvidenceRef | Source, receipt, reservation, ancestry, and diff proof. |
| `occupancy_proof` | EvidenceRef | Complete task/session and publication occupancy proof. |
| `checks` | Array of exactly three successful Check objects | One successful check per required context. |
| `source_run` | Successful SourceRun | Complete hosted Source run and attempt. |
| `sensitive` | Sensitive or null | Separate immutable owner command; null only for proven nonsensitive scope. |
| `role_request` | Text | Exact bot quotation, without rewriting or paraphrase. |
| `reasoning` | Text | Why that quotation is solely a non-required role request. |
| `machine_outcome` | MachineOutcome | Actual parser, collector, and authorization observations. |
| `status` | Literal `supervised-only` | Manual outcome, never automatic approval. |
| `reason` | Text | Outcome explanation, including every blocker or invalidation. |
| `evidence` | Array of 1–128 Evidence objects | Public responses, compound manifests, and bounded private attestations. |

`blocked` and `invalidated` are separate schema variants. They do not weaken this
successful qualification/revalidation shape, and must not be represented by filling
its required positive fields with invented values.

The exact top-level keys for a `blocked` qualification or revalidation are
`schema`, `kind`, `record_key`, `prior_comment_id`, `repository`, `pull`, `operator`,
`head_sha`, `base_sha`, `reserved_at`, `observed_at`, `status`, `reason`,
`gate_evidence`, and `evidence`. Here `kind` is `qualification` or `revalidation`,
`status` is the literal `blocked`, `head_sha` and `base_sha` are always SHAs (never
null), `gate_evidence` has 1–32 typed observations, and `evidence` retains 1–128
bounded evidence entries. If the actual head or base cannot be read, there is no
public blocked record: preserve the failed or incomplete read privately, block before
qualification publication, and do not infer either binding. A published blocked record
exists only once both bindings are known; it then records every missing, failing,
pending, rejected, stale, unknown, or uncertain gate truthfully.

Gate observations are discriminated objects with exact keys:

- Check: `gate` (`check`), `name` (one of the three required contexts), `outcome`
  (`success`, `failure`, `pending`, `missing`, or `unknown`), `id` (ID or null),
  `app_id` (ID or null), `head_sha` (SHA or null), `conclusion` (Text or null),
  `check_status` (Text or null), `completed_at` (Time or null), `proof`
  (EvidenceRef), and `reason` (Text).
- Source run: `gate` (`source-run`), `outcome` (`success`, `failure`, `pending`,
  `missing`, or `unknown`), `id` (ID or null), `attempt` (integer 1–100 or null),
  `head_sha` (SHA or null), `run_status` (Text or null), `conclusion` (Text or null),
  `completed_at` (Time or null), `proof` (EvidenceRef), and `reason` (Text).
- Sensitive authorization: `gate` (`sensitive`), `outcome` (`authorized`,
  `not-required`, `missing`, `rejected`, or `unknown`), `command` (Sensitive,
  EvidenceRef, or null), `proof` (EvidenceRef), and `reason` (Text). `authorized`
  requires the complete `Sensitive` object directly in `command`; `not-required`
  requires `command=null` and proof of a nonsensitive diff. `missing` requires
  `command=null` and a complete command inventory proving absence. `rejected`
  requires an EvidenceRef to the actual rejected command and its observed rejection.
  `unknown` retains whatever command evidence exists and never implies acceptance.
- Other gate: `gate` (`review`, `source`, `occupancy`, `machine`, or
  `authorization`), `outcome` (`missing`, `pending`, `failure`, `rejected`, `stale`,
  `unknown`, or `uncertain`), `observation` (EvidenceRef or null), `proof`
  (EvidenceRef), and `reason` (Text). Null observation is allowed only when the
  referenced complete inventory proves that no observation is available.

Every observation has a nonempty reason and a proof reference to actual retained
evidence. For an observed check or source run, include the actual ID, head, conclusion,
and completion time when available, plus the API status when observed. A pending
check or run retains its actual pending status and null conclusion and completion
time. A `missing` result has null ID, app ID, head, API status, conclusion, and
completion time, with evidence of the complete endpoint inventory. `unknown`
preserves only fields actually observed. The root `observed_at` must be no earlier
than every referenced observation time.
Require all three check contexts when a complete check inventory was read. The
required app binding still applies to observed `source-ci` and `issue-link` checks.
The record is `blocked` if any required gate is missing, pending, failing, rejected,
stale, unknown, or uncertain. Successful observations in a blocked record do not
qualify it.

The exact top-level keys for an `invalidated` event are `schema`, `kind`,
`record_key`, `prior_comment_id`, `repository`, `pull`, `operator`, `head_sha`,
`base_sha`, `reserved_at`, `observed_at`, `status`, `changed_binding`, `reason`,
and `evidence`. `kind` and `status` are the literals `invalidation` and
`invalidated`; `prior_comment_id` is a positive ID. `head_sha` and `base_sha` preserve
the old frozen snapshot. `changed_binding` has exactly `head_sha`, `base_sha`,
`observed_at`, and `proof`; new head and base must both be known SHAs actually
observed, never null. If either fresh binding is unavailable, there is no public
invalidation event: preserve the failed or incomplete read and reservation privately,
and block publication and merge without inventing a binding or replaying publication.
Unavailable evidence prevents reuse of prior qualification, even when no invalidation
can be published. Its proof references fresh change evidence, including any changed
review, command, check, run attempt, or occupancy binding. `observed_at` is no earlier
than `reserved_at` or the change observation. The reason identifies the changed
surface. The head and base need not differ from the old snapshot when another bound
surface changes. The prior record retains the old positive evidence; the invalidation does
not copy it as current proof or invent a replacement review, command, check, or
machine observation. Retain an authenticated readback of the prior record and the
fresh change proof in `evidence`; the prior comment ID and old root bindings must
match that readback. The prior snapshot time must be no later than its readback
or the fresh change proof's retrieval; that retrieval must be no later than the
change observation. Every evidence retrieval must be no later than the root observation.

For either variant, evidence keys and references follow the same bounds, exact
discriminators, identity rules, and acyclic graph rules below. Every blocker and
change reference must resolve to actual retained evidence. An invalidation is
historical only; it cannot be promoted to `supervised-only`. A replacement
qualification references it and gathers fresh evidence.

Primitive types: ID is an integer from 1 through 2^63−1. SHA is exactly 40 lowercase
hex characters; Digest is exactly 64 lowercase hex characters. Time is a UTC RFC3339
string `YYYY-MM-DDTHH:MM:SSZ`, with a valid calendar date, no future value, and no
invented precision. Key matches `[A-Za-z0-9._:-]{1,128}`. Text is a nonempty UTF-8
string of at most 8192 bytes. URL is an HTTPS GitHub API or github.com URL of at
most 2048 bytes for this repository (the owner user endpoint is also allowed).
EvidenceRef is a Key naming exactly one `evidence` entry; keys and public paths are
unique. Compound fields reference a manifest, never pretend that one response
contains unrelated endpoints or local bytes. The reference graph is acyclic, with
a maximum reference depth of 8 and no dangling or unused entries. All references,
including delegation and correlation, participate in these checks.
Digest calculation never trims, normalizes, decodes HTML entities, or reserializes
the body being hashed.

Named object types, with all keys mandatory:

- Repository: `name` (literal `lindayi/hermes-mobile`), `id` (ID).
- Pull: `number` (ID), `node_id` (Text), `url` (URL).
- Operator: `login` (Text), `id` (ID), `delegation` (EvidenceRef to a public response).
  Authenticate publisher and owner identities from GitHub, not login strings.
  The reference retains the unchanged owner scope/authority and delegate identity,
  or the owner's direct preparation statement.
- Review: `id` (ID), `node_id` (Text), `author_id` (literal integer 175728472),
  `state` (enum `COMMENTED`, `APPROVED`), `commit_id` (SHA equal to `head_sha`),
  `submitted_at` (Time), `last_edited_at` (Time or null), `raw_sha256` (Digest),
  `rendered_sha256` (Digest), `metadata` (EvidenceRef to a review-metadata manifest). Require sha256 over the
  exact raw UTF-8 `body` and sha256 over the exact rendered UTF-8 `body_html`.
  Metadata retains authenticated full-media bodies and complete edit history;
  explicit null means proven no edit, not absent metadata. An edited review blocks.
- Check: `name` (enum `source-ci`, `integration-tests`, `issue-link`), `id` (ID),
  `app_id` (ID or null), `head_sha` (SHA), `conclusion` (literal `success`),
  `completed_at` (Time), `proof` (EvidenceRef to a check manifest). Require Actions app 15368 for
  `source-ci` and `issue-link`; `integration-tests` remains app-unbound but the
  observed app ID or explicit null must be retained. All heads equal `head_sha`.
- SourceRun: `id` (ID), `attempt` (integer 1–100), `head_sha` (SHA),
  `conclusion` (literal `success`), `proof` (EvidenceRef to a source-run manifest). Retain workflow identity,
  run URL, complete jobs/pages, and every hosted suite's success; its head must
  equal the root `head_sha`. Bind `source-ci`
  and `integration-tests` check IDs to the same run ID and run attempt; a rerun,
  partial pass, or another attempt's success is not this proof.
- Sensitive: `comment_id` (ID), `author_id` (ID equal to owner), `body_sha256`
  (Digest), `created_at` (Time), `updated_at` (Time equal to `created_at`),
  `review_id` (ID equal to `review.id`), `review_raw_sha256` (Digest equal to
  `review.raw_sha256`), `head_sha` (SHA equal to root head), `proof` (EvidenceRef to a sensitive manifest).
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
  Each reference names a machine manifest for that actual observation.
  An unavailable observation is `unknown`, never inferred acceptance.
- Evidence is a discriminated union, selected by `type`. Each alternative has
  only its exact keys below; none can substitute for another.
- Public response: `key` (Key), `type` (literal `public-response`), `path` (relative
  POSIX path of at most 256 bytes, without absolute, empty, `.` or `..` components),
  `sha256` (Digest), `bytes` (integer 1–2097152), `url` (URL), `retrieved_at` (Time),
  `method` (enum `GET`, `POST-read-only`), `request_sha256` (Digest), `identity`
  (Binding), `pagination` (array of 0–128 Page objects). Retain exact response and
  request bytes, including GraphQL query/variables and full-media headers, locally.
  POST-read-only permits only GraphQL queries, never mutations. The GraphQL
  `https://api.github.com/graphql` URL is allowed only with proved repository identity.
  Only approved public responses may enter this inventory; API visibility alone
  does not make task/session contents safe to publish.
- Binding: `repository_id` (ID), `pull_number` (ID), `head_sha` (SHA), `base_sha`
  (SHA). In `supervised-only` qualification/revalidation, all equal the root
  bindings. Invalidation and blocked variants retain each observed binding alongside the frozen root
  binding; label chronology and mismatches, and never treat old proof as current.
  Every root head/base and invalidation's fresh `changed_binding` head/base is a
  known SHA; an unread binding blocks publication, merge, and reuse of prior
  qualification (see the blocked and invalidated keys above).
  Check actual endpoint identities, numeric
  authors, PR node IDs, source SHAs, and command/review identities; a self-declared
  Binding or request URL is not identity proof.
- Page: `connection` (Text identifying the endpoint or nested connection),
  `page` (integer 1–20), `cursor` (Text or null), `next` (Text or null), `total`
  (integer 0–2000), `count` (integer 0–100). Null cursor starts a collection;
  null next means proved termination, not an absent field. For each connection,
  require all consecutive pages, matching next/cursor or REST next URL, consistent
  totals equal to summed counts, no duplicate nodes, and authenticated terminal
  `hasNextPage=false` or REST Link/last-page proof. Nested comments have their own
  connection identities. Noncollection responses use an empty pagination array.
- Manifest: `key` (Key), `type` (literal `manifest`), `purpose` (enum from the
  table below), `members` (array of 1–8 Member objects). Member: `role` (enum from
  its purpose row), `refs` (array of 1–20 distinct EvidenceRefs). Roles are unique
  and must equal the complete required set, not a subset. Each reference has the
  specified target type. Reuse a leaf across roles only if it actually proves both.
- Private attestation: `key` (Key), `type` (literal `private-attestation`), `kind`
  (enum `coordinator-reservation`, `supervisor-reservation`, `publication-reservation`,
  `machine-observation`), `reference` (Key locating an operator-only proof),
  `snapshot_id` (Key), `observed_at` (Time), `verifier` (Operator), `bindings`
  (PrivateBinding), `correlation` (array of 1–20 distinct EvidenceRefs to public
  responses, together covering all applicable immutable remote bindings), `result`
  (enum `verified`, `blocked`, `unknown`), `reason` (Text). No local paths or private
  hashes are public fields. Publish only owner-approved public bindings and this
  bounded attestation/reference; missing approval blocks publication.
- PrivateBinding: all Binding keys, plus `task_id` (Text or null), `session_id`
  (Text or null), `receipt_comment_id` (ID or null), `prompt_sha256` (Digest or null).
  Reservation correlation requires the actual task/session/receipt/prompt bindings;
  null is allowed only when genuinely inapplicable in a verified proof. A blocked
  or unknown attestation may use null for unavailable bindings, with the precise
  missing facts in its reason and a private deficiency observation at its reference;
  it never qualifies. Publication
  and machine observations instead correlate to their actual public review/command
  snapshot through `correlation`. A verified attestation requires the separately
  retained original proof and authorized fresh read; its assertion alone cannot pass.

| Manifest purpose | Required member roles and target types |
| --- | --- |
| `review-inventory` | `reviews`, `threads`: public-response; `metadata`: review-metadata manifest. |
| `review-metadata` | `full-media`, `edit-history`: public-response. |
| `source` | `tasks`, `sessions`, `receipts`, `prompts`, `commands`, `ancestry`, `diff`: public-response; `reservations`: coordinator/supervisor-reservation private-attestation. |
| `occupancy` | `tasks`, `sessions`, `workflows`, `comments`: public-response; `state`: coordinator/supervisor-reservation private-attestation; `publication-reservation`: publication-reservation private-attestation. |
| `check` | `check-runs`, `binding`: public-response. |
| `source-run` | `run`, `jobs`, `binding`: public-response. |
| `sensitive` | `command`, `edit-history`: public-response; `review`: review-metadata manifest. |
| `machine` | `observation`: machine-observation private-attestation; `source`: public-response. |

Root `review_inventory`, `source_proof`, and `occupancy_proof` reference the
review-inventory, source, and occupancy purposes respectively. Member arrays
contain every required response page and relevant record within the bounds, not
just one representative endpoint. Metadata and other compound proofs use the same
representation. Private proof also counts toward the 20 MiB bundle bound.

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

### SYNTHETIC compound proof example

This complete reference graph illustrates four compound proofs, not a full
qualification record or live acceptance evidence. All identities, digests, times,
and responses are fictional public synthetic diagnostics. The omitted surrounding
root fields retain the inventory above. The task response contains a synthetic
task/session inventory and original prompt; the separate receipt response binds
that task/session to the result. Real sessions needing detail hydration require
additional entries. Private originals remain
outside this graph. Documentation/example validation is test-only, not a runtime
validator.

```json
{
  "roots": {"review_inventory": "inventory", "source_proof": "source", "occupancy_proof": "occupancy", "machine": "machine"},
  "evidence": [
    {"key": "inventory", "type": "manifest", "purpose": "review-inventory", "members": [{"role": "reviews", "refs": ["rest"]}, {"role": "threads", "refs": ["graphql"]}, {"role": "metadata", "refs": ["metadata"]}]},
    {"key": "metadata", "type": "manifest", "purpose": "review-metadata", "members": [{"role": "full-media", "refs": ["rest"]}, {"role": "edit-history", "refs": ["graphql"]}]},
    {"key": "source", "type": "manifest", "purpose": "source", "members": [{"role": "tasks", "refs": ["task"]}, {"role": "sessions", "refs": ["task"]}, {"role": "receipts", "refs": ["receipt"]}, {"role": "prompts", "refs": ["task"]}, {"role": "commands", "refs": ["authority"]}, {"role": "ancestry", "refs": ["commit"]}, {"role": "diff", "refs": ["commit"]}, {"role": "reservations", "refs": ["reservation"]}]},
    {"key": "occupancy", "type": "manifest", "purpose": "occupancy", "members": [{"role": "tasks", "refs": ["task"]}, {"role": "sessions", "refs": ["task"]}, {"role": "workflows", "refs": ["workflows"]}, {"role": "comments", "refs": ["comments"]}, {"role": "state", "refs": ["reservation"]}, {"role": "publication-reservation", "refs": ["publication"]}]},
    {"key": "machine", "type": "manifest", "purpose": "machine", "members": [{"role": "observation", "refs": ["observation"]}, {"role": "source", "refs": ["commit"]}]},
    {"key": "rest", "type": "public-response", "path": "reviews.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1/reviews", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "reviews", "page": 1, "cursor": null, "next": null, "total": 1, "count": 1}]},
    {"key": "graphql", "type": "public-response", "path": "threads.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/graphql", "retrieved_at": "2026-01-01T00:00:01Z", "method": "POST-read-only", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "threads", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}, {"connection": "review-edits", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}]},
    {"key": "task", "type": "public-response", "path": "task.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/agents/repos/lindayi/hermes-mobile/tasks", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "tasks", "page": 1, "cursor": null, "next": null, "total": 1, "count": 1}]},
    {"key": "authority", "type": "public-response", "path": "authority.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/1", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "receipt", "type": "public-response", "path": "receipt.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/2", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "commit", "type": "public-response", "path": "compare.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/compare/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb...aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "commits", "page": 1, "cursor": null, "next": null, "total": 1, "count": 1}]},
    {"key": "workflows", "type": "public-response", "path": "workflows.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/actions/runs", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "workflows", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}]},
    {"key": "comments", "type": "public-response", "path": "comments.json", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bytes": 100, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/1/comments", "retrieved_at": "2026-01-01T00:00:01Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "comments", "page": 1, "cursor": null, "next": null, "total": 1, "count": 1}]},
    {"key": "reservation", "type": "private-attestation", "kind": "supervisor-reservation", "reference": "synthetic-original", "snapshot_id": "synthetic-snapshot", "observed_at": "2026-01-01T00:00:02Z", "verifier": {"login": "synthetic-operator", "id": 2, "delegation": "authority"}, "bindings": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "task_id": "synthetic-task", "session_id": "synthetic-session", "receipt_comment_id": 2, "prompt_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "correlation": ["task", "receipt", "authority", "commit"], "result": "verified", "reason": "SYNTHETIC authorized read of original supervisor reservation; not remote proof."},
    {"key": "publication", "type": "private-attestation", "kind": "publication-reservation", "reference": "synthetic-publication", "snapshot_id": "synthetic-snapshot", "observed_at": "2026-01-01T00:00:02Z", "verifier": {"login": "synthetic-operator", "id": 2, "delegation": "authority"}, "bindings": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "task_id": null, "session_id": null, "receipt_comment_id": null, "prompt_sha256": null}, "correlation": ["comments", "rest", "authority"], "result": "verified", "reason": "SYNTHETIC original create-once publication reservation."},
    {"key": "observation", "type": "private-attestation", "kind": "machine-observation", "reference": "synthetic-observation", "snapshot_id": "synthetic-snapshot", "observed_at": "2026-01-01T00:00:02Z", "verifier": {"login": "synthetic-operator", "id": 2, "delegation": "authority"}, "bindings": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "task_id": null, "session_id": null, "receipt_comment_id": null, "prompt_sha256": null}, "correlation": ["rest", "graphql", "authority", "commit"], "result": "verified", "reason": "SYNTHETIC observed parser denial retained privately; not acceptance."}
  ]
}
```

### SYNTHETIC gate outcome examples

These complete blocked and invalidated records use fictional values and known
synthetic head/base bindings; they are not live evidence. `fixture` is a test label,
not an artifact field.
Each record carries every top-level key, typed gate observations, and the complete
`evidence` entries that its operator delegation and gate observations reference,
with no unused entries. The invalidation retains a synthetic readback of prior
comment 99, whose body is the `blocked-checks` record below without its `fixture`
label, plus a separate fresh PR binding response. The prior response binds the old
root; the fresh response binds `changed_binding`. Neither is current qualification
proof. The unchanged authority response discloses the synthetic operator delegation.

```json
{
  "fixture": "blocked-checks",
  "schema": "hermes-supervised-qualification/v1",
  "kind": "qualification",
  "record_key": "synthetic-blocked-checks",
  "prior_comment_id": null,
  "repository": {"name": "lindayi/hermes-mobile", "id": 1},
  "pull": {"number": 1, "node_id": "SYNTHETIC_PR_NODE", "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1"},
  "operator": {"login": "synthetic-operator", "id": 2, "delegation": "authority"},
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "reserved_at": "2026-01-01T00:00:00Z",
  "observed_at": "2026-01-01T00:00:05Z",
  "status": "blocked",
  "reason": "SYNTHETIC known bindings; a check is missing or failing, Source is pending, and the owner command is missing",
  "gate_evidence": [
    {"gate": "check", "name": "source-ci", "outcome": "missing", "id": null, "app_id": null, "head_sha": null, "check_status": null, "conclusion": null, "completed_at": null, "proof": "checks", "reason": "SYNTHETIC complete inventory has no check"},
    {"gate": "check", "name": "integration-tests", "outcome": "failure", "id": 71, "app_id": null, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "failure", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC failed check"},
    {"gate": "check", "name": "issue-link", "outcome": "success", "id": 72, "app_id": 15368, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "source-run", "outcome": "pending", "id": 73, "attempt": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "run_status": "in_progress", "conclusion": null, "completed_at": null, "proof": "source-run", "reason": "SYNTHETIC Source run is still pending"},
    {"gate": "sensitive", "outcome": "missing", "command": null, "proof": "owner-comments", "reason": "SYNTHETIC complete inventory has no owner command"}
  ],
  "evidence": [
    {"key": "authority", "type": "public-response", "path": "authority.json", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "bytes": 101, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/1", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "9999999999999999999999999999999999999999999999999999999999999999", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "checks", "type": "public-response", "path": "checks.json", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "bytes": 102, "url": "https://api.github.com/repos/lindayi/hermes-mobile/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/check-runs", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "check-runs", "page": 1, "cursor": null, "next": null, "total": 2, "count": 2}]},
    {"key": "source-run", "type": "public-response", "path": "source-run.json", "sha256": "3333333333333333333333333333333333333333333333333333333333333333", "bytes": 103, "url": "https://api.github.com/repos/lindayi/hermes-mobile/actions/runs/73", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "owner-comments", "type": "public-response", "path": "owner-comments.json", "sha256": "4444444444444444444444444444444444444444444444444444444444444444", "bytes": 104, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/1/comments", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "comments", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}]}
  ]
}
```

```json
{
  "fixture": "blocked-sensitive",
  "schema": "hermes-supervised-qualification/v1",
  "kind": "qualification",
  "record_key": "synthetic-blocked-sensitive",
  "prior_comment_id": null,
  "repository": {"name": "lindayi/hermes-mobile", "id": 1},
  "pull": {"number": 1, "node_id": "SYNTHETIC_PR_NODE", "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1"},
  "operator": {"login": "synthetic-operator", "id": 2, "delegation": "authority"},
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "reserved_at": "2026-01-01T00:00:00Z",
  "observed_at": "2026-01-01T00:00:05Z",
  "status": "blocked",
  "reason": "SYNTHETIC known bindings; checks and Source succeeded, the owner command was rejected, and the latest review is unknown",
  "gate_evidence": [
    {"gate": "check", "name": "source-ci", "outcome": "success", "id": 81, "app_id": 15368, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "check", "name": "integration-tests", "outcome": "success", "id": 82, "app_id": null, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "check", "name": "issue-link", "outcome": "success", "id": 83, "app_id": 15368, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "source-run", "outcome": "success", "id": 84, "attempt": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "run_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "source-run", "reason": "SYNTHETIC complete Source run succeeded"},
    {"gate": "sensitive", "outcome": "rejected", "command": "rejected-command", "proof": "command-readback", "reason": "SYNTHETIC observed owner command was rejected"},
    {"gate": "review", "outcome": "unknown", "observation": null, "proof": "review-pages", "reason": "SYNTHETIC complete inventory did not establish a usable latest review"}
  ],
  "evidence": [
    {"key": "authority", "type": "public-response", "path": "authority.json", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "bytes": 101, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/1", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "9999999999999999999999999999999999999999999999999999999999999999", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "checks", "type": "public-response", "path": "checks.json", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "bytes": 102, "url": "https://api.github.com/repos/lindayi/hermes-mobile/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/check-runs", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "check-runs", "page": 1, "cursor": null, "next": null, "total": 3, "count": 3}]},
    {"key": "source-run", "type": "public-response", "path": "source-run.json", "sha256": "3333333333333333333333333333333333333333333333333333333333333333", "bytes": 103, "url": "https://api.github.com/repos/lindayi/hermes-mobile/actions/runs/84", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "rejected-command", "type": "public-response", "path": "rejected-command.json", "sha256": "5555555555555555555555555555555555555555555555555555555555555555", "bytes": 105, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/85", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "command-readback", "type": "public-response", "path": "command-readback.json", "sha256": "6666666666666666666666666666666666666666666666666666666666666666", "bytes": 106, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/85", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "review-pages", "type": "public-response", "path": "review-pages.json", "sha256": "7777777777777777777777777777777777777777777777777777777777777777", "bytes": 107, "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1/reviews", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "reviews", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}]}
  ]
}
```

```json
{
  "fixture": "blocked-unknown-gate",
  "schema": "hermes-supervised-qualification/v1",
  "kind": "qualification",
  "record_key": "synthetic-blocked-unknown-gate",
  "prior_comment_id": null,
  "repository": {"name": "lindayi/hermes-mobile", "id": 1},
  "pull": {"number": 1, "node_id": "SYNTHETIC_PR_NODE", "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1"},
  "operator": {"login": "synthetic-operator", "id": 2, "delegation": "authority"},
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "reserved_at": "2026-01-01T00:00:00Z",
  "observed_at": "2026-01-01T00:00:05Z",
  "status": "blocked",
  "reason": "SYNTHETIC known bindings; the only blocker is the unknown latest review",
  "gate_evidence": [
    {"gate": "check", "name": "source-ci", "outcome": "success", "id": 81, "app_id": 15368, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "check", "name": "integration-tests", "outcome": "success", "id": 82, "app_id": null, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "check", "name": "issue-link", "outcome": "success", "id": 83, "app_id": 15368, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "checks", "reason": "SYNTHETIC successful check"},
    {"gate": "source-run", "outcome": "success", "id": 84, "attempt": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "run_status": "completed", "conclusion": "success", "completed_at": "2026-01-01T00:00:01Z", "proof": "source-run", "reason": "SYNTHETIC complete Source run succeeded"},
    {"gate": "sensitive", "outcome": "not-required", "command": null, "proof": "diff", "reason": "SYNTHETIC complete diff is nonsensitive"},
    {"gate": "review", "outcome": "unknown", "observation": null, "proof": "review-pages", "reason": "SYNTHETIC complete inventory did not establish a usable latest review"}
  ],
  "evidence": [
    {"key": "authority", "type": "public-response", "path": "authority.json", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "bytes": 101, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/1", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "9999999999999999999999999999999999999999999999999999999999999999", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "checks", "type": "public-response", "path": "checks.json", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "bytes": 102, "url": "https://api.github.com/repos/lindayi/hermes-mobile/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/check-runs", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "check-runs", "page": 1, "cursor": null, "next": null, "total": 3, "count": 3}]},
    {"key": "source-run", "type": "public-response", "path": "source-run.json", "sha256": "3333333333333333333333333333333333333333333333333333333333333333", "bytes": 103, "url": "https://api.github.com/repos/lindayi/hermes-mobile/actions/runs/84", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "diff", "type": "public-response", "path": "diff.json", "sha256": "8888888888888888888888888888888888888888888888888888888888888888", "bytes": 108, "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1/files", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "1010101010101010101010101010101010101010101010101010101010101010", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "files", "page": 1, "cursor": null, "next": null, "total": 1, "count": 1}]},
    {"key": "review-pages", "type": "public-response", "path": "review-pages.json", "sha256": "7777777777777777777777777777777777777777777777777777777777777777", "bytes": 107, "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1/reviews", "retrieved_at": "2026-01-01T00:00:02Z", "method": "GET", "request_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": [{"connection": "reviews", "page": 1, "cursor": null, "next": null, "total": 0, "count": 0}]}
  ]
}
```

```json
{
  "fixture": "invalidation",
  "schema": "hermes-supervised-qualification/v1",
  "kind": "invalidation",
  "record_key": "synthetic-invalidation",
  "status": "invalidated",
  "prior_comment_id": 99,
  "repository": {"name": "lindayi/hermes-mobile", "id": 1},
  "pull": {"number": 1, "node_id": "SYNTHETIC_PR_NODE", "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1"},
  "operator": {"login": "synthetic-operator", "id": 2, "delegation": "authority"},
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "changed_binding": {"head_sha": "cccccccccccccccccccccccccccccccccccccccc", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "observed_at": "2026-01-01T00:00:07Z", "proof": "fresh-head"},
  "reserved_at": "2026-01-01T00:00:06Z",
  "observed_at": "2026-01-01T00:00:08Z",
  "reason": "SYNTHETIC head changed after the prior blocked qualification",
  "evidence": [
    {"key": "authority", "type": "public-response", "path": "authority.json", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "bytes": 101, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/1", "retrieved_at": "2026-01-01T00:00:06Z", "method": "GET", "request_sha256": "9999999999999999999999999999999999999999999999999999999999999999", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "prior-record", "type": "public-response", "path": "prior-record.json", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "bytes": 102, "url": "https://api.github.com/repos/lindayi/hermes-mobile/issues/comments/99", "retrieved_at": "2026-01-01T00:00:06Z", "method": "GET", "request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []},
    {"key": "fresh-head", "type": "public-response", "path": "fresh-head.json", "sha256": "3333333333333333333333333333333333333333333333333333333333333333", "bytes": 103, "url": "https://api.github.com/repos/lindayi/hermes-mobile/pulls/1", "retrieved_at": "2026-01-01T00:00:07Z", "method": "GET", "request_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "identity": {"repository_id": 1, "pull_number": 1, "head_sha": "cccccccccccccccccccccccccccccccccccccccc", "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "pagination": []}
  ]
}
```

The examples contain no usable owner command, review, private proof, or live
observation. Their positive IDs and results exist only where the synthetic evidence
shows them. A blocked record cannot be changed to `status=supervised-only`: all
three successful checks, a successful complete Source run, and the valid sensitive
command or nonsensitive proof remain mandatory under the strict qualification schema.
A blocked record with only successful gates, or a record with a head/base missing,
null, or different from its evidence bindings, is invalid; never publish one.

### Publication and independent manual validation

Keep the retained public evidence bundle outside the checkout at
`${XDG_STATE_HOME:-$HOME/.local/state}/hermes-mobile/manual-qualification/PR/record_key/`,
with directory mode 0700 and files 0600. Store private proof separately in an
owner-only sibling directory `manual-qualification-private/PR/record_key/` with the
same modes. The public bundle and comment never contain private originals.
Retain exact comment/request/readback bytes and public responses in the public
bundle; reservation originals and observation records belong only in private
storage. Private evidence must not be uploaded. Never store credentials in either
bundle. Possession of copied bytes is not authority.

An authorized operator must independently repeat every read from the referenced
authenticated public endpoints, check identities, chronology and complete
pagination, and recompute hashes over the retained bytes. Fresh responses may
have different transport bytes or updated fields: retain and hash them separately,
compare the frozen facts, and block on a changed binding rather than invent byte
equality. The distinct fresh pass must also verify private originals as follows:

1. Establish the configured state origin and owning OS identity from trusted
   operator configuration, not from an arbitrary supplied JSON file. On that
   authorized non-coding-agent boundary, open the existing private
   `.<state-name>.run.lock` and `.<state-name>.lock` with `O_RDONLY` and
   `O_NOFOLLOW`, without creating files. Validate regular file, single link,
   owner, private modes and inode/path identity. Acquire `LOCK_SH | LOCK_NB` on the run lock
   first, then the state lock; contention or missing locks blocks, never stops a
   service or substitutes a new lock. These coordinate with StateStore's
   exclusive apply and mutation locks. No mutation method or apply cycle is called.
2. Under both locks read the exact existing state bytes once, with verified private
   directory/file ownership, no symlinks, stable inode and size, and
   `MAX_STATE_BYTES` bound. Preserve those bytes before parsing. Validate through
   the actual trusted version-1 StateStore read path on an isolated private copy,
   with no preparation state; `StateStore.snapshot() alone does not lock` and a
   missing/nonprivate file can return empty state, which is not absence proof.
   In-memory defaults are not original bytes. Version 1 has `cursor`, `events`,
   `event_watermark`, `enrollments`, `actions`, `outbox`, `retired`, and optional
   lifecycle data; require the applicable original fields and history, not invented
   defaults. Release locks after the coherent copy, without writing live state.
3. Retain privately the snapshot ID, trusted origin/configuration, OS owner and
   inode metadata, lock identities, source revision, exact byte hash/size, read
   start/end times, and verified bindings. Verify original `actions` reservations,
   enrollment counts, retained receipt proofs and tombstones against remote
   task/session/receipt/prompt/head/base records. `claim_action` persists its
   sending reservation and nonce-bound prompt before task POST; a current snapshot
   or tombstone alone cannot prove that earlier write. Require retained original
   pre-send provenance and chronology, never reconstruct it from a listed task.
4. A later disclosed supervisor reservation is not a coordinator action. Read the
   original supervisor create-once reservation and original request/response log
   from its independently established owner-controlled origin, under its actual
   writer lock or immutable closed-event boundary. Retain exact bytes, hashes,
   ownership and observation times privately. Correlate unchanged authority, prompt,
   starting/result head/base, task/session and receipt identity and pre-POST order.
   StateStore has no supervisor-reservation format to invent or import. If that
   original format, origin, lock or chronology cannot be verified, record the exact
   blocker. Neither reservation replaces the other or clears an uncertain claim.
5. Retain actual local parser/collector/authorization observation bytes, source
   revision, input snapshot hashes, execution origin, observation times, stdout,
   stderr and exit privately. Verify their coherent input correlation against the
   public responses and state snapshot. An operator attestation is
   not cryptographic remote proof of a local pre-POST write or machine execution.
   Public GitHub authenticates only remote facts. Missing, uncertain, mismatched,
   or unavailable original private proof blocks, even with a public attestation.

This is a read-only operator procedure, not a new validator command or permission
for a cloud coding task to inspect production, collect private proof, or attest it.
Do not rewrite receipts, claims, reservations, counters or history. Keep the actual
machine result and every original failure.

Use supported `gh api` commands as the authenticated authorized publisher. The
manually checked values are REPO=`lindayi/hermes-mobile`, PR=the target PR number,
and BUNDLE=the absolute directory above. COMMENT_ID is assigned only from a
validated positive integer in the successful POST response. PRIVATE is the separate
owner-only directory;
no actual private path is published. These are operator examples, not coding-agent
actions and not a new validator CLI. Set BUNDLE and PRIVATE to new, distinct absolute
record-key directories under already verified, owner-only, non-symlink parent
directories. Run the setup below once in the same shell. It refuses existing
destinations. It rejects noncanonical path components and symlinked parent ancestry
before any output or `mkdir`; each immediate parent must resolve to itself and be
owned by the current user with mode 0700. Choose a fresh record key rather than
reusing or changing permissions on an existing tree.

```sh
set -eu
umask 077
validate_record_path() {
  case "$1" in /*) ;; *) exit 1;; esac
  case "$1" in
    /|*/|*//*|*/./*|*/../*|*/.|*/..|/.|/..)
      exit 1;;
  esac
}
validate_record_path "$BUNDLE"
validate_record_path "$PRIVATE"
test "$BUNDLE" != "$PRIVATE"
case "$BUNDLE/" in "$PRIVATE/"*) exit 1;; esac
case "$PRIVATE/" in "$BUNDLE/"*) exit 1;; esac
for directory in "$BUNDLE" "$PRIVATE"; do
  parent="${directory%/*}"
  test -n "$parent" || parent=/
  test -d "$parent"
  test ! -L "$parent"
  test "$(realpath -e -- "$parent")" = "$parent"
  test "$(stat -c '%a' -- "$parent")" = 700
  test "$(stat -c '%u' -- "$parent")" = "$(id -u)"
done
for directory in "$BUNDLE" "$PRIVATE"; do
  test ! -e "$directory"
  test ! -L "$directory"
done
mkdir -m 700 -- "$BUNDLE"
if ! mkdir -m 700 -- "$PRIVATE"; then
  rmdir -- "$BUNDLE"
  exit 1
fi
for directory in "$BUNDLE" "$PRIVATE"; do
  test -d "$directory"
  test ! -L "$directory"
  test "$(stat -c '%a' -- "$directory")" = 700
  test "$(stat -c '%u' -- "$directory")" = "$(id -u)"
done
gh api user > "$BUNDLE/publisher.json"
gh api "repos/$REPO" > "$BUNDLE/repository.json"
gh api "repos/$REPO/pulls/$PR" > "$BUNDLE/pull.json"
gh api --paginate --slurp -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments?per_page=100" > "$BUNDLE/comments-before.json"
```

Before POST, prepare `comment.txt` in the exact format and persist a create-once
reservation using exclusive creation, for example
`( set -C; printf '%s\n' "$RECORD_KEY" > "$PRIVATE/reservation" )`.
Retain the frozen request digest, owner/repo/PR/head/base/review/command identities,
reserved time, and pre-send comment high-water ID with that reservation. Complete
the final evidence reads after reservation, then freeze the final comment/request
bytes and digest before POST. The publication commands below fsync the reservation,
comment, and request files and their parent directories before POST. Any open or
fsync failure stops the procedure before sending.
A reservation is for one POST only; never reuse its
key or reset it after failure. The event's `observed_at` must be no earlier than
`reserved_at` and review/command/check completion, and no later than GitHub's
comment `created_at`. Comment `updated_at` must equal `created_at`, with complete
GitHub edit history proving no edits. Server author/time/repo/issue URL and ID,
not self-declared JSON, supply the immutable publication binding.

```sh
sync_files_and_parent_directories() {
  python3 - "$@" <<'PY'
import os
import sys

for path in sys.argv[1:]:
    fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(
        os.path.dirname(path),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
PY
}
sync_files_and_parent_directories "$PRIVATE/reservation" || exit 1
jq -Rs '{body: .}' "$BUNDLE/comment.txt" > "$BUNDLE/request.json"
sync_files_and_parent_directories \
  "$BUNDLE/comment.txt" "$BUNDLE/request.json" "$PRIVATE/reservation" || exit 1
gh api --method POST -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments" --input "$BUNDLE/request.json" \
  > "$BUNDLE/create-response.json"
COMMENT_ID="$(jq -ser '
  if length == 1 and (.[0] | type == "object") then .[0].id
  else error("expected one response object")
  end
  | if type == "number" then . else error("id must be a number") end
  | select(. > 0 and floor == .)
  | tostring
' "$BUNDLE/create-response.json")"
gh api -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/comments/$COMMENT_ID" > "$BUNDLE/readback.json"
gh api --paginate --slurp -H 'Accept: application/vnd.github.full+json' \
  "repos/$REPO/issues/$PR/comments?per_page=100" > "$BUNDLE/comments-after.json"
```

Read back the returned ID and the complete comment inventory. Verify exact raw
body bytes, marker/schema, immutable authorized publisher/time/repo binding, unique record key,
and absent edit history. No PATCH or DELETE is permitted. Never replay an uncertain
POST: reconcile by fresh complete authenticated comment pages after the saved
high-water, match exactly one authorized publisher/body/key/repo/PR publication, then hydrate its
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
operator identity, request URLs/pages, and public digests in a new authorized `revalidation`
event (`operator` identifies its publisher). Reverify private originals separately.
This is a second manual read, not an
independent-review task or an extra required reviewer role.

`supervised-only` records only that narrow manual semantic disposition; it is not
merge acceptance, authorization, or autonomous-trial success. Any other failing,
missing, or uncertain gate requires `blocked`. Preserve the actual machine outcome;
never set `review_valid` or edit a ledger. A runtime rejection still blocks that
runtime path. In particular, if the existing sensitive command is rejected because
the selected review remains ambiguous, a manual record cannot authorize it:
record that exact contract conflict as `blocked`. Do not invent a command, positive
collector outcome, new status, or approval to bridge the conflict.

Read-only inspection of `selected_copilot_sensitive_review` and
`sensitive_review_authorized` shows that selection does not call the prose parser.
It checks the unique latest bot review, head/state, full-media bodies, raw digest
and submission identity; authorization separately binds the unchanged actual owner
command and its creation time. Do not infer either acceptance or rejection from
parser denial. Without actual command/state/collector observations, authorization
is `unknown`. The existing prose parser can still block review/merge independently;
this manual contract changes neither function nor runtime gate.

Immediately before any merge, perform and publish fresh complete revalidation,
including readback of every prior event and absence of competing successors.
Any head/base/review/command/check/run-attempt/occupancy change or edited/deleted
record invalidates the old snapshot immediately, whether or not an invalidation
has yet been published. Unavailable evidence also blocks reuse of the old
qualification immediately; absence of a publishable invalidation never permits
stale positive evidence. On change with known fresh head and base, append a new `invalidation` record
with a new key, `prior_comment_id`, `status=invalidated`, the old bound snapshot, current
observation time, and a precise reason plus fresh change evidence. If either fresh
binding cannot be read, keep the failed/incomplete read and reservation private,
block publication and merge, and reconcile without replay before any later event.
Old references are historical, not asserted current by an invalidation. A replacement qualification
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
