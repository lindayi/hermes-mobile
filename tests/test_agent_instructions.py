from pathlib import Path
import os
import re
import shutil
import subprocess

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


SYNCED_RULES = (
    'Start from an approved issue; search for duplicates and overlapping PRs, then '
    'record scope and acceptance cases before implementation.',
    'For behavior changes, preserve existing assertions and demonstrate a real RED '
    'test followed by GREEN.',
    'Use the managed test runner with explicit test paths for focused local checks; '
    'choose Python through HERMES_TEST_PYTHON or the repository .venv, never a fixed '
    'owner-specific path.',
    'Use cloud execution by default for substantial coding, testing, and review; local '
    'work remains supported for small fixes and offline work under the same pull-request '
    'gates.',
    'Never use production credentials, real accounts, real model calls, native production '
    'homes or databases, private evidence uploads, or live enrollment as tests.',
    'A review comment is not an approval or a passing review status; verify every review '
    'and test result against the exact head SHA.',
    'Merging and deployment are separate; only guarded deployment from verified main is '
    'allowed, and coding agents never access production.',
    'Write PR descriptions as plain paragraphs using the repository template; include a '
    'literal Closes #N and all required evidence, with no Markdown markup in the body.',
    'For task handoff, a literal Closes #N is a readability convention; verify linkage '
    'from authenticated GitHub closing-issue references, never PR body text.',
)


def test_agent_instructions_are_portable_and_copilot_rules_stay_synced():
    agents = (ROOT / 'AGENTS.md').read_text()
    copilot = (ROOT / '.github/copilot-instructions.md').read_text()

    assert len(agents) < 20_000
    assert '[AGENTS.md](../AGENTS.md)' in copilot
    for rule in SYNCED_RULES:
        assert rule in agents
        assert rule in copilot

    workflow = (ROOT / 'docs/development-workflow.md').read_text()
    assert 'HERMES_TEST_PYTHON' in workflow
    assert 'scripts/test.py python -- tests/test_agent_instructions.py' in workflow
    assert 'scripts/ci_tests.py' in workflow
    assert '.github/host-tests.json' in workflow
    assert 'copilot-setup-steps.yml' in workflow
    assert 'repository setting' in workflow


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


def test_product_preferences_remain_explicit_in_canonical_instructions():
    agents = (ROOT / 'AGENTS.md').read_text()
    for preference in (
        'Sessions',
        'New chat',
        'Search, Filter, and New',
        'parallel sessions',
        'Inbox is read-on-open',
        'local times',
        'Docker-free deployment',
        'shared-session identity',
        'public shared-origin limitations',
        'meaningful outcomes',
    ):
        assert preference in agents


def test_review_and_final_integration_contract_is_mandatory_on_both_routes():
    for path in ('AGENTS.md', 'docs/development-workflow.md'):
        text = ' '.join((ROOT / path).read_text().split())
        for clause in (
            'docs/git-development-spec.md',
            'source-ci` (Actions app 15368)',
            'integration-tests`, `agent-review`, and `issue-link` (Actions app 15368)',
            'owner-published structured independent-agent formal COMMENT review',
            'Copilot feedback is supplemental',
            'missing APPROVED or COMMENTED alone',
            'Publish `agent-review` only after verifying the independent review',
            'Publish `integration-tests` only after verifying complete final integration',
            'exact head SHA',
            'resolved review threads',
            'current with freshly fetched `origin/main`',
            'No owner/admin bypass',
        ):
            assert clause in text, (path, clause)
        assert 'review where specified' not in text

    workflow = ' '.join((ROOT / 'docs/development-workflow.md').read_text().split())
    for clause in (
        'The active required contexts are',
        'scripts/ci_tests.py',
        '`.github/host-tests.json`',
        'complete matching hosted results plus all residual host tests',
        'same exact head SHA',
        'If the documented partition is unavailable, conservatively use the existing managed `all` suite',
        'Final integration is a merge gate, not a full local suite per edit',
        'changed scope needs one independent delta review',
        'final authority/status is freshly bound to the current head',
        'fresh Linux',
        'Playwright OS dependencies',
        'playwright install-deps chromium',
    ):
        assert clause in workflow, clause
    assert 'Open PR #4' not in workflow
    assert 'neither exists on this baseline' not in workflow

    template = ' '.join((ROOT / '.github/pull_request_template.md').read_text().split())
    assert 'Full managed integration result, if required' not in template
    for clause in (
        'Mandatory final integration evidence',
        'The latest authenticated owner-published structured independent-agent formal COMMENT review',
        'Copilot is advisory',
        'source-ci (Actions app 15368), integration-tests, agent-review, and issue-link (Actions app 15368)',
        'cloud-review is advisory, not required or synthesized',
        'resolved review threads',
        'current with freshly fetched origin/main',
        'No owner or administrator bypass',
    ):
        assert clause in template, clause


