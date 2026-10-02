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
- Mandatory final integration evidence for the active policy phase on the exact head SHA:
- Governing gate/partition contract: [Git development spec](https://github.com/lindayi/hermes-mobile/blob/main/docs/git-development-spec.md), [development workflow](https://github.com/lindayi/hermes-mobile/blob/main/docs/development-workflow.md#final-integration-and-gate-transition), and [autonomy transition](https://github.com/lindayi/hermes-mobile/blob/main/docs/autonomy-policy.md):
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
- [ ] Until gate cutover, an independent formal COMMENT review is verified on the exact head and `agent-review` is published only after that verification; after cutover, `cloud-review` requires an actual authenticated Copilot `APPROVED` review on the exact head with complete resolved threads. A COMMENTED overview is not approval.
- [ ] Findings have follow-up fix commits/test evidence and resolved review threads, checked before resolution.
- [ ] Branch is current with freshly fetched `origin/main`; conflicts preserve both PRs' intents and are retested/reviewed on the resulting head.
- [ ] Required contexts for the active policy phase are verified on the exact head: before cutover `source-ci`, `integration-tests`, `agent-review`, and `issue-link`; staging retains those four plus `cloud-review`; after cutover `source-ci`, `issue-link`, and `cloud-review`. New commits invalidate old-head evidence.
- [ ] Before cutover, complete hosted `source-ci` and residual host coverage pass on the same head; after cutover, installed/private host compatibility remains a guarded exact-main deployment gate, not PR execution.
- [ ] No owner/admin bypass, self-approval, fabricated approvals/statuses/identities, or direct writes to `main`.
- [ ] Any required high-risk/semantic-conflict targeted review is complete.
- [ ] Merge is not represented as deployment; deploy only from guarded verified main with active sessions protected.
- [ ] Cleanup follows verified remote merge and preservation of any uncommitted work; remove only this task's clean worktree/merged branch and disposable artifacts, retaining migration inputs, unrelated work, runtime data and referenced rollback releases.

## Merged versus deployed

- Merge state: `not merged` / exact merged PR and main SHA:
- Deployment state: `not deployed` / separately verified deployed main SHA:
