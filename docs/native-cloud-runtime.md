# Native cloud-runtime reproducibility pilot

## Scope and boundary

This pilot reconstructs the public Hermes runtime on a disposable GitHub-hosted
Ubuntu runner. It does not access production, install on an arbitrary host, copy a
production checkout or virtualenv, upload private state, run operator scripts, or
make real model calls. The `/usr/local/lib/hermes-agent` path is created only after
the non-mutating preflight verifies the fixed GitHub-hosted repository, Ubuntu
image, x86_64 architecture, workspace and temporary-directory layout before
`actions/setup-python` runs. It also rejects any existing or symlinked runtime
target. The action repeats preflight before using `sudo mkdir`, which fails rather
than changing an existing target's ownership or mode. `HOME` and XDG directories
used for provisioning are private directories under `RUNNER_TEMP`; test execution
uses the existing managed `deploy.test_workspace.run_suite` lifecycle.

## Acceptance

1. Provisioning rejects local, self-hosted, non-Ubuntu, and non-canonical
   repository/workspace execution, or an existing/symlinked runtime target before
   setup-python or any privileged mutation.
2. Public upstream revision, lockfile bytes, patch bytes, patch targets, and all
   four installed/staged source hashes must match their reviewed pins.
3. Native provisioning uses Python 3.11 and locked public dependencies; test homes,
   caches, and evidence remain isolated on the disposable runner.
4. All eligible manifest-listed native tests are run unchanged through the managed
   harness; the four private-operator tests remain excluded from hosted native
   execution and remain in the installed-runtime host suite.
5. Existing local managed test paths remain supported for small tasks. Only this
   native provisioner refuses local execution.

The original pilot intentionally did not alter CI selection or classifications.
This integration adds `.github/native-tests.json` as a reason-bearing subset of
`.github/host-tests.json`, selects it through `scripts/ci_tests.py native`, and
runs it using the existing managed workspace. The complete host manifest and
`scripts/ci_tests.py host` behavior remain unchanged, including the four private
operator tests. The required hosted `source-ci` aggregate and release artifact job
set now include the native job.

The native job starts with the provisioner's non-mutating hosted-runner preflight,
then prepares project test dependencies under Python 3.12 and the pinned public
patched Hermes runtime under Python 3.11. It exports the provisioner's runner-local
SQLite 3.51.3 library path to the managed test process. Checkout credentials are
not persisted; the job runs only on disposable GitHub-hosted Ubuntu and has no
private state, deployment credential or operator upload.

Hosted native success demonstrates repeatable behavior against reconstructed
public source only. It does not replace the required installed/private host
compatibility run, prove the deployed bytes or live services, or justify reporting
the `integration-tests` status.

## Public provenance

Hermes source:

- Repository: <https://github.com/NousResearch/hermes-agent>
- Commit: `8911e2e0edf750b104edbdc106d63d6cdac88524`
- `pyproject.toml` SHA-256:
  `1f928b1560b0669291b3f7d562aa78c99ac4f927375939ca97fd3c3e7494cb91`
- `uv.lock` SHA-256:
  `8fd868b9da8b6bc2f4aa94a845e210eccdd5e31be7a0b404f0a8527ced0fddec`
- Pinned `uv`: `0.9.28`, matching the pinned version in upstream workflows at this
  same commit. Dependencies are synchronized with `--locked`; only the `messaging`
  extra is enabled because the selected native tests import `aiohttp`. No provider
  or model-generation extras are enabled.
- The uv bootstrap wheel is hash-pinned to the PyPI SHA-256
  `7b8460a2b624d8ab27cb293a2c9f2393f9efc4e36e0fb886a6c2360e23fb48be`
  for `uv-0.9.28-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`;
  preflight requires the matching x86_64 hosted runner.

The patch digests are verified before application; both pre- and postimage hashes
are checked against the public upstream tree and the installed staging directory.

| Patched source | `installed_sha256` | `staged_sha256` |
| --- | --- | --- |
| `cron/scheduler.py` | `ac0d2f0edcfaf26ffa21aa4e7478b44bb78e6f43e3072697e2849f2b1b13b7e7` | `4c75d873de809f72e865b9aab2a9f6e1c1008859b60373f9d721d0847d21e9fe` |
| `tools/send_message_tool.py` | `5c0f0898b5a16d5c63cd083281ac5c307541ed704c46acfcfbc368b5eea1dc21` | `56ed3549db505cb38c9e000cb56ca13017f386c9080c3e5467f34cf9ab12e31c` |
| `run_agent.py` | `a26e5264738f1c62347e63c1265e562d3cfae439dadc313db48572f3e9cc751b` | `fb58e81ac57c0f49370d72146d21250d1cfeaa0b964c9996e972954bbc343609` |
| `gateway/platforms/api_server.py` | `2893fba247bbe1523eaaf0b90646238c3cfb4208a9a69f3f80fadbc7dd1ddbdd` | `187c92509b3769c04756f0dc800d3597ea891ea21262e8a32ceaf3972ac95300` |

