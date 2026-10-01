"""Synthetic outbound API/subprocess/SQLite tests; never touch live services."""
import copy
import importlib.util
import re
from datetime import datetime, timezone

import pytest

SHA = 'a' * 40
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
REPO = {'id': 1399942965, 'full_name': 'lindayi/hermes-mobile',
        'owner': {'id': 5164171, 'login': 'lindayi'}, 'default_branch': 'main'}
PREFIX = '/repos/lindayi/hermes-mobile'
BASE = 'b' * 40
RELEASE = 'e' * 32
OID = '1' * 40
ZERO = '0' * 40
TREE_BASE = 'c' * 40
TREE_HEAD = 'd' * 40
V2_JOBS = PREFIX + '/actions/runs/20/jobs?filter=latest&per_page=100'


def module():
    assert importlib.util.find_spec('deploy.pull_delivery'), 'guarded pull worker is missing'
    from deploy import pull_delivery
    return pull_delivery


def evidence():
    def run(run_id, workflow_id, path, event):
        return {'id': run_id, 'workflow_id': workflow_id, 'path': path, 'event': event,
                'repository': copy.deepcopy(REPO), 'head_repository': copy.deepcopy(REPO),
                'head_branch': 'main', 'head_sha': SHA, 'status': 'completed',
                'conclusion': 'success', 'run_attempt': 1}
    request = {'id': 50, 'sha': SHA, 'ref': SHA, 'task': 'deploy:mobile',
               'environment': 'production', 'production_environment': True,
               'transient_environment': False,
               'url': PREFIX.replace('/repos/', 'https://api.github.com/repos/') + '/deployments/50',
               'created_at': '2026-10-01T11:30:00Z',
               'creator': {'id': 41898282, 'login': 'github-actions[bot]', 'type': 'Bot'},
               'payload': {'version': 1, 'source_run_id': 10, 'approval_run_id': 20}}
    api = {
        PREFIX: copy.deepcopy(REPO),
        PREFIX + '/git/ref/heads/main': {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': SHA}},
        PREFIX + '/actions/workflows/production.yml': {'id': 100, 'path': '.github/workflows/production.yml', 'state': 'active'},
        PREFIX + '/actions/runs/10': run(10, 372155405, '.github/workflows/ci.yml', 'push'),
        PREFIX + '/actions/runs/20': run(20, 100, '.github/workflows/production.yml', 'workflow_run'),
        PREFIX + '/actions/runs/20/jobs?filter=latest&per_page=100': {'total_count': 1, 'jobs': [
            {'id': 30, 'run_id': 20, 'name': 'Approve production', 'status': 'completed', 'conclusion': 'success',
             'started_at': '2026-10-01T11:29:00Z', 'completed_at': '2026-10-01T11:31:00Z'}]},
        PREFIX + '/actions/runs/20/approvals': [
            {'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'},
             'environments': [{'id': 40, 'name': 'production'}], 'comment': 'ship'}],
        PREFIX + '/deployments/50/statuses?per_page=100': [{'state': 'queued',
            'log_url': 'https://github.com/lindayi/hermes-mobile/actions/runs/20'}],
    }
    return request, api


def test_owner_approved_current_main_intent_is_accepted():
    m = module()
    request, api = evidence()
    intent = m.validate_intent(request, lambda path: copy.deepcopy(api[path]), now=NOW)
    assert (intent.sha, intent.source_run_id, intent.approval_run_id, intent.deployment_id) == (SHA, 10, 20, 50)
    assert intent.key == SHA + ':20'


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'running', 'queued',
                                    'stopping', 'waiting_for_approval', 'unknown', 'other', None])
def test_idle_inspection_is_read_only_and_fail_closed(tmp_path, status):
    import sqlite3
    m = module()
    assert hasattr(m, 'require_idle'), 'read-only idle preflight is missing'
    database = tmp_path / 'runs.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE runs(status TEXT)')
        db.execute('INSERT INTO runs VALUES (?)', (status,))
    before = database.read_bytes()
    if status in {'completed', 'failed', 'cancelled'}:
        m.require_idle(database)
    else:
        with pytest.raises(m.Deferred, match='busy|unknown'):
            m.require_idle(database)
    assert database.read_bytes() == before


def test_missing_database_is_not_created(tmp_path):
    m = module()
    assert hasattr(m, 'require_idle'), 'read-only idle preflight is missing'
    database = tmp_path / 'missing.sqlite'
    with pytest.raises(m.Deferred):
        m.require_idle(database)
    assert not database.exists()


def worker_fixture(tmp_path, monkeypatch):
    import json
    import sqlite3
    from types import SimpleNamespace
    m = module()
    assert hasattr(m, 'poll_once'), 'single-pass delivery is missing'
    request, api = evidence()
    api[PREFIX + '/deployments?task=deploy%3Amobile&environment=production&per_page=100'] = [request]
    paths = m.Paths(source=tmp_path / 'source', state=tmp_path / 'delivery',
                    controller_state=tmp_path / 'controller', database=tmp_path / 'runs.sqlite')
    paths.source.mkdir()
    paths.controller_state.mkdir()
    with sqlite3.connect(paths.database) as db:
        db.execute('CREATE TABLE runs(status TEXT)')
    effects = []
    head = ['b' * 40]

    def git(source, *args):
        effects.append(('git', args))
        if args == ('rev-parse', '--show-toplevel'):
            return str(source)
        if args == ('symbolic-ref', '--quiet', 'HEAD'):
            return 'refs/heads/main'
        if args == ('remote', 'get-url', '--all', 'origin'):
            return 'https://github.com/lindayi/hermes-mobile.git'
        if args == ('rev-parse', 'refs/remotes/origin/main'):
            return SHA
        if args == ('rev-parse', 'HEAD'):
            return head[0]
        if args == ('merge', '--ff-only', '--no-edit', SHA):
            head[0] = SHA
            return ''
        return ''

    from deploy import git_source
    monkeypatch.setattr(git_source, '_git', git)
    monkeypatch.setenv('INVOCATION_ID', 'f' * 32)

    def run(argv, **kwargs):
        effects.append(('run', argv, kwargs))
        (paths.controller_state / 'status.json').write_text(json.dumps({'status': 'succeeded', 'git_sha': SHA}))
        return SimpleNamespace(returncode=0)

    def post(path, body):
        effects.append(('post', path, body))

    return m, paths, request, api, effects, run, post


