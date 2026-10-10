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
   `integration-tests`, `copilot-pull-request-reviewer` (Actions app 15368), and `issue-link` (Actions app 15368), an
   up-to-date branch and resolved review conversations; protections apply to
   administrators. `cloud-review` is advisory and is not a required context.
3. Under owner-authorized issue #92, every PR requires an authenticated exact-head
   Copilot review from reviewer bot ID `175728472`, in state `COMMENTED` or
   `APPROVED`, complete review/thread pagination, resolved threads, and the real
   successful required review check. Coding bot ID `198982749` does not qualify.
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
