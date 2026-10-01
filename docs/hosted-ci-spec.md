# Hosted test offloading acceptance

## Goal and boundaries

Move reproducible work away from the resource-constrained production host onto
credential-free, disposable GitHub-hosted Ubuntu runners. Preserve all test
coverage and exact-revision evidence. This change does not enable automatic
production deployment, grant GitHub access to production, or restart services.

## Acceptance

1. PR and main workflows run syntax/secret checks, JavaScript tests, generated
   frontend browser tests and the portable Python suite on hosted runners.
2. Python tests requiring the installed patched Hermes runtime or private operator
   files remain an explicitly documented local compatibility suite. Classification
   covers every collected test file exactly once; new files default to hosted,
   not silently omitted. Missing/stale host entries, invalid paths and invalid
   shards fail before execution. No test outcome is converted to a skip/pass.
3. Hosted jobs install pinned dependencies without production secrets. Cache only
   package downloads, not private state. Browser workers are serial per shard;
   independent hosted shards provide parallelism instead of competing on this host.
4. Every shard uses the existing managed test workspace lifecycle, isolated HOME,
   short temp paths, process cleanup and bounded failure evidence. Browser tests
   exercise generated assets built from the checked-out revision. No raw runner
   bypass, blind retries, increased gesture thresholds or assertion removal.
5. A required hosted aggregate fails on failed/cancelled/skipped constituent jobs.
   Server compatibility and independent agent review remain separate exact-head
   gates. Existing required gates are not weakened during migration.
6. Failures expose useful bounded synthetic logs/screenshots with short retention;
   never upload native homes, databases, credentials or host operator files.
7. Small local RED/GREEN tests verify partitioning, shard union/disjointness,
   validation, concurrency and workflow gate contracts. Real GitHub Actions runs
   prove installation and all hosted suites; local validation runs only the
   residual host suite plus focused regressions, not another broad full suite.
8. Formal independent review, follow-up commits and exact-head gates precede
   protected merge. Reconcile intervening main changes without dropping parallel
   work, then remove only this task's merged branch/worktree and scratch.

## Deployment distinction

A push/merge to main triggers CI, not deployment. No production self-hosted Actions
runner, SSH deployment secret, webhook or automatic restart is added. Existing
release provenance, drain/gating, live checks and rollback protections remain.
Any reuse of hosted evidence by the release path must bind successful trusted
workflow results to the exact source and generated asset bytes; absent verified
evidence must fail closed or use the existing full validation, never skip it.
