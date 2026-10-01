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
read-back bytes. The validator compares those values with a narrow reviewed
fingerprint set; it does not parse, import, or execute evidence source. The
fingerprints for the hosted workflow, native/runtime pin and action, complete
native/host manifests, release verifier, test partition, runtime preflight, and
deployment controller are from merged main `66a64245b6c9c632d5ca4087e3d1e4e4fa2a4e83`.
The coordinator fingerprint is from reviewed but still-pending PR16 head
`403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83`; that pin is not evidence that PR16 is
merged or that current main contains the coordinator. A final PR16 assembly that
changes the source requires a follow-up fingerprint update. Readiness remains false
until required dependencies are merged and genuine current-head evidence is verified;
the synthetic unit fixtures do not establish readiness.

Any fingerprint-set update must be independently reviewed against the complete
source and its intended control flow. The digest comparison is only a consistency
check: it does not authenticate the evidence file, prove how hashes were collected,
or turn operator-supplied booleans into cryptographic proof. The operator must
independently verify authenticated API provenance and the exact current-main
readback. Readiness requires:

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
  changed-file classification must be complete for that exact head. The latest
  authenticated Copilot reviewer (ID `175728472`) must have an actual `APPROVED`
  review on the current PR head, and every review thread and page must be complete
  and resolved. A `COMMENTED` review, body text, empty overview, or a status without
  the authenticated review is not approval.

If the PR under review changes sensitive files, readiness additionally requires
owner ID `5164171` authorization for that exact head and a separate targeted
independent review of that head. A later commit invalidates both.

## Policy phases and activation

| Phase | Required main protection checks | Meaning |
| --- | --- | --- |
| `pre-cutover` | `source-ci`, `integration-tests`, `agent-review` | Existing manual policy. Exact-head `source-ci` and complete residual host coverage remain required; publish neither legacy status from this validator. |
| `post-cutover` | `source-ci`, `issue-link`, `cloud-review` | Target routine policy, only after the owner has verified replacement evidence on actual current heads and changed required checks through the protected repository settings. |

Both phases require strict main protection and conversation resolution. In either
phase, complete `source-ci` includes the public native suite. After cutover, private
installed-runtime compatibility remains an exact-main guarded deployment gate,
not a premerge host task. The validator never publishes `integration-tests`,
`agent-review`, `cloud-review`, or any other status and cannot perform the settings
transition.

The cutover is deliberately blocked if PR #19/PR20's native aggregate and release
provenance, or PR #15/PR16's fixed-identity coordinator and exact-head review
contract, are absent from current main. A PR branch or expected merge is not a
dependency proof. Keep current required contexts until the parent operator has
verified actual hosted results, release provenance, complete review evidence and
the retained installed-host gate, then independently applies the reversible
settings change. No coding task or validator performs that action.

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
open/draft state, complete changed-file classification for its exact head,
complete review/thread pagination, review authors/states/commit SHAs, resolved
thread state, and the `cloud-review` status context, state, head and creator ID.
For a sensitive change it also carries the exact-head owner authorization and
targeted-review identities. Consult `deploy/autonomy_policy.py` for the executable
field contract; missing fields block rather than defaulting to success.
