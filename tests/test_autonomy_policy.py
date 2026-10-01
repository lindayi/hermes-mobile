import copy
import hashlib
import json

import pytest

from deploy.autonomy_policy import (
    COPILOT_REVIEWER_ID,
    OWNER_ID,
    REPOSITORY,
    REPOSITORY_ID,
    REF,
    REQUIRED_FILES,
    RUN_JOBS,
    SOURCE_BASELINES,
    SOURCE_BLOCKERS,
    SOURCE_FINGERPRINTS,
    WORKFLOW_ID,
    WORKFLOW_PATH,
    validate_transition,
)
from scripts.autonomy_policy import main as cli_main

SHA = 'a' * 40
ARTIFACT_HASH = 'b' * 64
IDENTITY = f'https://github.com/{REPOSITORY}/{WORKFLOW_PATH}@{REF}'

_MERGED_MAIN_SOURCE_FIXTURE = {
    '.github/workflows/ci.yml': '39110e6f940fc59ef3aec9846f07616a86a2e07335d2aaed6c6cd0ab444ff195',
    '.github/native-tests.json': 'b97ddb088e595197cf65d97b0b4af89f4f83f5c4ad25463c566df7099817209f',
    '.github/host-tests.json': '8af5fc30188f81ba95d3d7c11ba6801f52795175c2cac7f634424f906947ce5a',
    '.github/actions/native-test-environment/action.yml': 'b1e5af03a4aa397f585e541805b4a292d1b3529090d54932b8945b6743ecd993',
    '.github/native-runtime.json': '705351eff7420cf3cbd3f91f3edc605feb304eba86f99e5a94902e89d3fb4d88',
    'deploy/release_artifact.py': '8bb1e62a1a4cb1a0239e05ab3d4b54c7d2896f2e1a09125ece5e4c36a44fc94e',
    'deploy/self_deploy.py': 'c8b9febf5e73c22de2aebbbf6ecb63e08597f58ea7ff5eede979d4be51783550',
    'deploy/ci_selection.py': '07493f74bc4b932e26342e0d27fee8c3c38601af8211e0a950920935173b2f16',
    'scripts/ci_tests.py': '6ed905a90720fb17226472a0453fb762395424b8370df474fed4536827b398d6',
    'scripts/prepare_native_test_runtime.py': '798195e6d9b284bae69cb6e27cf8dc7ad5ffc6dce62939b137fb6c6b370b58a8',
}
_PENDING_PR16_COORDINATOR_FIXTURE = {
    'deploy/cloud_coordinator.py': 'bd5513b06b6e9539b4224c2ec83a8a4769fe6f94c5526b377b5bac09ebb9d134',
}


def _source_files():
    return _MERGED_MAIN_SOURCE_FIXTURE | _PENDING_PR16_COORDINATOR_FIXTURE


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
                {'context': 'source-ci', 'app_id': None},
                {'context': 'integration-tests', 'app_id': None},
                {'context': 'agent-review', 'app_id': None},
                {'context': 'issue-link', 'app_id': 15368},
            ],
        },
        'source_ci': _source_ci(),
        'cloud_review': {
            'repository_id': REPOSITORY_ID, 'base_branch': 'main', 'base_sha': SHA,
            'state': 'open', 'draft': False,
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
            'change': {'head_sha': review_head, 'files_complete': True, 'sensitive': False},
        },
    }


PHASES = ('pre-cutover', 'staging', 'post-cutover')


def _phase_evidence(phase):
    evidence = _evidence()
    if phase == 'pre-cutover':
        evidence['cloud_review'].pop('status')
    else:
        evidence['protection']['required_checks'][0]['app_id'] = 15368
        evidence['protection']['required_checks'].append({'context': 'cloud-review', 'app_id': None})
        if phase == 'post-cutover':
            evidence['protection']['required_checks'] = [
                check for check in evidence['protection']['required_checks']
                if check['context'] not in ('integration-tests', 'agent-review')
            ]
    return evidence


def _blockers(evidence, phase='pre-cutover'):
    return set(validate_transition(evidence, phase=phase)['blockers'])


