import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.autonomy_policy import (
    AUTONOMY_LAUNCH_ROOTS,
    AUTONOMY_LAUNCH_UNITS,
    COPILOT_AGENT_ID,
    COPILOT_REVIEWER_ID,
    OWNER_ID,
    REPOSITORY,
    REPOSITORY_ID,
    REF,
    REQUIRED_FILES,
    RUN_JOBS,
    SOURCE_BASELINES,
    SOURCE_BLOCKERS,
    SOURCE_CONTROL_PYTHON_FILES,
    SOURCE_CONTROL_ROOTS,
    SOURCE_FINGERPRINTS,
    WORKFLOW_ID,
    WORKFLOW_PATH,
    validate_transition,
)
from scripts.autonomy_policy import MAX_EVIDENCE, main as cli_main

SHA = 'a' * 40
ARTIFACT_HASH = 'b' * 64
IDENTITY = f'https://github.com/{REPOSITORY}/{WORKFLOW_PATH}@{REF}'

# Historical main inventory with exactly three accepted native source refreshes;
# see docs/autonomy-policy.md. Literals remain independent of policy constants.
_MERGED_MAIN_SOURCE_FIXTURE = {
    '.github/workflows/ci.yml': '39110e6f940fc59ef3aec9846f07616a86a2e07335d2aaed6c6cd0ab444ff195',
    '.github/workflows/issue-link.yml': '5569875b9fc45d686f857712bc035d75a100fcfc4f3862f48b0d8bd045eca415',
    '.github/native-tests.json': 'b97ddb088e595197cf65d97b0b4af89f4f83f5c4ad25463c566df7099817209f',
    '.github/host-tests.json': '8af5fc30188f81ba95d3d7c11ba6801f52795175c2cac7f634424f906947ce5a',
    '.github/actions/test-environment/action.yml': '638ed56955c8c8202fdd41640abe3be8b8065129e2965b8dc67b69f024017604',
    '.github/actions/native-test-environment/action.yml': 'b1e5af03a4aa397f585e541805b4a292d1b3529090d54932b8945b6743ecd993',
    '.github/native-runtime.json': '705351eff7420cf3cbd3f91f3edc605feb304eba86f99e5a94902e89d3fb4d88',
    'deploy/assets.py': '0b5fee70ac71f61384b6501a40df9b7125c080fffa9930975ec2542553dffcc1',
    'deploy/frontend_release.py': '7749afb862a515fc673712b11278145adfb3d39ba2d012a34ecea257399eade3',
    'deploy/git_source.py': 'c69c7c5a45bc3a16ab26996872c258cc352cf2ec56c19d6256595e18ac713d63',
    'deploy/install_core.py': '2f60fbde34c02486450608fd844c3f9a0bf123a014849d4d992e5f61fd873dfc',
    'deploy/native_controls_release.py': 'b3d3c601db4afd9ff003f4df759fcb057c19a83b920206fb2766d1560fd99175',
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
    'requirements.lock': '1e912f6160c68f3ebb56a51da95af013875d0b4690434fe52fcd3f6b115de095',
    'package.json': 'a341a3a23a9425728ba38b83e5d7a4ab983c6f951f14667ba3cd69a6e35f13d3',
    'package-lock.json': '63199915d106fefd775451eb7d4aed9a3c2d04ca6f670ef9f27ba0cd6098218a',
    'patches/cron-delivery-baseline.json': '988ff4bda29998ce0f0743950e491f86e2b9d434e9da57c40aee5a5e06895af1',
    'patches/cron-delivery.patch': '444af4887abcea020baaf8c8cfbf4679670d38cdc2fc302a97ad5c54d68fc1ff',
    'patches/native-compat-baseline.json': '2daf996adbcab86d8ad5f1a3e15bd5ea26134ea116662b451cd09429c3ebc862',
    'patches/native-compat.patch': '2add04e93a5ea74eeeb407dc9d5556bb38c1454b5c8bf307c3e8490e9b72dd32',
    'backend/configuration.py': '03d4fb191ba53f04df25b935ae03d3f5cce9ba513c89938dbb809601dae9e636',
    'backend/model_controls.py': '206fb5164283c16f46c51da99babe1b5f5e8932f062b4a1ee5bc07affe1f2c34',
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
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}
# Accepted merged PR25 bytes; absent only from the historical b85c098 baseline.
_PENDING_PR25_NATIVE_NOTIFICATION_FIXTURE = {
    'deploy/native_notification_release.py': '364f5856f31a07117274a6855a0af573e85d4d197770188b6e9c734df4699582',
}

# Accepted PR29/PR40/PR42 dependency bytes retained from main5316, independently
# spelled out (not copied from policy constants at runtime). Issue43 source
# acceptance does not authorize operational activation.
_PENDING_PR40_LIFECYCLE_FIXTURE = {
    'deploy/task_receipts.py': '8ad9e60ec697de8135679b9110ed5d924057e0d8a59e731c67fceedec6525197',
    'deploy/workflow_events.py': '5234980515c0909d5170a3a9047766a0b355aa35372bedc2961b703afc37b9af',
    'deploy/workflow_lifecycle.py': '71be9101223f40511818bde2db9f6bd6b021736f35152e16cb3e1c9c3e2085a3',
    'deploy/workflow_lifecycle_sources.py': 'dfff5b5ec33b9bd1756a67150827541ea86193b87e6de5b5c3a965f02f19b837',
    'deploy/workflow_notifications.py': 'f0af01bdc797e0abd0494fa7a1fa304060c734ed8fc2ba1fa2b4515a9a3bcda2',
}

