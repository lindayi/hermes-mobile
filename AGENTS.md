# Contributor workflow

This is the authoritative, portable guide for Hermes Mobile development. Keep it
under 20,000 characters; detailed contracts belong in `docs/`. Read this file and
the relevant product specification before editing. The required review and final
integration gates are governed by [docs/git-development-spec.md](docs/git-development-spec.md),
with portable execution details in [docs/development-workflow.md](docs/development-workflow.md).
See [README.md](README.md) for the canonical source and local integration checkout.

## Plan and isolate work

Start from an approved actionable issue. Search existing issues and pull requests
for duplicates and overlapping work, then record the baseline, scope, and observable
acceptance cases before implementation. Work on a task-specific branch/worktree
based on freshly fetched `origin/main`; do not switch, reset, or edit another task's
checkout. If another change overlaps, preserve its intended behavior and coordinate
before expanding scope.

Start from an approved issue; search for duplicates and overlapping PRs, then record scope and acceptance cases before implementation.

For behavior changes, preserve existing assertions and demonstrate a real RED test followed by GREEN.

Use the managed test runner with explicit test paths for focused local checks; choose Python through HERMES_TEST_PYTHON or the repository .venv, never a fixed owner-specific path.

Until the owner-verified gate transition is activated, final PR coverage combines
required hosted `source-ci` (all portable Python, JS and generated-assets browser
shards) with the entire residual host-compatibility suite, using the same selected
Python:

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/ci_tests.py host
```

Run only focused regressions locally during iteration. Do not repeat hosted suites
on the production server routinely. The explicit host manifest is
`.github/host-tests.json`; new files default to hosted execution. The full managed
`all` remains an opt-in diagnostic and the conservative release-stage gate.

The active required contexts are `source-ci` (Actions app 15368),
`integration-tests`, `agent-review`, and `issue-link` (Actions app 15368), with
strict/up-to-date checks and resolved conversations. Do not require, synthesize, or
publish `cloud-review` as a required status. Any future policy change needs a
separate owner-authorized protection change and verified current-head evidence.
See [`docs/autonomy-policy.md`](docs/autonomy-policy.md). The installed/private
host-compatibility suite remains required at guarded exact-main deployment; never
execute untrusted PR code on a production or self-hosted runner.

## Develop and test

For behavior changes, write a focused regression that fails for the intended reason,
then make the smallest complete change and show it pass. Keep existing assertions;
do not weaken or remove unrelated tests. Review the final diff for scope and data
handling.

Use the managed runner for local checks and provide explicit test paths for focused
work:

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py python -- tests/test_agent_instructions.py
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py js -- tests/browser/example.test.mjs
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py browser -- tests/browser/example.spec.mjs
```

Replace the example paths with existing tests relevant to the change. The selected
Python must have the locked test dependencies; the current source runner also uses
the Hermes-compatible Node executable provided by its environment. Use the
installed Playwright cache. Do not bypass runner isolation or its cleanup, and do
not run a local full suite merely because hosted CI runs one.

Use cloud execution by default for substantial coding, testing, and review; local work remains supported for small fixes and offline work under the same pull-request gates.

Never use production credentials, real accounts, real model calls, native production homes or databases, private evidence uploads, or live enrollment as tests.

When cloud setup is available, it uses an ephemeral environment and repository
lockfiles. The setup workflow prepares dependencies only; it does not run the full
suite. Browser installation and browser tests use a private
`PLAYWRIGHT_BROWSERS_PATH` and set `PLAYWRIGHT_SKIP_BROWSER_GC=1`.

No production credentials, real accounts, real model calls, native production
homes or databases, private evidence uploads, or live enrollment may be used as
tests. Use only public synthetic diagnostics. Never run live probes or operator
scripts as tests.

## Pull requests and review

Every change links its issue and specification, describes the baseline and scope,
records acceptance cases, and reports exact observed test/CI evidence. Use the
repository issue and pull-request templates. State clearly when a check was not
run; do not imply an unobserved pass.

Write PR descriptions as plain paragraphs using the repository template; include a literal Closes #N and all required evidence, with no Markdown markup in the body.

For task handoff, a literal Closes #N is a readability convention; verify linkage from authenticated GitHub closing-issue references, never PR body text.

A review comment is not an approval or a passing review status; verify every review and test result against the exact head SHA.

Request Copilot review for pull requests when it is available, and request a fresh
review after follow-up commits. Automatic review/re-review is controlled by GitHub
repository settings, not by these instruction files; do not claim this repository
has that setting enabled unless verified. A Copilot suggestion is feedback, not an
instruction: judge it against the code and tests. Authentication, deployment,
migration, and semantic merge-conflict changes require targeted independent review.

