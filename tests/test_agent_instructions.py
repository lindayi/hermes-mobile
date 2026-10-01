from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]

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
    'A review comment is not an approval or a passing review status; require independent '
    'formal COMMENT review for every PR and verify evidence against the exact head SHA.',
    'Merging and deployment are separate; only guarded deployment from verified main is '
    'allowed, and coding agents never access production.',
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
    # Normalize wrapping, but keep the mandatory policy clauses under regression.
    for path in ('AGENTS.md', 'docs/development-workflow.md'):
        text = ' '.join((ROOT / path).read_text().split())
        for clause in (
            'docs/git-development-spec.md',
            '`source-ci`, `integration-tests`, and `agent-review` must pass',
            'independent formal COMMENT review for every PR',
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
        'When the documented hosted partition is available',
        '`scripts/ci_tests.py`',
        '`.github/host-tests.json`',
        'complete matching hosted results plus all residual host tests',
        'same exact head SHA',
        'Otherwise, conservatively use the existing managed `all` suite',
        'Final integration is a merge gate, not a full local suite per edit',
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
        '`source-ci`, `integration-tests`, and `agent-review`',
        'independent formal COMMENT review',
        'resolved review threads',
        'current with freshly fetched `origin/main`',
        'No owner/admin bypass',
    ):
        assert clause in template, clause


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
    assert 'exact head SHA' in template


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