| Patch | SHA-256 |
| --- | --- |
| `patches/native-compat.patch` | `2add04e93a5ea74eeeb407dc9d5556bb38c1454b5c8bf307c3e8490e9b72dd32` |
| `patches/cron-delivery.patch` | `444af4887abcea020baaf8c8cfbf4679670d38cdc2fc302a97ad5c54d68fc1ff` |

The runner image's Python 3.11.16 and 3.12.3 both linked SQLite 3.45.1, below the
existing native tests' explicit 3.51.3 minimum. The runner's SQLite source host did
not resolve in this environment, so the pilot fetched SQLite's public Git mirror
at commit `a5333afb9ad1aa473f8963b92caeaa955f47dc74` and verified its `VERSION`
file is `3.51.3`. It built a shared FTS5-enabled library under `RUNNER_TEMP`, checked
the loaded version in both Python interpreters, and exported its library path via
the Actions environment file. The build and caches stay on the disposable runner.
The only Hermes extra enabled is `messaging`; no provider/model calls or separate
Hermes provider, model, voice, wake-word, or audio-processing extras were enabled.

## RED/GREEN and cloud verification

The new provisioner acceptance tests were exercised through `run_suite`:

- Initial RED: test module import failed because the provisioner did not yet exist.
- Direct-entrypoint RED: invoking the script exposed a missing repository import
  path; the added regression passed after correction.
- SQLite/prerequisite RED: acceptance tests first failed because pinned SQLite
  checks and the runner environment-file publication were not implemented.
- Final GREEN: `tests/test_native_test_environment.py` — **13 passed** in managed
  isolation.
- Action-order and refusal-before-mutation regressions cover a pre-existing directory
  and symlink target, preserving their modes and contents. The native pilot's
  previously recorded 16-file run remains **419 passed**; this guard-only follow-up
  does not change native dependencies or test classifications.
- The 16-file native **419 passed, 1 warning** run is specifically bound to source
  commit `cc43015c57fdfd97ae3e6224f4e573a0902d343b`, original task shell 63/64.
  It is not evidence that the later guard head or this test-portability follow-up
  reran the native selection successfully. The test-project `requirements.lock`
  used for that managed run has SHA-256
  `1e912f6160c68f3ebb56a51da95af013875d0b4690434fe52fcd3f6b115de095`.
- Follow-up RED: running the new action-order regression against the preceding
  committed action failed at the first step (`KeyError: 'shell'`), because
  `setup-python` ran before a guarded shell preflight. Follow-up GREEN: the full
  guard test module passed **13/13** under managed isolation.
- Local-layout RED: the focused managed module in a second source copy under
  `RUNNER_TEMP` failed exactly the two target-refusal tests because the real
  canonical workspace check ran first. The tests now mock only that hosted-layout
  seam with the fixture checkout and private temp directory; the strict production
  guard and dedicated hosted-label/layout rejection test remain unchanged. The
  managed module then passed in both that noncanonical copy and the canonical hosted
  checkout.
- The follow-up pip check first tried `--hash` as a bare install option; pip rejected
  that syntax, so the provisioner now writes a fixed hash-locked requirements file
  inside its private work directory. Pip then installed the pinned wheel with
  `--require-hashes`, and its supported CLI reported `uv 0.9.28`. An initial module
  version probe was unsupported (`uv` has no `__version__` attribute); the CLI
  verified the version instead.

Before the prerequisite fixes, the first complete native selection produced
**385 passed, 34 failed**. The failures were environmental: `aiohttp` was absent
from the core Hermes dependencies, and the linked SQLite 3.45.1 tripped the
unchanged 3.51.3+ safety checks. With only the lock-backed messaging extra and the
pinned SQLite build, the same selection passed:

```text
419 passed, 1 warning in 124.39s
```

The one warning is Python's `audioop` deprecation from `discord/player.py`; it did
not fail a test. Project and native Python both reported SQLite 3.51.3 during the
successful run.

The host manifest contains 20 entries. These **16** public native-runtime tests were
selected, and only these four private-operator tests were excluded:
`test_notice_recovery_operation.py`, `test_notification_release_operator.py`,
`test_push_policy_rollback.py`, and `test_session_release_callers.py`.

