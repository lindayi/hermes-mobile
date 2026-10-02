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
read-back bytes. `SOURCE_BASELINES` retains historical provenance checkpoints:
non-coordinator inventory started at `b85c098857e7fb8229f47bd688d703bb677aeb34`,
and the old PR16 coordinator checkpoint was
`403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83`. Neither identifier describes all
current candidate bytes. The explicit refreshes below supersede the corresponding
historical fingerprints, not the requirement for final independent source acceptance.
The inventory includes `issue-link.yml` and the statically traversed local Python
dependencies of the listed roots, including `deploy.observe_release`. Traversal
resolves relative package imports and initializers and rejects unresolved local or
unsupported dynamic imports. It does not discover arbitrary subprocess roots or
establish complete execution-source coverage by itself.

### Retained merged source and deliberate native refreshes

The release controller imports `deploy/native_notification_release.py`, which
imports `backend/native_notifications.py`; both remain in the fixed inventory.
Exactly three historical native pins and their separately literal fixtures are
refreshed after matching the accepted source-review hashes, merged-preservation
hashes, and actual bytes from main `5316f7c75dced1bffafdb545cdd7677e98e855a6`.
No native source is changed and no other historical pin is automatically refreshed.

| Deliberately refreshed path | Current SHA-256 |
| --- | --- |
| `deploy/native_controls_release.py` | `b3d3c601db4afd9ff003f4df759fcb057c19a83b920206fb2766d1560fd99175` |
| `backend/model_controls.py` | `206fb5164283c16f46c51da99babe1b5f5e8932f062b4a1ee5bc07affe1f2c34` |
| `backend/native_notifications.py` | `230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610` |

The authorization lineage is `native25-final-runtime-review.json` (SHA-256
`92642f88cb6c9daa5c381ea31cdd2648aba5aec2d51aa5bed7c0a2160f23448e`),
which records these exact hashes and accepts the controller flow, together with
`pr33-prepare-closure-review.json` (SHA-256
`c7a8a2d8491f46e374493c948e66e9a05a93060e518fb3847af2935fc46ec082`),
which preserves those incoming merged PR25 bytes. These reports are parent-retained
source-review lineage, not runtime evidence or portable test inputs.
`deploy/native_notification_release.py` already matches its retained
`364f5856f31a07117274a6855a0af573e85d4d197770188b6e9c734df4699582` pin;
its `native25-route-bound-review.json` and PR33 merged-preservation lineage is
accepted source evidence, not a still-unreviewed PR25 candidate or activation approval.
It was absent only from the historical b85c098 baseline.

The coordinator's exact 36-file static closure and fixed dependency inventory are
retained. Shared backend dependencies keep their existing blocker labels. The five
PR29/PR40/PR42 dependency pins below match the accepted merged bytes in main5316;
the separate literal fixtures and actual-byte/missing/malformed/mutation checks
remain in every phase.

| Retained merged dependency | SHA-256 |
| --- | --- |
| `deploy/task_receipts.py` | `8ad9e60ec697de8135679b9110ed5d924057e0d8a59e731c67fceedec6525197` |
| `deploy/workflow_events.py` | `5234980515c0909d5170a3a9047766a0b355aa35372bedc2961b703afc37b9af` |
| `deploy/workflow_lifecycle.py` | `71be9101223f40511818bde2db9f6bd6b021736f35152e16cb3e1c9c3e2085a3` |
| `deploy/workflow_lifecycle_sources.py` | `dfff5b5ec33b9bd1756a67150827541ea86193b87e6de5b5c3a965f02f19b837` |
| `deploy/workflow_notifications.py` | `f0af01bdc797e0abd0494fa7a1fa304060c734ed8fc2ba1fa2b4515a9a3bcda2` |

`task_receipts.py` retains `pr29-2b49-assembly-review.json` lineage; the starter
retains `pr29-producer-identities-review.json` lineage. The lifecycle/event/
notification hashes are recorded in `pr40-final-status-assembly-review.json`,
including the accepted PR42 retention assembly. Earlier PR33/PR32 reports remain
historical lineage, not claims that their superseded hashes are current. All five
dependencies and all ten launch paths below are byte-identical to main5316.