_PENDING_ISSUE43_LAUNCH_FIXTURE = {
    'deploy/cloud_coordinator.py': '3f351989201ccbd87d13943ecbf819a1e871b6dede6e1257150f7c37abf6170c',
    'deploy/issue_starter.py': '701faa6e15a2717cb3c79f7e93c728bdde326e4f72e451ccd77ec1f8eabdc011',
    'deploy/review_evidence.py': 'c097e5ddb38119c992b8f5fac6581434a494242f48fdec6d07f037da18f188ae',
    'scripts/cloud_coordinator.py': '992d448a9ddfdd75abdab14fc48ad0dbff98e1c93a943f483d0788ef5ca57790',
    'scripts/issue_starter.py': '09008da255c56f370f73af6d2f8e8587f6a999c76a99e1bd798e8ac4bbd927f1',
    'scripts/workflow_notifications.py': '03731f93e1aa3ce297107ea3d0126e990c72d88401dda04e4499f0a7f505b55f',
    'deploy/hermes-mobile-coordinator.service': '672f1e134e2cb5acbcd648eb7e124947af8d7c11143f20cea8e5be5d97c42807',
    'deploy/hermes-mobile-coordinator.timer': 'ffa239c67b492b5a361b823c754d5f204eb4efaa2c69f7df99df577e12d2a6c1',
    'deploy/hermes-workflow-notifications.service': '998ee55dc5df990c6004f0f435e073766702e5efbf56aa83e946b6d05e21c000',
    'deploy/hermes-workflow-notifications.timer': '627463b4dd06eb72f7fecc88a79dad29ec8ab4b5514b29c62c131cdbd2963cc8',
    'deploy/hermes-mobile-issue-starter.service': '1711c53ee7b7e4f86b435d3e19ade679b20af960176c53125f14eee3a0dcdb69',
    'deploy/hermes-mobile-issue-starter.timer': '848e07d3f30f5d4c7ad881ca9bdeddd6fbf9eeb8ae1fb68ba0feb5b4425e5e92',
}

_PENDING_ISSUE50_RECEIPT_FIXTURE = {
    'deploy/cloud_coordinator.py': '0935088cb9e3429b83dcc7daa8552cf2c58830e5494549270ec276a076e3193b',
    'deploy/task_receipts.py': 'f97dbc809fa107388e1dc1954cff95ea4372cd003228be34e5780378dfbf8bcf',
}


