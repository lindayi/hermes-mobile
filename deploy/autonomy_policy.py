"""Read-only validation of the repository's conditional premerge gate transition."""

import ast
import json
import re
import textwrap

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
    'pre-cutover': {'source-ci': 15368, 'integration-tests': None, 'agent-review': None},
    'post-cutover': {'source-ci': 15368, 'issue-link': 15368, 'cloud-review': None},
}
REQUIRED_FILES = (
    WORKFLOW_PATH,
    '.github/native-tests.json',
    '.github/host-tests.json',
    'deploy/release_artifact.py',
    'deploy/self_deploy.py',
    'deploy/ci_selection.py',
    'scripts/ci_tests.py',
    'deploy/cloud_coordinator.py',
)
SHA_RE = re.compile(r'[0-9a-f]{40}\Z')
HEX_RE = re.compile(r'[0-9a-f]{64}\Z')


def _const(node, value):
    return isinstance(node, ast.Constant) and node.value == value


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _functions(tree):
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _function(tree, name):
    return _functions(tree).get(name)


def _assignments(tree):
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result[target.id] = value
    return result


def _literal(node):
    if isinstance(node, ast.Call) and _call_name(node.func) in {'set', 'frozenset'} and len(node.args) == 1:
        return ast.literal_eval(node.args[0])
    return ast.literal_eval(node)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest key')
        result[key] = value
    return result


def _source_tree(files, name, blockers):
    text = files.get(name)
    if not isinstance(text, str):
        blockers.add('main-source-missing')
        return None
    try:
        return ast.parse(text, filename=name)
    except (SyntaxError, ValueError):
        blockers.add('main-source-invalid')
        return None


def _strings_and_names(node):
    strings, names = set(), set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, (str, int)):
            strings.add(child.value)
        elif isinstance(child, ast.Name):
            names.add(child.id)
    return strings, names


def _call_strings(node):
    result = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            result.add(_call_name(child.func))
    return result


def _workflow_contract(files, blockers):
    try:
        import yaml
    except ImportError:
        blockers.add('hosted-workflow-contract')
        return

    class UniqueLoader(yaml.BaseLoader):
        def construct_mapping(self, node, deep=False):
            keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
            if len(keys) != len(set(keys)):
                raise ValueError('Duplicate workflow key')
            return super().construct_mapping(node, deep)

    try:
        workflow = yaml.load(files[WORKFLOW_PATH], Loader=UniqueLoader)
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        blockers.add('hosted-workflow-contract')
        return
    jobs = workflow.get('jobs') if isinstance(workflow, dict) else None
    if (not isinstance(jobs, dict) or set(jobs) != HOSTED_JOBS | {'source-ci', 'attest'}
            or any(not isinstance(job, dict) for job in jobs.values())):
        blockers.add('hosted-workflow-contract')
        return
    if any(job.get('runs-on') != 'ubuntu-24.04' for job in jobs.values()):
        blockers.add('hosted-runner-contract')

    def steps(job):
        result = job.get('steps')
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            blockers.add('hosted-workflow-contract')
            return []
        return result

    def commands(items):
        return '\n'.join(value for step in items
                         if isinstance((value := step.get('run', '')), str))

    native_steps = steps(jobs['native'])
    native_commands = commands(native_steps)
    if (not any('--preflight' in step.get('run', '') for step in native_steps
                if isinstance(step.get('run', ''), str))
            or 'scripts/ci_tests.py native' not in native_commands
            or any('self-hosted' in str(job.get('runs-on', '')) for job in jobs.values())):
        blockers.add('native-job-contract')

    gate = jobs['source-ci']
    needs = gate.get('needs')
    if (gate.get('if') != '${{ always() }}' or not isinstance(needs, list)
            or len(needs) != len(HOSTED_JOBS) or set(needs) != HOSTED_JOBS):
        blockers.add('native-aggregate-dependency')
        return
    gate_steps = steps(gate)
    step = gate_steps[0] if gate_steps else {}
    if (not isinstance(step, dict) or not isinstance(step.get('env'), dict)
            or step['env'].get('RESULTS') != '${{ toJSON(needs) }}'):
        blockers.add('native-aggregate-contract')
        return
    run = step.get('run', '')
    if not isinstance(run, str):
        blockers.add('native-aggregate-contract')
        return
    match = re.search(r'python3\s+-\s+<<[\'"]?PY[\'"]?\s*\n(.*?)\n\s*PY\s*$', run, re.S)
    try:
        gate_tree = ast.parse(textwrap.dedent(match.group(1))) if match else None
    except SyntaxError:
        gate_tree = None
    if not gate_tree or not _fail_closed_aggregate(gate_tree):
        blockers.add('native-aggregate-contract')

    build_steps, browser_steps = steps(jobs['build']), steps(jobs['browser'])
    attest_steps = steps(jobs['attest'])
    build_text, browser_text = commands(build_steps), commands(browser_steps)
    build_uploads = [
        (item.get('with') or {}).get('name')
        for item in build_steps
        if isinstance(item, dict) and str(item.get('uses', '')).startswith('actions/upload-artifact@')
    ]
    browser_downloads = [
        (item.get('with') or {}).get('name')
        for item in browser_steps
        if isinstance(item, dict) and str(item.get('uses', '')).startswith('actions/download-artifact@')
    ]
    attest = jobs['attest']
    attest_text = commands(attest_steps)
    attest_actions = [str(item.get('uses', '')) for item in attest_steps]
    if ('deploy.release_artifact build' not in build_text
            or 'release-${{ github.run_id }}-${{ github.run_attempt }}' not in build_uploads
            or 'release-${{ github.run_id }}-${{ github.run_attempt }}' not in browser_downloads
            or 'deploy.release_artifact unpack' not in browser_text
            or attest.get('needs') != 'source-ci'
            or attest.get('if') != "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && github.repository == 'lindayi/hermes-mobile' }}"
            or not any('attest-build-provenance@' in action for action in attest_actions)
            or attest_text):
        blockers.add('release-workflow-contract')


