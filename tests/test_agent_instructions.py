from pathlib import Path
from copy import deepcopy
import json
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
            'integration-tests`, and `issue-link` (Actions app 15368)',
            '175728472',
            'COMMENTED',
            'APPROVED',
            '198982749',
            'Never fabricate',
            'automatically emitted `integration-tests` result is bound to that',
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
        'The automatic `integration-tests` job succeeds only when the same-run `source-ci` aggregate',
        'The complete `.github/host-tests.json` partition must run through',
        'inside guarded deployment before activation',
        'same exact head SHA',
        'Final integration is a merge gate, not a full local suite per edit',
        'obtain Copilot review plus final integration evidence',
        'Review and test evidence is valid only for its exact head SHA',
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
        'The authenticated exact-head Copilot review, three successful CI checks',
        'COMMENTED or APPROVED',
        'No independent report or agent-review publication is required',
        'source-ci (Actions app 15368), integration-tests, and issue-link (Actions app 15368)',
        'cloud-review is advisory, not required or synthesized',
        'resolved review threads',
        'current with freshly fetched origin/main',
        'No owner or administrator bypass',
        'The exact-head integration-tests job and its same-run complete hosted source-ci aggregate pass',
        'Host-only compatibility is not PR-head evidence',
        'guarded exact-main deployment gate before activation',
    ):
        assert clause in template, clause


def test_supervised_review_qualification_contract_is_separate_and_fail_closed():
    spec = ' '.join((ROOT / 'docs/git-development-spec.md').read_text().split())
    assert 'Supervised manual qualification' in spec
    for clause in (
        'not a new reviewer requirement',
        'generic request for a non-required reviewer role',
        'no unresolved or uncertain source or safety concern',
        'zero-findings headline alone is insufficient',
        'complete authenticated raw and rendered review',
        'complete paginated nested review threads',
        'current on the exact head and in `COMMENTED` or `APPROVED` state',
        'exact-head sensitive owner command',
        'SHA-256 of the exact raw UTF-8 review body',
        'command must be created no earlier than the unchanged review submission',
        'no active or unknown task occupancy',
        'certain source receipt and reservation provenance',
        'fresh-main ancestry',
        'all three current exact-head required checks',
        'same-run complete `source-ci` aggregate',
        'durable supervised qualification record',
        'does not change `review_valid`, the owner-authorization ledger',
        'Any head, base, review, or command change invalidates',
        'missing, uncertain, or failing evidence blocks',
        'does not alter receipts, claims, budgets',
        'not an autonomous-trial success',
        'normal protected merge without bypass',
    ):
        assert clause in spec, clause

    for path in (
        'AGENTS.md',
        'docs/autonomy-policy.md',
        'docs/cloud-coordinator-spec.md',
        'docs/development-workflow.md',
    ):
        text = ' '.join((ROOT / path).read_text().split())
        assert 'Supervised manual qualification' in text, path


def test_manual_qualification_artifact_has_an_exact_typed_field_inventory():
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    assert '### Manual audit artifact v1' in spec
    section = spec.split('### Manual audit artifact v1', 1)[1].split(
        '### Publication and independent manual validation', 1)[0]
    fields = re.findall(r'^\| `([^`]+)` \| ([^|]+) \|',
                        section.split('Primitive types:', 1)[0], re.MULTILINE)
    assert dict(fields) == {
        'schema': 'Literal `hermes-supervised-qualification/v1`',
        'kind': 'Enum `qualification`, `revalidation`',
        'record_key': 'Key',
        'prior_comment_id': 'ID or null',
        'repository': 'Repository',
        'pull': 'Pull',
        'operator': 'Operator',
        'head_sha': 'SHA',
        'base_sha': 'SHA',
        'reserved_at': 'Time',
        'observed_at': 'Time',
        'review': 'Review',
        'review_inventory': 'EvidenceRef',
        'source_proof': 'EvidenceRef',
        'occupancy_proof': 'EvidenceRef',
        'checks': 'Array of exactly three successful Check objects',
        'source_run': 'Successful SourceRun',
        'sensitive': 'Sensitive or null',
        'role_request': 'Text',
        'reasoning': 'Text',
        'machine_outcome': 'MachineOutcome',
        'status': 'Literal `supervised-only`',
        'reason': 'Text',
        'evidence': 'Array of 1–128 Evidence objects',
    }
    assert len(fields) == len(dict(fields)), 'Duplicate fields are not a contract'
    assert 'The exact top-level keys for a `blocked` qualification or revalidation are' in spec
    assert 'The exact top-level keys for an `invalidated` event are' in spec
    assert '`supervised-only` qualification or revalidation' in spec


