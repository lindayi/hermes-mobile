# Test quality and disk hygiene

## Scope and requirements

This iteration improves test quality without a test-count target. Preserve security, approval, profile isolation, uncertain-outcome, deletion, recovery, touch/viewport and release-gate coverage. It changes development/deployment test tooling, not production data or running services.

Correct the recovery matrix so default-profile running work differs from member-profile work. Strengthen member preparation rejection assertions so unrelated programming exceptions cannot pass. Remove the existence-only activation test. Replace the historical service-worker cache-string assertion with cache lifecycle behavior, preserving online/offline anonymous asset checks. Consolidate only demonstrably incidental presentation assertions, not control placement or accessibility requirements.

Provide a standard test runner and an explicit cleanup command. Both developer runs and deployment run_checks must use the same managed workspace mechanism. Each run isolates pytest temporary files, browser temporary files, HOME/HERMES_HOME, XDG caches/state/config and screenshots. Preserve staged/generated asset selection and the intended fixed Python/Node executables; support source trees without a .venv through an explicit interpreter. Do not let inherited HERMES_MOBILE_CONFIG or PYTHONPATH direct tests at production.

Keep browser TMPDIR paths short enough for Chromium Unix sockets. Disable test bytecode and pytest cache creation outside the workspace. Honor HERMES_TEST_ARTIFACT_DIR through a shared browser helper. No scheduled daemon or production restart is necessary: cleanup runs at test start/end and has a manual dry-run/apply CLI.

Deletion is restricted to a private, same-user, explicitly managed test root and validated marked run directories. Refuse symlink roots/markers and unknown entries; never recursively clean arbitrary /tmp, HOME, profiles, application state, release/backup directories, browser installations or package caches. Hold a per-run lock and prevent cleanup of active runs. Recheck eligibility under lock before deletion. Concurrent runs must be safe. Interrupted runs must not leave live subprocesses while their workspace is deleted; SIGKILL leftovers are eligible only when demonstrably inactive and stale.

Successful runs remove disposable workspace content. Failed/interrupted runs retain bounded diagnostic logs/screenshots, not whole copied databases or native homes. Retain at most three failure records, seven days old, and 64 MiB total by default; provide reporting when evidence is trimmed. Orphaned managed scratch older than 24 hours may be removed only when inactive. Dry-run reports candidates/bytes without deletion. Cleanup must not mask a test's nonzero status, and failures in cleanup itself must be surfaced.

## Acceptance cases

A. The corrected recovery matrix covers default and member profiles at both admission phases. An unrelated NameError fails the strengthened member tests. Credential redaction, action binding and other safety matrices remain intact.
B. Service-worker activation preserves the current cache and unrelated caches and deletes obsolete Hermes caches; mutations disabling deletion or deleting the current cache are caught. Asset-version changes remain covered by build tests.
C. A synthetic child creates temporary/native/cache/screenshot files; after success these are gone and files outside the workspace are unchanged.
D. Failure retains bounded logs/screenshots and preserves the failure exit status while removing temporary databases/home/cache data.
E. Concurrent active workspace is never a cleanup candidate, including while its child is running. Signals terminate/reap the child before cleanup. Unknown, foreign-owned, symlinked or invalid-marker entries are not deleted.
F. Stale inactive marked workspaces are reported by dry-run and removed by apply; age/count/byte evidence limits are exercised. No general cache or production cleanup occurs.
G. Developer and deployment commands run all Python and both JS filename patterns with the expected interpreter, generated-assets and isolation environment; test selection is supported without shell interpretation. Existing no-venv staged checks stay functional.
H. Real full-suite verification runs through the new workflow. Report actual pass/fail results and managed disk usage before/after, rather than claiming all disk usage is test data.

## Commands and retention

From the project root, `python3 scripts/test.py all` runs the complete Python and
JavaScript suites. `python`, `js`, and `browser` select a suite; arguments after
`--` are a small allowlist of filters/verbosity options, not arbitrary shell
commands or output-directory overrides. For example:

```
python3 scripts/test.py python -- tests/test_native_concurrency.py -k lease
python3 scripts/test.py browser -- tests/browser/swipe-motion.spec.mjs --test-name-pattern=keyboard
python3 scripts/test.py python -- -k member_jobs
python3 scripts/test.py js -- --test-name-pattern=worker
python3 scripts/test.py --assets /absolute/path/to/generated/public browser
python3 scripts/clean_tests.py
python3 scripts/clean_tests.py --apply
```

Explicit paths are available now: within a selected `python`, `js`, or `browser`
suite, pass one or more existing normalized relative file paths under `tests/`.
They replace (never union with) the default collection and can be combined with
the allowlisted filters. Python requires `.py`, JS `.test.mjs`, and browser
`.spec.mjs`. Absolute paths, traversal, symlink components, missing files,
pytest node IDs, and additional interpreter options are rejected before scratch
allocation. `all` and unfiltered default collection are unchanged. The path
selection regression was observed red (3 rejected valid selections), then green
(20 cases) through the managed CLI.

The default developer frontend is source `frontend/`; deployment checks pass the
exact staged generated `public/` assets. The runner never installs dependencies
or copies the virtualenv. It uses `HERMES_TEST_PYTHON` when supplied or the source
`.venv/bin/python`, preserving virtualenv symlinks. Node uses the established
owner executable and concurrency two. The lifecycle manager is Linux/main-thread
code; an injected synchronous runner is a test seam and owns its child lifecycle.

