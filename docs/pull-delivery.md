# Guarded outbound production delivery

Issue #8 adds a **disabled-until-installed** user timer, not a GitHub Actions
runner on production. There is no inbound webhook, SSH command relay, arbitrary
command payload, or new deployment secret. The cloud job only creates an intent;
the existing guarded controller remains the only publisher/restart authority.

## Authorization and evidence

1. `Source checks` finishes successfully for a `push` to `main` in
   `lindayi/hermes-mobile` (repository ID **1399942965**, source workflow ID
   **372155405**, `.github/workflows/ci.yml`). PRs, forks, dispatch runs, and failed
   source runs are ineligible. The artifact verifier separately checks the actual
   job set, latest attempt, artifact bytes and restricted GitHub attestation.
2. `.github/workflows/production.yml` runs from the trusted default branch, with
   **no checkout**. Its single `Approve production` job uses the `production`
   environment. Configure that environment for main only and required reviewer
   `lindayi` (user ID **5164171**). The only permissions are contents/actions read
   and deployments write. The github-script action is commit-pinned.
3. After actual approval, the job rechecks source provenance and current main,
   then creates a deployment with exact SHA `ref`, task `deploy:mobile`, environment
   `production`, `auto_merge: false`, and `required_contexts: []`. The last setting
   is not a tests bypass: authenticated source CI plus artifact verification are
   the tests gate. It posts **queued**, never success.

   ```json
   {"version": 1, "source_run_id": 123, "approval_run_id": 456}
   ```

   These are integer IDs only; extra payload keys, booleans, stringified JSON,
   paths and commands are rejected. There are no executable request fields.
4. Both producer and host require **actual owner approval history**, not merely
   a bot creator or a successful protected job. The host binds the deployment to
   a successful first-attempt `workflow_run` run at the trusted production workflow
   path/ID, main/default ref, exact same SHA, one successful named job, and a
   creation timestamp inside that job's execution interval. The endpoint is
   [`GET /repos/{owner}/{repo}/actions/runs/{run_id}/approvals`](https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run).
   Its documented response is an array of `{state, user, environments, comment}`;
   `state` is `approved`, `rejected`, or `pending`, `user.id` identifies the reviewer,
   and `environments` contains objects with `name` and `id`. This schema was also
   checked against GitHub's official
   [OpenAPI description](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json).
   **There is no review run-attempt field.** Approval workflow reruns are therefore
   rejected rather than attributing old approval to a new attempt. Any production
   rejection blocks the request even if another history entry says approved.

GitHub's public environment API may leave `can_admins_bypass: true`; disabling it
is optional additional UI hardening. An admin-bypassed job with no positive owner
review is **not authorized** by either the workflow script or host. Never replace
this check with successful job status. Repository/workflow identity, reviewer ID,
task, environment and payload policy are fixed in the worker, not downloaded
policy supplied by a request.

## One timer tick

- Read at most the newest page of fixed task/environment deployments and consider
  **only the highest deployment ID**, never fall back to an older candidate.
  Persist a high-water mark; disappearance or out-of-order delivery fails closed.
- Require queued status, exact current main, age no more than 24 hours, successful
  source and approval runs, and the complete owner-review evidence above. A job
  still finishing is deferred; missing/rejected/expired evidence is blocked.
- Open the live `runs.sqlite` through a SQLite URI with **`mode=ro`** before source
  synchronization or checks. Do not instantiate `RunJournal` here. Missing or
  unreadable schema/database, SQL NULL, unknown status, and every status other than
  `completed`, `failed`, `cancelled` defer without closing admission or restarting.
- Hold a private nonblocking worker lock; take the existing protected controller
  `deploy.lock` while synchronizing. Require canonical clean main, approved HTTPS
  origin and no hidden/ignored deployable changes. Fetch main and **fast-forward
  only** to the approved SHA. Never stash, reset, switch branches or discard files.
- Release the preliminary controller lock (the controller takes it itself),
  recheck remote main and idle status, then durably reserve the attempt **before**
  posting in-progress or starting the controller. The fixed subprocess argv is:

  ```text
  /home/lindayi/projects/hermes-mobile-git/.venv/bin/python -m deploy.self_deploy --worker --hosted-run-id SOURCE_RUN_ID
  ```

  It inherits the real systemd `INVOCATION_ID`; the worker never invents one or
  supplies a shell command. This runs in the delivery unit's separate cgroup, not
  the mobile service cgroup. The controller performs its normal checks/verification
  callbacks, verifies the hosted artifact, runs installed-runtime compatibility
  checks, and retains final lock, admission/drain, protected native fingerprints,
  health checks and rollback. Native/dependency/migration maintenance stays a
  separate operator procedure. The poller does not cache private host evidence.
- Report success only after a zero subprocess return and a **fresh** controller
  `status.json` containing `status: succeeded` and the **exact approved SHA**.
  Failures are posted as failure, never disguised as success. If reporting success
  fails after deployment, retain local deployed state; do not deploy again to retry
  a status update.

The worker cannot atomically lock GitHub's remote main while activating. It checks
immediately before invoking and the controller/artifact verifier repeat provenance
checks. A later remote push cannot justify cancelling an in-flight activation.

## Durable state and status

