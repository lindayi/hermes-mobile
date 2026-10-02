# Routine delivery policy specification (issue #17)

Part of #5. The owner approved **unattended promotion of routine changes** while
keeping owner decisions for sensitive changes (authentication, credentials,
migrations, deployment policy, native/dependency maintenance). This replaces the
blanket per-release approval with a fail-closed risk policy. It does **not**
weaken artifact verification, Git provenance, idle/drain admission, locks,
protected native fingerprints, health checks or rollback; all of those still run
inside the unchanged guarded controller. This change activates nothing: the
production workflow and delivery timer stay disabled until the operator cutover.

## Terms

- **Deployed base**: the Git SHA the guarded controller last installed
  successfully. It is the only valid starting point for a diff.
- **Routine**: every path in the complete diff from the deployed base to the
  exact current-main SHA is on the routine allowlist below.
- **Sensitive**: anything else, including missing, truncated, overlarge or
  unverifiable evidence. Sensitive releases require actual owner approval.
- **Bootstrap**: either side cannot prove a deployed base. Bootstrap is always
  sensitive; it never blocks the owner-approved path and never permits routine.

## Routine allowlist (initial, deliberately small)

Source of truth: `deploy/release_policy.py` (host) and its exact policy mirrors in
all three jobs of `.github/workflows/production.yml` (cloud). Parity is tested by
executing the actual classifier and both promotion scripts.

| Routine | Pattern |
|---|---|
| Presentation-only frontend files (exact audited list, not a pattern) | `frontend/styles.css`, `frontend/viewport.mjs`, `frontend/session-swipe.mjs`, `frontend/disclosure-reachability.mjs`, `frontend/icons/{apple-touch-icon,icon-192,icon-512}.png` |
| Tests | `tests/test_<name>.py`, `tests/browser/<name>.{spec,test}.mjs`, `tests/browser/<name>_fixture.py`, `tests/fixtures/<name>.json` |
| Non-operational docs (exact audited list, not a pattern) | `docs/keyboard-viewport-spec.md`, `docs/sticky-activity-spacing-spec.md`, `docs/touch-fold-contract.md` |

Always sensitive, with no broad fallback:

- `backend/`, `hermes-plugin/`, `patches/`, `deploy/`, `scripts/`, `spikes/`,
  `.github/` (including the host-test manifest), root files such as `AGENTS.md`,
  `README.md`, `package.json`, `package-lock.json`, `requirements.lock`, and any
  unknown or future top-level path.
- Every other frontend file, including every **new** frontend file whatever its
  name. In particular `frontend/ui.mjs` (passkey login, registration, recovery,
  enrollment/recovery codes, session clearing, one-time secret display and
  credential/device revocation), `frontend/app.js` (bootstrap and service-worker
  registration), `frontend/index.html` (auth markup), `frontend/api.mjs`,
  `frontend/webauthn.mjs`, `frontend/sw.js`, `frontend/manifest.webmanifest`,
  `frontend/markdown.mjs` (link rendering), `frontend/tool-details.mjs` (secret
  redaction), `frontend/model-controls.mjs` (API calls, per-user storage),
  `frontend/background-placement.mjs` (cross-session placement) and SVG icons.
  A file joins the routine list only by a reviewed change to both classifiers
  (itself a sensitive change). `ui.mjs` stays sensitive until authentication and
  account management are split into explicitly sensitive modules.
- Test infrastructure outside the patterns above (probes, bridges, conftest,
  shared browser harness modules).
- Every other doc, including every **new** doc regardless of name or benign-looking
  content, docs in subdirectories, and instruction files. `DOCS_ROUTINE` is an
  exact positive list, not a keyword denylist. A doc joins only through a reviewed
  change to the host policy and all three cloud scripts (itself sensitive).
- Non-canonical paths (absolute, `.`/`..` or hidden segments, backslashes,
  empty segments, non-ASCII, longer than 200 characters).
- Unsupported change states: copies, type changes, unmerged/unknown states, a
  rename whose **from** path is sensitive, symlinks, gitlinks or executable modes
  (host modes must be `100644`, or `000000` for an add/delete side).
- An empty diff (redeploying the base), more than 250 changed files, or any
  truncated inventory.

Additions and deletions are classified by their path; renames are classified by
both from and to paths. Routine tests are still executed by the controller on
the host; the owner accepted that reviewed, merged test code is routine.

### Documentation audit / intentional fail-closed adjustment (PR #18)

The former filename heuristic was not fail-closed: `session-telemetry-contract.md`
defines an authenticated, owned loaded-profile/session endpoint but contains none
of its denied name fragments. The current-doc audit found the same class in
`chat-snapshot-contract.md`, `model-controls-contract.md`,
`guidance-history-contract.md`, `inbox-cleanup-contract.md`,
`session-delete-contract.md` and `steering-bridge-contract.md` (ownership, endpoint
authorization, CSRF or durable state contracts), and in `public-commentary-contract.md`
and `tool-summary-presentation.md` (private-content exclusion/redaction).
`technical-ui-spec.md` includes release recovery and profile-bound telemetry;
`test-cases.md` includes authentication/security acceptance requirements.