def _source_files():
    return (_MERGED_MAIN_SOURCE_FIXTURE | _PENDING_PR25_NATIVE_NOTIFICATION_FIXTURE
            | _PENDING_PR40_LIFECYCLE_FIXTURE | _PENDING_ISSUE43_LAUNCH_FIXTURE
            | _PENDING_ISSUE50_RECEIPT_FIXTURE)


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
            'pull_author_id': COPILOT_AGENT_ID,
            'head_sha': review_head, 'reviews_complete': True, 'threads_complete': True,
            'reviews': [{
                'id': 1,
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


def _sensitive_evidence(phase, state='COMMENTED'):
    evidence = _phase_evidence(phase)
    review = evidence['cloud_review']
    head = review['head_sha']
    body = json.dumps({
        'schema': 'hermes-independent-agent-review-v1',
        'reviewed_head_sha': head,
        'review_method': 'independent-agent',
        'verdict': 'pass',
        'evidence_sha256': 'c' * 64,
    }, separators=(',', ':'))
    body_sha256 = hashlib.sha256(body.encode('utf-8')).hexdigest()
    review['change'].update(
        sensitive=True,
        owner_authorization={
            'actor_id': OWNER_ID, 'head_sha': head, 'state': 'approved',
            'review_id': 2, 'body_sha256': body_sha256,
        },
        targeted_review={
            'review_id': 2, 'reviewer_id': OWNER_ID, 'head_sha': head,
            'state': state, 'body_sha256': body_sha256, 'evidence_sha256': 'c' * 64,
        },
    )
    review['reviews'].append({
        'id': 2, 'user': {'id': OWNER_ID}, 'commit_id': head, 'state': state,
        'submitted_at': '2026-10-01T22:00:00Z', 'body': body,
    })
    return evidence


def _blockers(evidence, phase='pre-cutover'):
    return set(validate_transition(evidence, phase=phase)['blockers'])


def _changed_source(evidence, path, source):
    evidence['main']['files'][path] = hashlib.sha256(source.encode()).hexdigest()


def _static_python_control_closure(source_root=None, roots=None):
    source_root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    pending = list(roots if roots is not None else SOURCE_CONTROL_ROOTS)
    seen = set()
    dynamic_imports = set()
    unresolved_imports = set()
    local_roots = {'backend', 'deploy', 'scripts'}

    def initializer_attributes(module_path):
        initializer = module_path / '__init__.py'
        if not initializer.is_file():
            return set()
        attributes = set()
        # Only direct declarations prove an attribute. Import requests, nested
        # scopes and annotation-only names do not prove a package member exists.
        for statement in ast.parse(initializer.read_text()).body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                attributes.add(statement.name)
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                if isinstance(statement, ast.AnnAssign) and statement.value is None:
                    continue
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                attributes.update(target.id for target in targets if isinstance(target, ast.Name))
        return attributes

    def enqueue(module, *, optional=False):
        namespace = module.partition('.')[0]
        if namespace not in local_roots:
            return
        module_path = source_root.joinpath(*module.split('.'))
        initializer = module_path / '__init__.py'
        source = module_path.with_suffix('.py')
        if initializer.is_file():
            target = initializer
        elif source.is_file():
            target = source
        elif module_path.is_dir():
            target = None
        elif not optional:
            unresolved_imports.add(module)
            return
        else:
            return
        if target is not None:
            pending.append(target.relative_to(source_root).as_posix())
        parts = module.split('.')
        for index in range(1, len(parts)):
            parent = source_root.joinpath(*parts[:index]) / '__init__.py'
            if parent.is_file():
                pending.append(parent.relative_to(source_root).as_posix())

    while pending:
        path = Path(pending.pop()).as_posix()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse((source_root / path).read_text())
        parts = Path(path).with_suffix('').parts
        package = '.'.join(parts[:-1])
        dynamic_names = {'__import__', 'eval', 'exec'}
        for imported in ast.walk(tree):
            if isinstance(imported, ast.ImportFrom) and imported.module in {
                    'builtins', 'importlib', 'importlib.util'}:
                for alias in imported.names:
                    if alias.name in {'__import__', 'eval', 'exec', 'import_module',
                                      'spec_from_file_location', 'load_module'}:
                        dynamic_names.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    enqueue(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level:
                    try:
                        module = importlib.util.resolve_name(
                            '.' * node.level + module, package,
                        )
                    except (ImportError, ValueError):
                        unresolved_imports.add(path)
                        continue
                if not module:
                    unresolved_imports.add(path)
                    continue
                enqueue(module)
                module_path = source_root.joinpath(*module.split('.'))
                if node.module is None or module_path.is_dir():
                    attributes = initializer_attributes(module_path)
                    for alias in node.names:
                        if alias.name == '*':
                            unresolved_imports.add(path)
                        else:
                            enqueue(f'{module}.{alias.name}', optional=alias.name in attributes)
            if isinstance(node, ast.Call):
                function = node.func
                if ((isinstance(function, ast.Name) and function.id in dynamic_names)
                        or (isinstance(function, ast.Attribute) and function.attr in
                            {'import_module', 'spec_from_file_location', 'load_module'})):
                    dynamic_imports.add(path)
    return seen, dynamic_imports, unresolved_imports


def _write_python(source_root, path, source):
    target = source_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


def test_static_python_closure_resolves_relative_package_and_initializer_imports(tmp_path):
    _write_python(tmp_path, 'deploy/__init__.py', 'from . import child\n')
    _write_python(tmp_path, 'deploy/runner.py', 'from .nested import checks\n')
    _write_python(tmp_path, 'deploy/child.py', 'from .nested import helpers\n')
    _write_python(tmp_path, 'deploy/nested/__init__.py', 'from . import startup\n')
    _write_python(tmp_path, 'deploy/nested/checks.py', 'from .. import child\n')
    _write_python(tmp_path, 'deploy/nested/helpers.py', '')
    _write_python(tmp_path, 'deploy/nested/startup.py', 'from ..child import run\n')

    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        tmp_path, roots={'deploy/runner.py'},
    )

    assert closure == {
        'deploy/__init__.py', 'deploy/runner.py', 'deploy/child.py',
        'deploy/nested/__init__.py', 'deploy/nested/checks.py',
        'deploy/nested/helpers.py', 'deploy/nested/startup.py',
    }
    assert not dynamic_imports
    assert not unresolved_imports


def test_static_python_closure_reports_unsupported_execution_and_missing_modules(tmp_path):
    _write_python(
        tmp_path, 'deploy/runner.py',
        "import importlib\nfrom importlib import import_module as load_module\n"
        "import deploy.missing\nimportlib.import_module('deploy.dynamic')\n"
        "load_module('deploy.also_dynamic')\n",
    )

    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        tmp_path, roots={'deploy/runner.py'},
    )

    assert closure == {'deploy/runner.py'}
    assert dynamic_imports == {'deploy/runner.py'}
    assert unresolved_imports == {'deploy.missing'}


@pytest.mark.parametrize(('module', 'name'), [
    ('builtins', '__import__'),
    ('builtins', 'eval'),
    ('builtins', 'exec'),
    ('importlib', 'import_module'),
    ('importlib.util', 'spec_from_file_location'),
    ('importlib.util', 'load_module'),
])
@pytest.mark.parametrize('aliased', [False, True])
def test_static_python_closure_reports_direct_dynamic_callable_imports(tmp_path, module, name, aliased):
    binding = 'load' if aliased else name
    suffix = ' as load' if aliased else ''
    _write_python(
        tmp_path, 'deploy/runner.py',
        f'from {module} import {name}{suffix}\n{binding}("synthetic")\n',
    )

    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        tmp_path, roots={'deploy/runner.py'},
    )

    assert closure == {'deploy/runner.py'}
    assert dynamic_imports == {'deploy/runner.py'}
    assert not unresolved_imports


@pytest.mark.parametrize('import_statement', [
    'from . import missing',
    'from deploy import missing as alias',
])
@pytest.mark.parametrize('initializer', [
    None,
    '',
    'known = 1\n',
    'missing: object\n',
    'class Container:\n    missing = 1\n',
    'def factory():\n    missing = 1\n',
    'from . import missing\n',
])
def test_static_python_closure_rejects_missing_package_members(tmp_path, import_statement, initializer):
    _write_python(tmp_path, 'deploy/runner.py', import_statement + '\n')
    if initializer is not None:
        _write_python(tmp_path, 'deploy/__init__.py', initializer)

    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        tmp_path, roots={'deploy/runner.py'},
    )

    expected = {'deploy/runner.py'}
    if initializer is not None:
        expected.add('deploy/__init__.py')
    assert closure == expected
    assert not dynamic_imports
    assert unresolved_imports == {'deploy.missing'}


