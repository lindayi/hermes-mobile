# Hermes Mobile

A mobile web interface to Hermes Agent with passkey authentication, shared native
sessions, streaming runs, scoped approvals, background activity, Inbox and push.

Canonical source: https://github.com/lindayi/hermes-mobile

## Development

Read [AGENTS.md](AGENTS.md) before editing. Every task uses a branch/worktree and a
GitHub pull request. GitHub reviews carry findings; fixes are separate follow-up
commits. Main requires `source-ci`, `integration-tests`, `agent-review`, resolved
review threads and an up-to-date branch, including for the repository owner.

The author can merge their own PR after these gates pass. GitHub does not allow
self-approval, so independent reviewer agents post formal COMMENT reviews and the
verified result is recorded as the exact-head `agent-review` status. This is not a
human approval or a claim of a separate GitHub identity. No routine owner action is
required. Do not manufacture passing review/test statuses.

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
  python3 scripts/test.py all
```

Use the managed runner, including for focused tests. It isolates HOME, native
state, browser/test scratch and caches, removes successful scratch, and bounds
failure evidence. Do not run account enrollment, real model probes or live operator
scripts as routine tests. Physical Safari/Home Screen push and biometric behavior
remain distinct from emulated browser verification.

Hosted `source-ci` runs syntax checks, a pinned secret scanner and managed JS unit
tests on a disposable GitHub runner. Full Python/browser/native integration runs
on the compatible host with isolated state and is reported on the exact tested PR
SHA as `integration-tests`. No public-PR code automatically runs on the production
host via a self-hosted Actions runner.

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

The migration itself does not restart the production services. Private operational
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
