# Autonomy gate transition

This policy defines the evidence required by its read-only gate validator. It does
not change or activate repository settings. The active protected checks are exactly
`source-ci` (Actions app 15368), `integration-tests`, `agent-review`, and `issue-link`
(Actions app 15368), with strict/up-to-date checks and conversation resolution.
Advisory `cloud-review` is not required or synthesized.

Every PR also requires the latest authenticated owner-published structured
independent-agent formal COMMENT review on its exact head, a positive verdict and
bound evidence digest, complete review/thread pagination, and resolved threads.
Copilot feedback is supplemental; actual findings and definite rejection remain
blockers. Unchanged reviewed blobs, configuration, and dependencies may retain
verifiable acceptance; changed scope needs one independent delta review, while final
current-head authority and statuses are freshly bound and never copied blindly.

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

| Historical native refresh path | SHA-256 at main5316 |
| --- | --- |
| `deploy/native_controls_release.py` | `b3d3c601db4afd9ff003f4df759fcb057c19a83b920206fb2766d1560fd99175` |
| `backend/model_controls.py` | `206fb5164283c16f46c51da99babe1b5f5e8932f062b4a1ee5bc07affe1f2c34` |
| `backend/native_notifications.py` | `230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610` |

Issue #46's post-release observer change deliberately updates only the runtime
inventory and independent test fixture for its two source files. These candidate
pins describe the exact proposed bytes, not a historical main revision, independent
review acceptance, or permission to activate the observer contract.

| Issue #46 candidate path | SHA-256 |
| --- | --- |
| `deploy/native_controls_release.py` | `0887a6ad4fa0bb5c1351089cc6c53fc8ff7be4c609f42d956d3a51642256dba5` |
| `deploy/observe_release.py` | `bf01500fb253f7d41ce49d375bba63a9e4d80569eb376a6aeae50f56cf71923f` |

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

### Issue #52 native hosted-artifact candidate

The proposed native hosted-artifact release change updates the current source
inventory for the two release controllers below. The policy fixture keeps their
historical merged-main bytes separate from these exact candidate hashes and checks
the candidate against the source bytes. These are issue #52 candidate pins only;
they do not assert independent review, merge, deployment, or gate activation.

| Issue #52 candidate path | SHA-256 |
| --- | --- |
| `deploy/native_controls_release.py` | `0887a6ad4fa0bb5c1351089cc6c53fc8ff7be4c609f42d956d3a51642256dba5` |
| `deploy/self_deploy.py` | `04036a85d2abba0cf38e7717c30d5e6b6f04da2fd7efcea16bbf21086f2ab68a` |

At the issue #52 stage, the native-controller hash in both candidate tables and
independent fixtures was the integrated issue #46 + #52 source: operational busy
observation remained separate from idle-required maintenance with verified hosted
artifacts. It replaced the separate PR47 and PR53 controller candidates, not
historical-main pins. Issue #59 updated the self-deploy candidate with reusable
public HTTPS transport. Those historical candidate pins do not authorize activation
or deployment.

The PR60 second transport follow-up replaces multiprocessing preparation transfer
with a fixed isolated child entrypoint in the already inventoried
`deploy/public_http.py`; the source closure and blockers do not change. Its
current candidate SHA-256 is
`a8d073c00574718c0662973f8f4002e77165166034935c71e25d8177b8e5a295`.
This PR60 + accepted PR45 assembly also overlays the existing `backend/app.py`
pin with `5f0d210cb24b6b49cbf30a4286e6b256c42baa1f552f4b063961c67c5d85ec33`
without changing naming source. Runtime policy and independent pending fixtures
pin these bytes; historical merged-main fixtures are retained unchanged. Focused
actual-byte checks cover both complete current pin maps, not activation approval.

### Issue #77 interactive clarification source candidate

Issue #77 adds the clarification journal and routes to the owner-only mobile run
bridge. `backend/clarifications.py` is included in the fixed source inventory and
the statically traversed Python closure; changes to it use the
`execution-source-contract` blocker. The candidate pins below supersede the
corresponding earlier application and native-controller candidate pins. They bind
exact bytes only and do not assert independent review acceptance, activation, or
deployment.