@pytest.mark.parametrize('initializer', [
    'exported = 1\n',
    'exported: int = 1\n',
    'def exported():\n    pass\n',
    'async def exported():\n    pass\n',
    'class exported:\n    pass\n',
])
def test_static_python_closure_accepts_declared_initializer_attributes(tmp_path, initializer):
    _write_python(tmp_path, 'deploy/__init__.py', initializer)
    _write_python(tmp_path, 'deploy/runner.py', 'from . import exported as alias\n')

    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        tmp_path, roots={'deploy/runner.py'},
    )

    assert closure == {'deploy/__init__.py', 'deploy/runner.py'}
    assert not dynamic_imports
    assert not unresolved_imports


def test_reviewed_source_fixture_matches_complete_required_contract():
    pending = {'deploy/cloud_coordinator.py', 'deploy/native_notification_release.py',
               'deploy/review_evidence.py'} | set(_PENDING_PR40_LIFECYCLE_FIXTURE)
    pending |= set(_PENDING_ISSUE43_LAUNCH_FIXTURE)
    pending |= set(_PENDING_ISSUE50_RECEIPT_FIXTURE)
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path not in pending
    } == _MERGED_MAIN_SOURCE_FIXTURE
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path in {'deploy/cloud_coordinator.py', 'deploy/review_evidence.py'}
    } == {
        'deploy/cloud_coordinator.py': _PENDING_ISSUE50_RECEIPT_FIXTURE[
            'deploy/cloud_coordinator.py'
        ],
        'deploy/review_evidence.py': _PENDING_ISSUE43_LAUNCH_FIXTURE[
            'deploy/review_evidence.py'
        ],
    }
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path == 'deploy/native_notification_release.py'
    } == _PENDING_PR25_NATIVE_NOTIFICATION_FIXTURE
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path in _PENDING_PR40_LIFECYCLE_FIXTURE
        and path not in _PENDING_ISSUE50_RECEIPT_FIXTURE
    } == {
        path: digest for path, digest in _PENDING_PR40_LIFECYCLE_FIXTURE.items()
        if path not in _PENDING_ISSUE50_RECEIPT_FIXTURE
    }
    assert {
        path: digest for path, digest in SOURCE_FINGERPRINTS.items()
        if path in _PENDING_ISSUE50_RECEIPT_FIXTURE
    } == _PENDING_ISSUE50_RECEIPT_FIXTURE
    assert set(SOURCE_FINGERPRINTS) == set(REQUIRED_FILES)
    assert set(SOURCE_BLOCKERS) == set(REQUIRED_FILES)
    assert len(REQUIRED_FILES) == len(set(REQUIRED_FILES))
    assert SOURCE_CONTROL_PYTHON_FILES <= set(REQUIRED_FILES)
    assert SOURCE_CONTROL_ROOTS <= SOURCE_CONTROL_PYTHON_FILES
    assert AUTONOMY_LAUNCH_ROOTS == {
        'deploy/issue_starter.py', 'scripts/cloud_coordinator.py',
        'scripts/issue_starter.py', 'scripts/workflow_notifications.py',
    }
    assert AUTONOMY_LAUNCH_UNITS == {
        'deploy/hermes-mobile-coordinator.service', 'deploy/hermes-mobile-coordinator.timer',
        'deploy/hermes-workflow-notifications.service',
        'deploy/hermes-workflow-notifications.timer',
        'deploy/hermes-mobile-issue-starter.service', 'deploy/hermes-mobile-issue-starter.timer',
    }
    assert AUTONOMY_LAUNCH_ROOTS | AUTONOMY_LAUNCH_UNITS <= set(
        _PENDING_ISSUE43_LAUNCH_FIXTURE,
    )
    assert SOURCE_BASELINES == {
        'main': 'b85c098857e7fb8229f47bd688d703bb677aeb34',
        'deploy/cloud_coordinator.py': '403ac3d87988b9d3c7dc45aaecb44f11f3ef4a83',
    }


@pytest.mark.parametrize('pins', [
    SOURCE_FINGERPRINTS, _PENDING_ISSUE43_LAUNCH_FIXTURE,
], ids=['policy', 'independent-fixture'])
@pytest.mark.parametrize(
    'path', sorted(set(_PENDING_ISSUE43_LAUNCH_FIXTURE) -
                   set(_PENDING_ISSUE50_RECEIPT_FIXTURE)),
)
def test_issue43_launch_pin_matches_actual_candidate_bytes(pins, path):
    source = Path(__file__).resolve().parents[1] / path
    assert pins[path] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize('pins', [
    SOURCE_FINGERPRINTS, _PENDING_ISSUE50_RECEIPT_FIXTURE,
], ids=['policy', 'independent-fixture'])
@pytest.mark.parametrize('path', sorted(_PENDING_ISSUE50_RECEIPT_FIXTURE))
def test_issue50_receipt_pin_matches_actual_candidate_bytes(path, pins):
    source = Path(__file__).resolve().parents[1] / path
    assert pins[path] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    'path', sorted(set(_PENDING_PR40_LIFECYCLE_FIXTURE) -
                   set(_PENDING_ISSUE50_RECEIPT_FIXTURE)),
)
@pytest.mark.parametrize('pins', [
    SOURCE_FINGERPRINTS, _PENDING_PR40_LIFECYCLE_FIXTURE,
], ids=['policy', 'independent-fixture'])
def test_pending_lifecycle_pin_matches_actual_candidate_bytes(path, pins):
    source = Path(__file__).resolve().parents[1] / path
    assert pins[path] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('path', sorted(_PENDING_PR40_LIFECYCLE_FIXTURE))