Body-only review evidence uses authenticated full-media `body_html`, never a raw
Markdown fallback. Keep rendered code/pre/blockquote inert and entity-decoded text
literal; see [docs/cloud-coordinator-spec.md](docs/cloud-coordinator-spec.md).

Every PR requires the latest authenticated, exact-head owner-published structured
independent-agent formal COMMENT review, a valid positive verdict and evidence
binding, complete review/thread pagination, and resolved conversations. Stale,
edited, removed, malformed, superseded, or unbound evidence fails closed; status
alone is not a review. Copilot feedback is supplemental: COMMENTED or missing
APPROVED alone does not block acceptance, repeat review requests, or consume fixer
budget. Actual open findings must be resolved, and definite rejection is not
acceptance. Sensitive changes still require separate exact-SHA owner authorization
and targeted independent review.

Required checks and protections are authoritative. Do not fabricate reviews,
approvals, check results, or identities. Review and test evidence applies only to
the exact head SHA; any new commit invalidates it. Address findings with follow-up
commits and reply to their actual GitHub threads. Never self-approve, bypass
protections, or write directly to `main`.

The exact four active contexts above must pass on the exact head SHA. Publish
`agent-review` only after verifying the independent review of that head. Publish
`integration-tests` only after verifying complete final integration under the
linked development workflow:
the entire residual host-compatibility suite and the matching hosted `source-ci`
aggregate must succeed on the same exact head SHA. Record both results and the
workflow URL. Never use only a hosted subset or only local host tests to claim full
coverage. Focused checks or dependency setup alone are not final integration.
Require all four active contexts, resolved review threads, and a branch current with
freshly fetched `origin/main` before GitHub merge. Never fabricate advisory status
success, retire a required context, or use owner/admin bypass.

Merge and deploy one revision at a time. Merge updated `origin/main` into the PR branch;
never rebase or force-push reviewed history. Gather both PR intents, the common base,
and both diffs for a neutral reviewer/reconciler. Preserve compatible behavior,
regenerate derived files from their sources, and test both intended behaviors and
their interaction; never resolve conflicts by wholesale choosing one side.
Record resolution decisions on GitHub and obtain independent review and final
integration evidence for the updated head. Escalate to the owner only for genuinely
incompatible product requirements, not routine technical conflicts.

## Privacy, merge, and deployment

Never commit credentials, private account/session content, production data, native
homes/databases, private operational evidence, or generated artifacts. Keep test
state synthetic and isolated. Preserve user/member isolation, shared-session
ownership, messaging, scheduling, and Docker-free deployment.

Merging and deployment are separate; only guarded deployment from verified main is allowed, and coding agents never access production.

Only the canonical, clean, freshly verified `origin/main` may be deployed through
the guarded deployment path. Preserve its drain, lock, native fingerprint, and
rollback protections. Never restart active sessions or use candidate operators to
bypass Git provenance. Record the deployed commit.

Native-controls maintenance normally uses `--hosted-run-id` with the independently
verified exact-main artifact; the scheduler-bound run ID and source SHA must match
at the worker, and the managed installed-runtime host partition still runs. Never
fall back to the full local suite when hosted evidence is missing;
`--local-full-checks` is an explicit diagnostic mode only.

Never edit immutable deployed releases. Older source copies are migration inputs,
not deployment sources. Port unfinished legacy work as a diff against its own verified
baseline into a new task worktree; never overlay an older source tree onto current main.
Preserve migration inputs until their work is merged or explicitly discarded.

Before cleanup, verify the remote merge and preserve any uncommitted work in a
recoverable location. Remove only that task's clean worktree and merged branch,
including its remote branch if still present, and disposable managed artifacts.
Preserve unknown/unmerged work, other active sessions' files, installed dependencies,
runtime recovery data and rollback releases still in use. Never sweep release roots
while any service references them.

## Product language and UX

Use **Sessions** and **New chat**, not “conversation”; do not add redundant summary
text. Keep Search, Filter, and New in-place. Swipe reveals Delete; tests must not
click hidden nodes. Use the native model picker. Put Stop at the right of Activity
and Expand at the top-right. Support parallel sessions.

Notifications have one master switch; show options only when enabled and save
category/privacy changes explicitly. Keep Compression, Jobs, and Inbox folded by
default. Inbox is read-on-open; put its actions at the title-right. Disclosures
must be short and scrollable, with fold controls reachable. Background results
retain their originating-turn chronology. Show rich tool previews in Activity rows
and fold bars. Use local times for user, assistant, and system messages.

Inbox/push is for meaningful outcomes, approvals, blockers, scheduled results,
and serious alerts—not routine child, tool, or process noise. Previews must be
informative and privacy-aware. Preserve owner/shared-session identity and member
isolation, along with existing messaging and scheduling. Be explicit about public shared-origin limitations; never disguise them.
