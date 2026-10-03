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

Write PR descriptions as plain paragraphs using the repository template; include a literal Closes #N and all required evidence, with no Markdown markup in the body.

For task handoff, a literal Closes #N is a readability convention; verify linkage from authenticated GitHub closing-issue references, never PR body text.

The active required contexts are `source-ci` (Actions app 15368),
`integration-tests`, `agent-review`, and `issue-link` (Actions app 15368), with
strict/up-to-date checks and resolved conversations. Do not require, synthesize, or
publish `cloud-review` as a required status. Independent technical acceptance
requires the latest authenticated, exact-head owner-published structured
independent-agent formal COMMENT review, a valid positive verdict and evidence
binding, complete review/thread pagination, and resolved conversations. Stale,
edited, removed, malformed, superseded, or unbound evidence fails closed; status
alone is not a review. Copilot feedback is supplemental: COMMENTED or missing
APPROVED alone does not block acceptance, repeat review requests, or consume fixer
budget. Actual open findings must be resolved, and definite rejection is not
acceptance. Sensitive changes still require separate exact-SHA owner authorization
and targeted independent review.
The automatic `integration-tests` job succeeds only when the same-run complete
hosted `source-ci` aggregate succeeds. Installed/private host compatibility is not
PR-head evidence; the complete host partition remains a guarded exact-main
deployment gate before activation.
See [`docs/autonomy-policy.md`](../docs/autonomy-policy.md). Coding agents and the
read-only validator never change settings, publish statuses, activate services, or
execute untrusted PR code on the host.

Native-controls maintenance normally requires the scheduler-bound run ID/source
SHA, an independently verified exact-main hosted artifact, and the managed
installed-runtime host checks. Never fall back to a full local suite when hosted
evidence is missing; `--local-full-checks` is an explicit diagnostic mode only.
