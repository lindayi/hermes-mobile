Linked issue and related work

Closes #NUMBER

Related issues, pull requests, and specifications:

Scope and baseline

Baseline main commit:

In scope:

Explicitly out of scope:

Acceptance cases:

RED/GREEN evidence

RED command and observed failure:

GREEN command and observed result:

Exact verification evidence

Exact tested head SHA:

Focused managed commands and observed results:

Hosted CI check names and results:

Mandatory final integration evidence for the active policy phase is verified on the exact head SHA:

Governing gate and partition contract: docs/git-development-spec.md, docs/development-workflow.md, and docs/autonomy-policy.md.

Security and publication checks:

Checks or device-only and operational gates not exercised:

Risks, privacy, and data handling

Security, privacy, and data risks and mitigations:

Confirm no production credentials, real accounts, model calls, private evidence, or production data were used:

Public synthetic diagnostics only:

Rollout and rollback

Rollout effect:

Rollback plan or not applicable:

Deployment and operator gates not exercised:

Review and integration

Copilot review was requested for this PR and re-requested after follow-up commits, or automatic review was verified enabled in repository settings:

Copilot suggestions were judged against the code and tests. Comments are not approvals or passing review status:

Before gate cutover, an independent formal COMMENT review is verified on the exact head and agent-review is published only after that verification. The post-cutover cloud-review requires an actual authenticated Copilot APPROVED review on the exact head with complete resolved threads. A COMMENTED overview is not approval:

Findings have follow-up fix commits and test evidence, and resolved review threads were checked before resolution:

The branch is current with freshly fetched origin/main. Any conflicts preserve both PRs' intents and are retested and reviewed on the resulting head:

Required contexts for the active policy phase are verified on the exact head. Before cutover source-ci, integration-tests, agent-review, and issue-link are required. Staging retains those four plus cloud-review; after cutover source-ci, issue-link, and cloud-review are required. New commits invalidate old-head evidence:

Before cutover, complete hosted source-ci and residual host coverage pass on the same head. After cutover, installed and private host compatibility remains a guarded exact-main deployment gate, not PR execution:

No owner or administrator bypass, self-approval, fabricated approvals, statuses, or identities, or direct writes to main:

Any required high-risk or semantic-conflict targeted review is complete:

Merge is not represented as deployment. Deploy only from guarded verified main with active sessions protected:

Cleanup follows verified remote merge and preservation of any uncommitted work. Remove only this task's clean worktree, merged branch, and disposable artifacts, retaining migration inputs, unrelated work, runtime data, and referenced rollback releases:

Merged versus deployed

Merge state: not merged or exact merged PR and main SHA:

Deployment state: not deployed or separately verified deployed main SHA:
