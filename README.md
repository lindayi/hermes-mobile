# Hermes Mobile

A mobile web interface to Hermes Agent with passkey authentication, shared native
sessions, streaming runs, scoped approvals, background activity, Inbox and push.

During a run, an agent can ask a single-choice or multiple-choice question, offer an
Other response when available, or request open-ended text. Submit the answer
explicitly to continue the same waiting run. Clarification is not approval for a
dangerous command or a deployment; configured approval policies remain separate.
See the [mobile clarification bridge contract](docs/clarification-bridge-contract.md).

Canonical source: https://github.com/lindayi/hermes-mobile

## Development

Read [AGENTS.md](AGENTS.md) before editing. Every task uses a branch/worktree and a
GitHub pull request. GitHub reviews carry findings; fixes are separate follow-up
commits. Under owner-authorized issue #92, main requires `source-ci`,
`integration-tests`, and `issue-link`, plus resolved review threads
and an up-to-date branch, including for the repository owner.

The author can merge their own PR after these gates pass. Genuine Copilot reviewer
bot ID `175728472` must submit a `COMMENTED` or `APPROVED` review on the exact head,
with complete pagination, resolved threads and all three successful CI checks.
Coding bot ID `198982749` does not qualify. No independent report or `agent-review`
publication is required. Do not manufacture passing review/test statuses.

Example task setup, from the canonical checkout:

```sh
git fetch origin
git worktree add -b fix/example ../hermes-mobile-worktrees/example origin/main
```

Merge upstream main into PR branches without rewriting reviewed history. Resolve
conflicts from both intents, test both behaviors and their interaction, then obtain
review again. Merge via GitHub only; deploy the resulting main revision.

## Tests

The source retains host-specific native integration seams from the existing app.
It is not yet a portable fresh-install distribution. Required host dependencies
are the compatible Hermes Agent checkout/venv and browser dependencies. The Node
dependency versions are also pinned in `package-lock.json` for hosted checks.

```sh
HERMES_TEST_PYTHON=/home/lindayi/projects/hermes-mobile/.venv/bin/python \
  python3 scripts/test.py python -- tests/test_public_source.py
HERMES_TEST_PYTHON=/home/lindayi/projects/hermes-mobile/.venv/bin/python \
  python3 scripts/ci_tests.py host
```

Use the managed runner, including for focused tests. It isolates HOME, native
state, browser/test scratch and caches, removes successful scratch, and bounds
failure evidence. Do not run account enrollment, real model probes or live operator
scripts as routine tests. Physical Safari/Home Screen push and biometric behavior
remain distinct from emulated browser verification.

Hosted `source-ci` requires the public build, syntax/secret checks, complete
JavaScript suite, portable-Python shards, generated-assets browser shards and native
suite. The `integration-tests` check succeeds only when `source-ci` succeeds in the
same workflow run. These hosted results, together with genuine exact-head Copilot review and
`issue-link` above, are required merge evidence on the exact PR head. The complete
installed/private compatibility partition in `.github/host-tests.json` is not run
on PR heads; it must pass against verified exact-main staged source and artifact
inside guarded deployment before activation, and any failure blocks activation.
See [hosted CI acceptance](docs/hosted-ci-spec.md). Use the managed runner for
focused local iteration; local checks do not replace hosted merge evidence.
`scripts/test.py all` remains available for explicit full-host diagnostics and
conservative release validation.

## Deployment

The canonical local main checkout is `/home/lindayi/projects/hermes-mobile-git`.
Runtime state and credentials remain outside the repository. Existing services are
`hermes-mobile` (bridge), `hermes-mobile-api` (native listener) and `hermes-gateway`
(WhatsApp/cron); the native listener is not another scheduler.

Deploy only a clean, freshly verified `origin/main` through `deploy.self_deploy`.
The controller keeps the existing exclusive deployment lock, active-run drain,
protected native fingerprints and rollback checks. Main provenance is recorded
with staged releases. Never deploy old copied candidate directories or restart an
active session. The installed dependency venv remains separate from tracked code.

Merging main starts CI. After the exact-main source workflow succeeds, the
Production approval workflow verifies current-main provenance and classifies the
full diff from the authenticated deployed base before creating a queued exact-SHA
production intent. The configured risk and environment policies remain in force:
only audited routine changes qualify for routine delivery, while sensitive changes
require the applicable owner approval. When installed and enabled, the outbound
guarded worker revalidates the intent and exact-main hosted artifact; the existing
controller stages the verified source and artifact and runs the complete
installed/private compatibility partition before activation. It defers for active
or unknown sessions and preserves the deployment lock, drain, native fingerprints,
health checks and rollback. Native, dependency or data changes unsupported by
ordinary delivery require the separate guarded maintenance path; hosted evidence
and owner approval do not bypass compatibility checks. See
[guarded delivery](docs/guarded-delivery-spec.md) and
[routine delivery policy](docs/routine-delivery-spec.md). Private operational
configuration, account/session data and historical incident evidence are omitted
from Git. Product specifications remain under `docs/`; legacy specs describe their
original feature scope, while [the Git workflow](docs/git-development-spec.md) and
AGENTS.md govern all new development.

## Cleanup

After a verified GitHub merge, remove that task's clean worktree and merged branch;
the remote merged branch is deleted automatically. Use `python3 scripts/clean_tests.py`
for managed scratch inspection and `--apply` for eligible cleanup. Never remove
unknown/unmerged work, another active session, installed dependencies, runtime data
or any release still referenced by a running service. Git replaces redundant source
copies; bounded deployment rollback material is a separate operational need.

## Public-source safety and licensing

No production credentials, private databases or generated artifacts belong here.
The secret scanner has exact reviewed false-positive entries for hashes and fake
privacy-test fixtures, not blanket file exclusions. Push contact metadata uses the
public site URL instead of a personal email address. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for Hermes-derived patch licensing.