def _fail_closed_aggregate(tree):
    assignments = _assignments(tree)
    failed = assignments.get('failed')
    if not isinstance(failed, ast.DictComp):
        return False
    if (len(failed.generators) != 1
            or not isinstance(failed.generators[0].target, ast.Tuple)
            or [item.id for item in failed.generators[0].target.elts if isinstance(item, ast.Name)] != ['name', 'value']
            or not isinstance(failed.generators[0].iter, ast.Call)
            or _call_name(failed.generators[0].iter.func) != 'items'
            or not isinstance(failed.generators[0].iter.func, ast.Attribute)
            or not isinstance(failed.generators[0].iter.func.value, ast.Name)
            or failed.generators[0].iter.func.value.id != 'results'
            or not isinstance(failed.value, ast.Subscript)
            or not isinstance(failed.value.value, ast.Name) or failed.value.value.id != 'value'
            or not _const(failed.value.slice, 'result')
            or not any(
                isinstance(condition, ast.Compare) and condition.ops
                and isinstance(condition.ops[0], ast.NotEq)
                and condition.comparators and _const(condition.comparators[0], 'success')
                and isinstance(condition.left, ast.Subscript)
                and isinstance(condition.left.value, ast.Name) and condition.left.value.id == 'value'
                and _const(condition.left.slice, 'result')
                for condition in failed.generators[0].ifs
            )):
        return False
    expected = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and _call_name(node.func) == 'exit'
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'exit'
                and node.args and isinstance(node.args[0], ast.BoolOp)
                and isinstance(node.args[0].op, ast.Or)):
            expression = node.args[0]
            left, right = expression.values
            if (isinstance(left, ast.Call) and _call_name(left.func) == 'bool'
                    and left.args and isinstance(left.args[0], ast.Name)
                    and left.args[0].id == 'failed'
                    and isinstance(right, ast.Compare) and isinstance(right.ops[0], ast.NotEq)
                    and isinstance(right.left, ast.Call) and _call_name(right.left.func) == 'set'
                    and right.left.args and isinstance(right.left.args[0], ast.Name)
                    and right.left.args[0].id == 'results'
                    and isinstance(right.comparators[0], ast.Set)):
                try:
                    expected = set(ast.literal_eval(right.comparators[0]))
                except (TypeError, ValueError):
                    expected = None
    return expected == HOSTED_JOBS and {'loads', 'exit'} <= _call_strings(tree)


def _hosted_release_contract(files, blockers):
    try:
        host = json.loads(files['.github/host-tests.json'], object_pairs_hook=_unique_object)
        native = json.loads(files['.github/native-tests.json'], object_pairs_hook=_unique_object)
    except (KeyError, TypeError, ValueError):
        blockers.add('installed-host-gate')
        return
    if (not isinstance(host, dict) or not host or not isinstance(native, dict) or not native
            or not set(native) <= set(host)
            or any(not isinstance(reason, str) or not reason.strip() for reason in (*host.values(), *native.values()))):
        blockers.add('installed-host-gate')

    deploy_tree = _source_tree(files, 'deploy/self_deploy.py', blockers)
    release_tree = _source_tree(files, 'deploy/release_artifact.py', blockers)
    selection_tree = _source_tree(files, 'deploy/ci_selection.py', blockers)
    cli_tree = _source_tree(files, 'scripts/ci_tests.py', blockers)
    if any(tree is None for tree in (deploy_tree, release_tree, selection_tree, cli_tree)):
        return
    source_trees = _assignments(deploy_tree).get('SOURCE_TREES')
    try:
        if '.github' not in ast.literal_eval(source_trees):
            blockers.add('installed-host-gate')
    except (TypeError, ValueError):
        blockers.add('installed-host-gate')
    run_host = _function(deploy_tree, 'run_host_checks')
    hosted_branch = _function(deploy_tree, '_deploy')
    host_calls = _call_strings(run_host) if run_host else set()
    host_text, host_names = _strings_and_names(run_host) if run_host else (set(), set())
    if not ({'select_tests', 'run_suite'} <= host_calls
            or 'host' not in host_text or 'extra_args' not in host_names):
        blockers.add('installed-host-gate')
    if hosted_branch is None or not _host_run_branch(hosted_branch):
        blockers.add('installed-host-gate')
    select_tests = _function(selection_tree, 'select_tests')
    select_text, select_names = _strings_and_names(select_tests) if select_tests else (set(), set())
    ci_main = _function(cli_tree, 'main')
    ci_text = _strings_and_names(ci_main)[0] if ci_main else set()
    ci_calls = _call_strings(ci_main) if ci_main else set()
    if (select_tests is None or not {'host', 'native', '.github/host-tests.json',
                                     '.github/native-tests.json'} <= select_text
            or not {'python', 'host', 'native'} <= select_names
            or ci_main is None or not {'host', 'native'} <= ci_text
            or not {'select_tests', 'run_suite'} <= ci_calls):
        blockers.add('installed-host-gate')

    assignments = _assignments(release_tree)
    try:
        expected_jobs = set(_literal(assignments['EXPECTED_JOBS']))
    except (KeyError, TypeError, ValueError):
        expected_jobs = set()
    run_record = _function(release_tree, '_run_record')
    check_jobs = _function(release_tree, '_check_jobs')
    attestation = _function(release_tree, '_attestation')
    acquire = _function(release_tree, 'acquire_verified_bundle')
    run_constants = _assignments(release_tree)
    try:
        trusted = (
            ast.literal_eval(run_constants['REPOSITORY']) == REPOSITORY
            and ast.literal_eval(run_constants['REPOSITORY_ID']) == REPOSITORY_ID
            and ast.literal_eval(run_constants['WORKFLOW_ID']) == WORKFLOW_ID
            and ast.literal_eval(run_constants['WORKFLOW']) == WORKFLOW_PATH
            and ast.literal_eval(run_constants['REF']) == REF
        )
    except (KeyError, TypeError, ValueError):
        trusted = False
    check_text, check_names = _strings_and_names(check_jobs) if check_jobs else (set(), set())
    attest_text, attest_names = _strings_and_names(attestation) if attestation else (set(), set())
    acquire_calls = _call_strings(acquire) if acquire else set()
    run_text, run_names = _strings_and_names(run_record) if run_record else (set(), set())
    if (expected_jobs != RUN_JOBS or not trusted
            or not {'push', 'main', 'completed', 'success', 'workflow_id', 'head_sha'} <= run_text
            or not {'REPOSITORY_ID', 'WORKFLOW_ID', 'REPOSITORY'} <= run_names
            or not {'run_id', 'run_attempt', 'head_sha', 'completed', 'success'} <= check_text
            or not {'len', '_listed'} <= _call_strings(check_jobs)
            or 'EXPECTED_JOBS' not in check_names
            or not {'_run_record', '_check_jobs', '_attestation'} <= acquire_calls
            or not {'--cert-identity', '--source-ref', '--source-digest', '--signer-digest',
                    '--deny-self-hosted-runners', 'runnerEnvironment', 'sourceRepositoryDigest',
                    'buildSignerDigest', 'runInvocationURI'} <= attest_text
            or not {'REPOSITORY_ID', 'WORKFLOW_ID', 'REPOSITORY', 'REF', 'WORKFLOW'} <= attest_names
            or run_record is None or check_jobs is None or attestation is None or acquire is None):
        blockers.add('release-artifact-provenance')