@pytest.mark.parametrize('change', ['missing', 'malformed', 'mutated'])
def test_pending_lifecycle_actual_byte_mutation_or_missing_source_blocks(phase, path, change):
    evidence = _phase_evidence(phase)
    source = (Path(__file__).resolve().parents[1] / path).read_bytes()
    evidence['main']['files'][path] = hashlib.sha256(source).hexdigest()
    assert _blockers(evidence, phase) == set()
    if change == 'missing':
        evidence['main']['files'].pop(path)
        blocker = 'main-source-missing'
    elif change == 'malformed':
        evidence['main']['files'][path] = 'not-a-sha256'
        blocker = 'main-source-invalid'
    else:
        evidence['main']['files'][path] = hashlib.sha256(
            source + b'\n# unreviewed source mutation\n',
        ).hexdigest()
        blocker = 'coordinator-review-contract'
    before = copy.deepcopy(evidence)
    assert validate_transition(evidence, phase=phase) == {
        'ready': False, 'phase': phase,
        'blockers': [blocker],
    }
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize(
    'path', sorted(set(_PENDING_ISSUE43_LAUNCH_FIXTURE) |
                   set(_PENDING_ISSUE50_RECEIPT_FIXTURE)),
)
@pytest.mark.parametrize('change', ['missing', 'malformed', 'mutated'])
def test_issue43_launch_actual_byte_mutation_or_missing_source_blocks(phase, path, change):
    evidence = _phase_evidence(phase)
    source = (Path(__file__).resolve().parents[1] / path).read_bytes()
    evidence['main']['files'][path] = hashlib.sha256(source).hexdigest()
    assert _blockers(evidence, phase) == set()
    if change == 'missing':
        evidence['main']['files'].pop(path)
        blocker = 'main-source-missing'
    elif change == 'malformed':
        evidence['main']['files'][path] = 'not-a-sha256'
        blocker = 'main-source-invalid'
    else:
        evidence['main']['files'][path] = hashlib.sha256(
            source + b'\n# unreviewed launch source mutation\n',
        ).hexdigest()
        blocker = SOURCE_BLOCKERS[path]
    before = copy.deepcopy(evidence)
    assert validate_transition(evidence, phase=phase) == {
        'ready': False, 'phase': phase,
        'blockers': [blocker],
    }
    assert evidence == before


def test_reviewed_static_python_closure_is_complete_and_has_no_dynamic_imports():
    closure, dynamic_imports, unresolved_imports = _static_python_control_closure()
    assert closure == SOURCE_CONTROL_PYTHON_FILES
    assert not dynamic_imports
    assert not unresolved_imports


def test_pinned_coordinator_local_import_closure_is_in_the_fixed_inventory():
    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        roots=['deploy/cloud_coordinator.py'],
    )
    # Exact assembled static closure, not a live-derived whitelist. Existing
    # shared imports keep their original execution-source-contract labels.
    shared = {
        'backend/app.py', 'backend/auth.py', 'backend/auth_store.py',
        'backend/background_delivery.py', 'backend/catalog_search.py',
        'backend/chat_snapshot.py', 'backend/configuration.py',
        'backend/context_compression_presentation.py', 'backend/delivery.py',
        'backend/hermes_client.py', 'backend/jobs.py', 'backend/model_controls.py',
        'backend/native_catalog.py', 'backend/notification_policy.py',
        'backend/notifications.py', 'backend/operational_notifications.py',
        'backend/orchestration.py', 'backend/profiles.py', 'backend/public_commentary.py',
        'backend/request_notifications.py', 'backend/runs.py', 'backend/runtime_binding.py',
        'backend/runtime_notice_presentation.py', 'backend/session_deletion.py',
        'backend/session_telemetry.py', 'backend/session_visibility.py',
        'backend/steering.py', 'backend/task_reminder_presentation.py',
        'backend/tool_presentation.py',
    }
    coordinator = {
        'deploy/cloud_coordinator.py', 'deploy/review_evidence.py',
        'deploy/task_receipts.py', 'deploy/workflow_events.py',
        'deploy/workflow_lifecycle.py', 'deploy/workflow_lifecycle_sources.py',
        'deploy/workflow_notifications.py',
    }
    assert closure == shared | coordinator
    assert len(closure) == 36
    assert closure <= set(REQUIRED_FILES)
    assert all(SOURCE_BLOCKERS[path] == 'coordinator-review-contract' for path in coordinator)
    assert all(SOURCE_BLOCKERS[path] == 'execution-source-contract' for path in shared)
    assert not dynamic_imports
    assert not unresolved_imports


