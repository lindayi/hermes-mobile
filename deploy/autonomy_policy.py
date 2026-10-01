"""Read-only validation of the repository's conditional premerge gate transition."""

import re

REPOSITORY = 'lindayi/hermes-mobile'
REPOSITORY_ID = 1399942965
OWNER_ID = 5164171
COPILOT_REVIEWER_ID = 175728472
COPILOT_AGENT_ID = 198982749
WORKFLOW_ID = 372155405
WORKFLOW_PATH = '.github/workflows/ci.yml'
REF = 'refs/heads/main'
HOSTED_JOBS = frozenset({'build', 'checks', 'js', 'python', 'browser', 'native'})
RUN_JOBS = frozenset({
    'build', 'checks', 'js', 'python (0)', 'python (1)',
    'browser (0)', 'browser (1)', 'browser (2)', 'browser (3)',
    'native', 'source-ci', 'attest',
})
REQUIRED_CHECKS = {
    'pre-cutover': {
        'source-ci': None, 'integration-tests': None, 'agent-review': None, 'issue-link': 15368,
    },
    'staging': {
        'source-ci': 15368, 'integration-tests': None, 'agent-review': None,
        'issue-link': 15368, 'cloud-review': None,
    },
    'post-cutover': {'source-ci': 15368, 'issue-link': 15368, 'cloud-review': None},
}
REQUIRED_FILES = (
    WORKFLOW_PATH,
    '.github/native-tests.json',
    '.github/host-tests.json',
    '.github/actions/native-test-environment/action.yml',
    '.github/native-runtime.json',
    'deploy/release_artifact.py',
    'deploy/self_deploy.py',
    'deploy/ci_selection.py',
    'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py',
    'deploy/cloud_coordinator.py',
)
SOURCE_FINGERPRINTS = {
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
    'deploy/cloud_coordinator.py': 'bd5513b06b6e9539b4224c2ec83a8a4769fe6f94c5526b377b5bac09ebb9d134',
}
SOURCE_BLOCKERS = {
    WORKFLOW_PATH: 'hosted-workflow-contract',
    '.github/native-tests.json': 'installed-host-gate',
    '.github/host-tests.json': 'installed-host-gate',
    '.github/actions/native-test-environment/action.yml': 'native-job-contract',
    '.github/native-runtime.json': 'native-job-contract',
    'deploy/release_artifact.py': 'release-artifact-provenance',
    'deploy/self_deploy.py': 'installed-host-gate',
    'deploy/ci_selection.py': 'installed-host-gate',
    'scripts/ci_tests.py': 'installed-host-gate',
    'scripts/prepare_native_test_runtime.py': 'native-job-contract',
    'deploy/cloud_coordinator.py': 'coordinator-review-contract',
}
SOURCE_BASELINES = {
    'main': '66a64245b6c9c632d5ca4087e3d1e4e4fa2a4e83',
    'deploy/cloud_coordinator.py': '403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83',
}
SHA_RE = re.compile(r'[0-9a-f]{40}\Z')
HEX_RE = re.compile(r'[0-9a-f]{64}\Z')


def _static_contracts(files, blockers):
    if set(files) != set(REQUIRED_FILES):
        if set(REQUIRED_FILES) - set(files):
            blockers.add('main-source-missing')
        if set(files) - set(REQUIRED_FILES):
            blockers.add('main-source-invalid')
    for name in REQUIRED_FILES:
        digest = files.get(name)
        if not isinstance(digest, str) or HEX_RE.fullmatch(digest) is None:
            blockers.add('main-source-invalid' if name in files else 'main-source-missing')
        elif digest != SOURCE_FINGERPRINTS[name]:
            blockers.add(SOURCE_BLOCKERS[name])


def _valid_sha(value):
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _check_identity(evidence, blockers):
    repository = evidence.get('repository')
    if (not isinstance(repository, dict) or repository.get('id') != REPOSITORY_ID
            or repository.get('full_name') != REPOSITORY):
        blockers.add('repository-identity')


