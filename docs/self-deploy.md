# Unprivileged mobile releases

## Git-controlled source prerequisite

Routine releases use the canonical `/home/lindayi/projects/hermes-mobile-git`
checkout only, after GitHub PR review, follow-up commits, required checks and merge
to main. Fetch and fast-forward that checkout to origin/main; never copy an older
candidate over it. The controller rejects dirty, stale, wrong-remote or unmerged
source before publication. Read the repository AGENTS.md for parallel tasks and
cleanup. Its local ignored `.venv` points to the existing installed test/runtime
environment; dependencies and private state are not committed or copied into Git.

The one-time setup below describes the original installation and is NOT part of
ordinary updates or the Git migration. Do not rerun it on the enrolled live app.

`deploy.self_deploy` publishes **only the mobile frontend and bridge**. It never
restarts the native API (`hermes-mobile-api.service`), WhatsApp, or member
schedulers. It never copies, restores, or rolls back user databases or private
configuration. No daemon or privileged command runner is installed.

## One-time owner setup

The owner runs the reviewed `deploy/enable_self_deploy.py` administrative helper
once, from an ordinary owner terminal:

```sh
sudo /usr/bin/python3 /home/lindayi/projects/hermes-mobile/deploy/enable_self_deploy.py
```

It hardens Apache to serve static allowlisted assets, grants `lindayi`
ownership of **only** `/var/www/html/hermes` and its static contents, then drops
privileges permanently before running the controller's `--bootstrap` mode.
The helper must not be run by a current conversation that needs the bridge.

**The initial bootstrap requires an operator maintenance window:** stop using the
web application and wait for its current run to finish first. The old bridge does
not contain the admission gate. Bootstrap refuses an active/unresolved run rather
than stopping it, but an old client can otherwise race the idle check; therefore
no concurrent clients may submit during this initial migration. If bootstrap was
refused after the ownership grant, finish the conversation and run, as `lindayi`:

```sh
cd /home/lindayi/projects/hermes-mobile-git && .venv/bin/python -m deploy.self_deploy --bootstrap
```

This is an **unprivileged** command. The controller refuses execution as root.
The existing private config remains at
`/home/lindayi/.local/share/hermes-mobile-live/config.json`; the existing runs DB
remains at `/home/lindayi/.local/share/hermes-mobile-live/runs.sqlite`.

## The single future agent command

After initial bootstrap, an agent can request a complete tested release with:

```sh
cd /home/lindayi/projects/hermes-mobile-git && .venv/bin/python -m deploy.self_deploy
```

This **schedules**, rather than performs, a bridge restart. It creates a separate
`systemd-run --user` transient timer/service, delayed five seconds. The command
returns its unique unit name immediately, so the current request can finish.
The worker stages and tests the source, closes new-run admission, then waits up to
30 minutes for already admitted runs to drain. It never interrupts the current
run just because five seconds elapsed. `unknown` or unrecognized run states block
deployment immediately; queued/running/stopping/waiting-for-approval states must
finish normally. No native gateway lock is taken or changed.

Both the worker and the bridge retain `NoNewPrivileges=yes`. The worker runs
outside the bridge's service cgroup. Do not invoke its internal `--worker` flag
manually. Do not replace this command with `systemctl restart` in a conversation.

### Frontend-only: direct replacement, no restart

```sh
cd /home/lindayi/projects/hermes-mobile-git && .venv/bin/python -m deploy.self_deploy --frontend-only
```

This runs immediately after local staged test gates. It publishes frontend files,
verifies them, and restores preimages on failure. It does **not** touch admission,
the runs DB, the bridge pointer/drop-in, or any service. A running chat need not
finish first. This mode cannot deliver backend/BFF changes: those need the full
release command above.

### Status and logs

```sh
cd /home/lindayi/projects/hermes-mobile-git && .venv/bin/python -m deploy.self_deploy --status
journalctl --user -u hermes-mobile-deploy-UNIT_ID.service
journalctl --user -u hermes-mobile-deploy-UNIT_ID-observer.service
```

Use the actual unique unit printed by the scheduling command. `--status` is a
read-only last-worker result, not proof that a just-scheduled timer has started.
`running` is also retained if a process is killed; inspect its journal and the
admission gate before concluding it is healthy. Test/staging errors are reported
as `failed`, a successful rollback as `rolled_back`, and rollback verification
failure as `rollback_failed`. A scheduling command succeeding is not a completed
release; report final success only after worker status/logs confirm it.
The separate observer journal reports `failed` only for an explicit terminal
deployment failure; `verification_failed` means that success could not be proven.
Its phase/reason fields are fixed allowlisted diagnostics, never raw exceptions.

