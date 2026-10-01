import copy
import json

import pytest

from deploy.autonomy_policy import (
    COPILOT_AGENT_ID,
    COPILOT_REVIEWER_ID,
    OWNER_ID,
    REPOSITORY,
    REPOSITORY_ID,
    REF,
    RUN_JOBS,
    WORKFLOW_ID,
    WORKFLOW_PATH,
    validate_transition,
)

SHA = 'a' * 40
ARTIFACT_HASH = 'b' * 64
IDENTITY = f'https://github.com/{REPOSITORY}/{WORKFLOW_PATH}@{REF}'


def _workflow():
    jobs = """  build:
    runs-on: ubuntu-24.04
    steps:
      - run: python -B -m deploy.release_artifact build --bundle release.tar
      - uses: actions/upload-artifact@0123456789012345678901234567890123456789
        with:
          name: release-${{ github.run_id }}-${{ github.run_attempt }}
  checks:
    runs-on: ubuntu-24.04
    steps: []
  js:
    runs-on: ubuntu-24.04
    steps: []
  python:
    runs-on: ubuntu-24.04
    steps: []
  browser:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/download-artifact@0123456789012345678901234567890123456789
        with:
          name: release-${{ github.run_id }}-${{ github.run_attempt }}
      - run: python -B -m deploy.release_artifact unpack --bundle release.tar
"""
    return f"""jobs:
{jobs}
  native:
    runs-on: ubuntu-24.04
    steps:
      - run: python3 scripts/prepare_native_test_runtime.py --preflight
      - run: '"$HERMES_TEST_PYTHON" -B scripts/ci_tests.py native'
  source-ci:
    if: "${{{{ always() }}}}"
    needs: [build, checks, js, python, browser, native]
    runs-on: ubuntu-24.04
    steps:
      - env:
          RESULTS: "${{{{ toJSON(needs) }}}}"
        run: |
          python3 - <<'PY'
          import json, os, sys
          results = json.loads(os.environ['RESULTS'])
          failed = {{name: value['result'] for name, value in results.items() if value['result'] != 'success'}}
          sys.exit(bool(failed) or set(results) != {{'build', 'checks', 'js', 'python', 'browser', 'native'}})
          PY
  attest:
    needs: source-ci
    runs-on: ubuntu-24.04
    if: "${{{{ github.event_name == 'push' && github.ref == 'refs/heads/main' && github.repository == 'lindayi/hermes-mobile' }}}}"
    steps:
      - uses: actions/attest-build-provenance@0123456789012345678901234567890123456789
"""


