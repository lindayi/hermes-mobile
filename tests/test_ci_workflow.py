"""Hosted gate must cover every suite; cancellations are not a passing result."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_release_stage_retains_ci_contract_files(tmp_path):
    from deploy.self_deploy import stage_release
    source = tmp_path / 'source'
    (source / '.github/workflows').mkdir(parents=True)
    (source / '.github/workflows/ci.yml').write_text('fixture workflow')
    (source / '.github/host-tests.json').write_text('{}')
    stage = stage_release(source, tmp_path / 'stage')
    assert (stage / '.github/workflows/ci.yml').read_text() == 'fixture workflow'
    assert (stage / '.github/host-tests.json').read_text() == '{}'


def test_hosted_gate_requires_every_suite_without_optional_failures():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'pull_request', 'push', 'workflow_dispatch'}
    assert workflow['permissions'] == {'contents':'read'}
    jobs = workflow['jobs']
    assert set(jobs['source-ci']['needs']) == {'checks','js','python','browser'}
    assert jobs['source-ci']['if'] == '${{ always() }}'
    gate = jobs['source-ci']['steps'][0]
    assert gate['env']['RESULTS'] == '${{ toJSON(needs) }}'
    assert "!= 'success'" in gate['run']
    for name in ('checks','js','python','browser','source-ci'):
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
        assert 'build_frontend' in run if suite=='browser' else True
    assert any('scripts/ci_tests.py js' in step.get('run','') for step in jobs['js']['steps'])
    # steering-retry.test.mjs launches a real browser despite its unit-test suffix.
    setup = next(step for step in jobs['js']['steps'] if step.get('uses') == './.github/actions/test-environment')
    assert setup['with']['browser'] == 'true'


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