Even the old routine test examples are not purely cosmetic: `ux.md` specifies
WebAuthn, CSRF, secret storage and private-cache rules; `chat-header-spec.md`
defines authenticated rename and account/run response fencing;
`activity-inbox-spec.md` defines authenticated/CSRF mutations and owned durable
cleanup. They now intentionally test **sensitive**, not routine. The routine
mixed-diff fixture instead uses `touch-fold-contract.md`; all parser, mode,
provenance and owner-approval assertions are retained.

The three retained presentation docs were read in full: viewport geometry and
lifecycle, CSS-only sticky spacing, and touch/fold/toolbar geometry respectively.
They preserve existing security/action behavior, not define endpoint authorization,
credential handling, operational configuration or deployment procedures. Explicit
auditing also admits `sticky-activity-spacing-spec.md`, previously rejected only
because `spacing` contains `ci`. All other current and future docs default sensitive;
adding more deny words or inspecting change text is not a safe substitute for review.
This adjusts **release classification only**, not endpoint authentication itself.
Sensitive changes still require actual owner approval, and all Git/base/tree/mode
provenance, guarded-controller and bootstrap checks remain unchanged.

## Deployed-base evidence

**Host (authoritative for what is installed).** The worker reads, before any
fetch or merge, the controller's `status.json`: it must say `succeeded` with a
40-hex `git_sha` and 32-hex `release`, the `current` symlink must resolve to
`releases/<release>`, and that release's `git-provenance.json` must be exactly
`{"git_sha": <same SHA>}`. Anything else (missing Git provenance, failed,
rolled back, running, malformed) is **bootstrap/sensitive**.

**Cloud (independent).** The read-only classify job lists the fixed
`deploy:mobile`/`production` deployments (one bounded page, newest first, at most
30 inspected). Only a deployment created by `github-actions[bot]` whose newest
status is `success` **created by the owner** (ID 5164171, the identity the host
worker reports with) counts. Queued-only, bot-only intents (never attempted by
the host) are skipped. A newer failure/in-progress/foreign/unknown record, a
non-bot deployment, a full page without a success or more than 30 inspected
records makes the base unknown (bootstrap/sensitive). Success statuses by
unrelated identities are never trusted.

**Diff.** The cloud uses the compare API `base...sha`; it must report
`ahead`/`identical`, `behind_by: 0` and `merge_base_commit.sha == base`
(otherwise the job fails and no intent is created). The host separately runs
`git merge-base --is-ancestor` and `git diff --raw -z --no-renames --no-abbrev
--no-ext-diff --no-textconv base sha` on fetched Git objects **before** merging
and before the controller runs, using the policy module already imported from
the deployed checkout. No unmerged or incoming code is imported or executed.

The cloud also reads each exact base/head commit and its recursive Git tree from
the fixed repository's authenticated Git API. The returned commit SHA and tree
SHA must match the requested objects; both trees must explicitly be complete
(`truncated: false`), contain at most 100,000 entries, and have unique,
well-formed path/mode/type/object records. The compare inventory must be
non-empty and at most 250 entries. Its statuses and old/new paths must describe
the exact set of changed paths in those trees, with no missing, duplicate or
unexpected entries. Any unverifiable or inconsistent inventory is sensitive,
never a path-only routine fallback. The classifier and each promotion job
independently repeat this same comparison/tree validation against the exact
base/head commits before selecting or creating an intent. The cloud maps the
tree modes into the host policy: only regular `100644` files (and `000000` add
or delete sides) can be routine; executable `100755`, symlink `120000`,
gitlink `160000`, type changes and unknown modes are sensitive. API requests
are read-only; producer jobs do not checkout or execute incoming source.

## Cloud workflow

`Production approval` still triggers only on a completed, successful `push` to
`main` run of `Source checks` (workflow ID 372155405) in repository ID
1399942965, on the trusted default branch, with no checkout and pinned
`actions/github-script`. Top-level permissions are empty.

1. `Classify release` (`contents: read`, `actions: read`, `deployments: read`,
   no environment): verifies source provenance and current main, derives the
   deployed base and outputs `risk` (`routine`/`sensitive`) and `base_sha`.
2. `Promote routine release` runs only for `routine`, in the **unprotected**
   `production` environment, and creates the intent without owner review.
3. `Promote sensitive release` runs only for `sensitive`, in the
   `production-sensitive` environment with required reviewer `lindayi`. Its
   script requires a positive owner review for `production-sensitive` in this
   run's history; admin bypass is not approval.