def _source_files():
    expected_jobs = ', '.join(repr(name) for name in sorted(RUN_JOBS))
    release_artifact = f"""
REPOSITORY = {REPOSITORY!r}
REPOSITORY_ID = {REPOSITORY_ID}
WORKFLOW = {WORKFLOW_PATH!r}
WORKFLOW_ID = {WORKFLOW_ID}
REF = {REF!r}
EXPECTED_JOBS = frozenset({{{expected_jobs}}})
def _run_record():
    return {{'repository_id': REPOSITORY_ID, 'head_sha': sha}}
def _check_jobs(jobs, run_id, run_attempt, sha):
    if len(jobs) != len(EXPECTED_JOBS) or {{job.get('name') for job in jobs}} != EXPECTED_JOBS:
        raise ValueError()
    for job in jobs:
        expected = {{'run_id': run_id, 'run_attempt': run_attempt, 'head_sha': sha,
                    'status': 'completed', 'conclusion': 'success'}}
        if any(job.get(key) != value for key, value in expected.items()):
            raise ValueError()
def _attestation():
    return ['--cert-identity', '--source-ref', '--source-digest', '--signer-digest',
            '--deny-self-hosted-runners', 'runnerEnvironment', REPOSITORY_ID, WORKFLOW_ID]
def acquire_verified_bundle():
    _run_record()
    _check_jobs()
    _attestation()
"""
    self_deploy = """
SOURCE_TREES = ('.github',)
def run_host_checks(paths, stage):
    selected = select_tests(stage, 'host')
    return run_suite(stage, suite='python', extra_args=selected)
def _deploy(hosted_run_id, checks, paths, stage):
    if hosted_run_id is None:
        checks(stage)
    else:
        run_host_checks(paths, stage)
"""
    coordinator = f"""
REPOSITORY = {REPOSITORY!r}
REPOSITORY_ID = {REPOSITORY_ID}
OWNER_ID = {OWNER_ID}
COPILOT_REVIEWER_ID = {COPILOT_REVIEWER_ID}
COPILOT_AGENT_ID = {COPILOT_AGENT_ID}
SOURCE_WORKFLOW_ID = {WORKFLOW_ID}
MAIN_BRANCH = 'main'
def _complete_resolved_threads(threads, complete=True):
    return (complete is True and isinstance(threads, list)
            and all(thread.get('isResolved') is True
                    and thread.get('comments_complete', True) is True for thread in threads))
def copilot_review_valid(head_sha, reviews, threads, *, threads_complete=True,
                         reviews_complete=True):
    if (not _is_sha(head_sha) or not reviews_complete or not isinstance(reviews, list)
            or not _complete_resolved_threads(threads, complete=threads_complete)):
        return False
    authored = [
        review for review in reviews
        if isinstance(review, dict)
        and isinstance(review.get('user'), dict)
        and type(review['user'].get('id')) is int
        and review['user']['id'] == COPILOT_REVIEWER_ID
    ]
    if not authored:
        return False
    latest = max(authored, key=lambda review: str(review.get('submitted_at') or ''))
    return latest.get('state') == 'APPROVED' and latest.get('commit_id') == head_sha
def classify_sensitive_paths(files, complete=True):
    return complete
class Coordinator:
    def _identity(self, api):
        repository = api.get(f'repos/{{REPOSITORY}}')
        if repository.get('id') != REPOSITORY_ID:
            return False
        user = api.get('user')
        return user.get('id') == OWNER_ID
def _is_owner_sensitive_command(comment):
    user = comment.get('user')
    body = comment.get('body')
    if user.get('id') != OWNER_ID:
        return None
    match = re.fullmatch(r'/hermes authorize-sensitive ([0-9a-f]{{40}})', body.strip())
    return match.group(1) if match else None
def _scan_enrollments(comment):
    return _is_owner_sensitive_command(comment)
def _plan_pull(head, snapshot):
    review_ok = copilot_review_valid(head, snapshot['reviews'], snapshot['threads'])
    sensitive = classify_sensitive_paths(snapshot['files'])
    authorized = not sensitive or snapshot['enrollment'].get('sensitive_sha') == head
    return review_ok and authorized
"""
    return {
        WORKFLOW_PATH: _workflow(),
        '.github/native-tests.json': json.dumps({'tests/test_native.py': 'synthetic native test'}),
        '.github/host-tests.json': json.dumps({'tests/test_host.py': 'synthetic installed compatibility test'}),
        'deploy/release_artifact.py': release_artifact,
        'deploy/self_deploy.py': self_deploy,
        'deploy/cloud_coordinator.py': coordinator,
    }


def _source_ci():
    run_id, attempt = 1234, 2
    return {
        'repository': REPOSITORY, 'repository_id': REPOSITORY_ID,
        'head_repository_id': REPOSITORY_ID, 'workflow_id': WORKFLOW_ID,
        'workflow_path': WORKFLOW_PATH, 'event': 'push', 'branch': 'main',
        'head_sha': SHA, 'status': 'completed', 'conclusion': 'success',
        'run_id': run_id, 'run_attempt': attempt, 'jobs_complete': True,
        'jobs': [
            {'id': index + 1, 'name': name, 'run_id': run_id, 'run_attempt': attempt,
             'head_sha': SHA, 'status': 'completed', 'conclusion': 'success'}
            for index, name in enumerate(sorted(RUN_JOBS))
        ],
        'artifact': {
            'name': f'release-{run_id}-{attempt}', 'run_id': run_id, 'run_attempt': attempt,
            'head_sha': SHA, 'repository_id': REPOSITORY_ID, 'head_repository_id': REPOSITORY_ID,
            'expired': False, 'size_bytes': 123, 'sha256': ARTIFACT_HASH,
            'attestation': {
                'verified': True, 'repository': REPOSITORY, 'repository_id': str(REPOSITORY_ID),
                'source_ref': REF, 'source_sha': SHA, 'signer_sha': SHA,
                'signer_identity': IDENTITY,
                'run_invocation': f'https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}',
                'runner_environment': 'github-hosted', 'trigger': 'push',
                'issuer': 'https://token.actions.githubusercontent.com',
            },
        },
    }