### Issue #43 accepted source prerequisites and remaining operational debt

The current coordinator and helper fingerprints below are issue #43 candidates,
not the old unsafe PR16 checkpoint. The helper combines issue #43 strict review
identity/owner-published structured evidence with the final PR40 helper
`0480bde8fdd3ce714011ca4604de8ef1f4c500bc2bd82d7801ed2fb4da8955de`
soft-text normalization, preserving literal context and structural boundaries.
`pr40-softline-review.json` covers that retained parser delta, not new authority
logic. The coordinator also revalidates the separately mandatory actual Copilot
approval against the final reviews read before auto-merge, in addition to the
selected sensitive review. Independent final **S1-S5 assembled-source and inventory
acceptance** is recorded in the parent-retained `pr44-final-review.json`, with the
assembly and focused/adjacent test evidence in `pr44-final-fix.json`. The accepted
bytes are preserved at `03dea0e87eeee91f2b88ee97a072b0eaf3fe2d5d`. These reports
accept source prerequisites, not runtime readiness or this follow-up delta.

The three historical native mismatches are reconciled and all 79 fixed source pins
match the accepted assembly. The separately authorized final source phase removes
only the historical unconditional `pending-source-contract` blocker. Its own
exact-head independent review and complete required hosted/residual-host evidence
remain merge obligations. Authenticated current-main readback and operational
acceptance remain separate; **issue #35 stays open**. Matching hashes or source
review reports alone authorize no activation.

Issue #43 adds four explicit launch roots to the source-control inventory:
`scripts/cloud_coordinator.py`, `scripts/workflow_notifications.py`,
`scripts/issue_starter.py`, and `deploy/issue_starter.py`. Their complete local
Python import closure is checked against the fixed inventory and rejects unresolved
or dynamic imports. Three service/timer pairs are separately required and pinned:
the coordinator, workflow notifications, and issue starter. The pins and an
independently spelled-out test fixture represent source consistency only; they
do not constitute independent review, runtime evidence, or activation permission.

| Issue #43 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `8ceaed2788467afd1ee677643bab3de7f594c5901fd98d8077c4d02b1058b7b1` |
| `deploy/review_evidence.py` | `c097e5ddb38119c992b8f5fac6581434a494242f48fdec6d07f037da18f188ae` |
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

This assembly integrates final PR40 main
`5316f7c75dced1bffafdb545cdd7677e98e855a6`, preserving its parser and previously
merged retention behavior. Source acceptance and removal of the historical hold
do not authorize activation.

The read-only validator can now report `ready: true` (CLI exit zero) in any selected
phase **only when every source, run, provenance, review, approval, thread and
protection component satisfies that phase's contract**. Every genuine component
failure retains its specific blocker and a nonzero CLI result. Invalid phase or
evidence roots retain their existing diagnostics. No source pins, identities,
exact-head or review-ordering requirements, authority predicates, freshness
requirements, or protection maps are relaxed. There is no caller override:
`pending_source_contract`, `source_contract_reviewed`, supplied `ready`/`blockers`,
or other caller claims cannot bypass a failed component.

Complete synthetic fixtures now expect readiness and an empty blocker list in all
three phases, including valid sensitive authorization. Negative fixtures continue
to assert their actual component blockers without filtering them; malicious caller
flags are tested against broken components. Synthetic readiness proves only the
source policy behavior, not evidence authenticity or permission to operate.

The activation follow-up in issue #35 must still verify the actual current-main
coordinator and complete control contract, authenticated API/source provenance and
actual current-head replacement evidence through the owner-controlled sequence
below. Neither a pin refresh alone nor a caller assertion is sufficient; pins never
auto-refresh. Merging this read-only validator is separate from activation and does
not change the coordinator, repository protection, services, or existing merge gates.

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
   never auto-pin candidate evidence or expand a whitelist. Issue #43 accepts the
   assembled source prerequisites and separately authorizes historical hold removal;
   issue #35 retains operational acceptance. Neither source change establishes the
   authenticated current-main or runtime evidence required by this sequence.
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