def _changed_source(evidence, path, source):
    evidence['main']['files'][path] = hashlib.sha256(source.encode()).hexdigest()


def test_reviewed_source_fixture_matches_complete_required_contract():
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path != 'deploy/cloud_coordinator.py'
    } == _MERGED_MAIN_SOURCE_FIXTURE
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path == 'deploy/cloud_coordinator.py'
    } == _PENDING_PR16_COORDINATOR_FIXTURE
    assert set(SOURCE_FINGERPRINTS) == set(REQUIRED_FILES)
    assert SOURCE_BASELINES == {
        'main': '66a64245b6c9c632d5ca4087e3d1e4e4fa2a4e83',
        'deploy/cloud_coordinator.py': '403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83',
    }


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


@pytest.mark.parametrize('source_app', [None, 15368])
@pytest.mark.parametrize('published_status', [False, True])
def test_pre_cutover_preserves_actual_four_checks_without_bootstrap_deadlock(source_app, published_status):
    # Only protection mirrors the observed policy; all other records are synthetic.
    evidence = _evidence()
    evidence['protection']['required_checks'] = [
        {'context': 'source-ci', 'app_id': source_app},
        {'context': 'integration-tests', 'app_id': None},
        {'context': 'agent-review', 'app_id': None},
        {'context': 'issue-link', 'app_id': 15368},
    ]
    if not published_status:
        evidence['cloud_review'].pop('status')
    before = copy.deepcopy(evidence)

    assert validate_transition(evidence, phase='pre-cutover') == {
        'ready': True, 'phase': 'pre-cutover', 'blockers': [],
    }
    assert evidence == before


def test_missing_pending_pr16_source_cannot_satisfy_current_main_contract():
    evidence = _evidence()
    evidence['main']['files'].pop('deploy/cloud_coordinator.py')

    report = validate_transition(evidence, phase='pre-cutover')

    assert report['ready'] is False
    assert report['blockers'] == ['main-source-missing']
    assert SOURCE_BASELINES['deploy/cloud_coordinator.py'] != SOURCE_BASELINES['main']


def test_missing_native_job_or_aggregate_dependency_blocks():
    evidence = _evidence()
    _changed_source(evidence, WORKFLOW_PATH, 'native job omitted')
    assert 'hosted-workflow-contract' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(evidence, WORKFLOW_PATH, 'native aggregate dependency omitted')
    assert 'hosted-workflow-contract' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(evidence, WORKFLOW_PATH, 'native removed from aggregate result set')
    assert 'hosted-workflow-contract' in _blockers(evidence)


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

    evidence = _evidence()
    _changed_source(evidence, 'deploy/release_artifact.py', 'native omitted from expected job set')
    assert 'release-artifact-provenance' in _blockers(evidence)


@pytest.mark.parametrize(('field', 'value'), [
    ('status', 'failure'),
    ('conclusion', 'skipped'),
    ('head_sha', 'd' * 40),
    ('run_attempt', 3),
])
def test_failed_skipped_or_stale_native_job_blocks(field, value):
    evidence = _evidence()
    native_job = next(job for job in evidence['source_ci']['jobs'] if job['name'] == 'native')
    native_job[field] = value

    assert 'source-ci-jobs' in _blockers(evidence)


def test_missing_native_job_record_and_truncated_review_block():
    evidence = _evidence()
    evidence['source_ci']['jobs'] = [
        job for job in evidence['source_ci']['jobs'] if job['name'] != 'native'
    ]
    assert 'source-ci-jobs' in _blockers(evidence)

    evidence = _evidence()
    evidence['cloud_review']['threads'][0]['comments_complete'] = False
    assert 'cloud-review-threads' in _blockers(evidence)


def test_missing_installed_host_gate_blocks():
    evidence = _evidence()
    _changed_source(evidence, '.github/host-tests.json', '{}')
    assert 'installed-host-gate' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(evidence, 'deploy/self_deploy.py', 'def _deploy(): pass')
    assert 'installed-host-gate' in _blockers(evidence)


