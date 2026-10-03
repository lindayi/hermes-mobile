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

Copilot review was supplemental feedback only; no approval was inferred from a comment or status:

Copilot suggestions were judged against the code and tests. Comments are not approvals or passing review status:

The latest authenticated owner-published structured independent-agent formal COMMENT review is verified on the exact head, with positive verdict and evidence binding, complete pagination, and resolved threads. Copilot is advisory; actual findings and definite rejection remain blockers:

Findings have follow-up fix commits and test evidence, and resolved review threads were checked before resolution:

The branch is current with freshly fetched origin/main. Any conflicts preserve both PRs' intents and are retested and reviewed on the resulting head:

The exact required contexts source-ci (Actions app 15368), integration-tests, agent-review, and issue-link (Actions app 15368) are verified on the exact head. cloud-review is advisory, not required or synthesized. New commits invalidate old-head authority and status evidence:

The exact-head integration-tests job and its same-run complete hosted source-ci aggregate pass. Host-only compatibility is not PR-head evidence; the complete installed/private host suite remains a guarded exact-main deployment gate before activation:

No owner or administrator bypass, self-approval, fabricated approvals, statuses, or identities, or direct writes to main:

Any required high-risk or semantic-conflict targeted review is complete:

Merge is not represented as deployment. Deploy only from guarded verified main with active sessions protected:

Cleanup follows verified remote merge and preservation of any uncommitted work. Remove only this task's clean worktree, merged branch, and disposable artifacts, retaining migration inputs, unrelated work, runtime data, and referenced rollback releases:

Merged versus deployed

Merge state: not merged or exact merged PR and main SHA:

Deployment state: not deployed or separately verified deployed main SHA:
