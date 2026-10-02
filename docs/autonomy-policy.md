# Autonomy gate transition

This policy defines the evidence required before the repository owner may replace
the manual premerge statuses with the cloud-first routine gate. It does not activate
the transition. Until the parent operator verifies the dependencies and changes the
repository rules, the existing required checks remain authoritative.

## Read-only validator

`deploy.autonomy_policy.validate_transition(evidence, phase=...)` is pure. The
`scripts/autonomy_policy.py` CLI reads one bounded JSON evidence snapshot and prints
a readiness report:

```sh
python3 scripts/autonomy_policy.py --phase pre-cutover /private/path/evidence.json
python3 scripts/autonomy_policy.py --phase staging /private/path/evidence.json
python3 scripts/autonomy_policy.py --phase post-cutover /private/path/evidence.json
```

Exit status zero means only that the supplied snapshot satisfies the selected
contract. A nonzero result is blocked; missing, malformed, partial, stale, failed,
skipped, cancelled, truncated, or mismatched data cannot be treated as success.
The CLI performs no network calls, writes, status publication, settings changes,
service activation, checkout, import, or execution of candidate code.

The evidence must be collected independently from authenticated, read-only GitHub
API responses for `lindayi/hermes-mobile` (repository ID `1399942965`) and the
current `refs/heads/main` source. The validator checks consistency and structure; it
does not authenticate the JSON file or independently prove how it was collected.
The operator must verify the source snapshot and API provenance, and verify release
attestation using the existing exact certificate identity and authenticated
certificate extensions. A caller assertion or PR overview is not proof.

`main.files` maps every required source path to its SHA-256 digest over the exact
read-back bytes. The reviewed current-main source baseline for non-coordinator
inventory paths is `b85c098857e7fb8229f47bd688d703bb677aeb34`. The inventory
includes the mandatory `issue-link.yml` workflow and the statically traversed local Python dependencies of
the explicitly listed source-control roots, including `deploy.observe_release`.
The closure test resolves relative package imports and initializers and rejects
unresolved local imports and unsupported dynamic imports. This bounded inventory
does not discover arbitrary subprocess roots or establish complete execution-source
coverage by itself.

The release controller statically imports `deploy/native_notification_release.py`,
which imports `backend/native_notifications.py`; both are therefore in the fixed
inventory. `backend/native_notifications.py` is pinned to its
`b85c098857e7fb8229f47bd688d703bb677aeb34` bytes like other inventory paths.
`deploy/native_notification_release.py` is absent from that baseline; its pin and
separate test fixture are the pending PR25 candidate bytes, awaiting parent review
against the exact source. They are not independently verified, are not runtime
evidence, and do not affect the unconditional `pending-source-contract` hold.

The source-level workflow, import traversal, subprocess-root, and mutation
regressions recorded in issue #35 are repaired in this revision. That does not
complete issue #35, certify operational source pins, or permit the
`pending-source-contract` hold to be cleared or updated. No digest is accepted
automatically. The current unconditional hold keeps every phase unready regardless
of the fixed inventory or caller-supplied evidence.

The recorded coordinator baseline remains the published checkpoint
`403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83` from pending PR16. That known unsafe
baseline lacks strict timezone-aware, deterministic review ordering and separate
targeted independent review enforcement for every sensitive head before status
publication or auto-merge. This branch adds a separate issue #43 candidate
fingerprint and fixture for the coordinator; neither changes that baseline nor
certifies the complete source contract.

The coordinator statically imports `deploy/review_evidence.py`, so that dependency
is an explicit inventory entry under the same `coordinator-review-contract` blocker.
Its issue #39 candidate digest is superseded by the issue #43 candidate fingerprint
and separate literal fixture below, which reflect the current source bytes. This
updated candidate is not independently verified, is not runtime evidence, and does
not affect the unconditional `pending-source-contract` hold.