def _validate_gate_outcome_fixture(fixture):
    """Validate the documented gate fragments without implementing a runtime validator."""
    if fixture['fixture'] in {'blocked-checks', 'blocked-sensitive'}:
        assert set(fixture) == {'fixture', 'kind', 'status', 'gate_evidence', 'evidence_refs'}
        assert fixture['kind'] in {'qualification', 'revalidation'}
        assert fixture['status'] in {'blocked', 'supervised-only'}
        refs = set(fixture['evidence_refs'])
        observations = fixture['gate_evidence']
        assert all(item['gate'] in {
            'check', 'source-run', 'sensitive', 'review', 'source',
            'occupancy', 'machine', 'authorization',
        }
                   for item in observations)
        checks = [item for item in observations if item['gate'] == 'check']
        assert len(checks) == 3
        assert {item['name'] for item in checks} == {
            'source-ci', 'integration-tests', 'issue-link',
        }
        for check in checks:
            assert set(check) == {
                'gate', 'name', 'outcome', 'id', 'app_id', 'head_sha',
                'check_status', 'conclusion', 'completed_at', 'proof', 'reason',
            }
            assert check['outcome'] in {
                'success', 'failure', 'pending', 'missing', 'unknown',
            }
            assert check['proof'] in refs
            assert check['reason']
            if check['outcome'] == 'missing':
                assert all(check[key] is None for key in (
                    'id', 'app_id', 'head_sha', 'check_status', 'conclusion',
                    'completed_at',
                ))
            elif check['outcome'] == 'pending':
                assert type(check['id']) is int and check['id'] > 0
                assert re.fullmatch(r'[a-f0-9]{40}', check['head_sha'])
                assert check['check_status'] and check['conclusion'] is None
                assert check['completed_at'] is None
            elif check['outcome'] in {'success', 'failure'}:
                assert type(check['id']) is int and check['id'] > 0
                assert re.fullmatch(r'[a-f0-9]{40}', check['head_sha'])
                assert check['check_status']
                assert check['conclusion'] is not None
                assert (check['conclusion'] == 'success') == \
                    (check['outcome'] == 'success')
                assert check['completed_at']
            else:
                assert check['id'] is None or check['id'] > 0
                assert check['head_sha'] is None or \
                    re.fullmatch(r'[a-f0-9]{40}', check['head_sha'])
            if check['name'] in {'source-ci', 'issue-link'} and check['id'] is not None:
                assert check['app_id'] == 15368

        source = next(item for item in observations if item['gate'] == 'source-run')
        assert set(source) == {
            'gate', 'outcome', 'id', 'attempt', 'head_sha', 'run_status', 'conclusion',
            'completed_at', 'proof', 'reason',
        }
        assert source['outcome'] in {'success', 'failure', 'pending', 'missing', 'unknown'}
        assert source['proof'] in refs
        assert source['reason']
        if source['outcome'] == 'missing':
            assert all(source[key] is None for key in (
                'id', 'attempt', 'head_sha', 'run_status', 'conclusion', 'completed_at',
            ))
        elif source['outcome'] == 'pending':
            assert type(source['id']) is int and source['id'] > 0
            assert type(source['attempt']) is int and 1 <= source['attempt'] <= 100
            assert re.fullmatch(r'[a-f0-9]{40}', source['head_sha'])
            assert source['run_status'] and source['conclusion'] is None
            assert source['completed_at'] is None
        elif source['outcome'] in {'success', 'failure'}:
            assert type(source['id']) is int and source['id'] > 0
            assert type(source['attempt']) is int and 1 <= source['attempt'] <= 100
            assert re.fullmatch(r'[a-f0-9]{40}', source['head_sha'])
            assert source['run_status']
            assert source['completed_at']
            assert source['conclusion'] is not None
            assert (source['conclusion'] == 'success') == \
                (source['outcome'] == 'success')
        else:
            assert source['id'] is None or source['id'] > 0
            assert source['attempt'] is None or 1 <= source['attempt'] <= 100
            assert source['head_sha'] is None or \
                re.fullmatch(r'[a-f0-9]{40}', source['head_sha'])

        sensitive = next(item for item in observations if item['gate'] == 'sensitive')
        assert set(sensitive) == {'gate', 'outcome', 'command', 'proof', 'reason'}
        assert sensitive['outcome'] in {
            'authorized', 'not-required', 'missing', 'rejected', 'unknown',
        }
        assert sensitive['proof'] in refs and sensitive['reason']
        if sensitive['outcome'] in {'missing', 'not-required'}:
            assert sensitive['command'] is None
        elif sensitive['outcome'] in {'authorized', 'rejected'}:
            if sensitive['outcome'] == 'rejected':
                assert sensitive['command'] in refs
            else:
                assert isinstance(sensitive['command'], dict)
        else:
            assert sensitive['command'] is None or sensitive['command'] in refs

        other = [item for item in observations if item['gate'] not in {
            'check', 'source-run', 'sensitive',
        }]
        for item in other:
            assert set(item) == {'gate', 'outcome', 'observation', 'proof', 'reason'}
            assert item['outcome'] in {
                'missing', 'pending', 'failure', 'rejected', 'stale', 'unknown', 'uncertain',
            }
            assert item['observation'] is None or item['observation'] in refs
            assert item['proof'] in refs and item['reason']

        if fixture['status'] == 'supervised-only':
            assert not other
            assert all(check['outcome'] == 'success' for check in checks)
            assert source['outcome'] == 'success'
            assert sensitive['outcome'] in {'authorized', 'not-required'}
        else:
            assert any(check['outcome'] != 'success' for check in checks) or \
                source['outcome'] != 'success' or \
                sensitive['outcome'] not in {'authorized', 'not-required'}
    else:
        assert set(fixture) == {
            'fixture', 'kind', 'status', 'prior_comment_id', 'head_sha', 'base_sha',
            'changed_binding', 'reserved_at', 'observed_at', 'reason', 'evidence_refs',
        }
        assert fixture['fixture'] == 'invalidation'
        assert fixture['kind'] == 'invalidation'
        assert fixture['status'] == 'invalidated'
        assert type(fixture['prior_comment_id']) is int and fixture['prior_comment_id'] > 0
        assert set(fixture['changed_binding']) == {
            'head_sha', 'base_sha', 'observed_at', 'proof',
        }
        assert re.fullmatch(r'[a-f0-9]{40}', fixture['head_sha'])
        assert re.fullmatch(r'[a-f0-9]{40}', fixture['base_sha'])
        assert fixture['changed_binding']['proof'] in fixture['evidence_refs']
        assert {'prior-record', fixture['changed_binding']['proof']} <= \
            set(fixture['evidence_refs'])
        assert fixture['changed_binding']['observed_at'] <= fixture['observed_at']
        assert fixture['reserved_at'] <= fixture['observed_at']
        assert fixture['changed_binding']['head_sha'] != fixture['head_sha']


