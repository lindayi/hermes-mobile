"""Read-only validation of the repository's conditional premerge gate transition."""

import re
from datetime import datetime

REPOSITORY = 'lindayi/hermes-mobile'
REPOSITORY_ID = 1399942965
OWNER_ID = 5164171
COPILOT_REVIEWER_ID = 175728472
COPILOT_AGENT_ID = 198982749
WORKFLOW_ID = 372155405
WORKFLOW_PATH = '.github/workflows/ci.yml'
REF = 'refs/heads/main'
SOURCE_CONTROL_ROOTS = frozenset({
    'deploy/release_artifact.py', 'deploy/self_deploy.py', 'deploy/ci_selection.py',
    'deploy/observe_release.py', 'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py', 'scripts/test.py',
})
SOURCE_CONTROL_PYTHON_FILES = frozenset({
    'backend/app.py', 'backend/auth.py', 'backend/auth_store.py',
    'backend/background_delivery.py', 'backend/catalog_search.py', 'backend/chat_snapshot.py',
    'backend/configuration.py', 'backend/model_controls.py', 'backend/native_api_service.py',
    'backend/native_notifications.py',
    'backend/context_compression_presentation.py', 'backend/delivery.py',
    'backend/hermes_client.py', 'backend/jobs.py', 'backend/native_catalog.py',
    'backend/notification_policy.py', 'backend/notifications.py',
    'backend/operational_notifications.py', 'backend/orchestration.py',
    'backend/profiles.py', 'backend/public_commentary.py', 'backend/request_notifications.py',
    'backend/runs.py', 'backend/runtime_binding.py', 'backend/runtime_notice_presentation.py',
    'backend/session_deletion.py', 'backend/session_telemetry.py',
    'backend/session_visibility.py', 'backend/steering.py',
    'backend/task_reminder_presentation.py', 'backend/tool_presentation.py',
    'deploy/assets.py', 'deploy/backup.py', 'deploy/ci_selection.py', 'deploy/frontend_release.py',
    'deploy/git_source.py', 'deploy/install_core.py', 'deploy/native_controls_release.py',
    'deploy/native_notification_release.py', 'deploy/native_readiness.py', 'deploy/observe_release.py', 'deploy/public_http.py',
    'deploy/release_artifact.py', 'deploy/self_deploy.py', 'deploy/test_workspace.py',
    'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py', 'scripts/test.py',
})
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
    '.github/workflows/issue-link.yml',
    '.github/native-tests.json',
    '.github/host-tests.json',
    '.github/actions/test-environment/action.yml',
    '.github/actions/native-test-environment/action.yml',
    '.github/native-runtime.json',
    'requirements.lock',
    'package.json',
    'package-lock.json',
    'deploy/release_artifact.py',
    'deploy/self_deploy.py',
    'deploy/ci_selection.py',
    'deploy/test_workspace.py',
    'deploy/assets.py',
    'deploy/install_core.py',
    'deploy/frontend_release.py',
    'deploy/git_source.py',
    'deploy/native_controls_release.py',
    'deploy/native_readiness.py',
    'deploy/observe_release.py',
    'deploy/public_http.py',
    'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py',
    'scripts/test.py',
    'patches/native-compat.patch',
    'patches/cron-delivery.patch',
    'patches/native-compat-baseline.json',
    'patches/cron-delivery-baseline.json',
    'deploy/cloud_coordinator.py',
    'deploy/review_evidence.py',
    'deploy/task_receipts.py',
    'deploy/workflow_events.py',
    'deploy/workflow_lifecycle.py',
    'deploy/workflow_lifecycle_sources.py',
    'deploy/workflow_notifications.py',
    'backend/app.py',
    'backend/auth.py',
    'backend/auth_store.py',
    'backend/background_delivery.py',
    'backend/catalog_search.py',
    'backend/chat_snapshot.py',
    'backend/configuration.py',
    'backend/context_compression_presentation.py',
    'backend/delivery.py',
    'backend/hermes_client.py',
    'backend/jobs.py',
    'backend/model_controls.py',
    'backend/native_catalog.py',
    'backend/native_api_service.py',
    'backend/notification_policy.py',
    'backend/notifications.py',
    'backend/operational_notifications.py',
    'backend/orchestration.py',
    'backend/profiles.py',
    'backend/public_commentary.py',
    'backend/request_notifications.py',
    'backend/runs.py',
    'backend/runtime_binding.py',
    'backend/runtime_notice_presentation.py',
    'backend/session_deletion.py',
    'backend/session_telemetry.py',
    'backend/session_visibility.py',
    'backend/steering.py',
    'backend/task_reminder_presentation.py',
    'backend/tool_presentation.py',
    'deploy/backup.py',
    'backend/native_notifications.py',
    'deploy/native_notification_release.py',
)
SOURCE_FINGERPRINTS = {
    '.github/workflows/ci.yml': '39110e6f940fc59ef3aec9846f07616a86a2e07335d2aaed6c6cd0ab444ff195',
    '.github/workflows/issue-link.yml': '5569875b9fc45d686f857712bc035d75a100fcfc4f3862f48b0d8bd045eca415',
    '.github/native-tests.json': 'b97ddb088e595197cf65d97b0b4af89f4f83f5c4ad25463c566df7099817209f',
    '.github/host-tests.json': '8af5fc30188f81ba95d3d7c11ba6801f52795175c2cac7f634424f906947ce5a',
    '.github/actions/test-environment/action.yml': '638ed56955c8c8202fdd41640abe3be8b8065129e2965b8dc67b69f024017604',
    '.github/actions/native-test-environment/action.yml': 'b1e5af03a4aa397f585e541805b4a292d1b3529090d54932b8945b6743ecd993',
    '.github/native-runtime.json': '705351eff7420cf3cbd3f91f3edc605feb304eba86f99e5a94902e89d3fb4d88',
    'requirements.lock': '1e912f6160c68f3ebb56a51da95af013875d0b4690434fe52fcd3f6b115de095',
    'package.json': 'a341a3a23a9425728ba38b83e5d7a4ab983c6f951f14667ba3cd69a6e35f13d3',
    'package-lock.json': '63199915d106fefd775451eb7d4aed9a3c2d04ca6f670ef9f27ba0cd6098218a',
    'deploy/assets.py': '0b5fee70ac71f61384b6501a40df9b7125c080fffa9930975ec2542553dffcc1',
    'deploy/frontend_release.py': '7749afb862a515fc673712b11278145adfb3d39ba2d012a34ecea257399eade3',
    'deploy/git_source.py': 'c69c7c5a45bc3a16ab26996872c258cc352cf2ec56c19d6256595e18ac713d63',
    'deploy/install_core.py': '2f60fbde34c02486450608fd844c3f9a0bf123a014849d4d992e5f61fd873dfc',
    'deploy/native_controls_release.py': 'a2d00ebe7fa8add88afdeda28599b47e68f2eaab6935a8b2c9eaa46f585a11fe',
    'deploy/native_readiness.py': 'f0556deb16fe9154048fdd0bc4a24fd3be947d52e287fee418a0e4d328f8183b',
    'deploy/observe_release.py': '49d784af17d0807c95b460b2a285254f0a616361c390afa69b3cc0345a56b498',
    'deploy/public_http.py': 'dd352b8d0295f242f4a5d31a55eaaec523ef8405601461d4ab5fa8dc6dc302e7',
    'deploy/release_artifact.py': '8bb1e62a1a4cb1a0239e05ab3d4b54c7d2896f2e1a09125ece5e4c36a44fc94e',
    'deploy/self_deploy.py': 'c8b9febf5e73c22de2aebbbf6ecb63e08597f58ea7ff5eede979d4be51783550',
    'deploy/ci_selection.py': '07493f74bc4b932e26342e0d27fee8c3c38601af8211e0a950920935173b2f16',
    'deploy/test_workspace.py': 'baeb1103608ff15db3903677fa8ec9c80c9c8b9a0246ce18ada27bf7c04a486a',
    'scripts/ci_tests.py': '6ed905a90720fb17226472a0453fb762395424b8370df474fed4536827b398d6',
    'scripts/prepare_native_test_runtime.py': '798195e6d9b284bae69cb6e27cf8dc7ad5ffc6dce62939b137fb6c6b370b58a8',
    'scripts/test.py': 'e6a53a5c0f7ff98f35b2efb2282cd65db65eacc2d94af9b07516ceaaffc4ec7c',
    'patches/native-compat.patch': '2add04e93a5ea74eeeb407dc9d5556bb38c1454b5c8bf307c3e8490e9b72dd32',
    'patches/cron-delivery.patch': '444af4887abcea020baaf8c8cfbf4679670d38cdc2fc302a97ad5c54d68fc1ff',
    'patches/native-compat-baseline.json': '2daf996adbcab86d8ad5f1a3e15bd5ea26134ea116662b451cd09429c3ebc862',
    'patches/cron-delivery-baseline.json': '988ff4bda29998ce0f0743950e491f86e2b9d434e9da57c40aee5a5e06895af1',
    'deploy/cloud_coordinator.py': 'bd5513b06b6e9539b4224c2ec83a8a4769fe6f94c5526b377b5bac09ebb9d134',
    # Pending issue #39 coordinator dependency candidate bytes; parent review only.
    'deploy/review_evidence.py': '82bc6edc915adad6f320199559850416caaa953c1739132d3b6ad7262ba03cb4',
    # Pending PR40 assembly candidates from incoming 80bf9e7. Historical source
    # review lineage: docs/autonomy-policy.md. Not assembled/operational approval;
    # existing pins and the unconditional pending-source-contract hold stay fixed.
    'deploy/task_receipts.py': '8ad9e60ec697de8135679b9110ed5d924057e0d8a59e731c67fceedec6525197',
    'deploy/workflow_events.py': '5234980515c0909d5170a3a9047766a0b355aa35372bedc2961b703afc37b9af',
    'deploy/workflow_lifecycle.py': '83643df2f642b6c949031e067968c0dd5a06e4c3230ab1b7f7bdb02b3be8c626',
    'deploy/workflow_lifecycle_sources.py': 'a6be88f79862966a6e09f10f9eee2e7f6c8957ededeb1005f435b70759a0e5a6',
    'deploy/workflow_notifications.py': '4684a5db2229a9491a99af04b6437ff215ffcbd6900d53c653fa7e897055d87d',
    'backend/configuration.py': '03d4fb191ba53f04df25b935ae03d3f5cce9ba513c89938dbb809601dae9e636',
    'backend/model_controls.py': 'a6276a114d770f8a52677ae11a48011b586daf7cd047dd667099e4a6251ccb6b',
    'backend/native_api_service.py': 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/notifications.py': '7d1fe9e4e2569f9596df4ded464c2264cd9e404cef715e88652b790e3ec9887c',
    'backend/runs.py': 'f8ee1d2547a794f277c5dc6b52e491e17ada1bda65acba9f3083eee8522de04e',
    'backend/app.py': 'fad986aa3fd601041e9f2926dd8c128292e796ca75dac9613c3c5592128e9e67',
    'backend/auth.py': '411529a1d53bdcd01eae4bf5e35c44d3d5099e4dc9e77eb9dbbe67df6433f335',
    'backend/auth_store.py': '8827856de744de03648924b0ff83011bdb70417781620724acd84078181c193e',
    'backend/background_delivery.py': '2da2bdd89d27f18beee0e0099d1bb39b325a9f70dcba099d55ceb3dea2e88629',
    'backend/catalog_search.py': 'f19c8f94816e6c68681c8db5aa7f7c21cedefb6e3ddaa3fb03612a61e5b2c433',
    'backend/chat_snapshot.py': 'f46435b77435deb6eaf419c46b5175247ae6f507ad2dd9d9f8c6f3c89ba9d99e',
    'backend/context_compression_presentation.py': 'dea9ea4a45e4a61a6df123b8622aa0ee0a5ee628c80d1644dc20f320b6daa729',
    'backend/delivery.py': 'c241a5bf745011a7a7820313da6db688a5446703ae42c8a97c77f96ce05ff18f',
    'backend/hermes_client.py': '20a0a6f48e42647e1536efb7395ebe2d24c1525c2bf36a53a65ccec45fb5a733',
    'backend/jobs.py': 'fd9c4a2ac2c2f292c4616fd7dd04281b4c6a5753e9c432756345d85d175eee5e',
    'backend/native_catalog.py': '8c161ec64446b9297cfde860ac1132df7cd388549bca6b6f6481ba676d6a1f5f',
    'backend/notification_policy.py': '69c7c6807210dbf9300cd25c5ba14604be75730d06f41879074208d62655684d',
    'backend/operational_notifications.py': '9edbc496bb931a02096bf820d984a4032107e003924492e1120684a0f5cdad31',
    'backend/orchestration.py': 'a3e3d88bf7e2918b63a9ad09e9715099878f13ac964b1082ca173669bee6cc9b',
    'backend/profiles.py': '8bf7681b69f6273d6a6f86a9835cc7e2fc0bd6f31fdb59e7934bd8dbb7515f40',
    'backend/public_commentary.py': 'a52a81b0852fdcff13057bcd25ee39836abdc2d8ab658b90a9db7197ca0cf57a',
    'backend/request_notifications.py': '1136806183f7b3cbb51b65691fc5c26b41f22c2d06098d1440bb3c42a869ede8',
    'backend/runtime_binding.py': '96912dfc875807d7f44e520caac33e408ca4897f021af68b55bf8eb5ef35c072',
    'backend/runtime_notice_presentation.py': '117649d3ef0779b08d5c540fdc88add671180a35e90d894e116f5b5bbd6cf34c',
    'backend/session_deletion.py': '44a9ef22524a7bbfc87a6a855583b1e1b88affbc2c4961c2c2efff9efc3f9d71',
    'backend/session_telemetry.py': '74141c1f154e4a7fb10d3eb25d5676b894e13b545be40335d8960222506bf825',
    'backend/session_visibility.py': 'e6332e0104c82cb871627078145d36006e7d681956c69ab73e968448b40c50c8',
    'backend/steering.py': '3bdd2134e8617d9b76f2431b4b1d07fdf9bd609cedd9587062b05fb8516b8fdd',
    'backend/task_reminder_presentation.py': '549a29784ef6f778ef55658d5a05961fed3919579faeeba567545004d7e9bcf3',
    'backend/tool_presentation.py': '4cfd8d468f75213d3aee3f8ab3c9f9409bb3b97e50a2aa05492181b49a1b474f',
    'deploy/backup.py': '3cbc5ace1c5110ded4eefe1298a2d6963280da9e7002d2cb092b2f66d25a6201',
    'backend/native_notifications.py': '0159fbdd02705469853f51be7bb32479ea9e2fa0d6fdbc6789253d3b3c1c85fe',
    # Pending PR25 candidate bytes, absent from the main baseline; parent review only.
    'deploy/native_notification_release.py': '364f5856f31a07117274a6855a0af573e85d4d197770188b6e9c734df4699582',
}
SOURCE_BLOCKERS = {
    WORKFLOW_PATH: 'hosted-workflow-contract',
    '.github/workflows/issue-link.yml': 'hosted-workflow-contract',
    '.github/native-tests.json': 'installed-host-gate',
    '.github/host-tests.json': 'installed-host-gate',
    '.github/actions/test-environment/action.yml': 'hosted-workflow-contract',
    '.github/actions/native-test-environment/action.yml': 'native-job-contract',
    '.github/native-runtime.json': 'native-job-contract',
    'requirements.lock': 'hosted-workflow-contract',
    'package.json': 'hosted-workflow-contract',
    'package-lock.json': 'hosted-workflow-contract',
    'deploy/release_artifact.py': 'release-artifact-provenance',
    'deploy/self_deploy.py': 'installed-host-gate',
    'deploy/ci_selection.py': 'installed-host-gate',
    'deploy/test_workspace.py': 'execution-source-contract',
    'deploy/assets.py': 'release-artifact-provenance',
    'deploy/install_core.py': 'native-job-contract',
    'deploy/frontend_release.py': 'execution-source-contract',
    'deploy/git_source.py': 'installed-host-gate',
    'deploy/native_controls_release.py': 'installed-host-gate',
    'deploy/native_readiness.py': 'execution-source-contract',
    'deploy/observe_release.py': 'execution-source-contract',
    'deploy/public_http.py': 'installed-host-gate',
    'scripts/ci_tests.py': 'installed-host-gate',
    'scripts/prepare_native_test_runtime.py': 'native-job-contract',
    'scripts/test.py': 'execution-source-contract',
    'patches/native-compat.patch': 'native-job-contract',
    'patches/cron-delivery.patch': 'native-job-contract',
    'patches/native-compat-baseline.json': 'native-job-contract',
    'patches/cron-delivery-baseline.json': 'native-job-contract',
    'deploy/cloud_coordinator.py': 'coordinator-review-contract',
    'deploy/review_evidence.py': 'coordinator-review-contract',
    'deploy/task_receipts.py': 'coordinator-review-contract',
    'deploy/workflow_events.py': 'coordinator-review-contract',
    'deploy/workflow_lifecycle.py': 'coordinator-review-contract',
    'deploy/workflow_lifecycle_sources.py': 'coordinator-review-contract',
    'deploy/workflow_notifications.py': 'coordinator-review-contract',
    'backend/configuration.py': 'execution-source-contract',
    'backend/model_controls.py': 'execution-source-contract',
    'backend/native_api_service.py': 'execution-source-contract',
    'backend/notifications.py': 'execution-source-contract',
    'backend/runs.py': 'execution-source-contract',
    'backend/app.py': 'execution-source-contract',
    'backend/auth.py': 'execution-source-contract',
    'backend/auth_store.py': 'execution-source-contract',
    'backend/background_delivery.py': 'execution-source-contract',
    'backend/catalog_search.py': 'execution-source-contract',
    'backend/chat_snapshot.py': 'execution-source-contract',
    'backend/context_compression_presentation.py': 'execution-source-contract',
    'backend/delivery.py': 'execution-source-contract',
    'backend/hermes_client.py': 'execution-source-contract',
    'backend/jobs.py': 'execution-source-contract',
    'backend/native_catalog.py': 'execution-source-contract',
    'backend/notification_policy.py': 'execution-source-contract',
    'backend/operational_notifications.py': 'execution-source-contract',
    'backend/orchestration.py': 'execution-source-contract',
    'backend/profiles.py': 'execution-source-contract',
    'backend/public_commentary.py': 'execution-source-contract',
    'backend/request_notifications.py': 'execution-source-contract',
    'backend/runtime_binding.py': 'execution-source-contract',
    'backend/runtime_notice_presentation.py': 'execution-source-contract',
    'backend/session_deletion.py': 'execution-source-contract',
    'backend/session_telemetry.py': 'execution-source-contract',
    'backend/session_visibility.py': 'execution-source-contract',
    'backend/steering.py': 'execution-source-contract',
    'backend/task_reminder_presentation.py': 'execution-source-contract',
    'backend/tool_presentation.py': 'execution-source-contract',
    'deploy/backup.py': 'execution-source-contract',
    'backend/native_notifications.py': 'execution-source-contract',
    'deploy/native_notification_release.py': 'installed-host-gate',
}
SOURCE_BASELINES = {
    'main': 'b85c098857e7fb8229f47bd688d703bb677aeb34',
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


def _review_timestamp(submitted_at):
    """Use the same fail-closed timestamp semantics for both review roles."""
    try:
        timestamp = datetime.fromisoformat(submitted_at.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp


def _check_review(evidence, main_sha, phase, blockers):
    review = evidence.get('cloud_review')
    if not isinstance(review, dict):
        blockers.add('cloud-review-evidence')
        return
    head = review.get('head_sha')
    if (review.get('repository_id') != REPOSITORY_ID or review.get('base_branch') != 'main'
            or review.get('base_sha') != main_sha or not _valid_sha(head)
            or review.get('state') != 'open' or review.get('draft') is not False
            or type(review.get('pull_author_id')) is not int or review['pull_author_id'] < 1
            or review.get('reviews_complete') is not True
            or review.get('threads_complete') is not True):
        blockers.add('cloud-review-evidence')
        return
    reviews, threads = review.get('reviews'), review.get('threads')
    if not isinstance(reviews, list) or not isinstance(threads, list):
        blockers.add('cloud-review-evidence')
        return
    authored = []
    malformed_review = False
    for item in reviews:
        if not isinstance(item, dict):
            malformed_review = True
            continue
        user = item.get('user')
        if (not isinstance(user, dict) or type(user.get('id')) is not int
                or user['id'] < 1):
            malformed_review = True
            continue
        if user['id'] == COPILOT_REVIEWER_ID:
            authored.append(item)
    ordered = []
    review_ids = set()
    for item in authored:
        review_id, submitted_at = item.get('id'), item.get('submitted_at')
        if type(review_id) is not int or review_id < 1 or review_id in review_ids:
            malformed_review = True
            continue
        review_ids.add(review_id)
        timestamp = _review_timestamp(submitted_at)
        if timestamp is None:
            malformed_review = True
            continue
        ordered.append((timestamp, review_id, item))
    if malformed_review:
        blockers.add('cloud-review-approval')
    latest = max(ordered, key=lambda record: record[:2], default=(None, None, None))[2]
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
                or targeted['reviewer_id'] < 1
                or targeted['reviewer_id'] in {
                    OWNER_ID, review['pull_author_id'], COPILOT_AGENT_ID, COPILOT_REVIEWER_ID,
                }
                or targeted.get('head_sha') != head
                or targeted.get('state') not in ('COMMENTED', 'APPROVED')
                or type(targeted.get('review_id')) is not int or targeted['review_id'] < 1):
            blockers.add('sensitive-review-authorization')
        else:
            # Resolve by ID across the complete collection before checking its claims;
            # filtering by reviewer/head/state first could hide conflicting duplicates.
            matches = [
                item for item in reviews
                if isinstance(item, dict) and item.get('id') == targeted['review_id']
            ]
            if len(matches) != 1:
                blockers.add('sensitive-review-authorization')
            else:
                record = matches[0]
                user = record.get('user')
                if (type(record.get('id')) is not int or not isinstance(user, dict)
                        or type(user.get('id')) is not int
                        or user['id'] != targeted['reviewer_id']
                        or record.get('commit_id') != head
                        or record.get('state') != targeted['state']
                        or _review_timestamp(record.get('submitted_at')) is None):
                    blockers.add('sensitive-review-authorization')


def validate_transition(evidence, *, phase):
    """Validate injected read-only GitHub evidence and main-source contracts; perform no I/O."""
    blockers = set()
    if not isinstance(phase, str) or phase not in REQUIRED_CHECKS:
        return {'ready': False, 'phase': phase, 'blockers': ['invalid-phase']}
    if not isinstance(evidence, dict):
        return {'ready': False, 'phase': phase, 'blockers': ['invalid-evidence']}
    # Source-enforced hold in every phase, never an evidence-supplied opt-out.
    # Pinned coordinator 403ac3d lacks strict timestamp ordering and targeted
    # independent review for every sensitive head. Issue #26 activation requires
    # a reviewed merged replacement, deliberate pin updates, and a source change
    # to clear this hold; matching the known unsafe fingerprint is insufficient.
    blockers.add('pending-source-contract')
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