The PR40 development snapshot with incoming PR29/33 parent
`80bf9e73de3aec689cd68374bc21ad50642ee14e` expands the coordinator's static
local-import closure from two files to exactly 36. The fixed closure regression
lists every path explicitly, rejects dynamic/unresolved imports, and requires
inventory coverage. Shared backend dependencies retain their existing
`execution-source-contract` labels. The main baseline remains unchanged; candidate
pins for the coordinator and review-evidence helper below reflect this branch's
current bytes, not final PR40-on-main acceptance.

Five previously absent dependencies are added as **pending assembly candidates**
under `coordinator-review-contract`, with separate literal test fixtures and
actual-byte, missing-source, malformed-digest and byte-mutation checks in every
phase. Their SHA-256 digests match the incoming parent's exact bytes:

| Pending path | Candidate SHA-256 |
| --- | --- |
| `deploy/task_receipts.py` | `8ad9e60ec697de8135679b9110ed5d924057e0d8a59e731c67fceedec6525197` |
| `deploy/workflow_events.py` | `5234980515c0909d5170a3a9047766a0b355aa35372bedc2961b703afc37b9af` |
| `deploy/workflow_lifecycle.py` | `83643df2f642b6c949031e067968c0dd5a06e4c3230ab1b7f7bdb02b3be8c626` |
| `deploy/workflow_lifecycle_sources.py` | `a6be88f79862966a6e09f10f9eee2e7f6c8957ededeb1005f435b70759a0e5a6` |
| `deploy/workflow_notifications.py` | `4684a5db2229a9491a99af04b6437ff215ffcbd6900d53c653fa7e897055d87d` |

Historical independent source-review lineage (parent-retained workflow reports,
not runtime evidence or portable test inputs):

- `task_receipts.py` matches `pr29-2b49-assembly-review.json`'s source hash,
  which retains the PR29 receipt and immutable-enrollment review lineage.
- `workflow_events.py`, `workflow_lifecycle.py` and
  `workflow_lifecycle_sources.py` match `lifecycle-second-followup-review.json`
  at `c69670cefc2e0264ad90498ba44a0d225e3f6cca`, and the explicit read-back
  hashes in `lifecycle-final-integration-review.json`.
- `workflow_notifications.py` matches the integrated PR32 repair hashes in
  `notifications-integrated-fix.json` and `lifecycle-final-integration-review.json`.
  The former binds the bounded independent review transcript
  `notifications-integrated-independent-review.txt` (SHA-256
  `1dee539ffa8cd17a1ce8009d68dfefd48a4dbdc2a368835e7e9443e0305dee58`).

Those historical reports have bounded scopes and include historical assembly or
activation blockers; matching a module hash is not an approval of the complete
PR40 assembly or its control flow. Parent independent **assembly and inventory
review remains required**. These candidate pins do not certify complete operational
source coverage or repair issue #35's targeted-review implementation. In particular,
exact static coverage of the current candidate is not a claim that the old baseline
coordinator has this same closure.

Issue #43 adds four explicit launch roots to the source-control inventory:
`scripts/cloud_coordinator.py`, `scripts/workflow_notifications.py`,
`scripts/issue_starter.py`, and `deploy/issue_starter.py`. Their complete local
Python import closure is checked against the fixed inventory and rejects unresolved
or dynamic imports. Three service/timer pairs are separately required and pinned:
the coordinator, workflow notifications, and issue starter. The pins and an
independently spelled-out test fixture represent candidate source bytes only; they
do not constitute independent review, runtime evidence, or permission to clear the
hold.