def _check_protection(evidence, phase, blockers):
    policy = evidence.get('protection')
    expected = REQUIRED_CHECKS[phase]
    if (not isinstance(policy, dict) or policy.get('repository_id') != REPOSITORY_ID
            or policy.get('repository') != REPOSITORY or policy.get('branch') != 'main'
            or policy.get('complete') is not True or policy.get('strict') is not True
            or policy.get('enforce_admins') is not True
            or policy.get('required_conversation_resolution') is not True):
        blockers.add('branch-protection')
        return
    checks = policy.get('required_checks')
    actual = {}
    if isinstance(checks, list):
        for check in checks:
            if (not isinstance(check, dict) or not isinstance(check.get('context'), str)
                    or 'app_id' not in check
                    or (check['app_id'] is not None and type(check['app_id']) is not int)):
                blockers.add('required-check-policy')
                return
            context = check['context']
            if context in actual:
                blockers.add('required-check-policy')
                return
            actual[context] = check.get('app_id')
    # The only pre-cutover variant is an already Actions-bound source-ci.
    if actual != expected and not (
        phase == 'pre-cutover' and actual == expected | {'source-ci': 15368}
    ):
        blockers.add('required-check-policy')


def _check_source_run(evidence, sha, blockers):
    source = evidence.get('source_ci')
    if not isinstance(source, dict):
        blockers.add('source-ci-evidence')
        return
    expected = {
        'repository': REPOSITORY, 'repository_id': REPOSITORY_ID,
        'head_repository_id': REPOSITORY_ID, 'workflow_id': WORKFLOW_ID,
        'workflow_path': WORKFLOW_PATH, 'event': 'push', 'branch': 'main',
        'head_sha': sha, 'status': 'completed', 'conclusion': 'success',
    }
    if any(source.get(key) != value for key, value in expected.items()):
        blockers.add('source-ci-evidence')
    run_id, attempt = source.get('run_id'), source.get('run_attempt')
    if type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1:
        blockers.add('source-ci-evidence')
        return
    jobs = source.get('jobs')
    names = [job.get('name') for job in jobs] if isinstance(jobs, list) and all(isinstance(job, dict) for job in jobs) else []
    if (source.get('jobs_complete') is not True or set(names) != RUN_JOBS or len(names) != len(set(names))
            or len(jobs or []) != len(RUN_JOBS)):
        blockers.add('source-ci-jobs')
    else:
        ids = []
        for job in jobs:
            job_id = job.get('id')
            ids.append(job_id)
            if (type(job_id) is not int or job_id < 1 or job.get('run_id') != run_id
                    or job.get('run_attempt') != attempt or job.get('head_sha') != sha
                    or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
                blockers.add('source-ci-jobs')
                break
        if len(ids) != len(set(ids)):
            blockers.add('source-ci-jobs')
    artifact = source.get('artifact')
    if not isinstance(artifact, dict):
        blockers.add('release-artifact-evidence')
        return
    if (artifact.get('name') != f'release-{run_id}-{attempt}'
            or artifact.get('run_id') != run_id or artifact.get('run_attempt') != attempt
            or artifact.get('head_sha') != sha
            or artifact.get('repository_id') != REPOSITORY_ID
            or artifact.get('head_repository_id') != REPOSITORY_ID
            or artifact.get('expired') is not False
            or type(artifact.get('size_bytes')) is not int or artifact.get('size_bytes', 0) <= 0
            or not isinstance(artifact.get('sha256'), str) or HEX_RE.fullmatch(artifact['sha256']) is None):
        blockers.add('release-artifact-evidence')
    certificate = artifact.get('attestation')
    identity = f'https://github.com/{REPOSITORY}/{WORKFLOW_PATH}@{REF}'
    if (not isinstance(certificate, dict) or certificate.get('verified') is not True
            or certificate.get('repository') != REPOSITORY
            or certificate.get('repository_id') != str(REPOSITORY_ID)
            or certificate.get('source_ref') != REF
            or certificate.get('source_sha') != sha or certificate.get('signer_sha') != sha
            or certificate.get('signer_identity') != identity
            or certificate.get('run_invocation') !=
            f'https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}'
            or certificate.get('runner_environment') != 'github-hosted'
            or certificate.get('trigger') != 'push'
            or certificate.get('issuer') != 'https://token.actions.githubusercontent.com'):
        blockers.add('release-attestation')


def _check_review(evidence, main_sha, phase, blockers):
    review = evidence.get('cloud_review')
    if not isinstance(review, dict):
        blockers.add('cloud-review-evidence')
        return
    head = review.get('head_sha')
    if (review.get('repository_id') != REPOSITORY_ID or review.get('base_branch') != 'main'
            or review.get('base_sha') != main_sha or not _valid_sha(head)
            or review.get('state') != 'open' or review.get('draft') is not False
            or review.get('reviews_complete') is not True
            or review.get('threads_complete') is not True):
        blockers.add('cloud-review-evidence')
        return
    reviews, threads = review.get('reviews'), review.get('threads')
    if not isinstance(reviews, list) or not isinstance(threads, list):
        blockers.add('cloud-review-evidence')
        return
    authored = [
        item for item in reviews
        if isinstance(item, dict) and isinstance(item.get('user'), dict)
        and type(item['user'].get('id')) is int
        and item['user']['id'] == COPILOT_REVIEWER_ID
    ]
    latest = max(authored, key=lambda item: str(item.get('submitted_at') or ''), default=None)
    if latest is None or latest.get('state') != 'APPROVED' or latest.get('commit_id') != head:
        blockers.add('cloud-review-approval')
    if any(not isinstance(thread, dict) or thread.get('isResolved') is not True
           or thread.get('comments_complete') is not True for thread in threads):
        blockers.add('cloud-review-threads')
    status = review.get('status')
    if (phase != 'pre-cutover' or 'status' in review) and (
            not isinstance(status, dict) or status.get('context') != 'cloud-review'
            or status.get('state') != 'success' or status.get('head_sha') != head
            or status.get('creator_id') != OWNER_ID):
        blockers.add('cloud-review-status')

    change = review.get('change')
    if (not isinstance(change, dict) or change.get('head_sha') != head
            or change.get('files_complete') is not True
            or type(change.get('sensitive')) is not bool):
        blockers.add('change-scope-evidence')
    elif change['sensitive']:
        authorization = change.get('owner_authorization')
        targeted = change.get('targeted_review')
        if (not isinstance(authorization, dict) or authorization.get('actor_id') != OWNER_ID
                or authorization.get('head_sha') != head or authorization.get('state') != 'approved'
                or not isinstance(targeted, dict) or type(targeted.get('reviewer_id')) is not int
                or targeted.get('reviewer_id') == OWNER_ID or targeted.get('head_sha') != head
                or targeted.get('state') not in ('COMMENTED', 'APPROVED')):
            blockers.add('sensitive-review-authorization')


def validate_transition(evidence, *, phase):
    """Validate injected read-only GitHub evidence and main-source contracts; perform no I/O."""
    blockers = set()
    if not isinstance(phase, str) or phase not in REQUIRED_CHECKS:
        return {'ready': False, 'phase': phase, 'blockers': ['invalid-phase']}
    if not isinstance(evidence, dict):
        return {'ready': False, 'phase': phase, 'blockers': ['invalid-evidence']}
    _check_identity(evidence, blockers)
    main = evidence.get('main')
    if (not isinstance(main, dict) or main.get('repository_id') != REPOSITORY_ID
            or main.get('ref') != REF or main.get('current') is not True
            or not _valid_sha(main.get('sha')) or main.get('snapshot_complete') is not True
            or not isinstance(main.get('files'), dict)):
        blockers.add('current-main-snapshot')
        sha, files = '', {}
    else:
        sha, files = main['sha'], main['files']
    try:
        _static_contracts(files, blockers)
        _check_protection(evidence, phase, blockers)
        if sha:
            _check_source_run(evidence, sha, blockers)
            _check_review(evidence, sha, phase, blockers)
    except (AttributeError, IndexError, KeyError, RecursionError, TypeError, ValueError):
        blockers.add('malformed-evidence')
    return {'ready': not blockers, 'phase': phase, 'blockers': sorted(blockers)}
