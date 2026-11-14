from pathlib import Path
from copy import deepcopy
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
import yaml

def _node():
    node = os.environ.get('HERMES_TEST_NODE') or shutil.which('node')
    assert node, 'The managed checks require Node'
    return node


def _dependency_probe(tmp_path):
    checkout = tmp_path / 'checkout with spaces'
    package = checkout / 'node_modules/hermes-portability-probe'
    package.mkdir(parents=True)
    (package / 'index.js').write_text('module.exports = "repository dependency";')
    anchor = tmp_path / 'absent-install/hermes-agent/package.json'
    assert not anchor.parent.exists()
    script = (
        'const assert = require("node:assert/strict");'
        'const fs = require("node:fs");'
        'const {createRequire} = require("node:module");'
        'assert.equal(fs.existsSync(process.argv[1]), false);'
        'assert.equal(createRequire(process.argv[1])("hermes-portability-probe"),'
        ' "repository dependency");'
    )
    env = dict(os.environ)
    env.pop('NODE_PATH', None)
    env.pop('NODE_OPTIONS', None)
    return checkout, [_node(), '-e', script, str(anchor)], env


def test_node_path_resolves_dependency_from_absent_create_require_anchor(tmp_path):
    checkout, command, env = _dependency_probe(tmp_path)
    missing = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert missing.returncode != 0
    assert 'MODULE_NOT_FOUND' in missing.stderr
    env['NODE_PATH'] = str(checkout / 'node_modules')
    resolved = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert resolved.returncode == 0, resolved.stderr


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('route', ['cloud', 'local'])
def test_fresh_setup_exports_repository_node_path(tmp_path, route):
    checkout, command, env = _dependency_probe(tmp_path)
    if route == 'cloud':
        workflow = yaml.load(
            (ROOT / '.github/workflows/copilot-setup-steps.yml').read_text(),
            Loader=yaml.BaseLoader,
        )
        steps = workflow['jobs']['copilot-setup-steps']['steps']
        exports = [step['run'] for step in steps if 'NODE_PATH' in step.get('run', '')]
        assert len(exports) == 1, 'Cloud setup must persist repository NODE_PATH'
        github_env = tmp_path / 'github-env'
        env.update(GITHUB_ENV=str(github_env), GITHUB_WORKSPACE=str(checkout))
        subprocess.run(['bash', '-eu', '-c', exports[0]], cwd=checkout,
                       env=env, check=True, capture_output=True, text=True, timeout=15)
        env.update(line.split('=', 1) for line in github_env.read_text().splitlines())
    else:
        docs = (ROOT / 'docs/development-workflow.md').read_text()
        exports = re.findall(r'^export NODE_PATH=.*$', docs, re.MULTILINE)
        assert len(exports) == 1, 'Local setup must export repository NODE_PATH'
        result = subprocess.run(
            ['bash', '-eu', '-c', exports[0] + '\nprintenv NODE_PATH'],
            cwd=checkout, env=env, check=True, capture_output=True, text=True, timeout=15,
        )
        env['NODE_PATH'] = result.stdout.strip()
    assert env['NODE_PATH'] == str(checkout / 'node_modules')
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_all_managed_browser_fixtures_honor_browser_override_without_launching():
    files = sorted(set((ROOT / 'tests/browser').glob('*.spec.mjs')) |
                   set((ROOT / 'tests/browser').glob('*.test.mjs')))
    expressions = [(path.name, expression) for path in files
                   for expression in re.findall(r'executablePath\s*:\s*([^,}]+)', path.read_text())]
    assert expressions, 'Managed browser executable selections must be covered'
    assert any(name == 'steering-retry-lifecycle.spec.mjs' for name, _ in expressions)
    assert any(name == 'steering-controls.spec.mjs' for name, _ in expressions)
    script = 'process.stdout.write(JSON.stringify([' + ','.join(
        '{name:' + repr(name) + ', value:(' + expression + ')}'
        for name, expression in expressions
    ) + ']));'
    for browser in ('/private cache/chromium', '', None):
        env = dict(os.environ)
        env.pop('HERMES_BROWSER', None)
        if browser is not None:
            env['HERMES_BROWSER'] = browser
        result = subprocess.run(
            [_node(), '-e', script],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        import json
        for selection in json.loads(result.stdout):
            assert selection['value'] == (browser or '/usr/bin/google-chrome'), selection['name']





def test_pull_request_template_keeps_plain_closing_reference_and_evidence_fields():
    template = (ROOT / '.github/pull_request_template.md').read_text()
    body = template.replace('NUMBER', '48')
    evidence = {
        'Baseline main commit:': 'Baseline main commit: 9aca9b642d9aa4a90e108bc8e3c821314125f583',
        'In scope:': 'In scope: user service hardening and authenticated handoff metadata.',
        'Explicitly out of scope:': 'Explicitly out of scope: host activation and deployment.',
        'Acceptance cases:': 'Acceptance cases: the user unit retains supported hardening.',
        'RED command and observed failure:': 'RED command and observed failure: the focused regression failed.',
        'GREEN command and observed result:': 'GREEN command and observed result: 884 focused tests passed.',
        'Exact tested head SHA:': 'Exact tested head SHA: eacef83b5a025867b32f1c41ca50f82566bf845b',
        'Rollout effect:': 'Rollout effect: source-only change pending operator qualification.',
        'Merge state: not merged or exact merged PR and main SHA:': 'Merge state: not merged.',
        'Deployment state: not deployed or separately verified deployed main SHA:':
            'Deployment state: not deployed.',
    }
    for field, value in evidence.items():
        body = body.replace(field, value)

    assert 'Closes #48' in body
    assert all(value in body for value in evidence.values())
    assert 'Review and integration' in body




def test_issue_form_captures_behavior_acceptance_scope_and_privacy_risks():
    path = ROOT / '.github/ISSUE_TEMPLATE/actionable_work.yml'
    form = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert form['name']
    assert form['body']
    fields = {field['id']: field for field in form['body'] if 'id' in field}
    for field_id in ('behavior', 'acceptance', 'scope', 'risks_privacy', 'duplicates'):
        assert field_id in fields


def test_pull_request_template_records_linked_scope_evidence_risks_and_review():
    template = (ROOT / '.github/pull_request_template.md').read_text()
    for heading in (
        'Linked issue and related work',
        'Scope and baseline',
        'RED/GREEN evidence',
        'Exact verification evidence',
        'Risks, privacy, and data handling',
        'Rollout and rollback',
        'Review and integration',
        'Merged versus deployed',
    ):
        assert heading in template
    assert 'Closes #' in template
    assert 'Closes #NUMBER' in template.splitlines()
    assert 'exact head SHA' in template


def test_pull_request_template_uses_plain_text_without_markdown_markup():
    template = (ROOT / '.github/pull_request_template.md').read_text()
    assert not re.search(r'(?m)^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|>)', template)
    assert not any(markup in template for markup in ('[', ']', '`', '<', '>', '*', '_', '~', '|'))


def test_pull_request_template_body_alone_is_not_canonical_linkage():
    from test_issue_link_policy import conclusion, issue, make_payload, run_pipeline

    # The template text is readable metadata; only API closing references count.
    assert conclusion(run_pipeline(make_payload(refs=[]))) == 'failure'
    assert conclusion(run_pipeline(make_payload(refs=[issue(7)]))) == 'success'


def test_copilot_setup_is_pinned_minimal_and_never_runs_tests():
    path = ROOT / '.github/workflows/copilot-setup-steps.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)

    assert workflow['on']['workflow_dispatch'] == ''
    assert 'push' in workflow['on'] and 'pull_request' in workflow['on']
    setup_path = '.github/workflows/copilot-setup-steps.yml'
    assert workflow['on']['push']['paths'] == [setup_path]
    assert workflow['on']['pull_request']['paths'] == [setup_path]
    assert list(workflow['jobs']) == ['copilot-setup-steps']
    job = workflow['jobs']['copilot-setup-steps']
    assert job['permissions'] == {'contents': 'read'}
    assert job['runs-on'] == 'ubuntu-24.04'

    actions = []
    checkout = None
    for step in job['steps']:
        action = step.get('uses')
        if action:
            assert re.fullmatch(r'[^@]+@[0-9a-f]{40}', action)
            actions.append(action)
            if action.startswith('actions/checkout@'):
                checkout = step
    assert checkout['with']['persist-credentials'] == 'false'
    assert any(action.startswith('actions/setup-python@') for action in actions)
    assert any(action.startswith('actions/setup-node@') for action in actions)
    assert job['steps'][1]['with']['python-version'] == '3.12'
    assert job['steps'][2]['with']['node-version'] == '22'
    assert job['steps'][2]['with']['cache-dependency-path'] == 'package-lock.json'

    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'python -m venv .venv' in commands
    assert 'requirements.lock' in commands
    assert 'npm ci' in commands
    assert 'PLAYWRIGHT_BROWSERS_PATH' in commands
    assert 'RUNNER_TEMP' in commands
    assert 'PLAYWRIGHT_SKIP_BROWSER_GC=1' in commands
    assert 'playwright install --with-deps chromium' in commands
    assert 'scripts/test.py' not in commands
    assert 'pytest' not in commands
    for name in ('HERMES_TEST_PYTHON', 'HERMES_TEST_NODE', 'HERMES_BROWSER'):
        assert name in commands, 'Cloud setup must expose the exact installed executables'