def _evidence():
    review_head = 'c' * 40
    return {
        'repository': {'id': REPOSITORY_ID, 'full_name': REPOSITORY},
        'main': {
            'repository_id': REPOSITORY_ID, 'ref': REF, 'current': True, 'sha': SHA,
            'snapshot_complete': True, 'files': _source_files(),
        },
        'protection': {
            'repository_id': REPOSITORY_ID, 'repository': REPOSITORY, 'branch': 'main',
            'complete': True, 'strict': True, 'enforce_admins': True,
            'required_conversation_resolution': True,
            'required_checks': [
                {'context': 'source-ci', 'app_id': 15368},
                {'context': 'integration-tests', 'app_id': None},
                {'context': 'agent-review', 'app_id': None},
            ],
        },
        'source_ci': _source_ci(),
        'cloud_review': {
            'repository_id': REPOSITORY_ID, 'base_branch': 'main', 'base_sha': SHA,
            'head_sha': review_head, 'reviews_complete': True, 'threads_complete': True,
            'reviews': [{
                'user': {'id': COPILOT_REVIEWER_ID}, 'state': 'APPROVED',
                'commit_id': review_head, 'submitted_at': '2026-10-01T21:00:00Z',
            }],
            'threads': [{'isResolved': True, 'comments_complete': True}],
            'status': {
                'context': 'cloud-review', 'state': 'success',
                'head_sha': review_head, 'creator_id': OWNER_ID,
            },
        },
    }


def _blockers(evidence, phase='pre-cutover'):
    return set(validate_transition(evidence, phase=phase)['blockers'])


def test_complete_synthetic_evidence_satisfies_each_policy_phase():
    pre = validate_transition(_evidence(), phase='pre-cutover')
    assert pre == {'ready': True, 'phase': 'pre-cutover', 'blockers': []}

    post_evidence = _evidence()
    post_evidence['protection']['required_checks'] = [
        {'context': 'source-ci', 'app_id': 15368},
        {'context': 'issue-link', 'app_id': 15368},
        {'context': 'cloud-review', 'app_id': None},
    ]
    post = validate_transition(post_evidence, phase='post-cutover')
    assert post == {'ready': True, 'phase': 'post-cutover', 'blockers': []}


def test_missing_native_job_or_aggregate_dependency_blocks():
    evidence = _evidence()
    evidence['main']['files'][WORKFLOW_PATH] = evidence['main']['files'][WORKFLOW_PATH].replace(
        '  native:\n', '  omitted-native:\n', 1,
    )
    assert 'hosted-workflow-contract' in _blockers(evidence)

    evidence = _evidence()
    evidence['main']['files'][WORKFLOW_PATH] = evidence['main']['files'][WORKFLOW_PATH].replace(
        'needs: [build, checks, js, python, browser, native]',
        'needs: [build, checks, js, python, browser]',
    )
    assert 'native-aggregate-dependency' in _blockers(evidence)


def test_missing_or_incomplete_release_provenance_blocks():
    evidence = _evidence()
    evidence['source_ci'].pop('artifact')
    assert 'release-artifact-evidence' in _blockers(evidence)

    evidence = _evidence()
    evidence['source_ci']['jobs_complete'] = False
    assert 'source-ci-jobs' in _blockers(evidence)

    evidence = _evidence()
    evidence['source_ci']['artifact']['attestation']['source_sha'] = 'd' * 40
    assert 'release-attestation' in _blockers(evidence)


