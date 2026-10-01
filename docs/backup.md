# Private local backup and inspection-only restore

`deploy/backup.py` uses the standard library plus the existing app configuration
loader. It does not initialize the app, create users, rotate credentials, start
Hermes, change a schedule, or contact an upstream service. Run it as the app owner,
not root. This is a supplementary **local disaster snapshot**, not an off-site or
encrypted backup policy.

## One local snapshot and isolated verification

From `/home/lindayi/projects/hermes-mobile`, using the existing environment:

```bash
cd /home/lindayi/projects/hermes-mobile
umask 077
backup_root=/home/lindayi/.local/share/hermes-mobile-backups
mkdir -p -m 700 "$backup_root"
snapshot="$backup_root/snapshot-$(date -u +%Y%m%dT%H%M%SZ)"
config=/home/lindayi/.local/share/hermes-mobile-live/config.json

.venv/bin/python3.12 -m deploy.backup create --config "$config" --destination "$snapshot"
.venv/bin/python3.12 -m deploy.backup verify --snapshot "$snapshot"
.venv/bin/python3.12 -m deploy.backup restore --config "$config" --snapshot "$snapshot" --destination "$snapshot-inspection"
.venv/bin/python3.12 -m deploy.backup verify --snapshot "$snapshot-inspection"
```

These commands are instructions, not a record of a live snapshot already made.
Creation and restore verify their own results before printing `Verified`.
A nonzero exit means failure; never treat a partial output directory as a backup.
The destination must not exist, even if empty. Its immediate parent must already
be owned by the current user with **exact mode 0700**; `mkdir -p` does not repair
an existing parent's permissions. Choose a new name after a failed operation.
The utility deliberately leaves failed private output for operator inspection;
it never deletes or overwrites any existing directory.

For disaster inspection when the original config is missing, `--config` may point
to `$snapshot/app/config.json`. It is read only to identify protected original
paths; no connection to a native runtime or live SQLite database is made by
restore. Verification needs only the snapshot.

## Exact scope and layout

The private JSON config is read through `backend.configuration.load_settings`.
Paths are checked *before* normalization so symlinks cannot disappear into
resolved settings. Only explicitly configured profiles are traversed; no implicit
scan of `~/.hermes/profiles` occurs.

| Snapshot path | Source | Requirement |
| --- | --- | --- |
| `app/config.json` | supplied private app JSON config, unchanged | Required |
| `app/state/auth.sqlite` | configured `state_dir/auth.sqlite` | Required |
| `app/state/runs.sqlite` | configured `state_dir/runs.sqlite` | Required |
| `app/state/notifications.sqlite` | configured `state_dir/notifications.sqlite` | Required |
| `app/state/vapid.pem` | configured VAPID private-key file; defaults to `state_dir/vapid.pem` | Required; file-backed keys only |
| `profiles/<name>/state.db` | each configured native profile's canonical state DB | Required |
| `profiles/<name>/{config.yaml,.env,auth.json,SOUL.md}` | corresponding profile files | Included if present |
| `profiles/<name>/memories/**` | regular files recursively, including nested memory files | Included if present |
| `profiles/<name>/cron/jobs.json` | canonical cron definitions | Included if present |
| `profiles/<name>/cron/executions.db` | durable execution/claim ledger | Included if present, SQLite backup API |
| `profiles/<name>/cron/notepad.db` | durable per-job key/value state | Included if present, SQLite backup API |

`manifest.json` records SHA-256 for each payload file, its SQLite classification,
and `absent_optional` paths. Review missing optional paths before relying on a
snapshot. Empty memory directories are not materialized. `DO-NOT-RUN.txt` is a
checksummed warning included in both the snapshot and inspection restore.

Cron scope was checked against the installed native source at
`/usr/local/lib/hermes-agent/cron/{jobs,executions,notepad}.py` and the
[official cron documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/).
The installed store has `jobs.json`, `executions.db`, and `notepad.db`.
Lock files, heartbeats, cron output, usage audit logs, WAL/SHM files, gateway
routing/session JSONL files, skills, plugins, uploads, caches, OS/service config,
app source, and unconfigured profiles are **not included**. References to scripts,
attachments, MCP services, or external credential files are not recursively
followed. This is not a full-machine or fully runnable runtime backup.

## Isolation and safety

