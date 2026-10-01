# Required development workflow

This is the canonical Hermes Mobile source: https://github.com/lindayi/hermes-mobile.
The local integration checkout is `/home/lindayi/projects/hermes-mobile-git`.
Older `hermes-mobile-*` source copies are migration inputs, not deployment sources.

## Before editing

Read this file and the relevant specification. Fetch origin. Use a dedicated branch
and worktree per task under `../hermes-mobile-worktrees/<task>` from `origin/main`.
Never switch/reset another session's checkout or edit immutable deployed releases.
Check open PRs for overlapping work; preserve their intended behaviors.
Record acceptance cases first; demonstrate RED then GREEN for behavior changes.

## Verification and pull requests

Use `HERMES_TEST_PYTHON=/home/lindayi/projects/hermes-mobile/.venv/bin/python
python3 scripts/test.py python|js|browser -- <explicit test files>` for focused tests;
use the managed `all` suite for final integration. Do not bypass its isolation or
cleanup. Browser checks use the installed Playwright dependency cache. Never run
live probes, account enrollment, real model calls, or operator scripts as tests.

Commit and push task branches; open GitHub PRs. Do not push changes directly to main.
Each PR records scope, baseline, acceptance tests and exact observed verification.
An independent reviewer agent reviews the exact head SHA, posting a formal GitHub
COMMENT review with actionable inline comments. The same GitHub account cannot
approve its own PR; do not fabricate a human approval. Report the verified outcome
as the `agent-review` commit status only after review. Full managed integration
results are reported as `integration-tests` on the exact tested head SHA. Hosted
`source-ci` is separately required. New commits invalidate all old-head evidence.

Address findings with follow-up commits, not silent amendments. Reply to each
review thread with the fix commit and test evidence; resolve it only after checking
that the finding is addressed. Preserve the discussion and dissent where relevant.
All required checks must pass, all review threads be resolved, and the branch be
current with main before merging through GitHub. No owner/admin bypass.

## Parallel integration

Merge and deploy one revision at a time. Merge updated origin/main into a PR branch
rather than force-pushing reviewed history. For conflicts, gather both PR intents,
base and both diffs; use a neutral reviewer/reconciler, combine compatible behavior,
regenerate derived files and add tests for both sides and their interaction. Record
resolution decisions in GitHub. Rerun tests and review on the resulting head.
Do not resolve wholesale with ours/theirs. Ask the owner only for genuinely
incompatible product requirements; technical conflicts are the agent's job.

## Deployment and cleanup

Only the canonical clean checkout of freshly fetched origin/main may deploy.
Use the guarded main deployment entrypoint; retain existing drain, lock, native
fingerprint and rollback protections. Never restart active sessions or use legacy
candidate operators to bypass Git provenance. Record the deployed commit.

After a PR is merged, verify the remote merge and preserve any uncommitted work,
then remove that task's worktree and merged local/remote branch. Clean disposable
build/test artifacts with the managed cleanup path. Never delete another active
session's files or unknown/unmerged work. Git history replaces source snapshots;
retain only bounded operational rollback releases still referenced by running
services, and runtime data/backups required for recovery. Do not sweep old release
roots while any service still references them.