| Issue #43 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `0810bb4509fca1806ef160cac917570a163084e34a2ac83e037a1b0492d4fa4f` |
| `deploy/review_evidence.py` | `6a146ff4fa90c8bd24ffe941391237d2a78ce1d2c51d6e4f55afd0130743b3f5` |
| `deploy/issue_starter.py` | `701faa6e15a2717cb3c79f7e93c728bdde326e4f72e451ccd77ec1f8eabdc011` |
| `scripts/cloud_coordinator.py` | `992d448a9ddfdd75abdab14fc48ad0dbff98e1c93a943f483d0788ef5ca57790` |
| `scripts/workflow_notifications.py` | `03731f93e1aa3ce297107ea3d0126e990c72d88401dda04e4499f0a7f505b55f` |
| `scripts/issue_starter.py` | `09008da255c56f370f73af6d2f8e8587f6a999c76a99e1bd798e8ac4bbd927f1` |
| `deploy/hermes-mobile-coordinator.service` | `672f1e134e2cb5acbcd648eb7e124947af8d7c11143f20cea8e5be5d97c42807` |
| `deploy/hermes-mobile-coordinator.timer` | `ffa239c67b492b5a361b823c754d5f204eb4efaa2c69f7df99df577e12d2a6c1` |
| `deploy/hermes-workflow-notifications.service` | `998ee55dc5df990c6004f0f435e073766702e5efbf56aa83e946b6d05e21c000` |
| `deploy/hermes-workflow-notifications.timer` | `627463b4dd06eb72f7fecc88a79dad29ec8ab4b5514b29c62c131cdbd2963cc8` |
| `deploy/hermes-mobile-issue-starter.service` | `1711c53ee7b7e4f86b435d3e19ade679b20af960176c53125f14eee3a0dcdb69` |
| `deploy/hermes-mobile-issue-starter.timer` | `848e07d3f30f5d4c7ad881ca9bdeddd6fbf9eeb8ae1fb68ba0feb5b4425e5e92` |

The task branch preserves PR40 history for development and does not modify PR40.
Final acceptance still requires integrating the final independently reviewed
PR40 merge on `main` and rechecking this inventory against that exact source; this
development snapshot is not represented as that accepted dependency. The
unconditional hold below is unchanged; no activation is authorized.

The validator therefore adds the explicit `pending-source-contract` blocker in
**every phase**, even for otherwise complete evidence matching every pinned digest.
No snapshot can report ready with this source version; the CLI returns nonzero.
The hold is enforced in source, not by an input flag: caller booleans, matching
hashes, claimed review success, or a supplied readiness result cannot clear it.
All other evidence checks still run, preserving their specific blockers. Component
positive tests require exactly this one known blocker, not readiness; negative
security assertions retain their own blockers as well. Invalid phase/evidence
roots remain rejected with their existing diagnostics.

The activation follow-up in issue #35 must independently review the actual
current-main coordinator and its complete control contract, establish strict review
timestamps/ordering and per-head sensitive review enforcement, deliberately
reconcile operational source pins and fixtures, and make a separately reviewed
source change to clear or update this hold. Neither a pin refresh alone nor a caller
assertion is sufficient; pins never auto-refresh.
Merging this inactive read-only validator is separate from activation and does not
change the coordinator, repository protection, services, or existing merge gates.
The synthetic fixtures exercise evidence components only, not live readiness.

Any fingerprint-set or dependency-inventory update must be independently reviewed
against the complete source, its local control dependencies, and intended control
flow; additions are never accepted automatically. The digest comparison is only a
consistency check: it does not authenticate the evidence file, prove how hashes
were collected, or turn operator-supplied booleans into cryptographic proof. The
operator must independently verify authenticated API provenance and the exact
current-main readback. Readiness requires:

- A hosted `native` job on GitHub-hosted Ubuntu that runs the managed native test
  suite, is a direct dependency of the `always()` `source-ci` aggregate, and is
  included in its exact fail-closed result set.
- The complete public hosted build, checks, JavaScript, portable Python, browser
  and native job contract; browser shards consume the exact generated artifact.
  `deploy/release_artifact.py` must require the matching complete job set and exact
  main-source provenance, including native success.
- A successful `push` workflow run for the same current main SHA, complete unique
  successful jobs for that attempt, an unexpired same-attempt artifact, and
  verified GitHub-hosted provenance bound to this repository, workflow, ref,
  source/signer SHA and run attempt.
- The installed host manifest and guarded deployment path must remain intact.
  Exact-main deployment continues to run the private/installed compatibility gate;
  untrusted PR code is never sent to a production or self-hosted runner.
- Actual fixed-repository branch-protection readback with strict up-to-date checks,
  administrator enforcement and resolved-conversation protection.