## Versioned public assets

Supersonic caches fixed-name CSS/JS even when query parameters change. Each staged
release therefore builds `public/` from unchanged frontend sources. Only index.html
keeps its name; every dependency gets the same source-graph version in its filename.
Imports, styles, icons, manifest, worker registration/precache and worker cache ID
are rewritten together. Real browser tests run against this generated directory.
The verifier compares generated bytes, not raw source filenames. Stable HTML must
still refresh; existing tabs may need reload when obsolete files are removed.
Changing builder transformation semantics requires its format-marker bump.

## Release mechanics and invariants

1. A filesystem lock serializes frontend and bridge releases. Private deployment
   state and backups live under `~/.local/share/hermes-mobile-deploy` (0700).
2. A unique private `releases/<id>` directory receives source copies, including
   backend/frontend, tests and their supporting deploy/patch/plugin/script/docs
   files. Project root config, state and `.venv` are not copied. Known nested
   runtime files are excluded; source-tree symlinks are refused. These are source
   snapshots, **not personal-data backups**.
3. The controller uses the candidate's established `.venv/bin/python` without
   copying the environment into releases. The managed test runner executes all
   Python tests and both `tests/browser/*.test.mjs` and `*.spec.mjs` against the
   staged source and exact generated assets, not live endpoints. Tests use a
   private umask of 077; permission fixtures explicitly set their intended modes.
   HOME, native state, caches, scratch and artifacts are isolated. Live config and
   Python path overrides are removed; `HERMES_TEST_PYTHON` explicitly names the
   established interpreter so no-venv stages can build fixture assets. The Node
   binary is `/home/lindayi/.hermes/node/bin/node`; installed JS/browser dependencies
   must already be present. The controller installs no packages. A post-test source
   fingerprint rejects mutated staged code.
4. Routine releases compare `requirements.lock`, native patches, the Hermes
   plugin, native service/member runtime/scheduler code, and native/member unit
   templates against the previous private release. Changes are refused before
   tests/gating: shared dependency/native updates need separate operator
   maintenance, not a pretend code-only rollback.
5. After tests, a persistent SQLite gate is acquired in `runs.sqlite`. Both gate
   acquisition and `RunJournal.submit` serialize with `BEGIN IMMEDIATE`; the gate
   check is inside the same transaction as admission. The gate rejects new runs
   with the existing HTTP 409 conflict path. Existing run completion/approval
   handling remains possible. Gate read errors fail closed.
6. Once idle and the public preimage has been captured/published (below), an
   atomic private `current` symlink selects the new release. The
   user drop-in at
   `~/.config/systemd/user/hermes-mobile.service.d/50-self-deploy.conf` changes only
   bridge `WorkingDirectory` and retains `NoNewPrivileges=yes`; the existing
   ExecStart and private-config environment remain unchanged. A newly launched
   process resolves the pointer into its release, so later pointer changes do
   not move its working directory.
7. The static publisher captures a full preimage/checksum manifest in a fresh
   private backup before modification, validates public filenames/no symlinks,
   and atomically replaces each public file. **This is per-file atomicity, not
   an atomic multi-file website switch.** There can be a short mixed-version
   window. Keep frontend/backend changes temporarily compatible.
8. Only the bridge restarts. Verification compares the exact public file set
   and bytes with the staged frontend (or saved preimages on rollback), checks
   bridge local health, systemd MainPID and `/proc/<pid>/cwd`, and requires the
   anonymous `/sessions` endpoint to return 401. It fetches the public launch URL (expected index.html bytes) and each asset over
   HTTPS with a cache-busting query to compare bytes. HTTP errors name the failed path/status. HTTP verification disables
   environment proxies and sends no credentials. Byte mismatches from CDN stale-while-revalidate are retried: both canonical
   browser and cache-busted URLs must match, at most six attempts per resource
   with 1/2/4/8/8-second backoff and a 90-second aggregate public-check budget.
   Persistent wrong bytes and HTTP failures still fail; TLS validation stays enabled.
   Public checks use one isolated strict-TLS HTTP session for the complete byte
   verification. Environment proxies are disabled; redirects are limited to five
   same-origin HTTPS hops; and each response is read only up to one byte beyond
   the expected asset size. The parent process enforces each whole-request deadline,
   including DNS, TLS, headers, and trickling bodies, and terminates a stalled
   worker. Transient network failures and byte mismatches may retry only inside
   the existing finite aggregate budget; a final deadline check rejects matching
   bytes that arrive too late. Reusing transport reduces connection churn in
   synthetic verification, but does not establish a CDN, rate-limit, or production
   root cause.