def test_single_pass_invokes_fixed_controller_then_persists_exact_success(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    result = m.poll_once(paths, get=lambda path: copy.deepcopy(api[path]), post=post, run=run, now=NOW)
    assert result['status'] == 'deployed'
    calls = [e for e in effects if e[0] == 'run']
    assert len(calls) == 1
    assert calls[0][1] == [str(paths.source / '.venv/bin/python'), '-m', 'deploy.self_deploy',
                            '--worker', '--hosted-run-id', '10']
    assert calls[0][2]['cwd'] == paths.source
    assert 'env' not in calls[0][2]  # Inherit, never forge systemd INVOCATION_ID.
    assert [e[2]['state'] for e in effects if e[0] == 'post'] == ['in_progress', 'success']
    assert (paths.state.stat().st_mode & 0o777) == 0o700
    assert ((paths.state / 'state.json').stat().st_mode & 0o777) == 0o600
    effects.clear()
    result = m.poll_once(paths, get=lambda path: copy.deepcopy(api[path]), post=post, run=run, now=NOW)
    assert result['status'] == 'duplicate'
    assert not effects


def test_api_transport_and_cli_check_only_never_write(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    assert hasattr(m, 'main') and hasattr(m, 'GitHub'), 'CLI/API transport is missing'
    calls = []

    def gh(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[:6] == ['gh', 'api', '--hostname', 'github.com', '--method', 'GET']
        assert 'input' not in kwargs
        return SimpleNamespace(stdout=json.dumps(api[argv[-1]]), returncode=0)

    client = m.GitHub(run=gh)
    assert m.main(['--check-only'], paths=paths, client=client, run=run, now=NOW) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ready'
    assert calls
    assert not paths.state.exists()
    assert not [e for e in effects if e[0] in {'post', 'run'} or e[1][0] in {'fetch', 'merge'}]
    calls.clear()
    assert m.main(['--status'], paths=paths, client=client, run=run) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'queued'
    assert not calls
    assert not paths.state.exists()


@pytest.mark.parametrize('target,field,value', [
    ('request', 'task', 'deploy'), ('request', 'environment', 'staging'),
    ('request', 'sha', 'b' * 40), ('request', 'ref', 'main'),
    ('request', 'production_environment', False), ('request', 'transient_environment', True),
    ('request', 'url', 'https://api.github.com/repos/evil/repo/deployments/50'),
    ('request', 'creator', {'id': 41898282, 'login': 'someone', 'type': 'Bot'}),
    ('request', 'created_at', '2026-09-29T11:30:00Z'),
    ('request', 'created_at', '2026-10-02T11:30:00Z'),
    ('request', 'payload', {'version': 1, 'source_run_id': True, 'approval_run_id': 20}),
    ('request', 'payload', {'version': 1, 'source_run_id': 10, 'approval_run_id': 20, 'command': 'id'}),
    ('request', 'payload', '{"version":1}'),
    ('repo', 'id', 9), ('repo', 'default_branch', 'develop'),
    ('source', 'event', 'pull_request'), ('source', 'head_branch', 'topic'),
    ('source', 'head_sha', 'b' * 40), ('source', 'head_repository', {'id': 9}),
    ('source', 'repository', {'id': 9}), ('source', 'workflow_id', 9),
    ('source', 'conclusion', 'failure'), ('source', 'status', 'in_progress'),
    ('source', 'path', '.github/workflows/evil.yml'),
    ('approval', 'event', 'workflow_dispatch'), ('approval', 'run_attempt', 2),
    ('approval', 'head_sha', 'b' * 40), ('approval', 'head_branch', 'topic'),
    ('approval', 'workflow_id', 999), ('approval', 'conclusion', 'cancelled'),
    ('approval', 'path', '.github/workflows/evil.yml'),
    ('job', 'name', 'Unapproved job'), ('job', 'conclusion', 'skipped'),
    ('job', 'completed_at', '2026-10-01T11:29:30Z'),
    ('job', 'started_at', '2026-10-01T11:30:30Z'),
])
def test_intent_provenance_field_tampering_is_blocked(target, field, value):
    m = module()
    request, api = evidence()
    targets = {'request': request, 'repo': api[PREFIX], 'source': api[PREFIX + '/actions/runs/10'],
               'approval': api[PREFIX + '/actions/runs/20'],
               'job': api[PREFIX + '/actions/runs/20/jobs?filter=latest&per_page=100']['jobs'][0]}
    targets[target][field] = value
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


@pytest.mark.parametrize('reviews', [[],
    [{'state': 'approved', 'user': {'id': 1, 'login': 'lindayi'}, 'environments': [{'name': 'production'}]}],
    [{'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'staging'}]}],
    [{'state': 'rejected', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production'}]}],
    [{'state': 'pending', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production'}]}],
])
def test_successful_job_or_admin_bypass_does_not_replace_actual_owner_approval(reviews):
    m = module()
    request, api = evidence()
    api[PREFIX + '/actions/runs/20/approvals'] = reviews
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


def test_busy_never_syncs_checks_or_closes_admission_and_can_later_retry(tmp_path, monkeypatch):
    import sqlite3
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(paths.database) as db:
        db.execute('INSERT INTO runs VALUES (NULL)')
    before = paths.database.read_bytes()
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'deferred'
    assert not effects
    assert paths.database.read_bytes() == before
    with sqlite3.connect(paths.database) as db:
        db.execute("UPDATE runs SET status='completed'")
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'deployed'


@pytest.mark.parametrize('failure', ['nonzero', 'wrong_sha', 'old_status', 'bad_status', 'post'])
def test_failed_or_ambiguous_attempt_never_automatically_redeploys(tmp_path, monkeypatch, failure):
    import json
    import subprocess
    from types import SimpleNamespace
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    (paths.controller_state / 'status.json').write_text(json.dumps({'status': 'succeeded', 'git_sha': SHA}))
    calls = []

    def fail_run(argv, **kwargs):
        calls.append(argv)
        if failure == 'nonzero':
            raise subprocess.CalledProcessError(1, argv)
        if failure != 'old_status':
            (paths.controller_state / 'status.json').write_text(json.dumps({
                'status': 'failed' if failure == 'bad_status' else 'succeeded',
                'git_sha': 'b' * 40 if failure == 'wrong_sha' else SHA}))
        return SimpleNamespace(returncode=0)

    def fail_post(path, body):
        if failure == 'post':
            raise RuntimeError('unavailable')
        post(path, body)

    assert m.poll_once(paths, get=api.__getitem__, post=fail_post, run=fail_run, now=NOW)['status'] == 'failed'
    count = len(calls)
    assert m.poll_once(paths, get=api.__getitem__, post=fail_post, run=fail_run, now=NOW)['status'] == 'duplicate'
    assert len(calls) == count
    assert not [e for e in effects if e[0] == 'post' and e[2]['state'] == 'success']


@pytest.mark.parametrize('lock_name', ['worker', 'controller'])
def test_concurrent_worker_or_controller_defers_without_sync(tmp_path, monkeypatch, lock_name):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    paths.state.mkdir(mode=0o700)
    path = paths.state / 'worker.lock' if lock_name == 'worker' else paths.controller_state / 'deploy.lock'
    with m.exclusive(path):
        result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'deferred'
    assert not effects


def test_main_moving_after_sync_blocks_controller(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    reads = []

    def get(path):
        result = copy.deepcopy(api[path])
        if path.endswith('/git/ref/heads/main'):
            if reads:
                result['object']['sha'] = 'b' * 40
            reads.append(path)
        return result

    assert m.poll_once(paths, get=get, post=post, run=run, now=NOW)['status'] == 'blocked'
    assert not [e for e in effects if e[0] in {'run', 'post'}]


def test_approval_workflow_not_complete_defers_without_consuming_intent(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    api[PREFIX + '/actions/runs/20']['status'] = 'in_progress'
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'approval'
    assert not effects
    api[PREFIX + '/actions/runs/20']['status'] = 'completed'
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'deployed'


def test_dirty_checkout_blocks_without_destructive_git_commands(tmp_path, monkeypatch):
    from deploy import git_source
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    original = git_source._git
    monkeypatch.setattr(git_source, '_git', lambda source, *args:
                        ' M owner-file' if args[0] == 'status' else original(source, *args))
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'blocked'
    assert not [e for e in effects if e[0] != 'git' or e[1][0] in {'fetch', 'merge', 'stash', 'reset'}]


def production_workflow():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[1] / '.github/workflows/production.yml'
    assert path.exists(), 'trusted production approval workflow is missing'
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


GITHUB_SCRIPT = 'actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3'  # v9.0.0
JOB_NAMES = {'classify': 'Classify release', 'promote-routine': 'Promote routine release',
             'promote-sensitive': 'Promote sensitive release'}


def test_workflow_and_units_have_no_untrusted_checkout_or_production_runner():
    from pathlib import Path
    workflow = production_workflow()
    assert workflow['on'] == {'workflow_run': {'workflows': ['Source checks'], 'types': ['completed'], 'branches': ['main']}}
    assert workflow['permissions'] == {}
    assert {key: job['name'] for key, job in workflow['jobs'].items()} == JOB_NAMES
    read = {'contents': 'read', 'actions': 'read', 'deployments': 'read'}
    write = {'contents': 'read', 'actions': 'read', 'deployments': 'write'}
    classify = workflow['jobs']['classify']
    assert classify['permissions'] == read and 'environment' not in classify and 'needs' not in classify
    for condition in ("github.repository == 'lindayi/hermes-mobile'", "github.repository_id == '1399942965'",
                      "github.ref == 'refs/heads/main'", "github.event.workflow_run.event == 'push'",
                      "github.event.workflow_run.head_branch == 'main'",
                      'github.event.workflow_run.repository.id == 1399942965',
                      'github.event.workflow_run.head_repository.id == 1399942965',
                      "github.event.workflow_run.conclusion == 'success'"):
        assert condition in classify['if']
    environments = {'promote-routine': 'production', 'promote-sensitive': 'production-sensitive'}
    for key, environment in environments.items():
        job = workflow['jobs'][key]
        risk = key.split('-')[1]
        assert job['environment'] == environment
        assert job['needs'] == 'classify'
        assert job['if'] == f"needs.classify.outputs.risk == '{risk}'"
        assert job['permissions'] == write
        assert job['steps'][0]['env']['RELEASE_RISK'] == risk
    scripts = [workflow['jobs'][key]['steps'][0]['with']['script'] for key in environments]
    assert scripts[0] == scripts[1]  # One reviewed promotion script; job ID selects the path.
    for job in workflow['jobs'].values():
        assert job['runs-on'] == 'ubuntu-24.04'
        assert len(job['steps']) == 1
        assert job['steps'][0]['uses'] == GITHUB_SCRIPT
        assert '${{' not in job['steps'][0]['with']['script']  # No expression-to-JavaScript injection.
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/production.yml').read_text()
    assert 'checkout' not in text and 'secrets.' not in text
    assert not re.search(r'^\s*(?:-\s*)?run:', text, re.M)  # No shell steps.
    root = Path(__file__).resolve().parents[1]
    service = root / 'deploy/hermes-mobile-delivery.service'
    timer = root / 'deploy/hermes-mobile-delivery.timer'
    assert service.exists() and timer.exists(), 'timer/service definitions are missing'
    text = service.read_text()
    for setting in ('NoNewPrivileges=yes', 'UMask=0077', 'Type=oneshot', 'TimeoutStartSec=infinity',
                    'RefuseManualStop=yes', 'Restart=no', '-m deploy.pull_delivery --once'):
        assert setting in text
    assert 'EnvironmentFile=' not in text
    assert 'OnUnitInactiveSec=' in timer.read_text()


HARNESS = r'''
const INPUT = __INPUT__;
const P = '/repos/lindayi/hermes-mobile';
const reads = [], writes = [], outputs = {};
const read = async (path, params) => {
  if (params.owner !== 'lindayi' || params.repo !== 'hermes-mobile') throw new Error('foreign repository');
  reads.push({path, params});
  if (!(path in INPUT.api)) { const error = new Error('Not Found: ' + path); error.status = 404; throw error; }
  return {data: JSON.parse(JSON.stringify(INPUT.api[path]))};
};
const core = {setFailed: msg => {throw new Error(msg)}, info: () => {}, notice: () => {},
  setOutput: (name, value) => { outputs[name] = String(value); }};
const context = {repo: {owner: 'lindayi', repo: 'hermes-mobile'}, runId: 20,
  ref: 'refs/heads/main', sha: INPUT.sha};
const github = {rest: {
  repos: {get: p => read(`/repos/${p.owner}/${p.repo}`, p),
    listDeployments: p => read(`${P}/deployments`, p),
    listDeploymentStatuses: p => read(`${P}/deployments/${p.deployment_id}/statuses`, p),
    compareCommitsWithBasehead: p => read(`${P}/compare/${p.basehead}`, p),
    createDeployment: async data => { writes.push(data); return {data: {id: 50}}; },
    createDeploymentStatus: async data => { writes.push(data); return {data: {}}; }},
  git: {getRef: p => read(`${P}/git/ref/${p.ref}`, p),
    getCommit: p => read(`${P}/git/commits/${p.commit_sha}`, p),
    getTree: p => read(`${P}/git/trees/${p.tree_sha}?recursive=${p.recursive}`, p)},
  actions: {getWorkflowRun: p => read(`${P}/actions/runs/${p.run_id}`, p)}},
  request: (route, p) => read(route.split(' ')[1].replace(/\{(\w+)\}/g, (_, key) => p[key]), p)};
(async () => { try { await (async () => { __SCRIPT__ })(); console.log(JSON.stringify({reads, writes, outputs})); }
catch (e) { console.log(JSON.stringify({reads, writes, outputs, error: String(e)})); }})();
'''


def run_script(job, api, *, env=None, main=None, sha=SHA):
    import json
    import os
    import shutil
    import subprocess
    script = production_workflow()['jobs'][job]['steps'][0]['with']['script']
    main = sha if main is None else main
    api = {**api, PREFIX + '/git/ref/heads/main': {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': main}}}
    harness = HARNESS.replace('__INPUT__', json.dumps({'api': api, 'sha': sha})).replace('__SCRIPT__', script)
    environment = {**os.environ, 'SOURCE_RUN_ID': '10', 'APPROVAL_RUN_ID': '20', 'SOURCE_SHA': sha,
                   'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_JOB': job, **(env or {})}
    node = os.environ.get('HERMES_TEST_NODE')
    if node is None:
        node = shutil.which('node')
    if not node:
        raise RuntimeError('Managed test runner must provide Node through HERMES_TEST_NODE or PATH')
    result = subprocess.run([node, '-e', harness],
                            env=environment, check=True, text=True, capture_output=True, timeout=60)
    return json.loads(result.stdout)


@pytest.mark.parametrize('explicit', [True, False])
def test_run_script_uses_managed_node_selection(monkeypatch, explicit):
    import shutil
    import subprocess
    from types import SimpleNamespace
    selected = '/managed/node'
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout='{}')

    if explicit:
        monkeypatch.setenv('HERMES_TEST_NODE', selected)
    else:
        monkeypatch.delenv('HERMES_TEST_NODE', raising=False)
        monkeypatch.setattr(shutil, 'which', lambda name: selected if name == 'node' else None)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert run_script('classify', cloud_api()) == {}
    assert calls[0][0][0] == selected


BOT = {'id': 41898282, 'login': 'github-actions[bot]', 'type': 'Bot'}
OWNER = {'id': 5164171, 'login': 'lindayi', 'type': 'User'}


def add_cloud_tree_api(api, files, *, base=BASE, sha=SHA, base_entries=None, head_entries=None):
    """Add authenticated-style commit/tree responses bound to exact Git commits."""
    if base_entries is None or head_entries is None:
        base_entries, head_entries = [], []
        for index, file in enumerate(files):
            status, path = file.get('status'), file.get('filename')
            old_path = file.get('previous_filename', path)
            if status in {'removed', 'modified', 'renamed'}:
                base_entries.append({'path': old_path, 'mode': '100644', 'type': 'blob',
                                     'sha': f'{index + 1:040x}'})
            if status in {'added', 'modified', 'renamed'}:
                head_entries.append({'path': path, 'mode': '100644', 'type': 'blob',
                                     'sha': f'{index + 2:040x}'})
    for commit, tree, entries in ((base, TREE_BASE, base_entries), (sha, TREE_HEAD, head_entries)):
        api[PREFIX + f'/git/commits/{commit}'] = {'sha': commit, 'tree': {'sha': tree}}
        api[PREFIX + f'/git/trees/{tree}?recursive=1'] = {
            'sha': tree, 'truncated': False, 'tree': entries}


def cloud_api(files=None, history=None, *, base=BASE, sha=SHA):
    """Synthetic API: deployment 7 for BASE succeeded (owner-reported) unless overridden."""
    _, api = evidence()
    history = [(7, base, BOT, [('success', OWNER), ('in_progress', OWNER), ('queued', BOT)])] \
        if history is None else history
    api[PREFIX + '/deployments'] = [{'id': ident, 'sha': deployed_sha, 'task': 'deploy:mobile', 'environment': 'production',
                                     'creator': creator} for ident, deployed_sha, creator, _ in history]
    for ident, _, _, statuses in history:
        api[PREFIX + f'/deployments/{ident}/statuses'] = [{'state': state, 'creator': who}
                                                          for state, who in statuses]
    files = [{'filename': 'frontend/styles.css', 'status': 'modified'}] if files is None else files
    api[PREFIX + f'/compare/{base}...{sha}'] = {'status': 'ahead', 'ahead_by': 2, 'behind_by': 0,
                                                'merge_base_commit': {'sha': base}, 'files': files}
    api[PREFIX + '/actions/runs/10']['head_sha'] = sha
    add_cloud_tree_api(api, files, base=base, sha=sha)
    return api


def test_classify_routine_from_owner_reported_deployed_base_is_read_only():
    output = run_script('classify', cloud_api())
    assert 'error' not in output, output
    assert output['outputs'] == {'risk': 'routine', 'base_sha': BASE}
    assert not output['writes']
    listing = [r for r in output['reads'] if r['path'] == PREFIX + '/deployments'][0]['params']
    assert (listing['task'], listing['environment']) == ('deploy:mobile', 'production')


@pytest.mark.parametrize('history,base', [
    ([], ''),  # Bootstrap: no authenticated deployment history.
    ([(7, BASE, BOT, [('success', {'id': 9, 'login': 'mallory'}), ('queued', BOT)])], ''),
    ([(7, BASE, BOT, [('success', BOT), ('queued', BOT)])], ''),
    ([(7, BASE, OWNER, [('success', OWNER)])], ''),  # Not a bot-created intent.
    ([(8, 'c' * 40, BOT, [('failure', OWNER), ('in_progress', OWNER), ('queued', BOT)]),
      (7, BASE, BOT, [('success', OWNER), ('queued', BOT)])], ''),
    ([(8, 'c' * 40, BOT, [('in_progress', OWNER), ('queued', BOT)]),
      (7, BASE, BOT, [('success', OWNER), ('queued', BOT)])], ''),
    ([(8, 'c' * 40, BOT, [('queued', BOT)]), (7, BASE, BOT, [('success', OWNER), ('queued', BOT)])], BASE),
    ([(8, 'c' * 40, BOT, []), (7, BASE, BOT, [('success', OWNER)])], ''),
])
def test_classify_trusts_only_authentic_owner_reported_success(history, base):
    output = run_script('classify', cloud_api(history=history))
    assert 'error' not in output, output
    assert output['outputs'] == {'risk': 'routine' if base else 'sensitive', 'base_sha': base}


def test_classify_history_inspection_is_bounded():
    history = [(200 - index, 'c' * 40, BOT, [('queued', BOT)]) for index in range(30)] + \
        [(7, BASE, BOT, [('success', OWNER)])]
    output = run_script('classify', cloud_api(history=history))
    assert output['outputs'] == {'risk': 'sensitive', 'base_sha': ''}


@pytest.mark.parametrize('compare', [
    {'status': 'diverged', 'behind_by': 1}, {'status': 'behind', 'behind_by': 2},
    {'merge_base_commit': {'sha': 'c' * 40}}, {'status': 'ahead', 'behind_by': None}])
def test_classify_refuses_source_not_descending_from_base(compare):
    api = cloud_api()
    api[PREFIX + f'/compare/{BASE}...{SHA}'].update(compare)
    output = run_script('classify', api)
    assert output.get('error') and not output['outputs'] and not output['writes']


@pytest.mark.parametrize('fault', ['pr', 'fork', 'old_main', 'rerun', 'source_failed', 'other_workflow'])
def test_classify_requires_trusted_current_push_source(fault):
    api = cloud_api()
    source = api[PREFIX + '/actions/runs/10']
    env, main = {}, SHA
    if fault == 'pr':
        source['event'] = 'pull_request'
    elif fault == 'fork':
        source['head_repository']['id'] = 123
    elif fault == 'old_main':
        main = 'c' * 40
    elif fault == 'rerun':
        env['GITHUB_RUN_ATTEMPT'] = '2'
    elif fault == 'source_failed':
        source['conclusion'] = 'failure'
    elif fault == 'other_workflow':
        source['workflow_id'] = 9
    output = run_script('classify', api, env=env, main=main)
    assert output.get('error') and not output['outputs']


def parity_cases():
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('release_policy_cases', Path(__file__).with_name('test_release_policy.py'))
    tables = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tables)
    cases = [[('modified', path, None)] for path in tables.ROUTINE_PATHS + tables.SENSITIVE_PATHS]
    docs_root = Path(__file__).resolve().parents[1] / 'docs'
    cases += [[('modified', path.relative_to(docs_root.parent).as_posix(), None)]
              for path in sorted(docs_root.rglob('*.md'))]
    cases += [
        [], [('added', 'frontend/new.js', None), ('removed', 'docs/ux.md', None)],
        [('removed', 'frontend/viewport.mjs', None), ('added', 'docs/ux.md', None)],
        [('renamed', 'frontend/styles.css', 'backend/app.py')],
        [('renamed', 'frontend/styles.css', 'frontend/ui.mjs')],
        [('renamed', 'frontend/viewport.mjs', 'frontend/session-swipe.mjs')],
        [('renamed', 'frontend/viewport.mjs', None)],
        [('copied', 'frontend/viewport.mjs', 'frontend/session-swipe.mjs')],
        [('changed', 'frontend/styles.css', None)], [('unchanged', 'frontend/styles.css', None)],
        [('modified', 'frontend/styles.css', 'frontend/styles.css')],
        [('modified', f'tests/test_{index}.py', None) for index in range(250)],
        [('modified', f'tests/test_{index}.py', None) for index in range(251)],
    ]
    return cases


def test_cloud_classifier_matches_host_policy_exactly():
    """Execute the actual workflow classifier for every case; compare with deploy.release_policy."""
    from deploy import release_policy
    letters = {'added': 'A', 'removed': 'D', 'modified': 'M', 'renamed': 'R'}
    mismatches = []
    for case in parity_cases():
        files = [{'filename': path, 'status': status, **({'previous_filename': old} if old else {})}
                 for status, path, old in case]
        changes = [release_policy.Change(letters.get(status, status), path, old) for status, path, old in case]
        expected = release_policy.classify(changes).risk
        output = run_script('classify', cloud_api(files=files))
        if output['outputs'].get('risk') != expected:
            mismatches.append((case[:2], expected, output))
    assert not mismatches


@pytest.mark.parametrize('fault', [
    'missing_file', 'duplicate_file', 'missing_tree_path', 'duplicate_tree_path', 'unexpected_tree_path',
    'partial_file_inventory', 'unexpected_compare_path', 'missing_mode', 'unsupported_mode',
    'unsupported_status', 'truncated_tree', 'missing_truncated', 'wrong_commit', 'wrong_tree',
])
def test_cloud_classifier_routes_incomplete_or_unexpected_git_metadata_sensitive(fault):
    files = [{'filename': path, 'status': 'modified'}
             for path in ('frontend/styles.css', 'docs/ux.md')] if fault == 'partial_file_inventory' else None
    api = cloud_api(files)
    comparison = api[PREFIX + f'/compare/{BASE}...{SHA}']
    tree_key = PREFIX + f'/git/trees/{TREE_HEAD}?recursive=1'
    tree = api[tree_key]
    if fault == 'missing_file':
        comparison['files'] = []
    elif fault == 'duplicate_file':
        comparison['files'].append(copy.deepcopy(comparison['files'][0]))
    elif fault == 'missing_tree_path':
        tree['tree'].clear()
    elif fault == 'duplicate_tree_path':
        tree['tree'].append(copy.deepcopy(tree['tree'][0]))
    elif fault == 'unexpected_tree_path':
        tree['tree'].append({'path': 'tests/test_unreported.py', 'mode': '100644', 'type': 'blob', 'sha': OID})
    elif fault == 'partial_file_inventory':
        comparison['files'].pop()
    elif fault == 'unexpected_compare_path':
        comparison['files'].append({'filename': 'tests/test_unreported.py', 'status': 'modified'})
    elif fault == 'missing_mode':
        tree['tree'][0].pop('mode')
    elif fault == 'unsupported_mode':
        tree['tree'][0]['mode'] = '100777'
    elif fault == 'unsupported_status':
        comparison['files'][0]['status'] = 'copied'
    elif fault == 'truncated_tree':
        tree['truncated'] = True
    elif fault == 'missing_truncated':
        tree.pop('truncated')
    elif fault == 'wrong_commit':
        api[PREFIX + f'/git/commits/{SHA}']['sha'] = BASE
    elif fault == 'wrong_tree':
        tree['sha'] = BASE
    output = run_script('classify', api)
    assert 'error' not in output, output
    assert output['outputs'] == {'risk': 'sensitive', 'base_sha': BASE}


def promote_api(path='routine', reviews=None, *, base=BASE, sha=SHA, files=None,
                base_entries=None, head_entries=None):
    request, api = evidence_v2(path, base=base)
    approvals = api[PREFIX + '/actions/runs/20/approvals']
    comparison_base = base or BASE
    files = files or ([{'filename': 'frontend/styles.css', 'status': 'modified'}] if path == 'routine' else [
        {'filename': 'backend/app.py', 'status': 'modified'}])
    api.update(cloud_api(files, base=comparison_base, sha=sha))
    add_cloud_tree_api(api, files, base=comparison_base, sha=sha,
                       base_entries=base_entries, head_entries=head_entries)
    api[PREFIX + '/actions/runs/10']['head_sha'] = sha
    api[PREFIX + '/actions/runs/20/approvals'] = approvals if reviews is None else reviews
    return api


def test_promote_routine_creates_strict_v2_intent_without_owner_review():
    output = run_script('promote-routine', promote_api('routine'),
                        env={'RELEASE_RISK': 'routine', 'CLASSIFIED_RISK': 'routine', 'BASE_SHA': BASE})
    assert 'error' not in output, output
    deployment, status = output['writes']
    assert deployment['ref'] == SHA and deployment['task'] == 'deploy:mobile'
    assert deployment['environment'] == 'production' and deployment['production_environment'] is True
    assert deployment['transient_environment'] is False
    assert deployment['auto_merge'] is False and deployment['required_contexts'] == []
    assert deployment['payload'] == {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE}
    assert status['state'] == 'queued' and status['deployment_id'] == 50
    assert status['log_url'] == 'https://github.com/lindayi/hermes-mobile/actions/runs/20'


@pytest.mark.parametrize('change', ['regular_to_executable', 'executable_to_regular',
                                    'add_symlink', 'regular_to_symlink'])
def test_both_promotion_paths_recompute_real_git_modes_before_intent(tmp_path, change):
    from deploy import release_policy

    m = module()
    repo, base, sha, base_entries, head_entries = real_git_mode_release(tmp_path, change)
    changes = m.release_changes(repo, base, sha)
    assert changes and release_policy.classify(changes).risk == 'sensitive'
    statuses = {'A': 'added', 'D': 'removed', 'M': 'modified', 'T': 'modified'}
    files = [{'filename': item.path, 'status': statuses[item.status]} for item in changes]
    api = promote_api('sensitive', base=base, sha=sha, files=files,
                      base_entries=base_entries, head_entries=head_entries)
    sensitive = run_script('promote-sensitive', api, sha=sha,
                           env={'RELEASE_RISK': 'sensitive', 'CLASSIFIED_RISK': 'sensitive', 'BASE_SHA': base})
    assert 'error' not in sensitive, sensitive
    assert sensitive['writes'][0]['payload']['base_sha'] == base
    calls = [call['path'] for call in sensitive['reads']]
    assert f'{PREFIX}/git/commits/{base}' in calls and f'{PREFIX}/git/commits/{sha}' in calls
    assert f'{PREFIX}/git/trees/{TREE_BASE}?recursive=1' in calls
    assert f'{PREFIX}/git/trees/{TREE_HEAD}?recursive=1' in calls
    routine = run_script('promote-routine', api, sha=sha,
                         env={'RELEASE_RISK': 'routine', 'CLASSIFIED_RISK': 'routine', 'BASE_SHA': base})
    assert routine.get('error') and not routine['writes']


@pytest.mark.parametrize('base,expected', [(BASE, BASE), ('', None)])
def test_promote_sensitive_requires_actual_owner_approval(base, expected):
    output = run_script('promote-sensitive', promote_api('sensitive'),
                        env={'RELEASE_RISK': 'sensitive', 'CLASSIFIED_RISK': 'sensitive', 'BASE_SHA': base})
    assert 'error' not in output, output
    assert output['writes'][0]['payload'] == {'version': 2, 'source_run_id': 10, 'approval_run_id': 20,
                                              'base_sha': expected}


@pytest.mark.parametrize('job,fault', [
    ('promote-sensitive', 'no_review'), ('promote-sensitive', 'wrong_environment'),
    ('promote-sensitive', 'other_user'), ('promote-sensitive', 'rejected'),
    ('promote-routine', 'rejected'), ('promote-routine', 'no_base'), ('promote-routine', 'bad_base'),
    ('promote-routine', 'risk_mismatch'), ('promote-routine', 'job_mismatch'),
    ('promote-routine', 'pr'), ('promote-routine', 'fork'), ('promote-routine', 'old_main'),
    ('promote-routine', 'rerun'), ('promote-sensitive', 'rerun'), ('promote-sensitive', 'old_main'),
])
def test_promote_rejects_bypass_replay_superseded_or_mismatched_intent(job, fault):
    risk = job.split('-')[1]
    approval = {'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'},
                'environments': [{'name': 'production-sensitive'}]}
    reviews = {'no_review': [], 'wrong_environment': [{**approval, 'environments': [{'name': 'production'}]}],
               'other_user': [{**approval, 'user': {'id': 1, 'login': 'lindayi'}}],
               'rejected': [approval, {**approval, 'state': 'rejected'}]}.get(fault)
    api = promote_api(risk, reviews)
    env = {'RELEASE_RISK': risk, 'CLASSIFIED_RISK': risk, 'BASE_SHA': BASE}
    main = SHA
    source = api[PREFIX + '/actions/runs/10']
    if fault == 'no_base':
        env['BASE_SHA'] = ''
    elif fault == 'bad_base':
        env['BASE_SHA'] = 'BASE'
    elif fault == 'risk_mismatch':
        env['CLASSIFIED_RISK'] = 'sensitive'
    elif fault == 'job_mismatch':
        env['GITHUB_JOB'] = 'promote-sensitive'
    elif fault == 'pr':
        source['event'] = 'pull_request'
    elif fault == 'fork':
        source['head_repository']['id'] = 123
    elif fault == 'old_main':
        main = 'c' * 40
    elif fault == 'rerun':
        env['GITHUB_RUN_ATTEMPT'] = '2'
    output = run_script(job, api, env=env, main=main)
    assert output.get('error') and not output['writes'], output


def test_latest_request_only_and_durable_high_watermark(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    listing = PREFIX + '/deployments?task=deploy%3Amobile&environment=production&per_page=100'
    newer = copy.deepcopy(request)
    newer['id'] = 51
    newer['sha'] = 'c' * 40  # Invalid newer intent must not fall back to the older valid one.
    api[listing] = [request, newer]
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'blocked'
    assert not effects
    api[listing] = [request]
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'blocked' and 'Out-of-order' in result['reason']
    assert not effects


def test_empty_listing_after_observed_request_is_blocked(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    api[PREFIX + '/actions/runs/20']['status'] = 'in_progress'
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'approval'
    api[PREFIX + '/deployments?task=deploy%3Amobile&environment=production&per_page=100'] = []
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'blocked' and 'missing' in result['reason']
    assert not effects


def test_success_report_network_failure_preserves_local_deployed_no_replay(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)

    def fail_success(path, body):
        if body['state'] == 'success':
            raise RuntimeError('offline')
        post(path, body)

    result = m.poll_once(paths, get=api.__getitem__, post=fail_success, run=run, now=NOW)
    assert result['status'] == 'deployed' and 'reporting failed' in result['reason']
    effects.clear()
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'duplicate'
    assert not effects


def test_worker_crash_reservation_and_state_symlinks_do_not_replay(tmp_path, monkeypatch):
    import json
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    paths.state.mkdir(mode=0o700)
    state = {'version': 1, 'latest_id': 50, 'records': {SHA + ':20': {'status': 'running'}},
             'last': {'status': 'running'}}
    (paths.state / 'state.json').write_text(json.dumps(state))
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'duplicate'
    assert not effects
    (paths.state / 'state.json').unlink()
    victim = tmp_path / 'victim'
    victim.write_text('must not touch')
    (paths.state / 'state.json').symlink_to(victim)
    with pytest.raises(ValueError, match='Symlink'):
        m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert victim.read_text() == 'must not touch'


# ---- Version 2: routine/sensitive risk policy (issue #17) -------------------



def raw(*entries):
    """Synthetic `git diff --raw -z --no-abbrev` output for (status, path[, old, new])."""
    text = ''
    for status, path, *modes in entries:
        old, new = modes or (('000000', '100644') if status == 'A' else
                             ('100644', '000000') if status == 'D' else ('100644', '100644'))
        text += f':{old} {new} {OID} {OID} {status}\0{path}\0'
    return text


def evidence_v2(path='routine', base=BASE):
    request, api = evidence()
    request['payload'] = {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': base}

    def job(job_id, name, conclusion, started, completed):
        return {'id': job_id, 'run_id': 20, 'run_attempt': 1, 'head_sha': SHA, 'head_branch': 'main',
                'workflow_name': 'Production approval', 'name': name, 'status': 'completed',
                'conclusion': conclusion, 'started_at': started, 'completed_at': completed}
    routine = path == 'routine'
    api[V2_JOBS] = {'total_count': 3, 'jobs': [
        job(31, 'Classify release', 'success', '2026-10-01T11:27:00Z', '2026-10-01T11:28:00Z'),
        job(32, 'Promote routine release', 'success' if routine else 'skipped',
            '2026-10-01T11:29:00Z', '2026-10-01T11:31:00Z'),
        job(33, 'Promote sensitive release', 'skipped' if routine else 'success',
            '2026-10-01T11:29:00Z', '2026-10-01T11:31:00Z')]}
    api[PREFIX + '/actions/runs/20/approvals'] = [] if routine else [
        {'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'},
         'environments': [{'id': 41, 'name': 'production-sensitive'}], 'comment': 'ship'}]
    return request, api


def test_v2_routine_intent_needs_no_owner_review_and_carries_no_risk_flag():
    m = module()
    request, api = evidence_v2('routine')
    intent = m.validate_intent(request, api.__getitem__, now=NOW)
    assert (intent.version, intent.risk, intent.base_sha) == (2, 'routine', BASE)
    assert intent.key == SHA + ':20'


def test_v2_sensitive_intent_requires_owner_approval_for_sensitive_environment():
    m = module()
    request, api = evidence_v2('sensitive')
    intent = m.validate_intent(request, api.__getitem__, now=NOW)
    assert (intent.version, intent.risk, intent.base_sha) == (2, 'sensitive', BASE)
    request, api = evidence_v2('sensitive', base=None)
    assert m.validate_intent(request, api.__getitem__, now=NOW).base_sha is None


def test_v1_owner_approved_intent_remains_legacy_owner_path():
    m = module()
    request, api = evidence()
    intent = m.validate_intent(request, api.__getitem__, now=NOW)
    assert (intent.version, intent.risk, intent.base_sha) == (1, None, None)


@pytest.mark.parametrize('payload', [
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE, 'risk': 'routine'},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE, 'safe': True},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE.upper()},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE[:39]},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': ''},
    {'version': 2, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': 7},
    {'version': 2, 'source_run_id': '10', 'approval_run_id': 20, 'base_sha': BASE},
    {'version': 2.0, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE},
    {'version': True, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE},
    {'version': 3, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE},
    {'version': 1, 'source_run_id': 10, 'approval_run_id': 20, 'base_sha': BASE},
])
def test_v2_payload_schema_is_strict_and_cannot_assert_safety(payload):
    m = module()
    request, api = evidence_v2('routine')
    request['payload'] = payload
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


def test_v2_routine_without_authenticated_base_is_blocked():
    m = module()
    request, api = evidence_v2('routine', base=None)
    with pytest.raises(m.Blocked, match='base'):
        m.validate_intent(request, api.__getitem__, now=NOW)


def _jobs(api):
    return api[V2_JOBS]['jobs']


@pytest.mark.parametrize('tamper', [
    'missing_job', 'extra_job', 'duplicate_name', 'renamed', 'both_success', 'both_skipped',
    'classify_failed', 'classify_skipped', 'promote_failed', 'in_progress', 'attempt', 'run_id',
    'head_sha', 'head_branch', 'workflow_name', 'total_count', 'created_before', 'created_after',
    'skipped_classify_but_success_promote'])
def test_v2_exact_job_set_and_runtime_binding(tamper):
    m = module()
    request, api = evidence_v2('routine')
    jobs = _jobs(api)
    if tamper == 'missing_job':
        jobs.pop()
        api[V2_JOBS]['total_count'] = 2
    elif tamper == 'extra_job':
        jobs.append({**jobs[0], 'id': 34, 'name': 'Approve production'})
        api[V2_JOBS]['total_count'] = 4
    elif tamper == 'duplicate_name':
        jobs[2] = {**jobs[1], 'id': 33}
    elif tamper == 'renamed':
        jobs[1]['name'] = 'Promote routine'
    elif tamper == 'both_success':
        jobs[2]['conclusion'] = 'success'
    elif tamper == 'both_skipped':
        jobs[1]['conclusion'] = 'skipped'
    elif tamper == 'classify_failed':
        jobs[0]['conclusion'] = 'failure'
    elif tamper == 'classify_skipped':
        jobs[0]['conclusion'] = 'skipped'
    elif tamper == 'promote_failed':
        jobs[1]['conclusion'] = 'failure'
    elif tamper == 'in_progress':
        jobs[2]['status'] = 'in_progress'
    elif tamper == 'attempt':
        jobs[1]['run_attempt'] = 2
    elif tamper == 'run_id':
        jobs[0]['run_id'] = 21
    elif tamper == 'head_sha':
        jobs[2]['head_sha'] = BASE
    elif tamper == 'head_branch':
        jobs[1]['head_branch'] = 'topic'
    elif tamper == 'workflow_name':
        jobs[1]['workflow_name'] = 'Source checks'
    elif tamper == 'total_count':
        api[V2_JOBS]['total_count'] = 4
    elif tamper == 'created_before':
        request['created_at'] = '2026-10-01T11:28:30Z'
    elif tamper == 'created_after':
        jobs[1]['completed_at'] = '2026-10-01T11:29:30Z'
    elif tamper == 'skipped_classify_but_success_promote':
        jobs[0]['conclusion'] = 'skipped'
        jobs[2]['conclusion'] = 'success'
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


@pytest.mark.parametrize('reviews', [
    [],
    [{'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production'}]}],
    [{'state': 'approved', 'user': {'id': 1, 'login': 'lindayi'}, 'environments': [{'name': 'production-sensitive'}]}],
    [{'state': 'approved', 'user': {'id': 5164171, 'login': 'other'}, 'environments': [{'name': 'production-sensitive'}]}],
    [{'state': 'pending', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production-sensitive'}]}],
    [{'state': 'approved', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production-sensitive'}]},
     {'state': 'rejected', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production-sensitive'}]}],
    {'state': 'approved'},
])
def test_v2_sensitive_bypass_wrong_environment_or_rejection_is_not_approval(reviews):
    m = module()
    request, api = evidence_v2('sensitive')
    api[PREFIX + '/actions/runs/20/approvals'] = reviews
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


def test_v2_any_rejection_blocks_routine_and_rerun_approval_is_not_replayed():
    m = module()
    request, api = evidence_v2('routine')
    api[PREFIX + '/actions/runs/20/approvals'] = [
        {'state': 'rejected', 'user': {'id': 5164171, 'login': 'lindayi'}, 'environments': [{'name': 'production'}]}]
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)
    request, api = evidence_v2('sensitive')
    api[PREFIX + '/actions/runs/20']['run_attempt'] = 2
    with pytest.raises(m.Blocked):
        m.validate_intent(request, api.__getitem__, now=NOW)


def installed(controller, sha=BASE, *, status='succeeded', release=RELEASE, provenance=None, link=None):
    import json
    import os
    releases = controller / 'releases'
    (releases / RELEASE).mkdir(parents=True, exist_ok=True)
    (releases / RELEASE / 'git-provenance.json').write_text(json.dumps(provenance or {'git_sha': sha}))
    current = controller / 'current'
    if current.is_symlink():
        current.unlink()
    os.symlink(link or releases / RELEASE, current)
    record = {'status': status, 'release': release, 'git_sha': sha, 'hosted_run_id': 9}
    (controller / 'status.json').write_text(json.dumps({k: v for k, v in record.items() if v is not None}))


def test_installed_basis_binds_controller_success_current_release_and_provenance(tmp_path):
    m = module()
    controller = tmp_path / 'controller'
    controller.mkdir()
    assert m.installed_basis(controller) is None  # No controller status: bootstrap.
    installed(controller)
    assert m.installed_basis(controller) == BASE


@pytest.mark.parametrize('variant', ['failed', 'rolled_back', 'running', 'rollback_failed', 'no_sha',
                                     'short_sha', 'bad_release', 'other_release', 'provenance',
                                     'outside_link', 'not_link', 'corrupt'])
def test_installed_basis_unverifiable_is_bootstrap(tmp_path, variant):
    m = module()
    controller = tmp_path / 'controller'
    controller.mkdir()
    kwargs = {}
    if variant in {'failed', 'rolled_back', 'running', 'rollback_failed'}:
        kwargs['status'] = variant
    elif variant == 'no_sha':
        kwargs['sha'] = None
    elif variant == 'short_sha':
        kwargs['sha'] = BASE[:12]
    elif variant == 'bad_release':
        kwargs['release'] = '../x'
    elif variant == 'other_release':
        kwargs['release'] = 'f' * 32
    elif variant == 'provenance':
        kwargs['provenance'] = {'git_sha': SHA}
    elif variant == 'outside_link':
        other = tmp_path / RELEASE
        other.mkdir()
        (other / 'git-provenance.json').write_text('{"git_sha": "%s"}' % BASE)
        kwargs['link'] = other
    installed(controller, **kwargs)
    if variant == 'not_link':
        (controller / 'current').unlink()
        (controller / 'current').mkdir()
    if variant == 'corrupt':
        (controller / 'status.json').write_text('{')
    assert m.installed_basis(controller) is None


def worker_v2(tmp_path, monkeypatch, path='routine', *, diff=None, base=BASE, local=BASE, ancestor=True):
    from deploy import git_source
    m, paths, request, api, effects, run, post = worker_fixture(tmp_path, monkeypatch)
    request_v2, api_v2 = evidence_v2(path, base=base)
    request.clear()
    request.update(request_v2)
    api.update(api_v2)
    if local:
        installed(paths.controller_state, local)
    original = git_source._git

    def git(source, *args):
        if args[:1] in {('cat-file',), ('merge-base',), ('diff',)}:
            effects.append(('git', args))
            if args[0] == 'merge-base' and not ancestor:
                raise RuntimeError('Git source verification failed')
            if args[0] == 'diff':
                return raw(('M', 'frontend/styles.css')) if diff is None else diff
            return ''
        return original(source, *args)
    monkeypatch.setattr(git_source, '_git', git)
    return m, paths, request, api, effects, run, post


def git_calls(effects):
    return [e[1] for e in effects if e[0] == 'git']


def test_v2_routine_recomputes_host_diff_from_git_objects_before_merge(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_v2(tmp_path, monkeypatch)
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'deployed', result
    calls = git_calls(effects)
    diff = ('diff', '--raw', '-z', '--no-renames', '--no-abbrev', '--no-ext-diff', '--no-textconv', BASE, SHA)
    assert ('merge-base', '--is-ancestor', BASE, SHA) in calls and diff in calls
    fetch = calls.index(('fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main'))
    merge = calls.index(('merge', '--ff-only', '--no-edit', SHA))
    assert fetch < calls.index(diff) < merge
    assert [e[2]['state'] for e in effects if e[0] == 'post'] == ['in_progress', 'success']
    assert [e[1] for e in effects if e[0] == 'run'][0][-2:] == ['--hosted-run-id', '10']


def test_v2_sensitive_owner_approved_bootstrap_without_any_base_deploys(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_v2(tmp_path, monkeypatch, 'sensitive',
                                                           base=None, local=None)
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'deployed'
    assert not [c for c in git_calls(effects) if c[0] == 'diff']


def test_v2_sensitive_owner_approved_sensitive_diff_deploys(tmp_path, monkeypatch):
    m, paths, request, api, effects, run, post = worker_v2(tmp_path, monkeypatch, 'sensitive',
                                                           diff=raw(('M', 'backend/app.py')))
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'deployed'


@pytest.mark.parametrize('case,reason', [
    ('host_sensitive_diff', 'disagree'),
    ('rename_from_backend', 'disagree'),
    ('symlink_mode', 'disagree'),
    ('executable_mode', 'disagree'),
    ('empty_diff', 'disagree'),
    ('malformed_diff', 'Malformed'),
    ('overlarge', 'disagree'),
    ('base_mismatch', 'bases disagree'),
    ('no_local_basis', 'disagree'),
    ('not_descendant', 'Git'),
    ('sensitive_but_host_routine', 'disagree'),
    ('sensitive_cloud_bootstrap_local_not_ancestor', 'Git'),
    ('sensitive_base_mismatch', 'bases disagree'),
])
def test_v2_disagreement_or_bad_evidence_blocks_before_merge_and_controller(tmp_path, monkeypatch, case, reason):
    kwargs = {}
    path = 'routine'
    if case == 'host_sensitive_diff':
        kwargs['diff'] = raw(('M', 'frontend/styles.css'), ('M', 'deploy/self_deploy.py'))
    elif case == 'rename_from_backend':
        kwargs['diff'] = raw(('R087', 'backend/app.py', '100644', '100644')).replace(
            '\0backend/app.py\0', '\0backend/app.py\0frontend/styles.css\0')
    elif case == 'symlink_mode':
        kwargs['diff'] = raw(('A', 'frontend/viewport.mjs', '000000', '120000'))
    elif case == 'executable_mode':
        kwargs['diff'] = raw(('M', 'tests/test_x.py', '100644', '100755'))
    elif case == 'empty_diff':
        kwargs['diff'] = ''
    elif case == 'malformed_diff':
        kwargs['diff'] = 'frontend/styles.css\0'
    elif case == 'overlarge':
        kwargs['diff'] = raw(*[('M', f'tests/test_{index}.py') for index in range(251)])
    elif case == 'base_mismatch':
        kwargs['local'] = 'c' * 40
    elif case == 'no_local_basis':
        kwargs['local'] = None
    elif case == 'not_descendant':
        kwargs['ancestor'] = False
    elif case == 'sensitive_but_host_routine':
        path = 'sensitive'
    elif case == 'sensitive_cloud_bootstrap_local_not_ancestor':
        path, kwargs['base'], kwargs['ancestor'] = 'sensitive', None, False
    elif case == 'sensitive_base_mismatch':
        path, kwargs['local'] = 'sensitive', 'c' * 40
    m, paths, request, api, effects, run, post = worker_v2(tmp_path, monkeypatch, path, **kwargs)
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)
    assert result['status'] == 'blocked' and reason in result['reason'], result
    assert not [e for e in effects if e[0] in {'run', 'post'}]
    assert not [c for c in git_calls(effects) if c[0] in {'merge', 'reset', 'stash', 'checkout'}]
    # Consumed: a later tick never retries the same intent.
    effects.clear()
    assert m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW)['status'] == 'duplicate'
    assert not effects


@pytest.mark.parametrize('local,status', [('c' * 40, 'blocked'), (BASE, 'ready')])
def test_v2_check_only_compares_bases_without_fetch_or_writes(tmp_path, monkeypatch, local, status):
    m, paths, request, api, effects, run, post = worker_v2(tmp_path, monkeypatch, local=local)
    result = m.poll_once(paths, get=api.__getitem__, post=post, run=run, now=NOW, check_only=True)
    assert result['status'] == status, result
    if status == 'blocked':
        assert 'bases disagree' in result['reason']
    assert not paths.state.exists()
    assert not [e for e in effects if e[0] in {'post', 'run'} or e[1][0] in {'fetch', 'merge', 'diff'}]


def test_v2_policy_module_is_loaded_before_any_source_sync():
    """Never import incoming policy code: it is bound at worker import time."""
    import sys
    m = module()
    assert m.release_policy is sys.modules['deploy.release_policy']


def real_git_release(tmp_path, edit):
    """Commit the actual frontend tree, apply `edit`, commit again; return (repo, base, sha)."""
    import shutil
    import subprocess
    from pathlib import Path
    repo = tmp_path / 'source'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'frontend', repo / 'frontend')

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@invalid',
                               '-c', 'commit.gpgsign=false', *args],
                              check=True, capture_output=True, text=True, timeout=60).stdout.strip()
    git('init', '-q', '-b', 'main')
    git('add', '-A')
    git('commit', '-q', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    edit(repo / 'frontend')
    git('add', '-A')
    git('commit', '-q', '-m', 'change')
    return repo, base, git('rev-parse', 'HEAD')


def real_git_mode_release(tmp_path, change):
    """Commit a real allowed-path mode/type change and return its exact tree entries."""
    import subprocess

    repo = tmp_path / 'mode-repo'
    test_dir = repo / 'tests'
    test_dir.mkdir(parents=True)
    mode_path = test_dir / 'test_mode.py'
    mode_path.write_text('assert True\n')
    mode_path.chmod(0o755 if change == 'executable_to_regular' else 0o644)
    type_path = test_dir / 'test_type.py'
    if change == 'regular_to_symlink':
        type_path.write_text('assert True\n')

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@invalid',
                               '-c', 'commit.gpgsign=false', *args],
                              check=True, capture_output=True, text=True, timeout=60).stdout.strip()

    git('init', '-q', '-b', 'main')
    git('add', '-A')
    git('commit', '-q', '-m', 'base')
    base = git('rev-parse', 'HEAD')

    if change == 'regular_to_executable':
        mode_path.chmod(0o755)
    elif change == 'executable_to_regular':
        mode_path.chmod(0o644)
    elif change == 'add_symlink':
        (test_dir / 'test_link.py').symlink_to('test_mode.py')
    elif change == 'regular_to_symlink':
        type_path.unlink()
        type_path.symlink_to('test_mode.py')
    git('add', '-A')
    git('commit', '-q', '-m', 'change')
    sha = git('rev-parse', 'HEAD')

    def tree_entries(revision):
        raw = subprocess.run(['git', '-C', str(repo), 'ls-tree', '-r', '-t', '-z', '--full-tree', revision],
                             check=True, capture_output=True, timeout=60).stdout
        entries = []
        for record in raw.split(b'\0'):
            if record:
                header, path = record.split(b'\t', 1)
                mode, kind, oid = header.decode('ascii').split()
                entries.append({'path': path.decode('utf-8'), 'mode': mode, 'type': kind, 'sha': oid})
        return entries

    return repo, base, sha, tree_entries(base), tree_entries(sha)


def replace_once(name, old, new):
    def edit(frontend):
        path = frontend / name
        text = path.read_text()
        assert text.count(old) == 1, (name, old)
        path.write_text(text.replace(old, new))
    return edit


def add_file(name, text):
    return lambda frontend: (frontend / name).write_text(text)


SEC1_CASES = {
    # SEC-1: passkey recovery enrollment lives in the ui.mjs authentication monolith.
    'ui_auth_recovery': (replace_once('ui.mjs', "kind === 'register' || kind === 'recovery'",
                                      "kind === 'register'"), 'sensitive'),
    'app_bootstrap_sw': (replace_once('app.js', "{scope:'/hermes/'}", "{scope:'/'}"), 'sensitive'),
    'index_auth_markup': (replace_once('index.html', 'secure passkey sign-in', 'sign-in'), 'sensitive'),
    'markdown_links': (replace_once('markdown.mjs', 'const token=match[0];', 'const token=match[0];//'),
                       'sensitive'),
    'new_code_module': (add_file('profile-card.mjs', 'export const x = 1;\n'), 'sensitive'),
    'new_stylesheet': (add_file('extra.css', 'body{}\n'), 'sensitive'),
    'presentation_only': (lambda frontend: (frontend / 'styles.css').write_text(
        (frontend / 'styles.css').read_text() + '\n/* spacing */\n'), 'routine'),
}


@pytest.mark.parametrize('change', ['regular_to_executable', 'executable_to_regular',
                                    'add_symlink', 'regular_to_symlink'])
def test_cloud_and_host_classify_real_git_mode_changes_as_sensitive(tmp_path, change):
    """The executed producer must use exact commit-tree modes just like the host."""
    from deploy import release_policy

    m = module()
    repo, base, sha, base_entries, head_entries = real_git_mode_release(tmp_path, change)
    changes = m.release_changes(repo, base, sha)
    assert changes and release_policy.classify(changes).risk == 'sensitive'
    statuses = {'A': 'added', 'D': 'removed', 'M': 'modified', 'T': 'modified'}
    files = [{'filename': item.path, 'status': statuses[item.status]} for item in changes]
    api = cloud_api(files, base=base, sha=sha)
    add_cloud_tree_api(api, files, base=base, sha=sha,
                       base_entries=base_entries, head_entries=head_entries)
    output = run_script('classify', api, sha=sha)
    assert 'error' not in output, output
    assert output['outputs'] == {'risk': 'sensitive', 'base_sha': base}


@pytest.mark.parametrize('case', sorted(SEC1_CASES))
def test_sec1_real_git_diff_and_executed_cloud_classifier_agree_on_auth_modules(tmp_path, case):
    """Host full Git diff and the actual workflow classifier both classify real frontend edits."""
    from deploy import release_policy
    m = module()
    edit, expected = SEC1_CASES[case]
    repo, base, sha = real_git_release(tmp_path, edit)
    changes = m.release_changes(repo, base, sha)
    assert changes and release_policy.classify(changes).risk == expected
    statuses = {'A': 'added', 'D': 'removed', 'M': 'modified'}
    files = [{'filename': change.path, 'status': statuses[change.status]} for change in changes]
    output = run_script('classify', cloud_api(files))
    assert 'error' not in output, output
    assert output['outputs'] == {'risk': expected, 'base_sha': BASE}