| Issue #77 candidate path | SHA-256 |
| --- | --- |
| `backend/app.py` | `3fbab2ca8c8d47dffdd1775d67026fbd537f7350aa6ec68f830cc9be87c266c5` |
| `backend/clarifications.py` | `6a6f042beb98881a482efdd85c20555d88a375156a024481ab18a0d5c80e94fd` |
| `backend/hermes_client.py` | `8c018cc4c6e566d481ffcecd87905f2c6dc40bd1fdc67980cb18e5438cdbdf4f` |
| `backend/model_controls.py` | `c35c6e7ed5c92715ef1bd5af236268d1ef080bbcef2a4069a647dbd04bc4e217` |
| `backend/native_maintenance.py` | `e79cfee2bf8d32c3f51dd3ee9247e23029376e10c5e55ea21aa373d6d72535c5` |
| `backend/native_catalog.py` | `8b3c398f52388e0e334d6d64381b677b651b9337ece2d1868a14d3de069c5ed6` |
| `backend/native_run_controls.py` | `564f0ab912a1d138f2ac5b1271935ef5aa2c99cdd2ab48d0527363f1a031e65c` |
| `backend/orchestration.py` | `1ea28e810f7c1827e9f40e934a24c4e7774d76b5fe0beb8d090a0ab0174dce7f` |
| `backend/runs.py` | `b2648c509c520189da024a7338b5556b64876e9542dd8b608c9033d1b2f42d29` |
| `deploy/native_controls_release.py` | `905e5b0f163e92383b7a72d9215a5ebd903751eca908500a48abe6251c1a9fb9` |
| `deploy/self_deploy.py` | `1a72fcf9ac6018a449bcac7bb76764af3f144817e8f6e764eb49aa5a95241363` |

The current native-controls source map binds the clarification-capable
`backend/native_maintenance.py` bytes, while its pre-clarification and rollback
maps retain their original hashes. A clarification wait is known active work;
maintenance and release drains cannot treat it as idle. This current table also
includes the bounded supervised native-409, lifecycle-reopen, and early-terminal
snapshot repairs from head `709ecdcea72b28b1314ee3cd9f6564cd33fd70dd`.
The adapter digest is bound by both current native-controls maps; only derived
pin literals change in `backend/model_controls.py` and
`deploy/native_controls_release.py`. Historical accepted reports and rollback
maps remain unchanged. Source consistency is not a new independent delta review,
pristine autonomous completion, merge authorization, or deployment evidence.

PR57 extends the assembled starter/consumer authority contract with the exact
head/issue/body-digest command and a final prepared-scan admission fence. Its new
`deploy/pull_handoff_binding.py` leaf is explicitly required, independently pinned,
and mapped to `coordinator-review-contract`; missing, malformed and mutated bytes
remain blocking. The PR57-only candidate at
`2447f7960141ecdc24ad445deb0e93f858818691` supplied the following pins. Its
coordinator digest predates the final PR55 restart-ordering integration; the
combined overlay below supersedes that digest, not this historical provenance.
PR58 notification-unit bytes and pins are unchanged.

| Historical PR57 paired admission path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `ce40eadc705bf3887b7f6a7bf9f35f3a9c73241baf473fa83a65316bda5eac6a` |
| `deploy/issue_starter.py` | `c28b18b003bf8761fb583fc72f6023701a22c736edd9e57765eb7886dbecbda9` |
| `deploy/pull_handoff_binding.py` | `e4cf47de3e1b7846da92796781f05437e8225773ddf13debcfc489b58aa50550` |