- The fixed repository/coordinator identities and a complete exact-head review
  contract. The reviewed PR must be open, non-draft, based on current main, and its
  changed-file classification must be complete for that exact head. Evidence binds
  the positive numeric PR-author ID. Every authenticated Copilot review (ID
  `175728472`) must have a positive review ID and a valid timezone-aware timestamp;
  all review IDs are unique. Malformed review records block. Latest is selected by
  chronological timestamp; every Copilot review tied at that instant must be an
  actual `APPROVED` review on the current PR head. Offsets compare as instants, not
  strings or review-ID order. Every review thread and page must be complete and
  resolved. A `COMMENTED` review, body text, empty overview, or a status without the
  authenticated review is not approval.

If the PR under review changes sensitive files, readiness additionally requires
owner ID `5164171` authorization for that exact head, bound to one owner-published
independent-agent review in the complete authenticated `cloud_review.reviews`
collection. The existing command is extended to
`/hermes authorize-sensitive <head-sha> review <positive-review-id> <body-sha256>`;
the former head-only command cannot authorize sensitive work. The review must be a
unique positive review ID authored by the owner, submitted with a valid
timezone-aware timestamp, in state `COMMENTED`, on the exact head. Its raw UTF-8
body must match the command's SHA-256 and be a bounded (at most 4096-character)
JSON object with exactly these fields: `schema` set to
`hermes-independent-agent-review-v1`, `reviewed_head_sha`, `review_method` set to
`independent-agent`, `verdict` set to `pass`, and a lowercase 64-character
`evidence_sha256`. The owner publication is the trust assertion that independent
review occurred; it does not prove cryptographic or model independence or claim a
different GitHub identity. The selected record must remain the sole latest owner
review; a missing, malformed, edited, removed, dismissed, duplicate, stale, negative,
or conflicting record blocks with `sensitive-review-authorization`. Both the owner
authorization and selected review are re-read during planning and the fresh status
and merge fences. A later head clears the authorization; approval is not blanket
consent for future commits.

## Policy phases and activation

| Phase | Required main protection checks | Meaning |
| --- | --- | --- |
| `pre-cutover` | `source-ci`, `integration-tests`, `agent-review`, `issue-link` | Existing manual policy, with issue-link retained. Genuine complete exact-head APPROVED review is required, but its not-yet-published cloud-review status may be absent. |
| `staging` | `source-ci`, `integration-tests`, `agent-review`, `issue-link`, `cloud-review` | Additive policy: bind source-ci to Actions and add cloud-review without retiring legacy gates. Require actual coordinator-published exact-head success and its genuine approved review. |
| `post-cutover` | `source-ci`, `issue-link`, `cloud-review` | Target routine policy, only after the owner has verified staging replacement evidence on actual current heads and authorized retirement of the two legacy contexts. |

These are exact context maps, not minimum subsets. No arbitrary supersets or unknown
contexts are accepted, and duplicate contexts block. Each check's app_id must be
explicitly present as a JSON integer or null. In pre-cutover, source-ci may be
unbound (null) or already bound to Actions app 15368; staging and post-cutover
require source-ci bound to Actions app 15368. The issue-link remains bound to
Actions app 15368 in every phase. The legacy integration-tests and agent-review
bindings remain null in pre-cutover and staging; cloud-review's app binding is
null in staging and post-cutover. Its separate status creator identity is checked
below. No other app-binding variants are accepted.

All three phases require strict main protection, administrator enforcement and
conversation resolution. In every phase, complete `source-ci` includes the public
native suite. After cutover, private
installed-runtime compatibility remains an exact-main guarded deployment gate,
not a premerge host task. The validator never publishes `integration-tests`,
`agent-review`, `cloud-review`, or any other status and cannot perform the settings
transition.

