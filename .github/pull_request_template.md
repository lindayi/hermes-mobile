## Linked issue and related work

<!-- Replace NUMBER with the actual issue number. -->
- Closes #NUMBER
- Related issues/PRs/specification:

## Scope and baseline

- Baseline (`main` commit):
- In scope:
- Explicitly out of scope:
- Acceptance cases:

## RED/GREEN evidence

- RED command and observed failure:
- GREEN command and observed result:

## Exact verification evidence

- Exact tested head SHA:
- Focused managed commands and results:
- Hosted CI check names/results:
- Mandatory final integration evidence on the exact head SHA (complete matching hosted + residual host coverage when the documented partition is available; otherwise existing managed `all`):
- Governing gate/partition contract: [Git development spec](../docs/git-development-spec.md) and [final integration compatibility](../docs/development-workflow.md#final-integration-compatibility):
- Security/publication checks:
- Checks or device-only/operational gates not exercised:

## Risks, privacy, and data handling

- Security/privacy/data risks and mitigations:
- Confirm no production credentials, real accounts/model calls, private evidence, or production data were used:
- Public synthetic diagnostics only:

## Rollout and rollback

- Rollout effect or `documentation-only`:
- Rollback plan or `not applicable`:
- Deployment/operator gates not exercised:

## Review and integration

- [ ] Copilot review was requested for this PR and re-requested after follow-up commits, or verified automatic review is enabled in repository settings.
- [ ] Copilot suggestions were judged against the code and tests; comments are not approvals or a passing review status.
- [ ] An independent formal COMMENT review is verified for this PR on the exact head SHA; `agent-review` is published only after that verification, not as a fabricated approval or identity.
- [ ] Findings have follow-up fix commits/test evidence and resolved review threads, checked before resolution.
- [ ] Branch is current with freshly fetched `origin/main`; conflicts preserve both PRs' intents and are retested/reviewed on the resulting head.
- [ ] `source-ci`, `integration-tests`, and `agent-review` are verified green on the exact head SHA; `integration-tests` was published only after complete final integration. New commits invalidate old-head evidence.
- [ ] No owner/admin bypass, self-approval, fabricated approvals/statuses/identities, or direct writes to `main`.
- [ ] Any required high-risk/semantic-conflict targeted review is complete.
- [ ] Merge is not represented as deployment; deploy only from guarded verified main with active sessions protected.
- [ ] Cleanup follows verified remote merge and preservation of any uncommitted work; remove only this task's clean worktree/merged branch and disposable artifacts, retaining migration inputs, unrelated work, runtime data and referenced rollback releases.

## Merged versus deployed

- Merge state: `not merged` / exact merged PR and main SHA:
- Deployment state: `not deployed` / separately verified deployed main SHA:
