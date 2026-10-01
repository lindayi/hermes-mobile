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


# ---- Version 2: routine/sensitive risk policy (issue #17) -------------------

BASE = 'b' * 40
RELEASE = 'e' * 32
OID = '1' * 40
ZERO = '0' * 40
V2_JOBS = PREFIX + '/actions/runs/20/jobs?filter=latest&per_page=100'


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
                return raw(('M', 'frontend/app.js')) if diff is None else diff
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
        kwargs['diff'] = raw(('M', 'frontend/app.js'), ('M', 'deploy/self_deploy.py'))
    elif case == 'rename_from_backend':
        kwargs['diff'] = raw(('R087', 'backend/app.py', '100644', '100644')).replace(
            '\0backend/app.py\0', '\0backend/app.py\0frontend/app.js\0')
    elif case == 'symlink_mode':
        kwargs['diff'] = raw(('A', 'frontend/x.js', '000000', '120000'))
    elif case == 'executable_mode':
        kwargs['diff'] = raw(('M', 'tests/test_x.py', '100644', '100755'))
    elif case == 'empty_diff':
        kwargs['diff'] = ''
    elif case == 'malformed_diff':
        kwargs['diff'] = 'frontend/app.js\0'
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