def test_noop_host_gate_and_earlier_non_gate_branch_block():
    evidence = _evidence()
    _changed_source(
        evidence, 'deploy/self_deploy.py',
        'def run_host_checks(paths, stage):\n    return True',
    )
    assert 'installed-host-gate' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(
        evidence, 'deploy/self_deploy.py',
        'def _deploy(hosted_run_id):\n'
        '    if hosted_run_id is None:\n        return\n'
        '    run_host_checks(paths, stage)',
    )
    assert 'installed-host-gate' in _blockers(evidence)


def test_dead_review_guards_and_unreachable_aggregate_exit_block():
    evidence = _evidence()
    _changed_source(
        evidence, 'deploy/cloud_coordinator.py',
        'def copilot_review_valid(head_sha, reviews, threads):\n    return True',
    )
    assert 'coordinator-review-contract' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(
        evidence, WORKFLOW_PATH,
        'sys.exit(0)\n' + "sys.exit(bool(failed) or set(results) != expected)",
    )
    assert 'hosted-workflow-contract' in _blockers(evidence)


def test_skipped_mandatory_native_step_and_invalid_runtime_pin_block():
    evidence = _evidence()
    _changed_source(evidence, WORKFLOW_PATH, 'if: ${{ false }}\nrun: ci_tests.py native')
    assert 'hosted-workflow-contract' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(evidence, '.github/native-runtime.json', '{}')
    assert 'native-job-contract' in _blockers(evidence)


def test_uses_only_checkout_and_later_host_branch_are_pinned_to_merged_source():
    evidence = _evidence()
    _changed_source(evidence, WORKFLOW_PATH, 'uses: actions/checkout@pin')
    assert 'hosted-workflow-contract' in _blockers(evidence)

    evidence = _evidence()
    _changed_source(evidence, 'deploy/self_deploy.py', 'run_host_checks before later release branch')
    assert 'installed-host-gate' in _blockers(evidence)


def test_removed_private_host_manifest_entry_blocks():
    evidence = _evidence()
    _changed_source(evidence, '.github/host-tests.json', '{"tests/test_native.py":"native"}')

    assert 'installed-host-gate' in _blockers(evidence)


def test_workflow_identity_uses_reviewed_release_source_fingerprint():
    assert SOURCE_FINGERPRINTS['deploy/release_artifact.py'] == _MERGED_MAIN_SOURCE_FIXTURE[
        'deploy/release_artifact.py'
    ]
    assert SOURCE_BLOCKERS['deploy/release_artifact.py'] == 'release-artifact-provenance'


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


def test_unreviewed_coordinator_source_changes_block():
    evidence = _evidence()
    _changed_source(evidence, 'deploy/cloud_coordinator.py', 'different coordinator implementation')
    assert 'coordinator-review-contract' in _blockers(evidence)