def test_missing_installed_host_gate_blocks():
    evidence = _evidence()
    evidence['main']['files']['.github/host-tests.json'] = '{}'
    assert 'installed-host-gate' in _blockers(evidence)

    evidence = _evidence()
    evidence['main']['files']['deploy/self_deploy.py'] = 'def _deploy(): pass'
    assert 'installed-host-gate' in _blockers(evidence)


@pytest.mark.parametrize(('field', 'value', 'blocker'), [
    ('repository', {'id': 1, 'full_name': REPOSITORY}, 'repository-identity'),
    ('strict', False, 'branch-protection'),
    ('enforce_admins', False, 'branch-protection'),
    ('required_conversation_resolution', False, 'branch-protection'),
])
def test_wrong_repository_or_unprotected_main_blocks(field, value, blocker):
    evidence = _evidence()
    if field == 'repository':
        evidence[field] = value
    else:
        evidence['protection'][field] = value
    assert blocker in _blockers(evidence)


def test_wrong_required_check_app_identity_or_partial_cutover_blocks():
    evidence = _evidence()
    evidence['protection']['required_checks'][0]['app_id'] = 175728472
    assert 'required-check-policy' in _blockers(evidence)

    evidence = _evidence()
    assert 'required-check-policy' in _blockers(evidence, phase='post-cutover')


@pytest.mark.parametrize('mutate', [
    lambda review: review['reviews'][0].update(state='COMMENTED'),
    lambda review: review['reviews'][0].update(commit_id='d' * 40),
    lambda review: review.update(reviews_complete=False),
    lambda review: review.update(threads_complete=False),
    lambda review: review['threads'][0].update(isResolved=False),
    lambda review: review['status'].update(head_sha='d' * 40),
])
def test_review_requires_authenticated_exact_head_approval_and_complete_threads(mutate):
    evidence = _evidence()
    mutate(evidence['cloud_review'])
    assert _blockers(evidence) & {
        'cloud-review-approval', 'cloud-review-evidence',
        'cloud-review-threads', 'cloud-review-status',
    }


def test_wrong_coordinator_identity_or_review_contract_blocks():
    evidence = _evidence()
    evidence['main']['files']['deploy/cloud_coordinator.py'] = (
        evidence['main']['files']['deploy/cloud_coordinator.py']
        .replace(f'COPILOT_REVIEWER_ID = {COPILOT_REVIEWER_ID}',
                 'COPILOT_REVIEWER_ID = 42')
    )
    assert 'coordinator-review-contract' in _blockers(evidence)

    evidence = _evidence()
    evidence['main']['files']['deploy/cloud_coordinator.py'] = (
        evidence['main']['files']['deploy/cloud_coordinator.py']
        .replace("'APPROVED'", "'COMMENTED'")
    )
    assert 'coordinator-review-contract' in _blockers(evidence)


def test_mismatched_current_head_and_sensitive_approval_block():
    evidence = _evidence()
    evidence['source_ci']['head_sha'] = 'd' * 40
    assert 'source-ci-evidence' in _blockers(evidence)

    evidence = _evidence()
    evidence['cloud_review']['change'] = {
        'sensitive': True,
        'owner_authorization': {'actor_id': OWNER_ID, 'head_sha': SHA, 'state': 'approved'},
        'targeted_review': {'reviewer_id': 76, 'head_sha': 'd' * 40, 'state': 'COMMENTED'},
    }
    assert 'sensitive-review-authorization' in _blockers(evidence)


def test_missing_evidence_blocks_and_validator_does_not_mutate_input():
    evidence = _evidence()
    before = copy.deepcopy(evidence)

    report = validate_transition({}, phase='pre-cutover')

    assert report['ready'] is False
    assert report['phase'] == 'pre-cutover'
    assert report['blockers']
    assert evidence == before