`~/.local/share/hermes-mobile-delivery/` is owner-only **0700**; state files are
**0600**, atomically replaced and fsynced. Symlink/special/hardlinked state files
are refused. Per-intent records are keyed by `SHA:approval_run_id`. A `running`
reservation left by a crash is consumed, not an invitation to replay. Blocked or
failed intents also do not retry forever; busy/pre-approval completion can retry.
Keep this state across upgrades and rollbacks. Do not delete it to force delivery.

From the reviewed canonical checkout, these commands do not activate anything:

```sh
.venv/bin/python -B -m deploy.pull_delivery --status
.venv/bin/python -B -m deploy.pull_delivery --check-only
# --dry-run is an alias for --check-only
```

`--status` reads local state only. `--check-only` uses read-only network API calls,
read-only idle/source inspection, and writes no delivery state, statuses or locks;
it does not fetch, merge, check artifacts, run checks or activate. A `ready` result
is preliminary evidence, not a deployment guarantee. JSON states include:

| State | Meaning |
|---|---|
| `queued` | No intent yet; inspect GitHub's environment UI for pending owner approval |
| `approval` | An intent exists, but its trusted approval workflow has not completed |
| `deferred` | Busy/unknown run journal or held worker/controller lock; later tick may retry |
| `blocked` | Invalid, stale, missing evidence, source issue or operator boundary; inspect reason |
| `running` | Durable controller-attempt reservation; never auto-replayed after interruption |
| `deployed` | Exact SHA verified by controller (reason notes any GitHub reporting failure) |
| `failed` | Attempt failed; inspect controller status and rollback evidence |
| `duplicate` | Already consumed; reason identifies its recorded terminal/running state |
| `ready` | Read-only check-only path passed its preliminary checks |

`--once` requires the unprivileged systemd context for actual execution. The timer
has no automatic process restart and no execution timeout that could kill an
activation. The controller, not a timer timeout, owns rollback.

## Install/enable and disable (operator only)

**Do not enable from an unmerged worktree.** First finish independent security
review, merge, obtain successful hosted evidence, inspect the canonical clean
checkout and current runtime, and verify the environment's owner/main restrictions.
Installation is user-level only. The owner needs the existing authenticated `gh`
CLI (repository/actions read, deployments-status write); do not add a token to the
repository, mobile application environment, unit file or Actions secrets. GitHub
uses its ephemeral workflow token to create the cloud intent. No new credential
file is required on the host.

The installation integration may copy these unit templates. Equivalent manual
steps **after approved merge/inspection**, as the unprivileged owner, are:

```sh
install -d -m 700 ~/.config/systemd/user
install -m 644 deploy/hermes-mobile-delivery.service deploy/hermes-mobile-delivery.timer ~/.config/systemd/user/
systemctl --user daemon-reload
# Only after successful read-only checks and explicit production enable decision:
systemctl --user enable --now hermes-mobile-delivery.timer
systemctl --user status hermes-mobile-delivery.timer hermes-mobile-delivery.service
journalctl --user -u hermes-mobile-delivery.service
```

The templates assume `/home/lindayi/projects/hermes-mobile-git`, its installed
`.venv`, the existing controller installation and owner `gh` authentication. They
must not be installed as root/system units. `NoNewPrivileges=yes`, `UMask=0077`,
and the single fixed entrypoint are mandatory.

Disable **future ticks** without stopping a deployment:

```sh
systemctl --user disable --now hermes-mobile-delivery.timer
```

Do **not** stop/restart/kill `hermes-mobile-delivery.service` or use force-cancel.
`RefuseManualStop=yes` intentionally rejects normal manual stops of the service;
it does not stop the timer from being disabled. Wait for any current invocation
to finish, then inspect both delivery and controller status. Remove unit templates
only after the service is inactive. Retain the durable ledger.

## Failure, rollback, and a new approval

The existing controller handles activation failure/rollback. A failed native
fingerprint, dependency/data compatibility boundary or failed rollback is operator
maintenance, never an excuse to bypass checks or restore a live database blindly.
Inspect `~/.local/share/hermes-mobile-deploy/status.json` and the unit journal;
follow the established guarded controller recovery procedure. Disable the timer
first when investigating, but let any current activation finish.

After correcting a blocked/failed condition, obtain a **new** successful Source
checks completion and a **new production workflow run with fresh owner approval**.
Rerunning Source checks on still-current main can produce such a new workflow run;
rerunning the old approval workflow itself is deliberately unsupported. Do not
edit the ledger, repost an old approval ID or set success manually to force replay.
No automatic GitHub status-repair queue or persistent daemon is implemented.

## Synthetic verification

No test starts a real service, calls production API, writes the production journal,
or uses production artifacts. The focused managed test command is:

```sh
HERMES_TEST_PYTHON=/home/lindayi/projects/hermes-mobile/.venv/bin/python \
  python3 scripts/test.py python -- tests/test_pull_delivery.py
```

Tests use temporary SQLite files, fake GitHub API and controller subprocesses,
nonblocking temporary-file locks, and execute the actual workflow JavaScript in
Node against a synthetic API. Hosted job/attestation and controller protections
have their own explicit test partitions. Live end-to-end activation remains a
post-merge, owner-approved operation and can safely defer while chats are active.