def test_all_active_context_lists_include_issue_link():
    for path in (
        'AGENTS.md',
        '.github/copilot-instructions.md',
        '.github/pull_request_template.md',
        'docs/development-workflow.md',
        'docs/git-development-spec.md',
        'docs/github-policy.md',
        'README.md',
    ):
        text = ' '.join((ROOT / path).read_text().split())
        assert all(context in text for context in (
            'source-ci', 'integration-tests', 'agent-review', 'issue-link',
        )), path

    policy = ' '.join((ROOT / 'docs/autonomy-policy.md').read_text().split())
    assert (
        '| `staging` | `source-ci`, `integration-tests`, `agent-review`, '
        '`issue-link` |'
    ) in policy


def test_agent_instructions_use_the_available_hosted_partition_portably():
    agents = (ROOT / 'AGENTS.md').read_text()
    text = ' '.join(agents.split())
    assert 'when the documented partition is available, otherwise' not in text
    assert '/home/lindayi/' not in agents
    assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', agents, re.MULTILINE)
    assert agents.count('python3 scripts/ci_tests.py host') == 1
    assert (
        'HERMES_TEST_PYTHON="${HERMES_TEST_PYTHON:-$PWD/.venv/bin/python}" '
        '\\ python3 scripts/ci_tests.py host'
    ) in text
    for clause in (
        'all portable Python, JS and generated-assets browser shards',
        '`.github/host-tests.json`; new files default to hosted execution',
        'Do not repeat hosted suites on the production server routinely',
        'The full managed `all` remains an opt-in diagnostic and the conservative '
        'release-stage gate',
        'the entire residual host-compatibility suite and the matching hosted '
        '`source-ci` aggregate must succeed on the same exact head SHA',
        'Record both results and the workflow URL',
        'Never use only a hosted subset or only local host tests to claim full coverage',
        'The active required contexts are `source-ci` (Actions app 15368)',
        '`cloud-review` as a required status',
        'installed/private host-compatibility suite remains required at guarded exact-main deployment',
    ):
        assert clause in text, clause
    assert (ROOT / 'scripts/ci_tests.py').is_file()
    assert (ROOT / '.github/host-tests.json').is_file()


def test_active_gate_contract_preserves_exact_head_review():
    paths = (
        'AGENTS.md',
        '.github/copilot-instructions.md',
        'docs/git-development-spec.md',
        'docs/development-workflow.md',
        'docs/autonomy-policy.md',
        '.github/pull_request_template.md',
    )
    for path in paths:
        text = ' '.join((ROOT / path).read_text().split())
        assert 'source-ci' in text and 'issue-link' in text and 'cloud-review' in text
        assert 'exact' in text and ('head' in text or 'SHA' in text)
        assert 'independent-agent' in text
        assert 'COMMENT' in text

    policy = ' '.join((ROOT / 'docs/autonomy-policy.md').read_text().split())
    for clause in (
        'The CLI performs no network calls, writes, status publication, settings changes',
        'does not authenticate the JSON file',
        'A status or Copilot review alone is not independent review',
        'Installed-runtime compatibility remains an exact-main guarded deployment gate',
        'pre-cutover',
        'post-cutover',
    ):
        assert clause in policy, clause