def test_mismatched_current_head_and_sensitive_approval_block():
    evidence = _evidence()
    evidence['source_ci']['head_sha'] = 'd' * 40
    assert 'source-ci-evidence' in _blockers(evidence)

    evidence = _evidence()
    evidence['cloud_review']['change'] = {
        'head_sha': evidence['cloud_review']['head_sha'],
        'files_complete': True,
        'sensitive': True,
        'owner_authorization': {'actor_id': OWNER_ID, 'head_sha': SHA, 'state': 'approved'},
        'targeted_review': {'reviewer_id': 76, 'head_sha': 'd' * 40, 'state': 'COMMENTED'},
    }
    assert 'sensitive-review-authorization' in _blockers(evidence)

    evidence = _evidence()
    evidence['cloud_review'].pop('change')
    assert 'change-scope-evidence' in _blockers(evidence)


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_synthetic_positive_and_no_input_mutation(phase):
    evidence = _phase_evidence(phase)
    before = copy.deepcopy(evidence)
    assert validate_transition(evidence, phase=phase) == {
        'ready': True, 'phase': phase, 'blockers': [],
    }
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_requires_every_context_and_no_unknown_superset(phase):
    evidence = _phase_evidence(phase)
    checks = evidence['protection']['required_checks']
    for removed in checks:
        changed = copy.deepcopy(evidence)
        changed['protection']['required_checks'].remove(removed)
        assert _blockers(changed, phase) == {'required-check-policy'}, removed['context']
    for added in ({'context': 'unknown', 'app_id': None}, checks[0]):
        changed = copy.deepcopy(evidence)
        changed['protection']['required_checks'].append(added)
        assert _blockers(changed, phase) == {'required-check-policy'}


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_requires_exact_app_bindings(phase):
    evidence = _phase_evidence(phase)
    for index, check in enumerate(evidence['protection']['required_checks']):
        bad_apps = [175728472, '15368', 15368.0]
        if phase != 'pre-cutover' or check['context'] != 'source-ci':
            bad_apps.append(None if check['app_id'] is not None else 15368)
        for app in bad_apps:
            changed = copy.deepcopy(evidence)
            changed['protection']['required_checks'][index]['app_id'] = app
            assert _blockers(changed, phase) == {'required-check-policy'}, (check, app)
        changed = copy.deepcopy(evidence)
        changed['protection']['required_checks'][index].pop('app_id')
        assert _blockers(changed, phase) == {'required-check-policy'}, check


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize(('path', 'value', 'blocker'), [
    (('repository', 'id'), 1, 'repository-identity'),
    (('protection', 'strict'), False, 'branch-protection'),
    (('protection', 'enforce_admins'), False, 'branch-protection'),
    (('protection', 'required_conversation_resolution'), False, 'branch-protection'),
    (('protection', 'complete'), False, 'branch-protection'),
    (('cloud_review', 'reviews'), [], 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'state'), 'COMMENTED', 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'state'), 'CHANGES_REQUESTED', 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'state'), 'DISMISSED', 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'user', 'id'), OWNER_ID, 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'commit_id'), 'd' * 40, 'cloud-review-approval'),
    (('cloud_review', 'reviews_complete'), False, 'cloud-review-evidence'),
    (('cloud_review', 'threads_complete'), False, 'cloud-review-evidence'),
    (('cloud_review', 'threads', 0, 'isResolved'), False, 'cloud-review-threads'),
    (('cloud_review', 'threads', 0, 'comments_complete'), False, 'cloud-review-threads'),
    (('cloud_review', 'base_sha'), 'd' * 40, 'cloud-review-evidence'),
    (('cloud_review', 'change', 'head_sha'), 'd' * 40, 'change-scope-evidence'),
    (('cloud_review', 'change', 'files_complete'), False, 'change-scope-evidence'),
    (('cloud_review', 'change', 'sensitive'), True, 'sensitive-review-authorization'),
    (('source_ci', 'head_sha'), 'd' * 40, 'source-ci-evidence'),
    (('source_ci', 'workflow_id'), 1, 'source-ci-evidence'),
    (('source_ci', 'jobs_complete'), False, 'source-ci-jobs'),
    (('source_ci', 'jobs', 0, 'conclusion'), 'skipped', 'source-ci-jobs'),
    (('source_ci', 'artifact'), None, 'release-artifact-evidence'),
    (('source_ci', 'artifact', 'attestation', 'verified'), False, 'release-attestation'),
    (('main', 'current'), False, 'current-main-snapshot'),
    (('main', 'files', 'deploy/cloud_coordinator.py'), '0' * 64, 'coordinator-review-contract'),
    (('main', 'files', WORKFLOW_PATH), '0' * 64, 'hosted-workflow-contract'),
    (('main', 'files', 'deploy/self_deploy.py'), '0' * 64, 'installed-host-gate'),
])
def test_additive_staging_never_substitutes_status_for_review_or_source(phase, path, value, blocker):
    evidence = _phase_evidence(phase)
    node = evidence
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert blocker in _blockers(evidence, phase)


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_missing_merged_coordinator_blocks(phase):
    evidence = _phase_evidence(phase)
    evidence['main']['files'].pop('deploy/cloud_coordinator.py')
    assert _blockers(evidence, phase) == {'main-source-missing'}


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_latest_review_and_sensitive_exact_head_authorization(phase):
    evidence = _phase_evidence(phase)
    review = evidence['cloud_review']
    review['reviews'].append(dict(review['reviews'][0], state='COMMENTED',
                                  submitted_at='2026-10-01T22:00:00Z'))
    assert _blockers(evidence, phase) == {'cloud-review-approval'}
    review['reviews'].pop()
    head = review['head_sha']
    review['change'].update(
        sensitive=True,
        owner_authorization={'actor_id': OWNER_ID, 'head_sha': head, 'state': 'approved'},
        targeted_review={'reviewer_id': 76, 'head_sha': head, 'state': 'COMMENTED'},
    )
    assert _blockers(evidence, phase) == set()
    for record, field, value in (
        ('owner_authorization', 'actor_id', 1),
        ('owner_authorization', 'head_sha', 'd' * 40),
        ('owner_authorization', 'state', 'pending'),
        ('targeted_review', 'reviewer_id', OWNER_ID),
        ('targeted_review', 'head_sha', 'd' * 40),
        ('targeted_review', 'state', 'DISMISSED'),
    ):
        changed = copy.deepcopy(evidence)
        changed['cloud_review']['change'][record][field] = value
        assert _blockers(changed, phase) == {'sensitive-review-authorization'}