def _host_run_branch(function):
    for node in ast.walk(function):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if (isinstance(test.left, ast.Name) and test.left.id == 'hosted_run_id'
                and any(_const(value, None) for value in test.comparators)):
            body_calls = _call_strings(ast.Module(body=node.body, type_ignores=[]))
            else_calls = _call_strings(ast.Module(body=node.orelse, type_ignores=[]))
            return 'checks' in body_calls and 'run_host_checks' in else_calls
    return False


def _coordinator_contract(files, blockers):
    tree = _source_tree(files, 'deploy/cloud_coordinator.py', blockers)
    if tree is None:
        return
    constants = _assignments(tree)
    expected = {
        'REPOSITORY': REPOSITORY, 'REPOSITORY_ID': REPOSITORY_ID,
        'OWNER_ID': OWNER_ID, 'COPILOT_REVIEWER_ID': COPILOT_REVIEWER_ID,
        'COPILOT_AGENT_ID': COPILOT_AGENT_ID, 'SOURCE_WORKFLOW_ID': WORKFLOW_ID,
        'MAIN_BRANCH': 'main',
    }
    try:
        identities_match = all(ast.literal_eval(constants[name]) == value for name, value in expected.items())
    except (KeyError, TypeError, ValueError):
        identities_match = False
    review = _function(tree, 'copilot_review_valid')
    threads = _function(tree, '_complete_resolved_threads')
    plan = _function(tree, '_plan_pull')
    identity = _function(tree, '_identity')
    sensitive_command = _function(tree, '_is_owner_sensitive_command')
    scan = _function(tree, '_scan_enrollments')
    review_contract = _valid_review_contract(review, threads)
    identity_names = _strings_and_names(identity)[1] if identity else set()
    sensitive_text, sensitive_names = _strings_and_names(sensitive_command) if sensitive_command else (set(), set())
    plan_calls = _call_strings(plan) if plan else set()
    scan_calls = _call_strings(scan) if scan else set()
    if (not identities_match or review is None or threads is None or plan is None
            or not review_contract or identity is None or sensitive_command is None or scan is None
            or not {'REPOSITORY', 'REPOSITORY_ID', 'OWNER_ID'} <= identity_names
            or 'OWNER_ID' not in sensitive_names
            or not any(isinstance(value, str) and 'authorize-sensitive' in value and '[0-9a-f]{40}' in value
                       for value in sensitive_text)
            or '_is_owner_sensitive_command' not in scan_calls
            or not {'copilot_review_valid', 'classify_sensitive_paths'} <= plan_calls
            or 'sensitive_sha' not in _strings_and_names(plan)[0]):
        blockers.add('coordinator-review-contract')


def _path(node):
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Subscript):
        key = node.slice.value if isinstance(node.slice, ast.Index) else node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return _path(node.value) + (key.value,)
    return ()