**DO NOT RUN the app, Hermes CLI, gateway, cron ticker, or job scripts from either
the snapshot or inspection tree.** Config files and databases are byte-preserving
copies (SQLite files are transactionally copied), **not remapped**. They still
contain production credentials, absolute paths, remote endpoints, passkey RP
identity, and job/delivery targets. The warning is an operator prohibition, not a
runtime enforcement mechanism or sandbox.

Before any future runtime test, an operator must separately review/remap app
`state_dir`, `profiles`, VAPID path, upstream/gateway endpoints and credentials,
delivery tokens and targets, and native config/environment/script references.
Keep execution, profile provisioning, messaging, and cron disabled; use an isolated
network/environment with no production credentials. That runtime recovery and
cutover is intentionally outside this utility. Do not copy this inspection tree
over a live directory. Old cron state can re-arm old occurrences; restoring the
ledger is not permission to start a scheduler.

The utility refuses existing destinations and descendants/ancestors of configured
live state/profile/config locations or the source snapshot. All generated
directories are 0700 and files 0600, independent of umask. Verification rejects
unsafe ownership/modes, missing or additional payload files, symlinks, hard-linked
files, special files, path traversal, checksum changes, and failed SQLite
`PRAGMA integrity_check` results. Source SQLite sidecars are checked for unsafe
links but never copied as files. Original live DB connections use `mode=ro` and
`sqlite3.Connection.backup`; logical live data is not changed. SQLite itself may
use/create shared-memory sidecars as part of normal online reads.

Never place output anywhere web-served. The known public names `frontend`,
`public`, `public_html`, `www`, `htdocs` and `/srv/http` are refused, covering this
app's frontend and `/var/www`. Arbitrary web-server aliases, bind mounts and
custom document roots cannot be discovered automatically; the operator must
choose an actually private local filesystem. The utility assumes a trusted local
owner: it is not a defense against root or malicious concurrent processes running
as the same UID that can replace directories between checks. Do not concurrently
edit snapshot files during verification/restore.

## Consistency, integrity, and limitations

* Each SQLite file is an online-consistent backup, including committed WAL rows.
  The app databases, native state, and cron databases are **not one distributed
  transaction**. Concurrent signup/run/job changes can fall on different sides of
  the per-file capture boundaries. The utility does not pause services.
* Plain config/memory/cron JSON files are copied individually. Avoid editing them
  during capture for a coherent operational snapshot. There is no cross-file
  lock or guarantee that paths referenced by a job exist in the snapshot.
* Snapshot SQLite files use DELETE journaling, without live WAL sidecars. Full
  integrity checks run on these private copies using `mode=rw` plus
  `PRAGMA query_only=ON`: the installed SQLite 3.45 skips CHECK-constraint checking
  on read-only connections. The utility issues no SQL writes during verification.
  Integrity checking is not a domain-level invariant or foreign-key audit.
* Checksums detect accidental corruption or payload tampering relative to the
  manifest. They are **not signatures**: an attacker who can rewrite both a valid
  payload and its manifest can forge consistency. Files contain plaintext secrets
  and personal history. Do not upload, commit, email, or put them in public roots.
* There is no scheduling, retention deletion, off-site destination, encryption,
  cryptographic key management, or automatic live recovery/cutover. A local copy
  on the same disk does not protect against disk/host loss. Power-loss durability
  of a just-created snapshot depends on the filesystem; rerun verification after
  storage problems. Large files require memory proportional to the largest file.

## Tests and observed results

```bash
.venv/bin/python3.12 -m pytest tests/test_backup.py -q --tb=short
```

51 tests passed after successive observed RED → GREEN slices. Fixtures contain
real SQLite tables/rows, keep WAL writers open, and exercise backup → restore →
query, cron stores, permissions including restrictive umask, input symlinks and
special files, traversal/public/live destination rejection, manifest tampering,
corrupt SQLite with recomputed checksum, CHECK-constraint integrity failure, and
subprocess CLI success/failure without secret output. Only synthetic fixtures
were backed up during development; production state was not backed up or altered.
A final focused regression run including configuration, native catalog, and manage
tests passed **61 tests**.

The full repository suite was also attempted at an implementation checkpoint:
403 passed, 10 failed, 41 errors, 16 subtests passed. The failures/errors were
outside this utility, caused by missing `yaml` in the app Python 3.12 environment
(native delivery integration and concurrent member-runtime tests). No dependency
was added and no other component was edited to work around that environment issue.