The final combined source overlay retains PR55's `_reconcile_actions` from
`181455bcb978e8fd9073ebccb90aea64f995efc4` and PR57's admission routines from
`2447f7960141ecdc24ad445deb0e93f858818691`. Its coordinator SHA-256 is derived
from the combined actual bytes, not either parent's digest. The shared helper
subsequently omits an absent first-page cursor so both production API adapters
request the same connection: the coordinator's CLI transport otherwise sends the
literal string `None`, while the starter's JSON transport sends null. It also
requires a valid lowercase 40-hex base SHA on the initial and final REST snapshots,
matching the paired consumer's admission contract. All other linkage checks and
pagination bounds remain unchanged:

| Final PR55 + PR57 + PR58 assembly overlay | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `4a94bd43f7d350cb8aaee08650726893e6775872e8af20f3a0c8f7f61127345d` |
| `deploy/pull_handoff_binding.py` | `3e279674d80426c017bd39b9ebf7777af4f92b0f6ec03fc5d8b8398c0f98898b` |

The runtime source pins and independent literal fixtures explicitly use these
combined/current digests. The starter pin above and issue #58 unit pin below are
unchanged. All other pins and historical baseline hashes are retained. Matching
bytes and focused synthetic tests are consistency evidence only: final exact-head
independent review and integration gates remain required, with no activation,
deployment, installed-service qualification, or operational approval implied.

Issue #67 refreshes the current issue-link workflow candidate and its independent
literal fixture. The historical merged-main fixture retains the previous workflow
digest; the candidate pin describes only these proposed workflow bytes, not an
observed Actions check run or activation approval. The publication-ordering follow-up
creates the blocking app check before its compatibility status and publishes final
app success only after compatibility success. Failure cleanup attempts the blocking
check and status independently. These are ordered best-effort writes, not atomic:
a total outage can preserve prior success, and an uncertain successful app write
can remain eligible if app cleanup fails even when the legacy failure lands.

| Issue #67 candidate path | SHA-256 |
| --- | --- |
| `.github/workflows/issue-link.yml` | `dd722de884fba6c9d613a72baee43f3b81d2d6816954f6a8e77d972b0fd1eafc` |

Issue #75 adds the automatic hosted `integration-tests` job and extends the exact
successful source-run job contract. The job depends on `source-ci`, always evaluates
its result, and succeeds only when the complete aggregate succeeds in that same
workflow run and attempt. Main attestation depends on both gates. These candidate
fingerprints are derived from the actual final workflow and verifier bytes; they do
not claim that this candidate has passed hosted CI, been independently reviewed, or
been activated.

| Issue #75 candidate path | SHA-256 |
| --- | --- |
| `.github/workflows/ci.yml` | `5a1b1a694d286d5f8a1a4188802af7e8f6ab46855d278224d9f248cccec844d9` |
| `deploy/release_artifact.py` | `ef47db3b7fa3e4805488ea4c941e770f26b6256842ad26ffb210476e5f86e721` |

The coordinator's exact 37-file static closure includes the PR57 shared canonical
handoff proof leaf; the fixed 80-file dependency inventory remains explicit. Shared backend
dependencies keep their existing blocker labels. The five
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
dependency hashes above describe main5316, not all current assembly bytes. The
receipt overlay and the retained issue #48 starter/unit changes supersede only
their corresponding historical hashes; the other dependency/launch bytes remain.

### Issue #43 accepted source prerequisites and remaining operational debt

The historical coordinator and helper fingerprints below are issue #43 candidates,
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

| Historical issue #43 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `3f351989201ccbd87d13943ecbf819a1e871b6dede6e1257150f7c37abf6170c` |
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

Issue #58 deliberately refreshes only the notification service pin and its
independent literal launch fixture; the historical issue #43 hash above is retained:

| Issue #58 candidate path | SHA-256 |
| --- | --- |
| `deploy/hermes-workflow-notifications.service` | `934effd6540b6a6ed026fcdac2dda6ebf176792e583c6738dc7b72dc737f199e` |