def test_launch_roots_have_complete_fixed_closure_and_independent_unit_pins():
    closure, dynamic_imports, unresolved_imports = _static_python_control_closure(
        roots=AUTONOMY_LAUNCH_ROOTS,
    )
    assert closure <= SOURCE_CONTROL_PYTHON_FILES
    assert not dynamic_imports
    assert not unresolved_imports


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('sensitive', [False, True])
@pytest.mark.parametrize('claimed_ready', [False, True])
@pytest.mark.parametrize(('path', 'value', 'blocker'), [
    (('main', 'files', 'deploy/cloud_coordinator.py'), '0' * 64, 'coordinator-review-contract'),
    (('main', 'current'), False, 'current-main-snapshot'),
    (('source_ci', 'conclusion'), 'failure', 'source-ci-evidence'),
    (('source_ci', 'jobs_complete'), False, 'source-ci-jobs'),
    (('source_ci', 'artifact', 'expired'), True, 'release-artifact-evidence'),
    (('source_ci', 'artifact', 'attestation', 'verified'), False, 'release-attestation'),
    (('cloud_review', 'reviews', 0, 'id'), True, 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'submitted_at'), '2026-10-01T21:00:00', 'cloud-review-approval'),
    (('cloud_review', 'reviews', 0, 'state'), 'COMMENTED', 'cloud-review-approval'),
    (('cloud_review', 'reviews_complete'), False, 'cloud-review-evidence'),
    (('cloud_review', 'threads', 0, 'isResolved'), False, 'cloud-review-threads'),
    (('protection', 'strict'), False, 'branch-protection'),
])
def test_caller_flags_cannot_override_failed_components(phase, sensitive, claimed_ready, path, value, blocker):
    evidence = _sensitive_evidence(phase) if sensitive else _phase_evidence(phase)
    node = evidence
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    # Historical hold flags and a claimed result cannot replace any actual gate.
    evidence.update(pending_source_contract=False, source_contract_reviewed=claimed_ready,
                    ready=claimed_ready, blockers=[])
    evidence['main'].update(source_contract_reviewed=claimed_ready,
                            pending_source_contract=False)
    before = copy.deepcopy(evidence)

    expected = {blocker}
    if path == ('main', 'current'):
        expected.add('main-source-missing')
    if sensitive and path == ('cloud_review', 'reviews', 0, 'id'):
        expected.add('sensitive-review-authorization')
    assert validate_transition(evidence, phase=phase) == {
        'ready': False, 'phase': phase, 'blockers': sorted(expected),
    }
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('sensitive', [False, True])
def test_complete_synthetic_components_report_source_policy_ready(phase, sensitive):
    # A complete synthetic snapshot is not authenticated operational authorization.
    evidence = _sensitive_evidence(phase) if sensitive else _phase_evidence(phase)
    before = copy.deepcopy(evidence)

    assert validate_transition(evidence, phase=phase) == {
        'ready': True, 'phase': phase, 'blockers': [],
    }
    assert evidence == before


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


@pytest.mark.parametrize('path', [
    '.github/actions/test-environment/action.yml',
    'deploy/assets.py',
    'deploy/frontend_release.py',
    'deploy/git_source.py',
    'deploy/install_core.py',
    'deploy/native_controls_release.py',
    'deploy/native_notification_release.py',
    'deploy/public_http.py',
    'deploy/test_workspace.py',
    'scripts/test.py',
    'requirements.lock',
    'package.json',
    'package-lock.json',
    'patches/cron-delivery-baseline.json',
    'patches/cron-delivery.patch',
    'patches/native-compat-baseline.json',
    'patches/native-compat.patch',
])
def test_mutating_each_executable_dependency_blocks(path):
    evidence = _evidence()
    _changed_source(evidence, path, 'reviewed source mutation')
    assert SOURCE_BLOCKERS[path] in _blockers(evidence)


@pytest.mark.parametrize('path', [
    '.github/workflows/issue-link.yml',
    'backend/app.py', 'backend/auth.py', 'backend/auth_store.py',
    'backend/background_delivery.py', 'backend/catalog_search.py', 'backend/chat_snapshot.py',
    'backend/configuration.py', 'backend/context_compression_presentation.py',
    'backend/delivery.py', 'backend/hermes_client.py', 'backend/jobs.py',
    'backend/model_controls.py', 'backend/native_api_service.py', 'backend/native_catalog.py',
    'backend/native_notifications.py',
    'backend/notification_policy.py', 'backend/notifications.py',
    'backend/operational_notifications.py', 'backend/orchestration.py',
    'backend/profiles.py', 'backend/public_commentary.py', 'backend/request_notifications.py',
    'backend/runs.py', 'backend/runtime_binding.py', 'backend/runtime_notice_presentation.py',
    'backend/session_deletion.py', 'backend/session_telemetry.py',
    'backend/session_visibility.py', 'backend/steering.py',
    'backend/task_reminder_presentation.py', 'backend/tool_presentation.py',
    'deploy/backup.py', 'deploy/native_readiness.py', 'deploy/observe_release.py',
    'deploy/review_evidence.py',
])
def test_new_source_inventory_mutations_keep_the_specific_blocker(path):
    evidence = _evidence()
    _changed_source(evidence, path, 'unreviewed source mutation')

    assert _blockers(evidence) == {
        SOURCE_BLOCKERS[path],
    }


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
    evidence = _sensitive_evidence(phase)
    assert _blockers(evidence, phase) == set()
    for record, field, value in (
        ('owner_authorization', 'actor_id', 1),
        ('owner_authorization', 'head_sha', 'd' * 40),
        ('owner_authorization', 'state', 'pending'),
        ('targeted_review', 'reviewer_id', 76),
        ('targeted_review', 'head_sha', 'd' * 40),
        ('targeted_review', 'state', 'DISMISSED'),
    ):
        changed = copy.deepcopy(evidence)
        changed['cloud_review']['change'][record][field] = value
        assert _blockers(changed, phase) == {'sensitive-review-authorization'}


