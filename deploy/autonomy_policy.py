"""Read-only validation of the repository's conditional premerge gate transition."""

import re
from deploy.review_evidence import (
    current_independent_agent_review,
    latest_reviews,
    sensitive_review_authorized,
)

REPOSITORY = 'lindayi/hermes-mobile'
REPOSITORY_ID = 1399942965
OWNER_ID = 5164171
COPILOT_REVIEWER_ID = 175728472
COPILOT_AGENT_ID = 198982749
WORKFLOW_ID = 372155405
WORKFLOW_PATH = '.github/workflows/ci.yml'
REF = 'refs/heads/main'
AUTONOMY_LAUNCH_ROOTS = frozenset({
    'deploy/issue_starter.py', 'scripts/cloud_coordinator.py',
    'scripts/issue_starter.py', 'scripts/workflow_notifications.py',
})
AUTONOMY_LAUNCH_UNITS = frozenset({
    'deploy/hermes-mobile-coordinator.service', 'deploy/hermes-mobile-coordinator.timer',
    'deploy/hermes-mobile-issue-starter.service', 'deploy/hermes-mobile-issue-starter.timer',
    'deploy/hermes-workflow-notifications.service',
    'deploy/hermes-workflow-notifications.timer',
})
SOURCE_CONTROL_ROOTS = frozenset({
    'deploy/release_artifact.py', 'deploy/self_deploy.py', 'deploy/ci_selection.py',
    'deploy/observe_release.py', 'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py', 'scripts/test.py',
    *AUTONOMY_LAUNCH_ROOTS,
})
SOURCE_CONTROL_PYTHON_FILES = frozenset({
    'backend/app.py', 'backend/auth.py', 'backend/auth_store.py',
    'backend/background_delivery.py', 'backend/catalog_search.py', 'backend/chat_snapshot.py',
    'backend/clarifications.py',
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
    'deploy/cloud_coordinator.py', 'deploy/issue_starter.py', 'deploy/review_evidence.py',
    'deploy/pull_handoff_binding.py',
    'deploy/task_receipts.py', 'deploy/workflow_events.py', 'deploy/workflow_lifecycle.py',
    'deploy/workflow_lifecycle_sources.py', 'deploy/workflow_notifications.py',
    'scripts/cloud_coordinator.py', 'scripts/issue_starter.py',
    'scripts/workflow_notifications.py',
    'scripts/ci_tests.py',
    'scripts/prepare_native_test_runtime.py', 'scripts/test.py',
})
HOSTED_JOBS = frozenset({'build', 'checks', 'js', 'python', 'browser', 'native'})
RUN_JOBS = frozenset({
    'build', 'checks', 'js', 'python (0)', 'python (1)',
    'browser (0)', 'browser (1)', 'browser (2)', 'browser (3)',
    'native', 'source-ci', 'integration-tests', 'attest',
})
REQUIRED_CHECKS = {
    phase: {
        'source-ci': 15368, 'integration-tests': None,
        'agent-review': None, 'issue-link': 15368,
    }
    for phase in ('pre-cutover', 'staging', 'post-cutover')
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
    'deploy/issue_starter.py',
    'deploy/pull_handoff_binding.py',
    'deploy/review_evidence.py',
    'deploy/task_receipts.py',
    'deploy/workflow_events.py',
    'deploy/workflow_lifecycle.py',
    'deploy/workflow_lifecycle_sources.py',
    'deploy/workflow_notifications.py',
    'scripts/cloud_coordinator.py',
    'scripts/issue_starter.py',
    'scripts/workflow_notifications.py',
    'deploy/hermes-mobile-coordinator.service',
    'deploy/hermes-mobile-coordinator.timer',
    'deploy/hermes-mobile-issue-starter.service',
    'deploy/hermes-mobile-issue-starter.timer',
    'deploy/hermes-workflow-notifications.service',
    'deploy/hermes-workflow-notifications.timer',
    'backend/app.py',
    'backend/auth.py',
    'backend/auth_store.py',
    'backend/background_delivery.py',
    'backend/catalog_search.py',
    'backend/chat_snapshot.py',
    'backend/clarifications.py',
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
    '.github/workflows/ci.yml': '5a1b1a694d286d5f8a1a4188802af7e8f6ab46855d278224d9f248cccec844d9',
    '.github/workflows/issue-link.yml': 'dd722de884fba6c9d613a72baee43f3b81d2d6816954f6a8e77d972b0fd1eafc',
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
    'deploy/native_controls_release.py': '905e5b0f163e92383b7a72d9215a5ebd903751eca908500a48abe6251c1a9fb9',
    'deploy/native_readiness.py': 'f0556deb16fe9154048fdd0bc4a24fd3be947d52e287fee418a0e4d328f8183b',
    'deploy/observe_release.py': 'bf01500fb253f7d41ce49d375bba63a9e4d80569eb376a6aeae50f56cf71923f',
    'deploy/public_http.py': 'a8d073c00574718c0662973f8f4002e77165166034935c71e25d8177b8e5a295',
    'deploy/release_artifact.py': 'ef47db3b7fa3e4805488ea4c941e770f26b6256842ad26ffb210476e5f86e721',
    'deploy/self_deploy.py': '1a72fcf9ac6018a449bcac7bb76764af3f144817e8f6e764eb49aa5a95241363',
    'deploy/ci_selection.py': '07493f74bc4b932e26342e0d27fee8c3c38601af8211e0a950920935173b2f16',
    'deploy/test_workspace.py': 'baeb1103608ff15db3903677fa8ec9c80c9c8b9a0246ce18ada27bf7c04a486a',
    'scripts/ci_tests.py': '6ed905a90720fb17226472a0453fb762395424b8370df474fed4536827b398d6',
    'scripts/prepare_native_test_runtime.py': '798195e6d9b284bae69cb6e27cf8dc7ad5ffc6dce62939b137fb6c6b370b58a8',
    'scripts/test.py': 'e6a53a5c0f7ff98f35b2efb2282cd65db65eacc2d94af9b07516ceaaffc4ec7c',
    'patches/native-compat.patch': '2add04e93a5ea74eeeb407dc9d5556bb38c1454b5c8bf307c3e8490e9b72dd32',
    'patches/cron-delivery.patch': '444af4887abcea020baaf8c8cfbf4679670d38cdc2fc302a97ad5c54d68fc1ff',
    'patches/native-compat-baseline.json': '2daf996adbcab86d8ad5f1a3e15bd5ea26134ea116662b451cd09429c3ebc862',
    'patches/cron-delivery-baseline.json': '988ff4bda29998ce0f0743950e491f86e2b9d434e9da57c40aee5a5e06895af1',
    # Issue #87 bounded-repair candidate; source consistency only.
    'deploy/cloud_coordinator.py': 'b0e30ca62566691f3847726125aa42f07b5fcc1bf3f2b40b9b5afb8c8d842ee5',
    # Issue #43 launch/authority candidates; final assembled review remains required.
    # PR57 paired admission fence and issue #63 fail-closed recovery boundary.
    'deploy/issue_starter.py': '6c4f645544119c3b317548ef01391edcce37af0bac02c2ed69cbdf714d97419a',
    'deploy/pull_handoff_binding.py': '3e279674d80426c017bd39b9ebf7777af4f92b0f6ec03fc5d8b8398c0f98898b',
    'deploy/review_evidence.py': 'bc2bea2e4cd17ac28ed96cc5d421f62f63e7ef14ee9bdec5045cf6f26bb8f290',
    # Accepted PR29/PR40/PR42 source lineage retained from main5316; see
    # docs/autonomy-policy.md. Not final issue43 assembly or operational approval.
    # Issue #65 producer-only overlay; historical receipt hashes remain documented.
    # Issue #79 recovery candidate bytes; not independent acceptance or activation.
    'deploy/task_receipts.py': 'bfc903eddf33a7b8e4b17ccafd8112af70611ce472a44520ed4fe5842845a1c7',
    # Issue #87 bounded-repair lifecycle overlay; earlier hashes remain historical.
    'deploy/workflow_events.py': '63d4066774e276d668d493a69d55e5cb03e4e13c02ab52d1a6dd726098d9b79e',
    'deploy/workflow_lifecycle.py': 'f8ecf4fa881d907d41a3f8482fa60f1166591dd51e75108aaa4d31f4f2df65b0',
    'deploy/workflow_lifecycle_sources.py': 'dfff5b5ec33b9bd1756a67150827541ea86193b87e6de5b5c3a965f02f19b837',
    'deploy/workflow_notifications.py': 'c599d19bc1f1b1976429d7e5ca834eade37e35c718b4c389ceaef4ad47ecb1ad',
    'scripts/cloud_coordinator.py': '992d448a9ddfdd75abdab14fc48ad0dbff98e1c93a943f483d0788ef5ca57790',
    'scripts/issue_starter.py': '09008da255c56f370f73af6d2f8e8587f6a999c76a99e1bd798e8ac4bbd927f1',
    'scripts/workflow_notifications.py': '03731f93e1aa3ce297107ea3d0126e990c72d88401dda04e4499f0a7f505b55f',
    'deploy/hermes-mobile-coordinator.service': '49d6ebb6e24b4c0a06af3d10e6e2cce11dd6af7ee8056c99268058c2f6f2cab3',
    'deploy/hermes-mobile-coordinator.timer': 'ffa239c67b492b5a361b823c754d5f204eb4efaa2c69f7df99df577e12d2a6c1',
    # Issue #58 user-unit replacement; source consistency, not activation evidence.
    'deploy/hermes-workflow-notifications.service': '934effd6540b6a6ed026fcdac2dda6ebf176792e583c6738dc7b72dc737f199e',
    'deploy/hermes-workflow-notifications.timer': '627463b4dd06eb72f7fecc88a79dad29ec8ab4b5514b29c62c131cdbd2963cc8',
    'deploy/hermes-mobile-issue-starter.service': '1711c53ee7b7e4f86b435d3e19ade679b20af960176c53125f14eee3a0dcdb69',
    'deploy/hermes-mobile-issue-starter.timer': '848e07d3f30f5d4c7ad881ca9bdeddd6fbf9eeb8ae1fb68ba0feb5b4425e5e92',
    'backend/configuration.py': '03d4fb191ba53f04df25b935ae03d3f5cce9ba513c89938dbb809601dae9e636',
    'backend/clarifications.py': '6a6f042beb98881a482efdd85c20555d88a375156a024481ab18a0d5c80e94fd',
    'backend/model_controls.py': 'c35c6e7ed5c92715ef1bd5af236268d1ef080bbcef2a4069a647dbd04bc4e217',
    'backend/native_api_service.py': 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',  # gitleaks:allow
    'backend/notifications.py': '7d1fe9e4e2569f9596df4ded464c2264cd9e404cef715e88652b790e3ec9887c',
    'backend/runs.py': 'b2648c509c520189da024a7338b5556b64876e9542dd8b608c9033d1b2f42d29',
    # Accepted PR45 naming bytes in this assembly; no naming-source rewrite.
    'backend/app.py': '3fbab2ca8c8d47dffdd1775d67026fbd537f7350aa6ec68f830cc9be87c266c5',
    'backend/auth.py': '411529a1d53bdcd01eae4bf5e35c44d3d5099e4dc9e77eb9dbbe67df6433f335',  # gitleaks:allow
    'backend/auth_store.py': '8827856de744de03648924b0ff83011bdb70417781620724acd84078181c193e',  # gitleaks:allow
    'backend/background_delivery.py': '2da2bdd89d27f18beee0e0099d1bb39b325a9f70dcba099d55ceb3dea2e88629',
    'backend/catalog_search.py': 'f19c8f94816e6c68681c8db5aa7f7c21cedefb6e3ddaa3fb03612a61e5b2c433',
    'backend/chat_snapshot.py': 'f46435b77435deb6eaf419c46b5175247ae6f507ad2dd9d9f8c6f3c89ba9d99e',
    'backend/context_compression_presentation.py': 'dea9ea4a45e4a61a6df123b8622aa0ee0a5ee628c80d1644dc20f320b6daa729',
    'backend/delivery.py': 'c241a5bf745011a7a7820313da6db688a5446703ae42c8a97c77f96ce05ff18f',
    'backend/hermes_client.py': '8c018cc4c6e566d481ffcecd87905f2c6dc40bd1fdc67980cb18e5438cdbdf4f',
    'backend/jobs.py': 'fd9c4a2ac2c2f292c4616fd7dd04281b4c6a5753e9c432756345d85d175eee5e',
    'backend/native_catalog.py': '8b3c398f52388e0e334d6d64381b677b651b9337ece2d1868a14d3de069c5ed6',
    'backend/notification_policy.py': '69c7c6807210dbf9300cd25c5ba14604be75730d06f41879074208d62655684d',
    'backend/operational_notifications.py': '9edbc496bb931a02096bf820d984a4032107e003924492e1120684a0f5cdad31',
    'backend/orchestration.py': '1ea28e810f7c1827e9f40e934a24c4e7774d76b5fe0beb8d090a0ab0174dce7f',
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
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
    # Accepted merged PR25 source, absent from the historical b85c098 baseline.
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
    'deploy/issue_starter.py': 'autonomy-launch-contract',
    'deploy/pull_handoff_binding.py': 'coordinator-review-contract',
    'deploy/review_evidence.py': 'coordinator-review-contract',
    'deploy/task_receipts.py': 'coordinator-review-contract',
    'deploy/workflow_events.py': 'coordinator-review-contract',
    'deploy/workflow_lifecycle.py': 'coordinator-review-contract',
    'deploy/workflow_lifecycle_sources.py': 'coordinator-review-contract',
    'deploy/workflow_notifications.py': 'coordinator-review-contract',
    'scripts/cloud_coordinator.py': 'autonomy-launch-contract',
    'scripts/issue_starter.py': 'autonomy-launch-contract',
    'scripts/workflow_notifications.py': 'autonomy-launch-contract',
    'deploy/hermes-mobile-coordinator.service': 'autonomy-launch-contract',
    'deploy/hermes-mobile-coordinator.timer': 'autonomy-launch-contract',
    'deploy/hermes-mobile-issue-starter.service': 'autonomy-launch-contract',
    'deploy/hermes-mobile-issue-starter.timer': 'autonomy-launch-contract',
    'deploy/hermes-workflow-notifications.service': 'autonomy-launch-contract',
    'deploy/hermes-workflow-notifications.timer': 'autonomy-launch-contract',
    'backend/configuration.py': 'execution-source-contract',
    'backend/clarifications.py': 'execution-source-contract',
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
# Historical provenance checkpoints, not identities of current candidate bytes.
# Explicit reviewed per-file refreshes are documented in docs/autonomy-policy.md.
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
    if actual != expected:
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
    review = evidence.get('independent_review')
    if not isinstance(review, dict):
        blockers.add('independent-review-evidence')
        return
    head = review.get('head_sha')
    if (review.get('repository_id') != REPOSITORY_ID or review.get('base_branch') != 'main'
            or review.get('base_sha') != main_sha or not _valid_sha(head)
            or review.get('state') != 'open' or review.get('draft') is not False
            or type(review.get('pull_author_id')) is not int or review['pull_author_id'] < 1
            or review['pull_author_id'] == OWNER_ID
            or review.get('reviews_complete') is not True
            or review.get('threads_complete') is not True):
        blockers.add('independent-review-evidence')
        return
    reviews, threads = review.get('reviews'), review.get('threads')
    if not isinstance(reviews, list) or not isinstance(threads, list):
        blockers.add('independent-review-evidence')
        return
    if current_independent_agent_review(
            reviews, head, owner_id=OWNER_ID,
            expected=review.get('selected_review'),
            complete=review.get('reviews_complete')) is None:
        blockers.add('independent-review')
    latest_copilot = latest_reviews(reviews, COPILOT_REVIEWER_ID)
    if latest_copilot and any(
            item.get('state') == 'CHANGES_REQUESTED' and item.get('commit_id') == head
            for item in latest_copilot):
        blockers.add('independent-review-rejection')
    if any(not isinstance(thread, dict) or thread.get('isResolved') is not True
           or thread.get('comments_complete') is not True for thread in threads):
        blockers.add('independent-review-threads')

    change = review.get('change')
    if (not isinstance(change, dict) or change.get('head_sha') != head
            or change.get('files_complete') is not True
            or type(change.get('sensitive')) is not bool):
        blockers.add('change-scope-evidence')
    elif change['sensitive']:
        authorization = change.get('owner_authorization')
        targeted = change.get('targeted_review')
        if not sensitive_review_authorized(
                reviews, head, authorization, targeted, owner_id=OWNER_ID):
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