```text
tests/test_cron_delivery_preflight.py
tests/test_delivery_adapter.py
tests/test_delivery_native.py
tests/test_member_jobs.py
tests/test_member_scheduler.py
tests/test_model_selection_transport_contract.py
tests/test_native_compression_admission.py
tests/test_native_controls_release.py
tests/test_native_controls_startup.py
tests/test_native_delete_rollout.py
tests/test_native_maintenance.py
tests/test_native_notifications.py
tests/test_native_parallel_lease_contract.py
tests/test_native_run_controls.py
tests/test_native_session_deletion.py
tests/test_native_session_deletion_review.py
```

The exact managed command used for that successful run was:

```sh
export PIP_CACHE_DIR="$RUNNER_TEMP/hermes-native-pip-cache"
mkdir -p "$PIP_CACHE_DIR"
chmod 700 "$PIP_CACHE_DIR"
python3.12 -m venv "$RUNNER_TEMP/hermes-native-project-tests"
"$RUNNER_TEMP/hermes-native-project-tests/bin/python" -m pip install --disable-pip-version-check -r requirements.lock
export LD_LIBRARY_PATH="$RUNNER_TEMP/hermes-native-sqlite-3.51.3/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
python3.12 - <<'PY'
import json
import shutil
from pathlib import Path
from deploy.test_workspace import run_suite

source = Path.cwd()
manifest = json.loads((source / '.github/host-tests.json').read_text())
private = {
    'tests/test_notice_recovery_operation.py',
    'tests/test_notification_release_operator.py',
    'tests/test_push_policy_rollback.py',
    'tests/test_session_release_callers.py',
}
selected = tuple(sorted(path for path in manifest if path not in private))
assert len(manifest) == 20 and len(selected) == 16
run_suite(
    source,
    python='/home/runner/work/_temp/hermes-native-project-tests/bin/python',
    node=shutil.which('node'),
    suite='python',
    root='/home/runner/work/_temp/hermes-native-final-root',
    extra_args=selected,
)
PY
```

The Python 3.12 test interpreter used the repository's `requirements.lock`; the
native runtime used Python 3.11 and the upstream `uv.lock`. No test assertions,
thresholds, retries, selectors, or classifications were weakened or altered.

## Failed attempts and CI status

- `uv 0.8.22` could not parse this revision's `uv.lock`; the script now pins the
  `0.9.28` version used by that revision's own workflows.
- The first `uv 0.9.28` package download rejected an unknown certificate issuer.
  `UV_NATIVE_TLS=true` made uv use the runner's native certificate store; TLS
  verification remains enabled.
- `uv python install 3.11.14` provided SQLite 3.50.4, still below the test
  requirement; it was not used as a workaround.
- The Actions environment file is runner-owned with mode `0644`. The provisioner
  accepts that observed mode but still rejects non-runner-owned, linked, escaped,
  or group/world-writable paths.
- An early selection-count assertion expected 17 tests and stopped before running
  anything. The manifest count was corrected to 20 total and 16 eligible; the
  manifest itself was not changed.
- On earlier head `2e7baa64d0c811b79a3b4e3e2f7563e9d7617c09`, Source checks run
  [#25](https://github.com/lindayi/hermes-mobile/actions/runs/36911491488) failed
  only in browser shard 1: `tests/browser/mobile.spec.mjs:95`,
  “composer visible above bottom navigation” (106 passed, 1 failed in that
  shard). This is unrelated to this native-runtime change; it was not retried and
  no assertion was relaxed. Earlier head `cc43015c57fdfd97ae3e6224f4e573a0902d343b`
  has an `action_required` Source checks run with no jobs; it is not a successful
  `source-ci` result. The native suite above is focused evidence, not full hosted
  CI coverage.

## Recommendation and limits

After a parent integrates a cloud-native partition, consider moving these 13
portable runtime-behavior suites out of the broad server suite:
`test_cron_delivery_preflight.py`, `test_delivery_adapter.py`,
`test_delivery_native.py`, `test_member_jobs.py`, `test_member_scheduler.py`,
`test_model_selection_transport_contract.py`,
`test_native_compression_admission.py`, `test_native_delete_rollout.py`,
`test_native_maintenance.py`, `test_native_notifications.py`,
`test_native_parallel_lease_contract.py`, `test_native_run_controls.py`, and
`test_native_session_deletion.py`.

Keep a narrow installed-byte/startup boundary on the server: at minimum
`test_native_controls_release.py` (installed dependency/process fingerprint),
`test_native_session_deletion_review.py` (checks installed
`api_server.py`/`hermes_state.py` bytes), and `test_native_controls_startup.py`
(starts the fixed installed Python 3.11 entrypoint). Keep all four private-operator
tests local as well. This pilot proves reproducibility from public source and
synthetic tests only; it does **not** prove compatibility with the actual installed
server, deployed bytes, production state, or services.