def test_manual_qualification_documents_truthful_blocked_and_invalidation_variants():
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    section = spec.split('### SYNTHETIC gate outcome examples', 1)[1].split(
        '### Publication and independent manual validation', 1)[0]
    fixtures = [json.loads(value) for value in re.findall(
        r'```json\n(.*?)\n```', section, re.DOTALL)]
    assert {fixture['fixture'] for fixture in fixtures} == {
        'blocked-checks', 'blocked-sensitive', 'invalidation',
    }
    for fixture in fixtures:
        _validate_gate_outcome_fixture(fixture)

    for fixture in fixtures[:2]:
        promoted = deepcopy(fixture)
        promoted['status'] = 'supervised-only'
        with pytest.raises(AssertionError):
            _validate_gate_outcome_fixture(promoted)


MANIFEST_ROLES = {
    'review-inventory': {'reviews', 'threads', 'metadata'},
    'review-metadata': {'full-media', 'edit-history'},
    'source': {'tasks', 'sessions', 'receipts', 'prompts', 'commands', 'ancestry',
               'diff', 'reservations'},
    'occupancy': {'tasks', 'sessions', 'workflows', 'comments', 'state',
                  'publication-reservation'},
    'check': {'check-runs', 'binding'},
    'source-run': {'run', 'jobs', 'binding'},
    'sensitive': {'command', 'edit-history', 'review'},
    'machine': {'observation', 'source'},
}


def _compound_example():
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    section = spec.split('### SYNTHETIC compound proof example', 1)[1]
    return json.loads(section.split('```json\n', 1)[1].split('\n```', 1)[0])