This replaces unsupported `PrivateDevices` with UNIX-only address-family,
native-architecture and raw-I/O syscall restrictions, retaining the other unit
settings. It does not recreate private device or network namespaces; see the
[security contract and activation boundary](workflow-notifications.md#issue-58-user-unit-security-contract).
The `autonomy-launch-contract` blocker and all review/approval gates remain
unchanged. This source pin proves neither installed-service qualification nor
permission to enable the timer.

The issue #48 starter and supported-user-unit changes retained from PR49 have
these current assembly pins, also independently literal in the launch fixture:

| Retained issue #48 path | SHA-256 |
| --- | --- |
| `deploy/issue_starter.py` | `49debd3c1da5252ea67322a0febb9eb5b56d0ac1a830f3718e4d07377993b517` |
| `deploy/hermes-mobile-coordinator.service` | `49d6ebb6e24b4c0a06af3d10e6e2cce11dd6af7ee8056c99268058c2f6f2cab3` |

Issue #56 updates the starter's current source candidate pin to
`c28b18b003bf8761fb583fc72f6023701a22c736edd9e57765eb7886dbecbda9`. This is
source consistency only, not independent review, runtime evidence, or activation
permission.

Issue #63 updates the current starter candidate pin to
`20603a350aa4c3972563010035a9faed8fcf086b251af847dbed7e4b32e900ca` for its
durable one-shot canonical issue-link recovery with complete readback.
The PR57 digest above remains historical. The refreshed current pin is source
consistency only, not independent review, runtime evidence, or activation permission.

Issue #83 updates the current coordinator and starter source pins for authenticated
initial-source provenance, first independent-review dispatch, and redundant classic
required-check projection handling, including current-main-bound normal anchor
identities, the producer/consumer shared issue edit-history page validator, and
restart-safe version-1 source recovery bound to its persisted start-command ID:

| Issue #83 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `497261d432e5ec8a1a200183ed6a0eca4bdb49f50ab7c8db3a03228bfddafe8a` |
| `deploy/issue_starter.py` | `6c4f645544119c3b317548ef01391edcce37af0bac02c2ed69cbdf714d97419a` |

The independent policy test fixture pins these exact candidate bytes. The update
preserves earlier issue #43, #50, and #79 history; it establishes source consistency
only, not independent review, merge, runtime evidence, or activation permission.

Issue #87 refreshes the current bounded-repair coordinator and lifecycle notification
pins. The candidate atomically splits legacy source and neutral counts only from
complete typed reservation provenance, recognizes authenticated retained receipts
after action compaction, and bounds progress fingerprints to 32 per attempt and
640 per enrollment. Rendered finding inventories now expose their completeness,
and source reservations carry bounded authenticated canonical target maps for
normal and report-correction review prompts. The current delta validates raw
GraphQL thread identities before writes, requires a changed ready-receipt result
for resolution credit, and leaves unknown reservation history in recoverable
waiting without emitting an exhaustion notice. Existing lifecycle event identities remain stable; only new
repair-exhaustion events project the linked issue. These exact source pins and
their independent literal fixture do not establish issue acceptance, independent
review, merge, runtime evidence, or activation permission:

| Issue #87 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `6327a23273250314880df3bac0dd57a31a0567f09841d763179c39c87ce13e43` |
| `deploy/review_evidence.py` | `bc2bea2e4cd17ac28ed96cc5d421f62f63e7ef14ee9bdec5045cf6f26bb8f290` |
| `deploy/task_receipts.py` | `bfc903eddf33a7b8e4b17ccafd8112af70611ce472a44520ed4fe5842845a1c7` |
| `deploy/workflow_events.py` | `63d4066774e276d668d493a69d55e5cb03e4e13c02ab52d1a6dd726098d9b79e` |
| `deploy/workflow_lifecycle.py` | `f8ecf4fa881d907d41a3f8482fa60f1166591dd51e75108aaa4d31f4f2df65b0` |
| `deploy/workflow_notifications.py` | `c599d19bc1f1b1976429d7e5ca834eade37e35c718b4c389ceaef4ad47ecb1ad` |

Issue #50's historical receipt-transport candidate overlaid only
`deploy/cloud_coordinator.py` and `deploy/task_receipts.py` in the source inventory.
The historical PR40 and issue #43 table hashes above remain unchanged:

| Historical issue #50 candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `0935088cb9e3429b83dcc7daa8552cf2c58830e5494549270ec276a076e3193b` |
| `deploy/task_receipts.py` | `60f8596f0cc336cab4ed1484ba67101d7884ff20f68c39634df210cf476541bb` |

Issue #65 overlays only the current `deploy/task_receipts.py` source pin and its
independent literal fixture with the producer-only instruction change. The
historical PR40 and issue #50 receipt digests above remain provenance, not current
bytes:

| Issue #65 producer candidate path | SHA-256 |
| --- | --- |
| `deploy/task_receipts.py` | `50a3a2177ce9bce3ac6626cae36c245aab72f1fa14b342d788e0ffc80e06b90c` |

Only `receipt_instruction` changes: complete-binding/session preflight before
source edits, contiguous fixed dispatch fields before plain ASCII replacement
labels, and explicit replacement instructions. The receipt consumer and all
identity, chronology, grammar and replay checks are unchanged. Focused synthetic
checks exercise the actual `GhApi.write` JSON request representation and reject
unreplaced labels at first acceptance and persisted-proof validation. They do not
establish provider-delivered model bytes, task compliance with the preflight, or
operational readiness. Exact-head review/integration and an owner-controlled real
task trial remain separate gates; no task history or service state is rewritten.

PR55 (issue #54) overlaid only the coordinator entry from the historical issue #50
candidate; issue #65 now supersedes its receipt producer bytes as recorded above.
The PR55 restart-ordering follow-up starts at `00f47230596fd68649088823268b8b76ac2dc884`
(coordinator SHA-256 `a3d011e97862aa76b07fb88ae40b5307db77d3cd9f46bf170a2cf5b78ec3b7d8`).
Review 5396963413 reproduced pending neutral recovery resurrecting a superseded
predecessor after remote head advancement. The follow-up resolves ownership before
handoff advancement and reads the updated prepared state. It preserves the prior
retirement, atomic task acceptance, receipt-proof and compare-evidence fixes.

| Historical PR55-only restart-ordering candidate path | SHA-256 |
| --- | --- |
| `deploy/cloud_coordinator.py` | `b089cf168c2d384b56855c0979853f0d692ec9564d6787bef6424b930d3b0e3e` |

At the PR55-only candidate, the policy pin and independent fixture spelled out
this digest explicitly. The final combined overlay above now supersedes only that
coordinator pin; no validator predicate or historical baseline is changed.
Focused synthetic RED/GREEN evidence is not independent source acceptance; the
combined bytes still require exact-head independent review and final integration
evidence.

These pins identify candidate bytes only. Both files retain the
`coordinator-review-contract` blocker; refreshing pins does not satisfy independent
review, clear the source-contract hold, or authorize operational activation.

Before the Issue #50 overlay, this assembly integrated final PR40 main
`5316f7c75dced1bffafdb545cdd7677e98e855a6`, preserving its parser and previously
merged retention behavior. The Issue #50 transport adjustment is separately pinned
above and does not change that historical lineage. Source acceptance and removal of
the historical hold do not authorize activation.

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
- An always-evaluated `integration-tests` job directly depending on `source-ci`,
  which fails unless that same-run result is `success`; main attestation depends on
  both checks. Its job result and every source job remain bound to the exact run,
  attempt and head SHA.
- The complete public hosted build, checks, JavaScript, portable Python, browser
  and native job contract; browser shards consume the exact generated artifact.
  `deploy/release_artifact.py` must require the exact set including
  `integration-tests` and exact main-source provenance, including hosted gate success.
- A successful `push` workflow run for the same current main SHA, complete unique
  successful jobs for that attempt, an unexpired same-attempt artifact, and
  verified GitHub-hosted provenance bound to this repository, workflow, ref,
  source/signer SHA and run attempt.
- The installed host manifest and guarded deployment path must remain intact.
  Exact-main deployment runs the full private/installed compatibility gate against
  the verified staged source and artifact before activation; a failure blocks
  activation. This is not PR-head evidence, and untrusted PR code is never sent to a
  production or self-hosted runner.
- Actual fixed-repository branch-protection readback with strict up-to-date checks,
  administrator enforcement and resolved-conversation protection.
- The fixed repository/coordinator identities and a complete exact-head review
  contract. The reviewed PR must be open, non-draft, based on current main, and its
  changed-file classification must be complete for that exact head. Evidence binds
  the positive numeric PR-author ID. Every review record must have a positive unique
  ID, valid authenticated author identity, and valid timezone-aware timestamp;
  malformed review records block. The latest owner review is selected by chronological
  timestamp, and its current COMMENT body must exactly match the selected review ID
  and body digest, contain a valid positive structured independent-agent verdict on
  the current PR head, and bind the reviewed evidence digest. Offsets compare as
  instants, not strings or review-ID order. Every review and thread page must be
  complete and every thread resolved. A status or Copilot review alone is not
  independent review.

If the PR under review changes sensitive files, readiness additionally requires
owner ID `5164171` authorization for that exact head, bound to one owner-published
independent-agent review in the complete authenticated `independent_review.reviews`
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
and merge fences. The positive proof also requires authenticated GraphQL
`PullRequestReview` edit metadata bound to the same REST review identity/body/head;
missing metadata, mismatches, or any edit signal block exactly like an edited
record. A later head clears the authorization; approval is not blanket
consent for future commits.

## Policy phases and activation

| Phase | Required main protection checks | Meaning |
| --- | --- | --- |
| `pre-cutover` | `source-ci`, `integration-tests`, `agent-review`, `issue-link` | Current four-check policy; independent exact-head structured COMMENT review is separately required. |
| `staging` | `source-ci`, `integration-tests`, `agent-review`, `issue-link` | Same four-check policy; no advisory `cloud-review` status is required. |
| `post-cutover` | `source-ci`, `integration-tests`, `agent-review`, `issue-link` | Same four-check policy; installed/private host compatibility remains a guarded exact-main deployment gate. |

These are exact context maps, not minimum subsets. No arbitrary supersets or unknown
contexts are accepted, and duplicate contexts block. Each check's app_id must be
explicitly present as a JSON integer or null. `source-ci` and `issue-link` are bound
to Actions app 15368; `integration-tests` and `agent-review` remain unbound. No other
app-binding variants are accepted.

All three phases require strict main protection, administrator enforcement and
conversation resolution. In every phase, complete `source-ci` includes the public
native suite. Installed-runtime compatibility remains an exact-main guarded
deployment gate, not a premerge host task. The validator never publishes statuses
or performs a settings transition. It requires complete source/run/artifact
provenance, changed-file classification, current structured independent review, and
sensitive owner authorization where applicable. Phase names remain for compatibility;
they do not authorize changing the active four-check map.

No coding task or validator performs settings changes or service activation. Do not
equate synthetic unit-test success with live readiness, a merge, or a deployment.

## Evidence fields

The JSON root contains `repository`, `main`, `protection`, `source_ci`, and
`independent_review` records. `main.files` maps the exact required source paths to their
SHA-256 digests and is bound to `main.sha`, `main.ref` and the fixed repository ID.
`source_ci.jobs` carries every unique GitHub job ID, name, run ID, attempt, head
SHA, completion status and conclusion. The artifact record carries its run/attempt,
repository IDs, expiration state, size and SHA-256. Its attestation record carries
the verified certificate identity, issuer, repository ID, ref, source/signer SHA,
run invocation URI, trigger and runner environment.

`independent_review` identifies the current PR repository, base ref/SHA and head SHA,
positive `pull_author_id`, open/draft state, complete changed-file classification
for its exact head, complete review/thread pagination, review IDs, authors, valid
timestamps, states and commit SHAs, resolved thread state, and the selected review
ID, current raw-body digest, and evidence digest.
For a sensitive change it also carries the exact-head owner authorization and
targeted-review identities. Consult `deploy/autonomy_policy.py` for the executable
field contract; missing fields block rather than defaulting to success.