def test_policy_documents_bounded_four_check_maps_and_independent_review():
    policy = (ROOT / 'docs/autonomy-policy.md').read_text()
    expected = {
        'pre-cutover': ('source-ci', 'integration-tests', 'agent-review', 'issue-link'),
        'staging': ('source-ci', 'integration-tests', 'agent-review', 'issue-link'),
        'post-cutover': ('source-ci', 'integration-tests', 'agent-review', 'issue-link'),
    }
    for phase, contexts in expected.items():
        assert f'--phase {phase} /private/path/evidence.json' in policy
        row = next(line for line in policy.splitlines() if line.startswith(f'| `{phase}` |'))
        assert tuple(re.findall(r'`([^`]+)`', row.split('|')[2])) == contexts
    text = ' '.join(policy.split())
    for clause in (
        'No arbitrary supersets or unknown contexts',
        'app_id must be explicitly present as a JSON integer or null',
        '`source-ci` and `issue-link` are bound to Actions app 15368',
        '`integration-tests` and `agent-review` remain unbound',
        'Phase names remain for compatibility',
        'advisory `cloud-review`',
    ):
        assert clause in text, clause


def test_parallel_integration_preserves_reviewed_history_and_both_intents():
    for path in ('AGENTS.md', 'docs/development-workflow.md'):
        text = ' '.join((ROOT / path).read_text().split())
        for clause in (
            'Merge and deploy one revision at a time',
            'Merge updated `origin/main` into the PR branch',
            'never rebase or force-push reviewed history',
            'both PR intents, the common base, and both diffs',
            'neutral reviewer/reconciler',
            'regenerate derived files',
            'both intended behaviors and their interaction',
            'Record resolution decisions on GitHub',
            'only for genuinely incompatible product requirements',
        ):
            assert clause in text, (path, clause)


def test_legacy_inputs_and_uncommitted_work_survive_cleanup():
    for path, readme in (
        ('AGENTS.md', '[README.md](README.md)'),
        ('docs/development-workflow.md', '[README.md](../README.md)'),
    ):
        text = ' '.join((ROOT / path).read_text().split())
        for clause in (
            readme,
            'canonical source and local integration checkout',
            'Never edit immutable deployed releases',
            'diff against its own verified baseline',
            'never overlay an older source tree onto current main',
            'Preserve migration inputs until their work is merged or explicitly discarded',
            'verify the remote merge and preserve any uncommitted work',
            "only that task's clean worktree and merged branch",
        ):
            assert clause in text, (path, clause)
        assert '/home/lindayi/' not in text

    template = ' '.join((ROOT / '.github/pull_request_template.md').read_text().split())
    assert 'verified remote merge and preservation of any uncommitted work' in template


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


def test_completed_pull_request_template_passes_issue_link_policy():
    import hashlib
    import json
    from test_issue_link_policy import issue, make_payload, run_pipeline

    template = (ROOT / '.github/pull_request_template.md').read_text()
    body = template.replace('#NUMBER', '#7')
    # The template supplies readable metadata, not proof of a closing relation.
    # Supply that independent API evidence explicitly, without parsing the body.
    payload = make_payload(body=body, refs=[issue(7, node_id='I_template_7')])
    pending, validation, published = run_pipeline(payload)
    assert pending['errors'] == validation['errors'] == published['errors'] == []
    assert validation['calls']['graphql']
    expected_relation = hashlib.sha256(
        json.dumps([['I_template_7', 7]], separators=(',', ':')).encode()).hexdigest()
    assert validation['outputs']['relation_digest'] == expected_relation
    assert validation['calls']['statuses'] == []
    assert validation['calls']['checkCreates'] == validation['calls']['checkUpdates'] == []
    assert published['calls']['checkUpdates'][-1]['conclusion'] == 'success'
    assert published['calls']['statuses'][-1]['state'] == 'success'


def test_completed_pull_request_template_alone_is_not_canonical_linkage():
    from test_issue_link_policy import make_payload, run_pipeline

    template = (ROOT / '.github/pull_request_template.md').read_text()
    pending, validation, published = run_pipeline(
        make_payload(body=template.replace('#NUMBER', '#7'), refs=[]))
    assert pending['errors'] == []
    assert validation['errors']
    assert validation['calls']['statuses'] == []
    assert published['calls']['checkUpdates'][-1]['conclusion'] == 'failure'
    assert all(status['state'] != 'success'
               for stage in (pending, validation, published)
               for status in stage['calls']['statuses'])


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