def _validate_documented_graph(example, private_proofs):
    """Test-only documentation/example validation, not a runtime validator."""
    assert set(example) == {'roots', 'evidence'}
    entries = example['evidence']
    assert 1 <= len(entries) <= 128
    by_key = {entry['key']: entry for entry in entries}
    assert len(by_key) == len(entries)
    binding = next(entry['identity'] for entry in entries
                   if entry['type'] == 'public-response')
    visiting, visited = set(), set()

    def visit(key, depth=0):
        assert key in by_key, 'dangling reference'
        assert key not in visiting, 'cyclic reference'
        assert depth <= 8
        if key in visited:
            return
        visiting.add(key)
        entry = by_key[key]
        kind = entry['type']
        if kind == 'manifest':
            assert set(entry) == {'key', 'type', 'purpose', 'members'}
            roles = {member['role'] for member in entry['members']}
            assert len(roles) == len(entry['members'])
            assert roles == MANIFEST_ROLES[entry['purpose']], 'missing members'
            for member in entry['members']:
                assert set(member) == {'role', 'refs'}
                refs = member['refs']
                assert 1 <= len(refs) <= 20 and len(set(refs)) == len(refs)
                for ref in refs:
                    visit(ref, depth + 1)
                    target = by_key[ref]
                    private_role = member['role'] in {
                        'reservations', 'state', 'publication-reservation', 'observation',
                    }
                    if private_role:
                        assert target['type'] == 'private-attestation'
                        allowed = {
                            'reservations': {'coordinator-reservation', 'supervisor-reservation'},
                            'state': {'coordinator-reservation', 'supervisor-reservation'},
                            'publication-reservation': {'publication-reservation'},
                            'observation': {'machine-observation'},
                        }
                        assert target['kind'] in allowed[member['role']]
                    elif member['role'] in {'metadata', 'review'}:
                        assert target['type'] == 'manifest'
                        assert target['purpose'] == 'review-metadata'
                    else:
                        assert target['type'] == 'public-response'
        elif kind == 'public-response':
            assert set(entry) == {
                'key', 'type', 'path', 'sha256', 'bytes', 'url', 'retrieved_at',
                'method', 'request_sha256', 'identity', 'pagination',
            }
            assert entry['method'] in {'GET', 'POST-read-only'}
            assert entry['url'].startswith('https://api.github.com/')
            assert set(entry['identity']) == {'repository_id', 'pull_number', 'head_sha', 'base_sha'}
            assert entry['identity'] == binding
            for page in entry['pagination']:
                assert set(page) == {'connection', 'page', 'cursor', 'next', 'total', 'count'}
                assert page['page'] == 1 and page['next'] is None  # One-page synthetic collections.
                assert page['total'] == page['count'] <= 100
        elif kind == 'private-attestation':
            assert set(entry) == {
                'key', 'type', 'kind', 'reference', 'snapshot_id', 'observed_at',
                'verifier', 'bindings', 'correlation', 'result', 'reason',
            }
            assert set(entry['verifier']) == {'login', 'id', 'delegation'}
            assert set(entry['bindings']) == {
                'repository_id', 'pull_number', 'head_sha', 'base_sha',
                'task_id', 'session_id', 'receipt_comment_id', 'prompt_sha256',
            }
            visit(entry['verifier']['delegation'], depth + 1)
            assert by_key[entry['verifier']['delegation']]['type'] == 'public-response', \
                'delegation must reference a public response'
            refs = entry['correlation']
            assert 1 <= len(refs) <= 20 and len(set(refs)) == len(refs)
            for ref in refs:
                visit(ref, depth + 1)
                assert by_key[ref]['type'] == 'public-response'
            assert all(entry['bindings'][key] == value for key, value in binding.items())
            assert entry['reference'] in private_proofs, 'absent private proof'
            proof = private_proofs[entry['reference']]
            assert proof['snapshot_id'] == entry['snapshot_id']
            assert proof['bindings'] == entry['bindings']
            assert proof['original_bytes'] and proof['origin_verified']
            assert entry['result'] == 'verified'
        else:
            raise AssertionError('unknown evidence type')
        visiting.remove(key)
        visited.add(key)

    root_purposes = {
        'review_inventory': 'review-inventory',
        'source_proof': 'source',
        'occupancy_proof': 'occupancy',
        'machine': 'machine',
    }
    assert set(example['roots']) == set(root_purposes), 'wrong root names'
    for name, root in example['roots'].items():
        assert by_key[root]['type'] == 'manifest'
        assert by_key[root]['purpose'] == root_purposes[name], 'wrong root purpose'
        visit(root)
    assert visited == set(by_key), 'unreferenced evidence'


def _synthetic_private_proofs(example):
    return {
        entry['reference']: {
            'snapshot_id': entry['snapshot_id'], 'bindings': entry['bindings'],
            'original_bytes': b'SYNTHETIC original private observation',
            'origin_verified': True,
        }
        for entry in example['evidence'] if entry['type'] == 'private-attestation'
    }


def test_compound_example_represents_rest_graphql_and_separate_private_proof():
    example = _compound_example()
    _validate_documented_graph(example, _synthetic_private_proofs(example))
    assert {'review_inventory', 'source_proof', 'occupancy_proof', 'machine'} == set(example['roots'])
    assert {'GET', 'POST-read-only'} == {
        entry['method'] for entry in example['evidence'] if entry['type'] == 'public-response'
    }
    assert {'supervisor-reservation', 'publication-reservation', 'machine-observation'} == {
        entry['kind'] for entry in example['evidence'] if entry['type'] == 'private-attestation'
    }
    assert all('path' not in entry and 'sha256' not in entry
               for entry in example['evidence'] if entry['type'] == 'private-attestation')
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    table = spec.split('| Manifest purpose |', 1)[1].split(
        'Root `review_inventory`', 1)[0]
    rows = re.findall(r'^\| `([^`]+)` \| ([^|]+) \|', table, re.MULTILINE)
    assert {purpose: set(re.findall(r'`([^`]+)`', roles))
            for purpose, roles in rows} == MANIFEST_ROLES


@pytest.mark.parametrize('target', ('metadata', 'publication'))
def test_compound_example_rejects_nonpublic_delegation(target):
    example = deepcopy(_compound_example())
    proofs = _synthetic_private_proofs(example)
    reservation = next(entry for entry in example['evidence'] if entry['key'] == 'reservation')
    reservation['verifier']['delegation'] = target
    with pytest.raises(AssertionError, match='delegation must reference a public response'):
        _validate_documented_graph(example, proofs)


@pytest.mark.parametrize('root', ('review_inventory', 'source_proof', 'occupancy_proof', 'machine'))
def test_compound_example_rejects_wrong_root_purpose(root):
    example = deepcopy(_compound_example())
    by_key = {entry['key']: entry for entry in example['evidence']}
    target = by_key[example['roots'][root]]
    other = by_key[example['roots']['occupancy_proof' if root == 'source_proof' else 'source_proof']]
    target['purpose'], other['purpose'] = other['purpose'], target['purpose']
    target['members'], other['members'] = other['members'], target['members']
    with pytest.raises(AssertionError, match='wrong root purpose'):
        _validate_documented_graph(example, _synthetic_private_proofs(example))