Both promote jobs run an identical script (selected by the static job ID),
reject any rejected review and reruns, recheck source provenance and current
main, and create a deployment whose strict version 2 payload carries only IDs and
SHAs:

```json
{"version": 2, "source_run_id": 123, "approval_run_id": 456, "base_sha": "<40 hex or null>"}
```

There is no risk flag in the payload. `base_sha` is `null` only for bootstrap,
and the routine job refuses to run without a base.

## Host acceptance of version 2

All version 1 checks still apply (latest request only, high-water mark, queued
status with the run's log URL, exact current main, 24-hour age, bot creator,
first-attempt successful `workflow_run` of the trusted production workflow and
successful push source run). Additionally:

- The latest-attempt job list must be **exactly** the three job names above, all
  completed with `run_id`, `run_attempt == 1`, `head_sha`, `head_branch: main`
  and `workflow_name: Production approval` bound to this run. Classify must
  succeed and exactly one promote job must succeed while the other is
  `skipped`; the deployment must be created inside that promote job's interval.
  Neither the job name nor a successful workflow alone is accepted.
- Any rejected review in the run blocks. The sensitive path additionally needs
  an approved review by user ID 5164171 (`lindayi`) for `production-sensitive`.
  Approvals for another environment, another run (replay), or reruns do not count.
- Cloud base `B` (payload) and host base `L`: if both exist and differ, **block**.
  If both exist and match, the host classifies `L..sha` itself; otherwise the
  host class is bootstrap/sensitive (and the SHA must descend from any base
  present). The host class must equal the cloud path; any disagreement blocks.
- Policy enforcement happens after fetch and before `merge --ff-only`; failure
  leaves the checkout untouched and the controller never starts.

Durable reservation before side effects, no replay after ambiguous outcomes,
deferral while chats are active, and the fixed controller argv are unchanged.
Native, dependency and migration maintenance are never routine.

## Operator bootstrap and recovery

- **Initial cutover** (parent task, after review/tests): create the
  `production-sensitive` environment (main only, required reviewer `lindayi`),
  then remove the reviewer from `production`. Until that cutover the routine job
  still waits for the existing `production` review (more restrictive, not less),
  and a not-yet-created `production-sensitive` environment has no reviews, so
  sensitive intents fail closed.
- The first intent has no authenticated cloud base, so it is **sensitive** and
  needs owner approval. Its host-reported success becomes the cloud base.
- After an out-of-band deployment, a failed or ambiguous attempt, or a missed
  success report, bases are unknown or disagree. Unknown bases route to the
  owner-approved path; disagreement blocks. To recover from a missed success
  report, the owner may post a `success` status on that exact bot-created
  deployment after verifying the controller status. Never edit the ledger,
  fabricate statuses for other SHAs, or reuse an old approval.

## Acceptance cases

1. Routine audited-presentation/test/doc-only diff from verified base → routine
   job, no review, host recomputes routine, controller runs.
1a. (SEC-1) A real Git diff changing passkey recovery handling in `frontend/ui.mjs`,
   bootstrap in `app.js`, auth markup in `index.html`, link rendering, or adding
   any new frontend file → sensitive on both host and cloud.
1b. Real Git chmod changes in either direction on an allowlisted test, plus
    symlink additions and regular-file-to-symlink changes, → sensitive on both
    host and cloud; the promotion job recomputes the same mode-aware result.
1c. Telemetry-contract changes and new unknown docs, including benign-content
    `endpoint-map.md`, `egress-routing.md` and `quickstart.md`, → sensitive for
    modifications, additions, deletions and renames in either direction with a
    routine doc. Real Git inventories and all three executed cloud scripts agree;
    routine promotion refuses, sensitive promotion needs actual owner approval.
    The audited docs still support modifications/additions/deletions and renames
    between routine paths.
2. Any backend/deploy/patch/lock/.github/script/instruction/unknown path, a
   rename from such a path, a symlink/executable mode, >250 files, an empty or
   truncated diff → sensitive.
3. Routine claim with a host-sensitive diff, base mismatch, missing host base or
   non-descendant SHA → blocked before merge; no controller, no status post.
4. Sensitive with owner approval for `production-sensitive` → accepted; missing,
   bypassed, other-environment, rejected or rerun approval → blocked.
5. Payload extras (for example `"risk": "routine"`), wrong types, unknown
   versions or routine with `null` base → blocked.
6. Job-set tampering (missing/extra/renamed jobs, both or neither promote job
   successful, failed classify, wrong attempt/SHA/branch/workflow) → blocked.
7. Version 1 owner-approved intents keep their original behaviour.
8. Cloud history: owner success → base; unrelated-identity success, newer
   failure/in-progress, non-bot creator, no history → bootstrap/sensitive.
