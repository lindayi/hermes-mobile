"""Hosted gate must cover every suite; cancellations are not a passing result."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_release_stage_retains_ci_contract_files(tmp_path):
    from deploy.self_deploy import stage_release
    source = tmp_path / 'source'
    (source / '.github/workflows').mkdir(parents=True)
    (source / '.github/workflows/ci.yml').write_text('fixture workflow')
    (source / '.github/host-tests.json').write_text('{}')
    (source / '.github/native-tests.json').write_text('{}')
    stage = stage_release(source, tmp_path / 'stage')
    assert (stage / '.github/workflows/ci.yml').read_text() == 'fixture workflow'
    assert (stage / '.github/host-tests.json').read_text() == '{}'
    assert (stage / '.github/native-tests.json').read_text() == '{}'


def test_hosted_gate_requires_every_suite_without_optional_failures():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'pull_request', 'push', 'workflow_dispatch'}
    assert workflow['permissions'] == {'contents':'read'}
    jobs = workflow['jobs']
    assert set(jobs['source-ci']['needs']) == {'build','checks','js','python','browser','native'}
    assert jobs['source-ci']['if'] == '${{ always() }}'
    gate = jobs['source-ci']['steps'][0]
    assert gate['env']['RESULTS'] == '${{ toJSON(needs) }}'
    assert "!= 'success'" in gate['run']
    for name in ('build','checks','js','python','browser','native','source-ci',
                 'integration-tests','attest'):
        assert jobs[name]['runs-on'] == 'ubuntu-24.04'
        assert 'continue-on-error' not in jobs[name]
        for step in jobs[name]['steps']:
            assert 'continue-on-error' not in step
    for suite, count in [('python',2),('browser',4)]:
        job = jobs[suite]
        assert len(job['strategy']['matrix']['shard']) == count
        assert job['strategy']['fail-fast'] == 'false'
        run = '\n'.join(step.get('run','') for step in job['steps'])
        assert 'scripts/ci_tests.py' in run
        assert f'--shards {count}' in run
        if suite == 'browser':
            assert job['needs'] == 'build'
            download = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/download-artifact@'))
            assert download['with'] == {
                'name': 'release-${{ github.run_id }}-${{ github.run_attempt }}',
                'path': '${{ runner.temp }}/bundle',
            }
            assert 'deploy.release_artifact unpack --bundle "$RUNNER_TEMP/bundle/release.tar" --destination "$RUNNER_TEMP/consumed"' in run
            assert '--assets "$RUNNER_TEMP/consumed/public"' in run
            assert 'build_frontend' not in run
            assert 'deploy.release_artifact build' not in run
    assert any('scripts/ci_tests.py js' in step.get('run','') for step in jobs['js']['steps'])
    # steering-retry.test.mjs launches a real browser despite its unit-test suffix.
    setup = next(step for step in jobs['js']['steps'] if step.get('uses') == './.github/actions/test-environment')
    assert setup['with']['browser'] == 'true'


@pytest.mark.parametrize('job', ['build', 'checks', 'js', 'python', 'browser', 'native'])
@pytest.mark.parametrize('result', ['success', 'failure', 'cancelled', 'skipped', 'missing', 'unexpected'])
def test_hosted_gate_executes_fail_closed_for_complete_job_set(monkeypatch, job, result):
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    gate = workflow['jobs']['source-ci']['steps'][0]['run']
    code = gate.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    results = {
        name: {'result': 'success'}
        for name in ('build', 'checks', 'js', 'python', 'browser', 'native')
    }
    if result == 'missing':
        del results[job]
    elif result == 'unexpected':
        results['unexpected'] = {'result': 'success'}
    else:
        results[job]['result'] = result
    monkeypatch.setenv('RESULTS', json.dumps(results))
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(code, 'source-ci-gate', 'exec'), {})
    assert bool(exit_info.value.code) == (result != 'success')


def test_native_job_is_confined_to_public_disposable_hosted_runtime():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['native']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert job['timeout-minutes'] == '30'
    steps = job['steps']
    checkout = steps[0]
    assert checkout['uses'].startswith('actions/checkout@')
    assert checkout['with']['persist-credentials'] == 'false'
    assert 'prepare_native_test_runtime.py --preflight' in steps[1]['run']
    assert steps[2]['uses'].startswith('actions/setup-python@')
    assert steps[2]['with']['python-version'] == '3.12'
    assert steps[3]['uses'].startswith('actions/setup-node@')
    assert steps[3]['with']['node-version'] == '22'
    assert 'pip install -r requirements.lock' in steps[4]['run']
    assert 'HERMES_TEST_PYTHON=' in steps[4]['run']
    assert steps[5]['uses'] == './.github/actions/native-test-environment'
    assert '"$HERMES_TEST_PYTHON" -B scripts/ci_tests.py native' in steps[6]['run']
    workflow_text = (ROOT / '.github/workflows/ci.yml').read_text()
    assert 'self-hosted' not in workflow_text
    assert 'secrets.' not in workflow_text


def test_build_once_bundle_is_attested_only_after_trusted_main_gate():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    commands = [step.get('run', '') for job in jobs.values() for step in job['steps']]
    assert sum('deploy.release_artifact build' in command for command in commands) == 1
    assert any('deploy.release_artifact build --bundle "$RUNNER_TEMP/release.tar"' in step.get('run', '') for step in jobs['build']['steps'])
    upload = next(step for step in jobs['build']['steps'] if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['with']['name'] == 'release-${{ github.run_id }}-${{ github.run_attempt }}'
    assert upload['with']['path'] == '${{ runner.temp }}/release.tar'
    assert upload['with']['if-no-files-found'] == 'error'
    attest = jobs['attest']
    assert attest['needs'] == ['source-ci', 'integration-tests']
    assert attest['if'] == "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && github.repository == 'lindayi/hermes-mobile' }}"
    assert attest['permissions'] == {'contents': 'read', 'id-token': 'write', 'attestations': 'write'}
    for name, job in jobs.items():
        if name != 'attest':
            assert job.get('permissions', workflow['permissions']) == {'contents': 'read'}
    assert all('run' not in step and not step.get('uses', '').startswith('actions/checkout@') for step in attest['steps'])
    download = next(step for step in attest['steps'] if step.get('uses', '').startswith('actions/download-artifact@'))
    assert download['with'] == {'name': upload['with']['name'], 'path': '${{ runner.temp }}/bundle'}
    provenance = next(step for step in attest['steps'] if step.get('uses', '').startswith('actions/attest-build-provenance@'))
    assert provenance['with']['subject-path'] == '${{ runner.temp }}/bundle/release.tar'


def test_integration_gate_requires_successful_source_ci_in_the_same_run():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    gate = jobs['integration-tests']
    assert gate['if'] == '${{ always() }}'
    assert gate['needs'] == 'source-ci'
    assert gate['runs-on'] == 'ubuntu-24.04'
    assert gate.get('permissions', workflow['permissions']) == {'contents': 'read'}
    assert all('uses' not in step for step in gate['steps'])
    command = '\n'.join(step.get('run', '') for step in gate['steps'])
    assert command
    assert jobs['attest']['needs'] == ['source-ci', 'integration-tests']

    for source_result in ('success', 'failure', 'cancelled', 'skipped', ''):
        result = subprocess.run(
            ['bash', '-eu', '-c', command],
            env={**os.environ, 'SOURCE_CI_RESULT': source_result},
            capture_output=True, text=True, timeout=10,
        )
        assert (result.returncode == 0) == (source_result == 'success'), source_result


def test_hosted_setup_uses_pinned_dependencies_without_deploy_access():
    path = ROOT / '.github/actions/test-environment/action.yml'
    setup = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    text = path.read_text()
    assert 'npm ci --ignore-scripts' in text
    assert 'pip install -r requirements.lock' in text
    assert './node_modules/.bin/playwright install --with-deps chromium' in text
    assert 'PLAYWRIGHT_BROWSERS_PATH' in text
    for step in setup['runs']['steps']:
        if 'uses' in step:
            assert len(step['uses'].split('@')[-1]) == 40
    workflow = (ROOT / '.github/workflows/ci.yml').read_text()
    assert 'self-hosted' not in workflow
    assert 'secrets.' not in workflow
    assert 'pull_request_target' not in workflow
    assert 'self_deploy' not in workflow