@pytest.mark.parametrize('defect', ('swapped-roots', 'wrong-root-name', 'extra-root'))
def test_compound_example_rejects_wrong_root_bindings(defect):
    example = deepcopy(_compound_example())
    roots = example['roots']
    if defect == 'swapped-roots':
        roots['source_proof'], roots['occupancy_proof'] = roots['occupancy_proof'], roots['source_proof']
        message = 'wrong root purpose'
    elif defect == 'wrong-root-name':
        roots['source'] = roots.pop('source_proof')
        message = 'wrong root names'
    else:
        roots['extra'] = roots['source_proof']
        message = 'wrong root names'
    with pytest.raises(AssertionError, match=message):
        _validate_documented_graph(example, _synthetic_private_proofs(example))


@pytest.mark.parametrize('defect', (
    'missing-member', 'dangling', 'cycle', 'duplicate', 'wrong-type', 'absent-private',
    'unknown-origin', 'changed-binding', 'duplicate-role', 'duplicate-ref',
    'private-cycle', 'private-dangling', 'wrong-identity', 'incomplete-page',
))
def test_compound_example_rejects_incomplete_or_uncertain_proof(defect):
    example = deepcopy(_compound_example())
    proofs = _synthetic_private_proofs(example)
    manifest = next(entry for entry in example['evidence'] if entry['type'] == 'manifest')
    if defect == 'missing-member':
        manifest['members'].pop()
    elif defect in {'dangling', 'cycle', 'wrong-type'}:
        manifest['members'][0]['refs'] = [{
            'dangling': 'missing', 'cycle': manifest['key'], 'wrong-type': 'observation',
        }[defect]]
    elif defect == 'duplicate':
        example['evidence'].append(deepcopy(example['evidence'][0]))
    elif defect == 'duplicate-role':
        manifest['members'].append(deepcopy(manifest['members'][0]))
    elif defect == 'duplicate-ref':
        manifest['members'][0]['refs'] *= 2
    elif defect in {'private-cycle', 'private-dangling'}:
        entry = next(entry for entry in example['evidence'] if entry['type'] == 'private-attestation')
        entry['correlation'] = [entry['key'] if defect == 'private-cycle' else 'missing']
    elif defect == 'wrong-identity':
        next(entry for entry in example['evidence']
             if entry['key'] == 'graphql')['identity']['repository_id'] = 99
    elif defect == 'incomplete-page':
        next(entry for entry in example['evidence']
             if entry['key'] == 'graphql')['pagination'][0]['next'] = 'unread-cursor'
    elif defect == 'absent-private':
        proofs.clear()
    elif defect == 'unknown-origin':
        next(iter(proofs.values()))['origin_verified'] = False
    else:
        next(iter(proofs.values()))['bindings'] = {}
    with pytest.raises(AssertionError):
        _validate_documented_graph(example, proofs)


@pytest.mark.parametrize('clause', (
    'Evidence is a discriminated union',
    'acyclic',
    'maximum reference depth of 8',
    'StateStore.snapshot() alone does not lock',
    'O_RDONLY',
    'LOCK_SH',
    'original supervisor',
    'not cryptographic remote proof of a local pre-POST write',
    'No local paths or private hashes',
    'explicitly delegated operator',
    'not a second person',
    'does not call the prose parser',
))
def test_compound_private_contract_has_realizable_boundaries(clause):
    spec = ' '.join((ROOT / 'docs/git-development-spec.md').read_text().split())
    assert clause in spec
    assert 'not a bot or a delegated writer' not in spec
    assert 'Another manual operator must' not in spec
    assert 'If necessary proof cannot be reproduced from public authenticated evidence' not in spec


@pytest.mark.parametrize('clause', (
    '<!-- hermes-supervised-qualification/v1 -->',
    'at most 60 KiB of UTF-8',
    'Missing, extra, duplicate, mistyped, or malformed fields block',
    'No runtime validator or coordinator qualification command is implemented',
    'sha256 over the exact raw UTF-8',
    'sha256 over the exact rendered UTF-8 `body_html`',
    'complete review/thread/comment pagination',
    'complete source task/session, receipt, reservation, and prompt bindings',
    'full repository task inventory',
    'same run ID and run attempt',
    'actual machine acceptance or rejection',
    'never set `review_valid` or edit a ledger',
    'gh api --method POST',
    'gh api --paginate --slurp',
    'repos/$REPO/issues/comments/$COMMENT_ID',
    'create-once reservation',
    'Never replay an uncertain POST',
    'No PATCH or DELETE',
    'Immediately before any merge',
    'append a new `invalidation` record',
    'independently repeat every read',
    'A runtime rejection still blocks that runtime path',
    'Private evidence must not be uploaded',
))
def test_manual_qualification_artifact_lifecycle_is_documented(clause):
    spec = ' '.join((ROOT / 'docs/git-development-spec.md').read_text().split())
    assert clause in spec, clause


