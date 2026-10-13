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
