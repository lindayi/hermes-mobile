# Hermes Mobile Copilot instructions

[AGENTS.md](../AGENTS.md) is the portable source of truth. Read it and the
relevant specification before editing; keep these critical rules synchronized:

Start from an approved issue; search for duplicates and overlapping PRs, then record scope and acceptance cases before implementation.

For behavior changes, preserve existing assertions and demonstrate a real RED test followed by GREEN.

Use the managed test runner with explicit test paths for focused local checks; choose Python through HERMES_TEST_PYTHON or the repository .venv, never a fixed owner-specific path.

Use cloud execution by default for substantial coding, testing, and review; local work remains supported for small fixes and offline work under the same pull-request gates.

Never use production credentials, real accounts, real model calls, native production homes or databases, private evidence uploads, or live enrollment as tests.

A review comment is not an approval or a passing review status; verify every review and test result against the exact head SHA.

Merging and deployment are separate; only guarded deployment from verified main is allowed, and coding agents never access production.

Prefer small complete changes within the approved issue scope. Preserve existing behavior and assertions outside the requested change. Report exact observed verification; never claim a test, review, or check passed unless it did.

The autonomous gate transition is conditional, not active by documentation alone.
Until the parent operator verifies dependencies and actual current-head evidence and
changes repository protection, require the exact pre-cutover contexts `source-ci`,
`integration-tests`, `agent-review`, and `issue-link`, plus independent formal
COMMENT review. After
activation, routine premerge requires complete hosted `source-ci` (including native),
`issue-link`, a successful `cloud-review` status, and an actual authenticated Copilot `APPROVED` review on the exact
current head with complete resolved threads; `COMMENTED` is never approval. Sensitive
changes still require exact-SHA owner authorization and targeted independent review.
Installed/private host compatibility remains a guarded exact-main deployment gate.
See [`docs/autonomy-policy.md`](../docs/autonomy-policy.md). Coding agents and the
read-only validator never change settings, publish statuses, activate services, or
execute untrusted PR code on the host.