def test_manual_qualification_shell_outputs_and_reservation_are_private(tmp_path):
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    section = spec.split('Use supported `gh api` commands', 1)[1]
    blocks = re.findall(r'```sh\n(.*?)\n```', section, re.DOTALL)
    setup = next(block for block in blocks if 'gh api user >' in block)
    publication = next(block for block in blocks if 'jq -Rs' in block)
    reservation = re.search(r'`(\( set -C; printf .*?\))`', section).group(1)

    bundle = tmp_path / 'bundle'
    private = tmp_path / 'private'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_gh = fake_bin / 'gh'
    fake_gh.write_text(
        '#!/bin/sh\nprintf invoked >> "$GH_CALL_LOG"\n'
        'if [ ! -f "$BUNDLE/comment.txt" ]; then '
        'printf synthetic > "$BUNDLE/comment.txt"; fi\n'
        'case "$*" in\n'
        '  *"--method POST"*) printf \'{"id":7}\\n\';;\n'
        '  *) printf \'{"synthetic":true}\\n\';;\n'
        'esac\n')
    fake_gh.chmod(0o700)
    env = dict(os.environ)
    env.update({
        'PATH': f'{fake_bin}:{env["PATH"]}',
        'BUNDLE': str(bundle),
        'PRIVATE': str(private),
        'REPO': 'lindayi/hermes-mobile',
        'PR': '107',
        'RECORD_KEY': 'synthetic-record',
        'GH_CALL_LOG': str(tmp_path / 'gh-calls'),
    })
    result = subprocess.run(
        ['/bin/bash', '-e', '-c',
         f'umask 022\n{setup}\n{reservation}\n{publication}'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in (bundle, private))
    files = list(bundle.iterdir()) + list(private.iterdir())
    assert {path.name for path in files} == {
        'publisher.json', 'repository.json', 'pull.json', 'comments-before.json',
        'comment.txt', 'request.json', 'create-response.json', 'readback.json',
        'comments-after.json', 'reservation',
    }
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert (private / 'reservation').read_text() == 'synthetic-record\n'


def _run_manual_qualification_publication(
        tmp_path, *, inherited_comment_id=None, post_response='{"id":731}',
        post_fails=False, bundle_path=None, private_path=None, setup_only=False,
        fake_parent_uid=None):
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    section = spec.split('Use supported `gh api` commands', 1)[1]
    blocks = re.findall(r'```sh\n(.*?)\n```', section, re.DOTALL)
    setup = next(block for block in blocks if 'gh api user >' in block)
    publication = next(block for block in blocks if 'jq -Rs' in block)
    reservation = re.search(r'`(\( set -C; printf .*?\))`', section).group(1)
    bundle_env = str(bundle_path) if bundle_path is not None else str(tmp_path / 'bundle')
    private_env = str(private_path) if private_path is not None else str(tmp_path / 'private')
    bundle = Path(bundle_env)
    private = Path(private_env)
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_gh = fake_bin / 'gh'
    fake_gh.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, sys\n'
        'args = sys.argv[1:]\n'
        'with open(os.environ["GH_CALL_LOG"], "a") as calls:\n'
        '    calls.write(" ".join(args) + "\\n")\n'
        'route = next((arg for arg in args if arg == "user" or arg.startswith("repos/")), "")\n'
        'if "--method" in args and "POST" in args:\n'
        '    if os.environ["POST_FAIL"] == "1":\n'
        '        sys.exit(22)\n'
        '    print(os.environ["POST_RESPONSE"])\n'
        'elif route == "user":\n'
        '    open(os.path.join(os.environ["BUNDLE"], "comment.txt"), "w").write("synthetic")\n'
        '    print(\'{"login":"synthetic","id":1}\')\n'
        'elif route == "repos/lindayi/hermes-mobile":\n'
        '    print(\'{"id":2}\')\n'
        'elif route == "repos/lindayi/hermes-mobile/pulls/107":\n'
        '    print(\'{"number":107}\')\n'
        'elif route == "repos/lindayi/hermes-mobile/issues/107/comments?per_page=100":\n'
        '    print("[]")\n'
        'elif route.startswith("repos/lindayi/hermes-mobile/issues/comments/"):\n'
        '    print(\'{"id":731}\')\n'
        'else:\n'
        '    sys.exit(23)\n')
    fake_gh.chmod(0o700)
    if fake_parent_uid is not None:
        fake_stat = fake_bin / 'stat'
        fake_stat.write_text(
            '#!/bin/sh\n'
            'case "$2" in\n'
            '  %a) printf "700\\n";;\n'
            f'  %u) printf "{fake_parent_uid}\\n";;\n'
            '  *) exit 1;;\n'
            'esac\n')
        fake_stat.chmod(0o700)
    call_log = tmp_path / 'gh-calls'
    env = dict(os.environ)
    env.update({
        'PATH': f'{fake_bin}:{env["PATH"]}',
        'BUNDLE': bundle_env,
        'PRIVATE': private_env,
        'REPO': 'lindayi/hermes-mobile',
        'PR': '107',
        'RECORD_KEY': 'synthetic-record',
        'GH_CALL_LOG': str(call_log),
        'POST_RESPONSE': post_response,
        'POST_FAIL': '1' if post_fails else '0',
    })
    if inherited_comment_id is not None:
        env['COMMENT_ID'] = inherited_comment_id
    result = subprocess.run(
        ['/bin/bash', '-e', '-c',
         f'umask 022\n{setup}' +
         ('' if setup_only else f'\n{reservation}\n{publication}')],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    calls = call_log.read_text().splitlines() if call_log.exists() else []
    return result, calls, private


@pytest.mark.parametrize('unsafe_layout', (
    'dot-component', 'private-dot-component', 'empty-component',
    'private-empty-component', 'aliased-bundle-contains-private',
    'aliased-private-contains-bundle', 'equal-paths', 'symlink-parent',
    'unsafe-parent-mode', 'unsafe-parent-owner',
))
def test_manual_qualification_shell_rejects_aliased_or_untrusted_paths_before_output(
        tmp_path, unsafe_layout):
    parent = tmp_path / 'trusted-parent'
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    bundle = parent / 'bundle'
    private = parent / 'private'
    if unsafe_layout == 'dot-component':
        bundle = f'{parent}/./bundle'
    elif unsafe_layout == 'private-dot-component':
        private = f'{parent}/./private'
    elif unsafe_layout == 'empty-component':
        bundle = f'{parent}//bundle'
    elif unsafe_layout == 'private-empty-component':
        private = f'{parent}//private'
    elif unsafe_layout == 'aliased-bundle-contains-private':
        (parent / 'alias').mkdir(mode=0o700)
        bundle = f'{parent}/alias/../record'
        private = parent / 'record/private'
    elif unsafe_layout == 'aliased-private-contains-bundle':
        (parent / 'alias').mkdir(mode=0o700)
        private = f'{parent}/alias/../record'
        bundle = parent / 'record/bundle'
    elif unsafe_layout == 'equal-paths':
        bundle = private = parent / 'record'
    elif unsafe_layout == 'symlink-parent':
        real_parent = tmp_path / 'real-parent'
        real_parent.mkdir(mode=0o700)
        real_parent.chmod(0o700)
        (tmp_path / 'linked-parent').symlink_to(real_parent, target_is_directory=True)
        bundle = tmp_path / 'linked-parent/bundle'
        private = real_parent / 'private'
    elif unsafe_layout == 'unsafe-parent-mode':
        parent.chmod(0o755)

    result, calls, _ = _run_manual_qualification_publication(
        tmp_path, bundle_path=bundle, private_path=private, setup_only=True,
        fake_parent_uid=(os.getuid() + 1 if unsafe_layout == 'unsafe-parent-owner' else None))

    assert result.returncode != 0
    assert not calls
    assert not Path(bundle).exists()
    assert not Path(private).exists()


@pytest.mark.parametrize('inherited_comment_id', (None, '999'))
def test_manual_qualification_shell_reads_the_id_returned_by_post(
        tmp_path, inherited_comment_id):
    result, calls, private = _run_manual_qualification_publication(
        tmp_path, inherited_comment_id=inherited_comment_id)

    assert result.returncode == 0, result.stderr
    assert sum('--method POST' in call for call in calls) == 1
    assert any('repos/lindayi/hermes-mobile/issues/comments/731' in call
               for call in calls)
    assert not any('repos/lindayi/hermes-mobile/issues/comments/999' in call
                   for call in calls)
    assert (private / 'reservation').read_text() == 'synthetic-record\n'


@pytest.mark.parametrize('post_response', (
    '{}',
    '{"id":null}',
    '{"id":"731"}',
    '{"id":true}',
    '{"id":731.5}',
    '{"id":0}',
    '{"id":-1}',
    'not-json',
))
def test_manual_qualification_shell_rejects_invalid_post_ids_before_readback(
        tmp_path, post_response):
    result, calls, private = _run_manual_qualification_publication(
        tmp_path, inherited_comment_id='999', post_response=post_response)

    assert result.returncode != 0
    assert sum('--method POST' in call for call in calls) == 1
    assert not any('repos/lindayi/hermes-mobile/issues/comments/' in call
                   for call in calls)
    assert (private / 'reservation').read_text() == 'synthetic-record\n'


def test_manual_qualification_shell_keeps_reservation_when_post_fails(tmp_path):
    result, calls, private = _run_manual_qualification_publication(
        tmp_path, inherited_comment_id='999', post_fails=True)

    assert result.returncode != 0
    assert sum('--method POST' in call for call in calls) == 1
    assert not any('repos/lindayi/hermes-mobile/issues/comments/' in call
                   for call in calls)
    assert (private / 'reservation').read_text() == 'synthetic-record\n'


@pytest.mark.parametrize('target', ('BUNDLE', 'PRIVATE'))
@pytest.mark.parametrize('kind', ('directory', 'symlink', 'file'))
def test_manual_qualification_shell_rejects_existing_destinations(tmp_path, target, kind):
    spec = (ROOT / 'docs/git-development-spec.md').read_text()
    section = spec.split('Use supported `gh api` commands', 1)[1]
    blocks = re.findall(r'```sh\n(.*?)\n```', section, re.DOTALL)
    setup = next(block for block in blocks if 'gh api user >' in block)
    reservation = re.search(r'`(\( set -C; printf .*?\))`', section).group(1)
    bundle = tmp_path / 'bundle'
    private = tmp_path / 'private'
    unsafe = bundle if target == 'BUNDLE' else private
    if kind == 'directory':
        unsafe.mkdir()
        unsafe.chmod(0o755)
    elif kind == 'symlink':
        outside = tmp_path / 'outside'
        outside.mkdir()
        unsafe.symlink_to(outside, target_is_directory=True)
    else:
        unsafe.write_text('preserve')

    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_gh = fake_bin / 'gh'
    fake_gh.write_text(
        '#!/bin/sh\nprintf invoked >> "$GH_CALL_LOG"\n'
        'printf \'{"synthetic":true}\\n\'\n')
    fake_gh.chmod(0o700)
    env = dict(os.environ)
    env.update({
        'PATH': f'{fake_bin}:{env["PATH"]}',
        'BUNDLE': str(bundle),
        'PRIVATE': str(private),
        'REPO': 'lindayi/hermes-mobile',
        'PR': '107',
        'RECORD_KEY': 'synthetic-record',
        'GH_CALL_LOG': str(tmp_path / 'gh-calls'),
    })
    result = subprocess.run(
        ['/bin/bash', '-e', '-c', f'umask 022\n{setup}\n{reservation}'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert not (tmp_path / 'gh-calls').exists()
    assert not (private if target == 'BUNDLE' else bundle).exists()
    if kind == 'directory':
        assert unsafe.stat().st_mode & 0o777 == 0o755
    elif kind == 'symlink':
        assert unsafe.is_symlink()
        assert not any(outside.iterdir())
    else:
        assert unsafe.read_text() == 'preserve'


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
            'source-ci', 'integration-tests', 'issue-link',
        )), path

    policy = ' '.join((ROOT / 'docs/autonomy-policy.md').read_text().split())
    assert (
        '| `staging` | `source-ci`, `integration-tests`, '
        '`issue-link` |'
    ) in policy


def test_agent_instructions_use_the_available_hosted_partition_portably():
    agents = (ROOT / 'AGENTS.md').read_text()
    text = ' '.join(agents.split())
    assert 'when the documented partition is available, otherwise' not in text
    assert '/home/lindayi/' not in agents
    assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', agents, re.MULTILINE)
    assert agents.count('scripts/ci_tests.py host') == 1
    assert 'run `scripts/ci_tests.py host` against verified exact-main staged' in text
    for clause in (
        'which requires every hosted suite',
        '`.github/host-tests.json`',
        'New tests default to hosted execution',
        'Do not repeat hosted suites on the production server routinely',
        'The full managed `all` remains an opt-in diagnostic and the conservative '
        'release-stage gate',
        'The hosted `integration-tests` check is merge evidence only when the same-run',
        'The complete `.github/host-tests.json` partition remains a mandatory guarded',
        'any failure blocks activation',
        'the host-only compatibility suite is verified during guarded deployment, not',
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
        assert '175728472' in text
        assert '198982749' in text
        assert 'Copilot' in text
        assert 'COMMENT' in text
        assert 'APPROVED' in text

    policy = ' '.join((ROOT / 'docs/autonomy-policy.md').read_text().split())
    for clause in (
        'The default offline CLI performs no network calls, writes, status publication, '
        'settings changes, service activation, checkout, import, or execution of candidate code.',
        'The opt-in `--collect-review --state STATE_JSON --pull NUMBER --head SHA` mode',
        'read-only coordinator state snapshot',
        'authenticated edit metadata, complete check runs and thread pagination',
        'It writes no files or state and never plans/applies coordinator actions.',
        'A collection error blocks without emitting partial evidence or clearing persisted authorization.',
        'Malformed current owner-comment responses at either the initial or final read abort collection with `invalid-evidence`, not `sensitive-review-authorization`;',
        'does not authenticate the JSON file',
        'Ordinary merge evidence has no structured independent report',
        'Installed-runtime compatibility remains an exact-main guarded deployment gate',
        'pre-cutover',
        'post-cutover',
    ):
        assert clause in policy, clause

    for clause in (
        'Sensitive',
        'owner authorization',
        'targeted review',
    ):
        assert clause in ' '.join((ROOT / 'AGENTS.md').read_text().split()), clause
    for clause in (
        '`copilot_review.reviews`',
        '`copilot_review.change.owner_command`',
        '`sensitive-review-authorization`',
        'current genuine Copilot review',
        'Never reconstruct this record from the persisted authorization',
    ):
        assert clause in policy, clause


def test_policy_documents_bounded_three_check_maps_and_copilot_review():
    policy = (ROOT / 'docs/autonomy-policy.md').read_text()
    expected = {
        'pre-cutover': ('source-ci', 'integration-tests', 'issue-link'),
        'staging': ('source-ci', 'integration-tests', 'issue-link'),
        'post-cutover': ('source-ci', 'integration-tests', 'issue-link'),
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
        'Actions app 15368; `integration-tests` remains unbound',
        'Phase names remain for compatibility',
        'advisory `cloud-review`',
    ):
        assert clause in text, clause
    assert '`statusCheckRollup` omits it' in policy
    assert 'cannot be a required branch context' in policy
    assert 'An absent review still blocks merge' in policy
    assert "GitHub's automatic Copilot review ruleset requests reviews" in policy


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
