# Development workflow

This guide expands the rules in [AGENTS.md](../AGENTS.md), which remains the
portable source of truth. Search issues and open pull requests before starting;
link new work to its approved issue and its acceptance cases. Keep product specs
with the feature documentation, and do not use a task to expand into unrelated
application, CI, or deployment behavior.

## Supported development routes

Cloud execution is the default for substantial coding, testing, and review. Small
fixes and offline work are supported locally under the same branch, issue, review,
and required-check gates. Neither route may access production state or use real
accounts, credentials, model calls, or private evidence as test inputs.

The local test runner uses isolated workspaces. Choose the interpreter from the
environment or the repository virtual environment, and select explicit tests for
focused checks:

```sh
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py python -- tests/test_agent_instructions.py
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py js -- tests/browser/example.test.mjs
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" \
  python3 scripts/test.py browser -- tests/browser/example.spec.mjs
```

For a fresh local checkout, use Python 3.12, Node 22, and the committed locks:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci --ignore-scripts --no-audit --no-fund
export NODE_PATH="$PWD/node_modules"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/hermes-mobile-playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
chmod 700 "$PLAYWRIGHT_BROWSERS_PATH"
export PLAYWRIGHT_SKIP_BROWSER_GC=1
./node_modules/.bin/playwright install chromium
export HERMES_TEST_NODE="$(command -v node)"
export HERMES_BROWSER="$(node -e 'process.stdout.write(require("playwright").chromium.executablePath())')"
```

Run the setup above from the repository root and keep these exports in the shell
used for managed checks. `NODE_PATH` lets legacy CommonJS
`createRequire('/usr/local/lib/hermes-agent/package.json')` imports find the locked
repository dependencies even when that anchor does not exist. Do not create,
install into, or change ownership of `/usr/local/lib/hermes-agent` for tests.
Cloud setup persists the same repository-local `NODE_PATH` through `GITHUB_ENV`.

On fresh Linux, downloading Chromium does not install its shared-library/system
prerequisites. Playwright OS dependencies must already be provisioned by the
machine/container owner before local browser checks. The documented provisioning
command is `./node_modules/.bin/playwright install-deps chromium` (or
`./node_modules/.bin/playwright install --with-deps chromium` for both); it can
require elevated OS-package installation privileges. Do not silently install system
packages or bypass approvals. If unavailable/offline, report the browser check as
blocked rather than passed and use a provisioned environment. Keep the private cache
and GC protection above for browser installation and execution.

Use existing paths relevant to the change. `python`, `js`, and `browser` select a
managed suite; explicit paths replace that suite's default collection. The Python
interpreter needs the locked dependencies in `requirements.lock`; Node dependencies
come from `package-lock.json`. Browser checks use a private
`PLAYWRIGHT_BROWSERS_PATH` with `PLAYWRIGHT_SKIP_BROWSER_GC=1`. Do not bypass the
managed workspace, its isolation, or cleanup. Prefer focused local checks and
actual hosted/final integration evidence; do not run a local full suite merely
because cloud CI runs one.

The managed CLI resolves Node from explicit `HERMES_TEST_NODE`, then PATH, then
the legacy installed-server fallback; invalid explicit paths fail closed. Cloud
setup exports the exact installed Python, Node and Chromium executables.

## Final integration and gate transition

The authoritative [Git development contract](git-development-spec.md)
(`docs/git-development-spec.md`) governs required gates; use the contract and
runner capabilities on the revision being integrated, not a pending PR's promises.

Until the parent operator verifies the dependencies and exact-head replacement
evidence and changes repository protection, the exact pre-cutover contexts
`source-ci`, `integration-tests`, `agent-review`, and `issue-link` remain required.
In this pre-cutover policy, integration requires complete matching hosted results plus all
residual host tests listed in `.github/host-tests.json` through
`scripts/ci_tests.py host` on the same exact head SHA.
Verify coverage, configuration and successful results against that revision's
documented partition; file presence, dependency setup, selected tests, or a partial
hosted pass alone are not full integration. If the documented partition is
unavailable, conservatively use the existing managed `all` suite on a compatible
isolated host (`python3 scripts/test.py all`, with Python selected through
`HERMES_TEST_PYTHON` or the repository `.venv`).

The target routine premerge policy is complete hosted `source-ci` (portable Python,
JavaScript, generated-assets browser shards and the required native suite),
`issue-link`, and exact-head `cloud-review`. This policy is conditional, not active
from documentation or validator changes alone. After the owner activates it,
installed/private compatibility remains required at guarded exact-main deployment;
do not execute public PR code on a production-host self-hosted runner. See
[`autonomy-policy.md`](autonomy-policy.md) for the read-only evidence contract and
pre/post-cutover requirements. Final integration is a merge gate, not a full local
suite per edit; focused local RED/GREEN checks remain the development loop.

## Cloud dependency setup

The `.github/workflows/copilot-setup-steps.yml` Copilot setup workflow creates an
ephemeral Python environment, installs the Python and Node dependencies from the
committed lockfiles, and installs Chromium in a private runner cache. It is
dependency setup only: it must not run the full
test suite, use production credentials, or access production homes/data. GitHub
requires this workflow on the default branch and a single job named
`copilot-setup-steps`. The setup job checks out its source explicitly with
read-only contents permission; Copilot then checks out its task branch after
setup. It does not select a base branch or replace the agent's task checkout.
See [GitHub's setup workflow documentation](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/customize-the-agent-environment).

## Change and review evidence

For behavior changes, record a real focused RED followed by GREEN and preserve
existing assertions. Describe the baseline, linked issue, acceptance cases,
scope, risks, exact commands/results, and any unrun checks in the pull request.
Until cutover, the exact pre-cutover contexts `source-ci`, `integration-tests`,
`agent-review`, and `issue-link` must pass on the exact head SHA before GitHub merge.
Require an independent formal COMMENT review
for every PR, with actionable inline findings where needed. Publish `agent-review`
only after verifying the independent review of that head; a COMMENT review alone
is neither an approval nor a passing status, and does not claim a human approval
or a separate GitHub identity. Publish `integration-tests` only after verifying
complete final integration under the compatibility rules above; hosted `source-ci`
remains a separate mandatory gate. New commits invalidate all old-head review/test
evidence.

After the parent operator has verified replacements on actual current heads and
activated post-cutover protection, require `source-ci`, `issue-link`, and
`cloud-review` on the exact head. Cloud review is valid only for the latest
authenticated Copilot `APPROVED` review on that SHA with complete review/thread
pagination and resolved threads; a `COMMENTED` review, overview text, or status
alone is insufficient. Do not publish the retired `agent-review` or
`integration-tests` contexts as substitutes. Sensitive changes still require
explicit owner authorization for the exact SHA, bound to the selected current
owner-published structured independent-agent review as specified in
[`autonomy-policy.md`](autonomy-policy.md).

Use follow-up fix commits, reply in actual GitHub threads with the fix SHA and test
evidence, and check findings before resolving them. Require resolved review threads
and a branch current with freshly fetched `origin/main` before merging through
GitHub. No owner/admin bypass; never self-approve, fabricate approvals, review/test
statuses or identities, bypass protections, or write directly to `main`. Report
unrun or blocked checks honestly. Merging is not deployment.

Copilot code review and re-review should be requested on the PR when available,
including after follow-up commits. Automatic review is a GitHub repository setting,
not something these files can enable. Verify that the setting is actually
configured before describing review as automatic. If the capability is unavailable
or its plan/permissions block it, report that limitation and request review
manually. Copilot can submit an independent formal COMMENT review, but that is
not an approval or passing `agent-review` status. Keep targeted review for authentication,
deployment, migration, and semantic conflict changes. Review and test evidence is
valid only for its exact head SHA.

## Parallel integration

Merge and deploy one revision at a time; parallel authoring does not permit
concurrent merge/deploy operations. Merge updated `origin/main` into the PR branch;
never rebase or force-push reviewed history. For conflicts, gather both PR intents,
the common base, and both diffs for a neutral reviewer/reconciler. Combine compatible
behavior, regenerate derived files from their source inputs, and test both intended
behaviors and their interaction. Never resolve by wholesale choosing ours/theirs.
Record resolution decisions on GitHub, preserving discussion and relevant dissent;
rerun affected tests and obtain independent review plus final integration evidence
on the resulting exact head. Escalate to the owner only for genuinely incompatible
product requirements; agents resolve technical conflicts rather than offloading
them to the owner.

Only public synthetic diagnostics belong in issues, tests, and PRs. Do not upload
private session/profile content or operational evidence. Keep source changes
separate from deployment: only the guarded controller/operator may deploy a clean,
verified `main` revision; coding agents do not access production.

## Source preservation and safe cleanup

See [README.md](../README.md) for the canonical source and local integration checkout.
Never edit immutable deployed releases. Older source copies are migration inputs,
not deployment sources. Port unfinished legacy work into a new task worktree as a
diff against its own verified baseline; never overlay an older source tree onto
current main. Preserve migration inputs until their work is merged or explicitly
discarded.

Before cleanup, verify the remote merge and preserve any uncommitted work in a
recoverable location; a local merge or an assumed GitHub outcome is insufficient.
Remove only that task's clean worktree and merged branch (including its remote
branch if still present), and use the managed cleanup path for disposable artifacts.
Never delete unknown/unmerged work, another active session's files, installed
dependencies, runtime data/backups needed for recovery, or rollback releases still
referenced by a running service. Do not sweep release roots while any service uses
them. Source history does not replace operational recovery material.