@pytest.mark.parametrize('phase', PHASES)
def test_sensitive_owner_published_independent_review_matches_authenticated_record(phase):
    evidence = _sensitive_evidence(phase)
    before = copy.deepcopy(evidence)
    assert validate_transition(evidence, phase=phase) == {
        'ready': True, 'phase': phase, 'blockers': [],
    }
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
def test_sensitive_owner_review_must_be_a_formal_comment_not_approval(phase):
    evidence = _sensitive_evidence(phase, 'APPROVED')
    assert 'sensitive-review-authorization' in _blockers(evidence, phase)


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('mutate', [
    pytest.param(lambda review: review['reviews'].pop(), id='absent-record'),
    pytest.param(lambda review: review['change'].pop('targeted_review'), id='absent-claim'),
    pytest.param(lambda review: review['change']['targeted_review'].pop('review_id'), id='absent-id'),
    pytest.param(lambda review: review['change']['targeted_review'].update(review_id=3), id='wrong-id'),
    pytest.param(lambda review: review['reviews'][-1].update(id=3), id='record-id-mismatch'),
    pytest.param(lambda review: review['reviews'][-1]['user'].update(id=77), id='wrong-reviewer'),
    pytest.param(lambda review: review['reviews'][-1]['user'].update(id='76'), id='untyped-reviewer'),
    pytest.param(lambda review: review['reviews'][-1].pop('user'), id='missing-reviewer'),
    pytest.param(lambda review: review['reviews'][-1].update(commit_id='d' * 40), id='stale-record'),
    pytest.param(lambda review: review['change']['targeted_review'].update(head_sha='d' * 40), id='stale-claim'),
    pytest.param(lambda review: review['reviews'][-1].update(state='APPROVED'), id='state-mismatch'),
    pytest.param(lambda review: review['reviews'][-1].update(state='DISMISSED'), id='dismissed-record'),
    pytest.param(lambda review: review['reviews'][-1].update(state='CHANGES_REQUESTED'), id='changes-requested'),
    pytest.param(lambda review: review['reviews'].append(copy.deepcopy(review['reviews'][-1])), id='duplicate-id'),
    pytest.param(lambda review: review['reviews'].append(dict(review['reviews'][-1], user={'id': 77})), id='conflicting-reviewer'),
    pytest.param(lambda review: review['reviews'].append(dict(review['reviews'][-1], state='DISMISSED')), id='conflicting-state'),
    pytest.param(lambda review: review['reviews'].append(dict(review['reviews'][-1], commit_id='d' * 40)), id='conflicting-head'),
])
def test_sensitive_targeted_review_rejects_unbound_claim(phase, mutate):
    evidence = _sensitive_evidence(phase)
    mutate(evidence['cloud_review'])
    expected = {'sensitive-review-authorization'}
    reviewer = evidence['cloud_review']['reviews'][-1].get('user')
    if (not isinstance(reviewer, dict) or type(reviewer.get('id')) is not int
            or reviewer['id'] < 1):
        expected.add('cloud-review-approval')
    review_ids = [record.get('id') for record in evidence['cloud_review']['reviews']
                  if isinstance(record, dict)]
    if len(review_ids) != len(set(review_ids)):
        expected.add('cloud-review-approval')
    assert _blockers(evidence, phase) == expected


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('state', ['COMMENTED'])
@pytest.mark.parametrize('timestamp', [
    None, '', 'not-a-date', '2026-10-01T22:00:00',
    '2026-02-30T22:00:00Z', '2026-10-01T22:00:00+25:00', 123, {},
])
def test_targeted_review_timestamp_must_be_valid_and_timezone_aware(phase, state, timestamp):
    evidence = _sensitive_evidence(phase, state)
    record = evidence['cloud_review']['reviews'][-1]
    if timestamp is None:
        record.pop('submitted_at')
    else:
        record['submitted_at'] = timestamp
    # A valid timestamp on the claim cannot replace the matched record's timestamp.
    evidence['cloud_review']['change']['targeted_review']['submitted_at'] = '2026-10-01T22:00:00Z'
    before = copy.deepcopy(evidence)

    assert _blockers(evidence, phase) == {
        'sensitive-review-authorization',
    }
    assert evidence == before


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('timestamp', ['2026-10-01T22:00:00Z', '2026-10-01T18:00:00-04:00'])
def test_targeted_review_timestamp_accepts_valid_aware_record(phase, timestamp):
    evidence = _sensitive_evidence(phase)
    evidence['cloud_review']['reviews'][-1]['submitted_at'] = timestamp
    assert _blockers(evidence, phase) == set()


@pytest.mark.parametrize('review_id', [None, 0, -1, True, 2.0, '2'])
def test_sensitive_targeted_review_requires_positive_integer_id(review_id):
    evidence = _sensitive_evidence('pre-cutover')
    review = evidence['cloud_review']
    review['change']['targeted_review']['review_id'] = review_id
    review['reviews'][-1]['id'] = review_id
    assert 'sensitive-review-authorization' in _blockers(evidence)


@pytest.mark.parametrize('phase', PHASES)
def test_sensitive_targeted_review_requires_complete_collection(phase):
    evidence = _sensitive_evidence(phase)
    evidence['cloud_review']['reviews_complete'] = False
    assert _blockers(evidence, phase) == {'cloud-review-evidence'}