@pytest.mark.parametrize('phase', PHASES)
def test_additive_staging_status_omission_only_before_staging(phase):
    evidence = _phase_evidence(phase)
    evidence['cloud_review'].pop('status', None)
    assert _blockers(evidence, phase) == (
        set() if phase == 'pre-cutover' else {'cloud-review-status'}
    )


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('status', [
    None, {}, 'success',
    {'context': 'agent-review', 'state': 'success', 'head_sha': 'c' * 40, 'creator_id': OWNER_ID},
    {'context': 'cloud-review', 'state': 'pending', 'head_sha': 'c' * 40, 'creator_id': OWNER_ID},
    {'context': 'cloud-review', 'state': 'success', 'head_sha': 'd' * 40, 'creator_id': OWNER_ID},
    {'context': 'cloud-review', 'state': 'success', 'head_sha': 'c' * 40, 'creator_id': 15368},
])
def test_additive_staging_supplied_status_must_be_fixed_creator_exact_head_success(phase, status):
    evidence = _phase_evidence(phase)
    evidence['cloud_review']['status'] = status
    assert _blockers(evidence, phase) == {'cloud-review-status'}


def test_additive_staging_rejects_other_phase_maps():
    for selected in PHASES:
        for supplied in PHASES:
            if selected != supplied:
                assert 'required-check-policy' in _blockers(_phase_evidence(supplied), selected)


def test_missing_evidence_blocks_and_validator_does_not_mutate_input():
    evidence = _evidence()
    before = copy.deepcopy(evidence)

    report = validate_transition({}, phase='pre-cutover')

    assert report['ready'] is False
    assert report['phase'] == 'pre-cutover'
    assert report['blockers']
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('published_status', [False, True])
def test_cli_phase_selection_is_read_only_and_never_bootstraps_status(tmp_path, capsys, phase, published_status):
    evidence = _phase_evidence(phase)
    if published_status:
        evidence['cloud_review']['status'] = _evidence()['cloud_review']['status']
    else:
        evidence['cloud_review'].pop('status', None)
    path = tmp_path / 'evidence.json'
    raw = json.dumps(evidence)
    path.write_text(raw)
    expected_ready = phase == 'pre-cutover' or published_status

    result = cli_main(['--phase', phase, str(path)])

    assert result == (0 if expected_ready else 1)
    assert json.loads(capsys.readouterr().out) == {
        'ready': expected_ready, 'phase': phase,
        'blockers': [] if expected_ready else ['cloud-review-status'],
    }
    assert path.read_text() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_cli_only_reads_evidence_and_returns_blocked_for_missing_fields(tmp_path, capsys):
    path = tmp_path / 'evidence.json'
    path.write_text('{}')

    result = cli_main(['--phase', 'pre-cutover', str(path)])
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report['ready'] is False
    assert path.read_text() == '{}'


def test_cli_rejects_duplicate_evidence_keys(tmp_path, capsys):
    path = tmp_path / 'evidence.json'
    path.write_text('{"repository": {}, "repository": {}}')

    result = cli_main(['--phase', 'pre-cutover', str(path)])
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report == {'ready': False, 'phase': 'pre-cutover', 'blockers': ['invalid-evidence']}