9. Failure restores the prior pointer/drop-in and public preimages, restarts the
   old bridge if activation began, and verifies old health/public serving before
   reopening admission. Source project files and user DBs are never restored.
   Failed rollback leaves the gate closed and records `rollback_failed`.

## Retrying the initial Apache 403 bootstrap

The original static hardening conflicted with inherited WordPress rewrite rules
and denied the directory launch URL. The corrected helper disables rewrite only
inside Hermes, permits DirectoryIndex directory traversal, and retains static-file,
no-handler and no-symlink protections. Real Apache fixtures cover both root/index,
unchanged parent WordPress routing, and disallowed-file/symlink denial.

With web activity idle, rerun:

    sudo python3 /home/lindayi/projects/hermes-mobile/deploy/enable_self_deploy.py

After applying the fixed Apache include and permanently dropping root, bootstrap
recognizes an initial `rollback_failed` record. Under the deployment lock it
requires absent current/drop-in pointers, exact recorded gate owner, idle runs,
an existing database, and a validated backup manifest with matching backup/public
bytes. It verifies source bridge PID/CWD, health, anonymous 401, public landing and
all asset bytes. Only after transactional rechecks does it clear that exact gate;
then normal tested bootstrap proceeds. Any failed check retains the failure record
and gate. This is NOT a force-unlock or general crash recovery. Routine/frontend
releases refuse to overwrite a `rollback_failed` record. No user DB is restored.

## Limits and operator recovery

* This controller trusts owner-edited code/tests. It is not a sandbox for hostile
  code. Tests must remain offline and fixture-based. Do not put secrets in source
  trees or deploy backward-incompatible database migrations with this mechanism.
* Source directories stay writable for development; releases are independent
  snapshots, not a security boundary against the same user. Do not edit a staged
  or active release manually, or change dependencies while a release is testing.
* Existing application data is never rewound. Schema/data migrations must remain
  readable by the previous bridge; otherwise use explicit operator maintenance.
* Native API and WhatsApp keep their original source/runtime bindings. Updating
  files in that source project does not itself restart them, and the controller
  cannot promise their future manually triggered restart will ignore edits.
* Process death/power loss during publication is not automatically recovered.
  There is no polling daemon, blind auto-unlock or arbitrary shell endpoint.
  Except for the verified initial-bootstrap retry above, a persisted gate stays
  closed and a subsequent deployment cannot steal it.
* For other `rollback_failed` cases or an interrupted `running` deployment: inspect the unique
  worker journal, private `status.json`, `current`, user unit/drop-in and backup
  manifest. Have the operator restore the indicated pointer/assets if needed and
  verify bridge health, exact process CWD, and public files. Reconcile any unknown
  runs. **Only then** may the operator clear the single owned row in
  `deployment_gate` using an explicit SQLite transaction. Do not blindly remove
  the DB, restore a DB backup, or clear the gate to make a deploy pass.
* Releases/backups are retained for investigation; no automatic pruning is done.
  They can consume disk. Remove obsolete private snapshots only in maintenance,
  never the target of `current` or a backup referenced by an unresolved failure.

## Local verification without deploying

```sh
python3 scripts/test.py all
# Focused loops use explicit existing test files:
python3 scripts/test.py python -- tests/test_activity_event_times.py
python3 scripts/test.py browser -- tests/browser/reachable-disclosures.spec.mjs
python3 scripts/clean_tests.py  # dry-run only
```

Use the managed runner rather than raw pytest/Node: it isolates scratch, native
homes and caches, binds generated assets, and removes successful test output.
The same manager runs the staged release gate. Keep installed Playwright browser
and ffmpeg lookup pinned with `PLAYWRIGHT_BROWSERS_PATH` when scheduling the worker.

Controller tests use temporary private release trees, real SQLite journals, the
real static publisher, fixture subprocess checks, and a local temporary HTTP
server. Systemd/health boundaries are injected. They do not restart real services
or read/change live configuration or credentials.