@pytest.mark.parametrize('reviewer_id', [
    OWNER_ID, COPILOT_AGENT_ID, COPILOT_REVIEWER_ID, 77, 0, -1, True, '5164171', 5164171.0,
])
def test_sensitive_targeted_reviewer_must_be_positive_and_independent(reviewer_id):
    evidence = _sensitive_evidence('pre-cutover', 'APPROVED')
    review = evidence['cloud_review']
    review['pull_author_id'] = 77
    review['change']['targeted_review']['reviewer_id'] = reviewer_id
    review['reviews'][-1]['user']['id'] = reviewer_id
    expected = {'sensitive-review-authorization'}
    if type(reviewer_id) is not int or reviewer_id < 1:
        expected.add('cloud-review-approval')
    assert _blockers(evidence) == expected


@pytest.mark.parametrize('author_id', [None, 0, -1, '198982749', 1.5])
def test_pr_author_identity_is_required_and_well_formed(author_id):
    evidence = _evidence()
    evidence['cloud_review']['pull_author_id'] = author_id
    assert 'cloud-review-evidence' in _blockers(evidence)


@pytest.mark.parametrize('field,value', [
    ('submitted_at', None),
    ('submitted_at', ''),
    ('submitted_at', 'not-a-date'),
    ('submitted_at', '2026-10-01T21:00:00'),
    ('id', None),
    ('id', 0),
    ('id', -1),
    ('id', '1'),
])
def test_malformed_authenticated_review_ordering_fails_closed(field, value):
    evidence = _evidence()
    evidence['cloud_review']['reviews'][0][field] = value
    assert 'cloud-review-approval' in _blockers(evidence)


@pytest.mark.parametrize('malformed', [
    pytest.param(None, id='non-object-review'),
    pytest.param({'state': 'CHANGES_REQUESTED'}, id='missing-user'),
    pytest.param({'user': None, 'state': 'CHANGES_REQUESTED'}, id='non-object-user'),
    pytest.param({'user': {}, 'state': 'CHANGES_REQUESTED'}, id='missing-user-id'),
    *[
        pytest.param({'user': {'id': value}, 'state': 'CHANGES_REQUESTED'}, id=f'user-id-{label}')
        for label, value in (
            ('string', '76'), ('boolean', True), ('zero', 0), ('negative', -1),
            ('float', 76.0), ('null', None),
        )
    ],
])
def test_every_review_author_is_validated_before_copilot_filtering(malformed):
    evidence = _evidence()
    evidence['cloud_review']['reviews'].append(malformed)

    assert _blockers(evidence) == {'cloud-review-approval'}


def test_well_formed_non_copilot_review_is_validated_then_filtered():
    evidence = _evidence()
    evidence['cloud_review']['reviews'].append({
        'id': 2, 'user': {'id': 76}, 'state': 'CHANGES_REQUESTED',
        'commit_id': evidence['cloud_review']['head_sha'],
        'submitted_at': '2026-10-01T22:00:00Z',
    })

    assert _blockers(evidence) == set()


def test_tied_latest_reviews_require_every_copilot_review_to_approve():
    evidence = _evidence()
    review = evidence['cloud_review']
    review['reviews'].append({
        'id': 2, 'user': {'id': COPILOT_REVIEWER_ID}, 'state': 'COMMENTED',
        'commit_id': review['head_sha'], 'submitted_at': '2026-10-01T21:00:00Z',
    })
    assert 'cloud-review-approval' in _blockers(evidence)

    review['reviews'][0]['state'] = 'COMMENTED'
    review['reviews'][1]['state'] = 'APPROVED'
    review['reviews'].reverse()
    assert _blockers(evidence) == {'cloud-review-approval'}
    for record in review['reviews']:
        record['state'] = 'APPROVED'
    assert _blockers(evidence) == set()


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
    expected_blockers = []
    if phase != 'pre-cutover' and not published_status:
        expected_blockers.insert(0, 'cloud-review-status')

    result = cli_main(['--phase', phase, str(path)])

    assert result == int(bool(expected_blockers))
    assert json.loads(capsys.readouterr().out) == {
        'ready': not expected_blockers, 'phase': phase, 'blockers': expected_blockers,
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


@pytest.mark.parametrize('phase', PHASES)
def test_cli_rejects_bounded_deeply_nested_json(tmp_path, phase):
    path = tmp_path / 'evidence.json'
    raw = '[' * 10000 + '0' + ']' * 10000
    assert len(raw.encode()) < MAX_EVIDENCE
    path.write_text(raw)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'autonomy_policy.py'

    result = subprocess.run(
        [sys.executable, str(script), '--phase', phase, str(path)],
        capture_output=True, text=True, timeout=10,
    )

    assert 'maximum recursion depth exceeded' in result.stderr
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert 'Autonomy policy evidence rejected:' in result.stderr
    assert json.loads(result.stdout) == {
        'ready': False, 'phase': phase, 'blockers': ['invalid-evidence'],
    }
    assert path.read_text() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_cli_rejects_duplicate_evidence_keys(tmp_path, capsys):
    path = tmp_path / 'evidence.json'
    path.write_text('{"repository": {}, "repository": {}}')

    result = cli_main(['--phase', 'pre-cutover', str(path)])
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report == {'ready': False, 'phase': 'pre-cutover', 'blockers': ['invalid-evidence']}