Normal runs use `/tmp/hmt-<uid>/r-<random>/`. Success removes the run directory.
Failure keeps only `run.log`, screenshots in `artifacts/`, and private management
metadata; it discards test databases, homes, caches and temporary browser data.
Logs retain their last 1 MiB. The default retention policy keeps at most three
failed records, seven days old, and 64 MiB total. Pruning happens at test start/end
or through the cleanup command, not through a newly installed daemon. An inactive
orphaned run becomes eligible after 24 hours. Active runs/processes take precedence
over age or space limits. Cleanup defaults to a preview; `--apply` deletes only
eligible marked entries, not every record. Malformed entries remain untouched and
are reported rather than blocking other valid cleanup candidates.

Browser test screenshots honor `HERMES_TEST_ARTIFACT_DIR`; no screenshot changes
are required in production frontend assets. `tests/browser/render-icons.mjs` is
an explicit icon-generation utility, not a test: its frontend icon output is
unchanged and it is excluded from test filename patterns.

The manager is a lifecycle/storage boundary for trusted tests, not a sandbox for
arbitrary hostile code. It does not authorize deletion of legacy unmarked proof
folders, installed browser caches, backups or deployment recovery records.

Detached browser ownership is established through inherited per-execution tokens
and observed parent/child lineage, bound to same-user kernel PID/start-time
identities; PID file descriptors pin signal targets. A workspace-bound path alone
prevents deletion but does not authorize terminating a process. Newer unreadable
same-user processes preserve scratch. An opaque process demonstrably predating
the untouched run-lock mtime (using kernel start time and a conservative one-second
allowance) is excluded from child uncertainty. This assumes trusted tests do not
hand the new workspace to a pre-existing opaque service. Inspectable older
processes still receive workspace-reference checks. This exception avoids treating
unrelated long-lived private services as children of every test run. Direct test
children are waited for and known adopted children are reaped. In nested runners,
an exited helper's zombie entry can briefly remain under an outer reaper; it has
no open files or live address space. Browser safety checks therefore bind PID
identity and require no live process, rather than requiring every `/proc` entry
to disappear immediately. Explicit owned-child reaping tests remain separate.

## Live-baseline harness readiness (not final product-suite sign-off)

- Deployment `run_checks` uses `run_suite`, the exact stage/public assets, and
  `paths.source/.venv/bin/python`; inherited interpreter overrides cannot replace
  that deployment seam. The new deployment regression failed before the port.
- The installed Playwright dependency cache is pinned before HOME/XDG isolation
  (explicit `PLAYWRIGHT_BROWSERS_PATH` is preserved). The cache regression failed
  before the fix. No dependency cache is copied or cleaned.
- Existing browser fixture changes are limited to imports, generated-asset
  selection, scratch and screenshot/video locations. UI assertions are retained.
  `artifacts.mjs` and `generated-assets.mjs` provide shared test-only helpers.
- Targeted managed verification: 31 runner/path/isolation cases passed; the seven
  deployment/cache cases passed; all six artifact/generated-asset helper cases
  passed. A real video-recording browser fixture passed both fresh-build and
  supplied-generated-assets modes in this candidate, which has no `.venv`.
- This host has `/usr/bin/google-chrome` (the existing fixture executable), not
  `/usr/bin/chromium`. Its installed ffmpeg cache was verified at
  `/home/lindayi/.cache/ms-playwright`.
- An exploratory `all` run was stopped on parent request to avoid competing with
  other workers on the two-core host. The parent owns the final integrated full
  suite after product freeze; the historical counts below are not that result.

## Historical result from the original hygiene integration

The numbers below describe the earlier `hermes-mobile` integration, not this
live-baseline candidate. Candidate verification must be recorded separately.

The original unchanged code candidate passed 2,135 Python cases plus 16 subtests
and all 319 JavaScript/browser cases, with no Node skips or failures, through the
managed runner against generated frontend assets. This includes the complete
36-case browser suite, not only the older README's mobile smoke command.

Success removed temporary generated assets and run scratch. The source screenshot
inventory and modification times were unchanged. During verification a workspace
was observed above 130 MiB; afterward the entire managed root occupied 68 KiB,
containing only three bounded failed-run diagnostic records from deliberate
negative checks/earlier verification. No temporary homes, databases or pytest
scratch remained in those records. Dry-run and apply both skipped the actual
active full-suite workspace; after completion apply reported zero eligible
candidates. A separate real SIGKILL proof preserved a surviving child and removed
its 1 MiB orphan scratch only after that child exited.

Fifteen consecutive real nested Chromium interruptions passed after correcting
an over-strict zombie/PID bookkeeping assertion. Disabling detached termination
in a disposable copied runner still failed the smoke because live scratch was
retained. The stricter explicit owned-child reaping cases remain. An initial
full run exposed an implicit-umask backup fixture; its unsafe mode is now set
explicitly rather than relaxing private output permissions.

Independent review approved the cleanup/lifecycle implementation, the malformed
lock correction, and the final test-only browser contract correction. Detailed
logs, mutation evidence and hash-bound reviews are under
`/home/lindayi/test-audits/`; the final suite log is
`test-hygiene-final-full.log`. The bridge, native API and existing gateway all
remained active; no production deployment or service restart was performed.

The filesystem still showed 96% usage (about 1.8 GiB available). This work prevents
new test-scratch accumulation; it does not represent a general purge of installed
browsers, old releases, backups or unmarked historical evidence.

## Initial observations

The root filesystem was 96% used with about 1.9 GiB available. Standard pytest temporary files were only 1.4 MiB, source browser artifacts 3.7 MiB, and /tmp approximately 383 MiB. The general user cache was approximately 1.4 GiB, mostly installed Camoufox browser/font assets; these are not disposable test outputs and are outside this cleanup scope. Legacy unmarked /tmp review/proof directories are not automatically safe to delete merely because their names resemble tests.