Only pre-cutover permits omission of cloud_review.status. An explicitly supplied
null or malformed status blocks; if supplied, it must meet the same success,
exact-head, context and creator checks as the later phases. Staging and post-cutover
require an actual `cloud-review` success on the reviewed PR head, with creator ID
5164171, published by the genuinely merged coordinator. The operator must verify
that provenance independently; a supplied status record or fixed creator ID alone
does not authenticate who ran which source. Review approval, complete pagination,
resolved threads, source/run/artifact provenance, changed-file classification and
sensitive owner authorization remain mandatory even when pre-cutover status is
absent. A pre-cutover pass is permission to consider additive staging, not to retire
legacy gates.

The coordinator publishes cloud-review only when that context is required. Requiring
its status before adding the context would deadlock. Do not synthesize a status to
bridge this gap. The owner-controlled sequence is:

1. Merge the independently reviewed dependencies through the existing protected
   process. Pre-cutover remains blocked if PR #19/PR20's native aggregate/release
   provenance or PR #15/PR16's fixed-identity coordinator and exact-head review
   contract are absent from current main. A PR branch or expected merge is not a
   dependency proof. Once the final merged core (and later PR33 changes) is known,
   deliberately review and replace any changed fingerprint and provenance fixture;
   never auto-pin candidate evidence or expand a whitelist. This is an issue #26
   activation follow-up, including the separately reviewed source change required
   to clear/update `pending-source-contract`, not part of this validator's merge.
2. Collect authenticated current-main source, successful complete hosted results,
   release provenance, retained installed-host gate and genuine complete exact-head
   APPROVED review evidence, including sensitive authorization where required.
   Run pre-cutover validation against the actual four-context readback. Keep existing
   issue-link and both legacy gates; exact-head hosted plus full residual host
   coverage and the independent formal COMMENT/legacy publication obligations in
   the development workflow remain in force until final cutover.
3. Only the owner may authorize and apply the reversible additive settings change:
   tighten source-ci to Actions app 15368 and add cloud-review, retaining issue-link,
   integration-tests, agent-review and all strict/admin/conversation protections.
   This is staging, not final activation. Read back the exact five-context map.
4. Under that staging policy, verify the genuinely merged coordinator publishes
   cloud-review success from actual exact-head APPROVED review with complete resolved
   threads and any required sensitive authorization. Recollect fresh evidence and
   pass staging validation before retiring either legacy context. Missing status,
   stale heads, fake approval, or mismatched source remain blockers, not reasons to
   fabricate success or bypass protection.
5. Only after that verification may the owner authorize the final reversible change
   retiring integration-tests and agent-review. Retain exactly source-ci/app15368,
   issue-link/app15368 and cloud-review/unbound plus all protections. Recollect actual
   readback, main and PR-head evidence and pass post-cutover validation. New heads
   invalidate old evidence; re-read current main and PR head to fence collection
   races. A post-cutover snapshot does not prove the earlier staging verification;
   the parent operator must retain and independently verify that record.

No coding task or validator performs settings changes or service activation. Do not
equate synthetic unit-test success with live readiness, a merge, or a deployment.

## Evidence fields

The JSON root contains `repository`, `main`, `protection`, `source_ci`, and
`cloud_review` records. `main.files` maps the exact required source paths to their
SHA-256 digests and is bound to `main.sha`, `main.ref` and the fixed repository ID.
`source_ci.jobs` carries every unique GitHub job ID, name, run ID, attempt, head
SHA, completion status and conclusion. The artifact record carries its run/attempt,
repository IDs, expiration state, size and SHA-256. Its attestation record carries
the verified certificate identity, issuer, repository ID, ref, source/signer SHA,
run invocation URI, trigger and runner environment.

`cloud_review` identifies the current PR repository, base ref/SHA and head SHA,
positive `pull_author_id`, open/draft state, complete changed-file classification
for its exact head, complete review/thread pagination, review IDs, authors, valid
timestamps, states and commit SHAs, resolved thread state, and the `cloud-review`
status context, state, head and creator ID
(status may be omitted only in pre-cutover, as described above).
For a sensitive change it also carries the exact-head owner authorization and
targeted-review identities. Consult `deploy/autonomy_policy.py` for the executable
field contract; missing fields block rather than defaulting to success.
