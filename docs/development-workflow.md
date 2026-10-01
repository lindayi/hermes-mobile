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
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$(command -v python3)}" \
  python3 scripts/test.py python -- tests/test_agent_instructions.py
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$(command -v python3)}" \
  python3 scripts/test.py js -- tests/browser/example.test.mjs
HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$(command -v python3)}" \
  python3 scripts/test.py browser -- tests/browser/example.spec.mjs
```

For a fresh local checkout, use Python 3.12, Node 22, and the committed locks:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci --ignore-scripts --no-audit --no-fund
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/hermes-mobile-playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
chmod 700 "$PLAYWRIGHT_BROWSERS_PATH"
export PLAYWRIGHT_SKIP_BROWSER_GC=1
./node_modules/.bin/playwright install chromium
```

Use existing paths relevant to the change. `python`, `js`, and `browser` select a
managed suite; explicit paths replace that suite's default collection. The Python
interpreter needs the locked dependencies in `requirements.lock`; Node dependencies
come from `package-lock.json`. Browser checks use a private
`PLAYWRIGHT_BROWSERS_PATH` with `PLAYWRIGHT_SKIP_BROWSER_GC=1`. Do not bypass the
managed workspace, its isolation, or cleanup. Prefer focused local checks and
actual hosted/final integration evidence; do not run a local full suite merely
because cloud CI runs one.

**Transitional test-runner note for the current main baseline:** the managed runner
still uses its legacy fixed Node path. Open PR #4 proposes `scripts/ci_tests.py`
and `.github/host-tests.json`; neither exists on this baseline. Do not assume that
partitioner or host manifest is available until PR #4 is merged. Continue to use
the current `scripts/test.py` interface and existing required checks; update this
note when the runner change is integrated.

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
The required repository checks remain authoritative; never manufacture a passing
status or describe an unobserved result as verified. Merging is not deployment.

Copilot code review and re-review should be requested on the PR when available,
including after follow-up commits. Automatic review is a GitHub repository setting,
not something these files can enable. Verify that the setting is actually
configured before describing review as automatic. If the capability is unavailable
or its plan/permissions block it, report that limitation and request review
manually. A Copilot comment is not an independent formal review, approval, or
passing `agent-review` status. Keep independent targeted review for authentication,
deployment, migration, and semantic conflict changes. Review and test evidence is
valid only for its exact head SHA.

Only public synthetic diagnostics belong in issues, tests, and PRs. Do not upload
private session/profile content or operational evidence. Keep source changes
separate from deployment: only the guarded controller/operator may deploy a clean,
verified `main` revision; coding agents do not access production.
