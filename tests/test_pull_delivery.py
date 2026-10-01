"""Synthetic outbound API/subprocess/SQLite tests; never touch live services."""
import copy
import importlib.util
from datetime import datetime, timezone

import pytest

SHA = 'a' * 40
NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
REPO = {'id': 1399942965, 'full_name': 'lindayi/hermes-mobile',
        'owner': {'id': 5164171, 'login': 'lindayi'}, 'default_branch': 'main'}
PREFIX = '/repos/lindayi/hermes-mobile'


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


def test_workflow_and_units_have_no_untrusted_checkout_or_production_runner():
    from pathlib import Path
    workflow = production_workflow()
    assert workflow['on'] == {'workflow_run': {'workflows': ['Source checks'], 'types': ['completed'], 'branches': ['main']}}
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read', 'deployments': 'write'}
    job = workflow['jobs']['approve-production']
    assert job['environment'] == 'production'
    assert job['name'] == 'Approve production'
    assert job['runs-on'] == 'ubuntu-24.04'
    assert len(job['steps']) == 1
    assert job['steps'][0]['uses'] == 'actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd'
    assert 'github.event.workflow_run.event' in job['if']
    assert 'github.event.workflow_run.conclusion' in job['if']
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


@pytest.mark.parametrize('fault', [None, 'pr', 'fork', 'old_main', 'no_owner', 'rerun'])
def test_approval_script_executes_only_fixed_validated_intent(tmp_path, fault):
    import json
    import os
    import subprocess
    workflow = production_workflow()
    script = workflow['jobs']['approve-production']['steps'][0]['with']['script']
    assert '${{' not in script  # No expression-to-JavaScript injection.
    request, api = evidence()
    source = api[PREFIX + '/actions/runs/10']
    if fault == 'pr':
        source['event'] = 'pull_request'
    if fault == 'fork':
        source['head_repository']['id'] = 123
    main_sha = 'b' * 40 if fault == 'old_main' else SHA
    reviews = [] if fault == 'no_owner' else api[PREFIX + '/actions/runs/20/approvals']
    harness = '''
const source = INPUT.source;
const calls = [];
const core = {setFailed: msg => {throw new Error(msg)}, info: () => {}};
const context = {repo:{owner:'lindayi',repo:'hermes-mobile'}, runId:20,
  ref:'refs/heads/main', sha:INPUT.sha, payload:{workflow_run:source}};
const github = {rest:{
  repos:{get:async()=>({data:INPUT.repo}),
    createDeployment:async data=>{calls.push(data); return {data:{id:50}}},
    createDeploymentStatus:async data=>{calls.push(data); return {data:{}}}},
  git:{getRef:async()=>({data:{ref:'refs/heads/main',object:{type:'commit',sha:INPUT.main}}})},
  actions:{getWorkflowRun:async()=>({data:source})}},
  request:async()=>({data:INPUT.reviews})};
(async()=>{try { await (async()=>{ SCRIPT })(); console.log(JSON.stringify({calls})); }
catch (e) { console.log(JSON.stringify({calls,error:String(e)})); }})();
'''.replace('INPUT', json.dumps({'source': source, 'sha': SHA, 'repo': REPO, 'main': main_sha,
                               'reviews': reviews})).replace('SCRIPT', script)
    # Syntax above uses object literals in property access; bracket for parser clarity.
    env = {**os.environ, 'SOURCE_RUN_ID': '10', 'APPROVAL_RUN_ID': '20', 'SOURCE_SHA': SHA,
           'GITHUB_RUN_ATTEMPT': '2' if fault == 'rerun' else '1'}
    result = subprocess.run(['/home/lindayi/.hermes/node/bin/node', '-e', harness],
                            env=env, check=True, text=True, capture_output=True)
    output = json.loads(result.stdout)
    if fault:
        assert output.get('error') and not output['calls']
    else:
        assert 'error' not in output
        deployment, status = output['calls']
        assert deployment['ref'] == SHA and deployment['task'] == 'deploy:mobile'
        assert deployment['auto_merge'] is False and deployment['required_contexts'] == []
        assert deployment['payload'] == {'version': 1, 'source_run_id': 10, 'approval_run_id': 20}
        assert status['state'] == 'queued' and status['deployment_id'] == 50


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
