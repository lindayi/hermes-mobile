# GitHub migration acceptance

## Scope

Import the deployed application's source into public GitHub without changing its
runtime behavior or interrupting concurrent sessions. Do not import credentials,
account state, generated assets, private operational evidence or redundant archives.
The first README-only bootstrap commit is the sole direct-to-main initialization;
source import and subsequent changes use protected pull requests.

## Required observable outcomes

1. Repository `lindayi/hermes-mobile` exists; remote source readback matches commits.
2. Main requires `source-ci`, `integration-tests`, `agent-review`, an up-to-date
   branch and resolved review conversations; protections apply to administrators.
3. Zero separate human approvals are required. An independent agent records formal
   COMMENT reviews on exact commits. A passing status does not claim a different
   GitHub identity or a human approval.
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

Hosted GitHub Actions verifies syntax, secrets, all JavaScript/browser tests and
the portable Python partition without credentials or production access. The
explicit remaining native/private-operator partition runs through the managed
local `scripts/ci_tests.py host` suite. Exact-head integration status requires BOTH
partitions to pass. See hosted-ci-spec.md; `source-ci` is the fail-closed aggregate
of every hosted shard. No production-host self-hosted Actions runner executes
public PR code automatically. Main merges trigger CI, not deployment. Release
staging retains its conservative full test gate until exact-source/asset CI
evidence reuse is separately implemented and reviewed.
