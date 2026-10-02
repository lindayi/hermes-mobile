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
read-back bytes. The reviewed main-source baseline is the merged commit
`f84063e9aed55994c4ae4d3eae14fec922e12929`; its workflow, actions, manifests,
deployment sources, runtime/test inputs, dependency locks, and patch inputs are
fingerprinted. This includes the test-environment action and the statically
reachable local Python control modules rooted at the pinned CI, installer, test
runner, and release entrypoints. The static closure is a deliberately reviewed
inventory, not a runtime-discovered allowlist. The focused closure test rejects
unlisted local imports and recognized dynamic import/loading constructs. If dynamic
loading or another unbounded local execution path is introduced, readiness remains
blocked until that dependency closure is independently established; do not silently
add its digest or claim that this inventory proves arbitrary transitive execution.

The coordinator fingerprint remains tied to the reviewed published checkpoint
`403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83` from pending PR16, not to a candidate
or the current main tree. That checkpoint does not establish that PR16 is merged,
and `deploy/cloud_coordinator.py` is absent from merged main at the baseline above.
Consequently, an actual complete current-main readback is missing a required source
and readiness remains blocked until final coordinator assembly is merged, its exact
bytes are reviewed, and the fingerprint is deliberately updated. The synthetic
fixtures exercise the contract only and do not establish readiness.

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
  latest is selected by chronological timestamp, then review ID as a deterministic
  tie-breaker. Malformed authenticated review records block. The latest review must
  be an actual `APPROVED` review on the current PR head, and every review thread and
  page must be complete and resolved. A `COMMENTED` review, body text, empty
  overview, or a status without the authenticated review is not approval.

If the PR under review changes sensitive files, readiness additionally requires
owner ID `5164171` authorization for that exact head and a separate targeted
independent review of that head. The targeted reviewer must be a positive numeric
identity distinct from the owner, PR author, coding-agent identity (`198982749`),
and mandatory Copilot reviewer (`175728472`). A later commit invalidates both.

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
   never auto-pin candidate evidence or expand a whitelist.
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