def _get_comparison(node, obj, key, expected):
    if (not isinstance(node, ast.Compare) or len(node.ops) != 1
            or not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1
            or not isinstance(node.left, ast.Call) or _call_name(node.left.func) != 'get'
            or not isinstance(node.left.func, ast.Attribute)
            or not isinstance(node.left.func.value, ast.Name)
            or node.left.func.value.id != obj or not node.left.args
            or not _const(node.left.args[0], key)):
        return False
    value = node.comparators[0]
    if isinstance(expected, tuple) and expected[0] == 'name':
        return isinstance(value, ast.Name) and value.id == expected[1]
    return _const(value, expected)


def _valid_review_contract(review, threads):
    if review is None or threads is None:
        return False
    returns = [node.value for node in review.body if isinstance(node, ast.Return)]
    if not returns or not isinstance(returns[-1], ast.BoolOp) or not isinstance(returns[-1].op, ast.And):
        return False
    final = returns[-1].values
    if len(final) != 2 or not {
        'APPROVED', 'head_sha',
    } <= {item for node in final for item in _strings_and_names(node)[0] | _strings_and_names(node)[1]}:
        return False
    if not (_get_comparison(final[0], 'latest', 'state', 'APPROVED')
            and _get_comparison(final[1], 'latest', 'commit_id', ('name', 'head_sha'))):
        return False
    assignments = {
        target.id: node.value
        for node in ast.walk(review)
        if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)
    }
    authored = assignments.get('authored')
    if (not isinstance(authored, ast.ListComp) or len(authored.generators) != 1
            or not isinstance(authored.generators[0].iter, ast.Name)
            or authored.generators[0].iter.id != 'reviews'
            or not any(isinstance(condition, ast.Compare) and len(condition.ops) == 1
                       and isinstance(condition.ops[0], ast.Eq)
                       and _path(condition.left) == ('review', 'user', 'id')
                       and isinstance(condition.comparators[0], ast.Name)
                       and condition.comparators[0].id == 'COPILOT_REVIEWER_ID'
                       for condition in ast.walk(authored))):
        return False
    latest = assignments.get('latest')
    if (not isinstance(latest, ast.Call) or _call_name(latest.func) != 'max'
            or not latest.args or not isinstance(latest.args[0], ast.Name)
            or latest.args[0].id != 'authored'
            or not any(keyword.arg == 'key' and isinstance(keyword.value, ast.Lambda)
                       and 'submitted_at' in _strings_and_names(keyword.value)[0]
                       for keyword in latest.keywords)):
        return False
    names = _strings_and_names(review)[1]
    if not {'reviews_complete', 'threads_complete', '_complete_resolved_threads'} <= names | _call_strings(review):
        return False
    thread_calls = _call_strings(threads)
    thread_text = _strings_and_names(threads)[0]
    if 'all' not in thread_calls or not {'isResolved', 'comments_complete'} <= thread_text:
        return False
    return True


def _static_contracts(files, blockers):
    for name in REQUIRED_FILES:
        if not isinstance(files.get(name), str):
            blockers.add('main-source-missing')
    if 'main-source-missing' in blockers:
        return
    _workflow_contract(files, blockers)
    _hosted_release_contract(files, blockers)
    _coordinator_contract(files, blockers)


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
            if not isinstance(check, dict) or not isinstance(check.get('context'), str):
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


def _check_review(evidence, main_sha, blockers):
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
    if (not isinstance(status, dict) or status.get('context') != 'cloud-review'
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
        if any(not isinstance(value, str) for value in files.values()):
            blockers.add('main-source-invalid')
    try:
        _static_contracts(files, blockers)
        _check_protection(evidence, phase, blockers)
        if sha:
            _check_source_run(evidence, sha, blockers)
            _check_review(evidence, sha, blockers)
    except (AttributeError, IndexError, KeyError, RecursionError, TypeError, ValueError):
        blockers.add('malformed-evidence')
    return {'ready': not blockers, 'phase': phase, 'blockers': sorted(blockers)}
