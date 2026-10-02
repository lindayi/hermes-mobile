"""Single-pass outbound delivery. GitHub requests contain IDs, never commands."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from contextlib import contextmanager

# Bound at worker start from the deployed checkout, before any fetch/merge: the
# incoming revision's policy code is never imported or executed by this worker.
from deploy import release_policy

REPOSITORY = 'lindayi/hermes-mobile'
REPOSITORY_ID = 1399942965
OWNER_ID = 5164171
SOURCE_WORKFLOW_ID = 372155405
API = '/repos/' + REPOSITORY
APPROVAL_PATH = '.github/workflows/production.yml'
MAX_AGE_SECONDS = 24 * 60 * 60
WORKFLOW_NAME = 'Production approval'
SENSITIVE_ENVIRONMENT = 'production-sensitive'
# Exact version 2 job set: name -> risk path it promotes (None: read-only classifier).
V2_JOBS = {'Classify release': None, 'Promote routine release': release_policy.ROUTINE,
           'Promote sensitive release': release_policy.SENSITIVE}
SHA_PATTERN = re.compile('[0-9a-f]{40}')


class Blocked(RuntimeError):
    """Evidence is missing, invalid, stale, or requires operator intervention."""


class Deferred(RuntimeError):
    """Read-only preflight can be retried on a later timer tick."""


@dataclass(frozen=True)
class Intent:
    sha: str
    source_run_id: int
    approval_run_id: int
    deployment_id: int
    version: int = 1
    base_sha: str | None = None  # Cloud-derived deployed base (version 2; None: bootstrap).
    risk: str | None = None  # Version 2 cloud path proven by job/approval evidence.

    @property
    def key(self):
        return f'{self.sha}:{self.approval_run_id}'


def require(condition, message):
    if not condition:
        raise Blocked(message)


def positive_id(value):
    return type(value) is int and value > 0


def timestamp(value):
    require(isinstance(value, str), 'Missing evidence timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None, 'Timestamp must include timezone')
        return parsed
    except ValueError as exc:
        raise Blocked('Invalid evidence timestamp') from exc


def repository(value):
    return (isinstance(value, dict) and value.get('id') == REPOSITORY_ID
            and value.get('full_name') == REPOSITORY)


def current_main(get):
    repo = get(API)
    require(repository(repo) and repo.get('default_branch') == 'main'
            and repo.get('owner', {}).get('id') == OWNER_ID, 'Repository/default branch mismatch')
    ref = get(API + '/git/ref/heads/main')
    sha = ref.get('object', {}).get('sha')
    require(ref.get('ref') == 'refs/heads/main' and ref.get('object', {}).get('type') == 'commit'
            and isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha), 'Invalid main reference')
    return sha


def validate_intent(request, get, *, now=None):
    now = now or datetime.now(timezone.utc)
    require(isinstance(request, dict), 'Invalid deployment')
    deployment_id = request.get('id')
    payload = request.get('payload')
    require(positive_id(deployment_id), 'Invalid deployment ID')
    version = payload.get('version') if isinstance(payload, dict) else None
    fields = {1: {'version', 'source_run_id', 'approval_run_id'},
              2: {'version', 'source_run_id', 'approval_run_id', 'base_sha'}}
    require(type(version) is int and version in fields and set(payload) == fields[version]
            and positive_id(payload['source_run_id']) and positive_id(payload['approval_run_id'])
            and (version == 1 or payload['base_sha'] is None
                 or (isinstance(payload['base_sha'], str) and SHA_PATTERN.fullmatch(payload['base_sha']))),
            'Unsupported deployment payload')
    sha = current_main(get)
    require(request.get('sha') == sha and request.get('ref') == sha, 'Request is not exact current main')
    require(request.get('task') == 'deploy:mobile' and request.get('environment') == 'production'
            and request.get('production_environment') is True
            and request.get('transient_environment') is False, 'Deployment policy mismatch')
    require(request.get('url') == 'https://api.github.com' + API + f'/deployments/{deployment_id}',
            'Deployment repository mismatch')
    creator = request.get('creator', {})
    require(creator.get('id') == 41898282 and creator.get('login') == 'github-actions[bot]'
            and creator.get('type') == 'Bot', 'Unexpected deployment creator')
    created = timestamp(request.get('created_at'))
    require(0 <= (now - created).total_seconds() <= MAX_AGE_SECONDS, 'Expired/future deployment')
    intent = Intent(sha, payload['source_run_id'], payload['approval_run_id'], deployment_id,
                    version, payload.get('base_sha'))
    workflow = get(API + '/actions/workflows/production.yml')
    require(positive_id(workflow.get('id')) and workflow.get('path') == APPROVAL_PATH
            and workflow.get('state') == 'active', 'Untrusted approval workflow')
    for run_id, workflow_id, path, event in (
            (intent.source_run_id, SOURCE_WORKFLOW_ID, '.github/workflows/ci.yml', 'push'),
            (intent.approval_run_id, workflow['id'], APPROVAL_PATH, 'workflow_run')):
        run = get(API + f'/actions/runs/{run_id}')
        require(run.get('id') == run_id and run.get('workflow_id') == workflow_id
                and run.get('path') == path and run.get('event') == event
                and run.get('head_branch') == 'main' and run.get('head_sha') == sha
                and repository(run.get('repository')) and repository(run.get('head_repository')),
                'Run provenance mismatch')
        require(positive_id(run.get('run_attempt')), 'Missing run attempt')
        if event == 'workflow_run':
            # Review history has no attempt field: do not attribute an earlier approval to a rerun.
            require(run['run_attempt'] == 1, 'Approval reruns require a new intent')
            if run.get('status') in {'queued', 'in_progress', 'waiting', 'pending', 'requested'}:
                raise Deferred('approval: workflow has not completed')
        require(run.get('status') == 'completed' and run.get('conclusion') == 'success',
                'Run did not complete successfully')
    jobs = get(API + f'/actions/runs/{intent.approval_run_id}/jobs?filter=latest&per_page=100')
    reviews = get(API + f'/actions/runs/{intent.approval_run_id}/approvals')
    require(isinstance(reviews, list), 'Missing approval history')
    if intent.version == 2:
        intent = validate_v2(intent, jobs, reviews, created)
    else:
        validate_v1(intent, jobs, reviews, created)
    statuses = get(API + f'/deployments/{deployment_id}/statuses?per_page=100')
    require(isinstance(statuses, list) and statuses and statuses[0].get('state') == 'queued'
            and statuses[0].get('log_url') == f'https://github.com/{REPOSITORY}/actions/runs/{intent.approval_run_id}',
            'Deployment is not a queued approved request')
    return intent


def owner_approved(review, environment):
    return (isinstance(review, dict) and review.get('state') == 'approved'
            and isinstance(review.get('user'), dict) and review['user'].get('id') == OWNER_ID
            and review['user'].get('login') == 'lindayi'
            and isinstance(review.get('environments'), list)
            and any(isinstance(env, dict) and env.get('name') == environment for env in review['environments']))


def validate_v1(intent, jobs, reviews, created):
    """Legacy owner-approved single-job intent: unchanged semantics."""
    require(jobs.get('total_count') == 1 and len(jobs.get('jobs', [])) == 1,
            'Unexpected approval job set')
    job = jobs['jobs'][0]
    require(job.get('run_id') == intent.approval_run_id and job.get('name') == 'Approve production'
            and job.get('status') == 'completed' and job.get('conclusion') == 'success'
            and timestamp(job.get('started_at')) <= created <= timestamp(job.get('completed_at')),
            'Approved job does not bind deployment')
    production = [review for review in reviews if isinstance(review, dict)
                  and any(env.get('name') == 'production' for env in review.get('environments', [])
                          if isinstance(env, dict))]
    require(not any(review.get('state') == 'rejected' for review in production), 'Production review rejected')
    require(any(review.get('state') == 'approved' and review.get('user', {}).get('id') == OWNER_ID
                and review.get('user', {}).get('login') == 'lindayi' for review in production),
            'Actual owner production approval is required')


def validate_v2(intent, jobs, reviews, created):
    """Exact classify/promote job set; the promoted path is proven, never asserted."""
    listed = jobs.get('jobs') if isinstance(jobs, dict) else None
    require(isinstance(listed, list) and jobs.get('total_count') == len(V2_JOBS) == len(listed)
            and all(isinstance(job, dict) and isinstance(job.get('name'), str) for job in listed)
            and sorted(job['name'] for job in listed) == sorted(V2_JOBS),
            'Unexpected production job set')
    by_name = {job['name']: job for job in listed}
    for job in listed:
        require(job.get('run_id') == intent.approval_run_id and job.get('run_attempt') == 1
                and job.get('head_sha') == intent.sha and job.get('head_branch') == 'main'
                and job.get('workflow_name') == WORKFLOW_NAME and job.get('status') == 'completed',
                'Production job is not bound to this run attempt/SHA')
    require(by_name['Classify release'].get('conclusion') == 'success', 'Release classification did not succeed')
    promoted = [name for name, risk in V2_JOBS.items() if risk and by_name[name].get('conclusion') == 'success']
    skipped = [name for name, risk in V2_JOBS.items() if risk and by_name[name].get('conclusion') == 'skipped']
    require(len(promoted) == 1 and len(skipped) == 1, 'Exactly one promotion path must succeed')
    job = by_name[promoted[0]]
    require(timestamp(job.get('started_at')) <= created <= timestamp(job.get('completed_at')),
            'Promotion job does not bind deployment')
    risk = V2_JOBS[promoted[0]]
    require(not any(isinstance(review, dict) and review.get('state') == 'rejected' for review in reviews),
            'Production review rejected')
    if risk == release_policy.ROUTINE:
        require(intent.base_sha is not None, 'Routine promotion requires an authenticated deployed base')
    else:
        require(any(owner_approved(review, SENSITIVE_ENVIRONMENT) for review in reviews),
                'Actual owner approval for production-sensitive is required; bypass is not approval')
    return Intent(intent.sha, intent.source_run_id, intent.approval_run_id, intent.deployment_id,
                  intent.version, intent.base_sha, risk)


def require_idle(database):
    """Never instantiate RunJournal: its constructor can migrate/write live state."""
    import sqlite3
    from contextlib import closing
    from deploy.assets import checked_path
    try:
        database = checked_path(database)
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            if connection.execute("SELECT 1 FROM runs WHERE status IS NULL OR status NOT IN "
                                  "('completed','failed','cancelled') LIMIT 1").fetchone():
                raise Deferred('busy: active or unknown run status')
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise Deferred('busy: run journal unavailable/unknown') from exc


@dataclass(frozen=True)
class Paths:
    source: Path = Path('/home/lindayi/projects/hermes-mobile-git')
    state: Path = Path('/home/lindayi/.local/share/hermes-mobile-delivery')
    controller_state: Path = Path('/home/lindayi/.local/share/hermes-mobile-deploy')
    database: Path = Path('/home/lindayi/.local/share/hermes-mobile-live/runs.sqlite')


def safe_path(path):
    from deploy.assets import checked_path
    path = checked_path(path)
    if path.exists():
        info = path.stat()
        require(info.st_uid == os.getuid() and (stat.S_ISDIR(info.st_mode)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)), 'Unsafe delivery state path')
    return path


def load_state(paths):
    path = safe_path(paths.state / 'state.json')
    if not path.exists():
        return {'version': 1, 'latest_id': 0, 'records': {}, 'last': {'status': 'queued'}}
    state = json.loads(path.read_text())
    require(state.get('version') == 1 and type(state.get('latest_id')) is int
            and isinstance(state.get('records'), dict) and isinstance(state.get('last'), dict),
            'Invalid durable delivery state; operator inspection required')
    return state


def save_state(paths, state):
    """fsync both file and rename: a crash must not silently replay a started intent."""
    target = safe_path(paths.state / 'state.json')
    fd, name = tempfile.mkstemp(prefix='.state-', dir=paths.state)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        directory = os.open(paths.state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def exclusive(path):
    path = safe_path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Deferred('busy: delivery/deployment lock is held') from exc
        yield
    finally:
        os.close(fd)


def installed_basis(controller_state):
    """Controller-verified installed SHA, or None (bootstrap/unknown: sensitive).

    Binds the controller's successful status to the live `current` release and
    that release's own Git provenance record. Never trusts the checkout HEAD.
    """
    from deploy.assets import checked_path
    try:
        status_path = safe_path(Path(controller_state) / 'status.json')
        if not status_path.is_file():
            return None
        status = json.loads(status_path.read_text())
        sha, release = status.get('git_sha'), status.get('release')
        if (status.get('status') != 'succeeded' or not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha)
                or not isinstance(release, str) or not re.fullmatch('[0-9a-f]{32}', release)):
            return None
        current = Path(controller_state) / 'current'
        releases = checked_path(Path(controller_state) / 'releases')
        if not current.is_symlink() or current.resolve(strict=True) != releases / release:
            return None
        provenance = safe_path(releases / release / 'git-provenance.json')
        if not provenance.is_file() or json.loads(provenance.read_text()) != {'git_sha': sha}:
            return None
        return sha
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None


def release_changes(source, base, sha):
    """Complete diff inventory from trusted, already-fetched Git objects; no checkout/execution."""
    from deploy.git_source import _git
    for revision in (base, sha):
        require(isinstance(revision, str) and SHA_PATTERN.fullmatch(revision), 'Invalid Git revision')
        _git(source, 'cat-file', '-e', revision + '^{commit}')
    _git(source, 'merge-base', '--is-ancestor', base, sha)  # Non-zero exit (not descendant) raises.
    return release_policy.parse_git_raw(_git(
        source, 'diff', '--raw', '-z', '--no-renames', '--no-abbrev', '--no-ext-diff', '--no-textconv', base, sha))


def enforce_policy(paths, intent, *, classify_diff=True):
    """Fail closed unless host and cloud agree on the deployed base and risk path."""
    if intent.version == 1:
        return  # Legacy intents always carry actual owner approval.
    local = installed_basis(paths.controller_state)
    cloud = intent.base_sha
    require(not (cloud and local and cloud != local), 'Cloud and host deployed bases disagree')
    if not classify_diff:
        return
    if cloud and local:
        result = release_policy.classify(release_changes(paths.source, local, intent.sha))
        risk = result.risk
    else:
        for base in {cloud, local} - {None}:
            release_changes(paths.source, base, intent.sha)  # Still require descent.
        risk = release_policy.SENSITIVE  # Bootstrap: no verified deployed base.
    require(risk == intent.risk, f'Host classification {risk} disagrees with cloud path {intent.risk}')


def sync_source(paths, sha, *, before_merge=None):
    """Only clean approved origin main, fast-forward only; never reset/stash."""
    from deploy.git_source import _check_checkout, _git
    _check_checkout(paths.source)
    _git(paths.source, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    _check_checkout(paths.source)
    require(_git(paths.source, 'rev-parse', 'refs/remotes/origin/main') == sha,
            'Fetched main superseded the approved SHA')
    if before_merge:
        before_merge()  # Policy runs on fetched objects before the worktree changes.
    _git(paths.source, 'merge', '--ff-only', '--no-edit', sha)
    _check_checkout(paths.source)
    require(_git(paths.source, 'rev-parse', 'HEAD') == sha, 'Checkout differs from approved SHA')


def status_stamp(path):
    path = safe_path(path)
    if not path.exists():
        return None
    info = path.stat()
    return (info.st_ino, info.st_mtime_ns, info.st_size)


def poll_once(paths, *, get, post, run=subprocess.run, now=None, check_only=False):
    """One bounded request per invocation. No shell, cancellation, or retry loop."""
    if check_only:
        return _poll(paths, get=get, post=post, run=run, now=now, check_only=True)
    root = safe_path(paths.state)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    try:
        with exclusive(root / 'worker.lock'):
            return _poll(paths, get=get, post=post, run=run, now=now, check_only=False)
    except Deferred as exc:
        return {'status': 'deferred', 'reason': str(exc)}


def _poll(paths, *, get, post, run, now, check_only):
    state = load_state(paths)
    key = None
    intent = None
    attempted = False

    def finish(status, reason='', *, terminal=False):
        result = {'status': status, 'reason': reason}
        if intent:
            result.update(sha=intent.sha, approval_run_id=intent.approval_run_id,
                          source_run_id=intent.source_run_id, deployment_id=intent.deployment_id)
            if intent.version == 2:
                result.update(version=2, risk=intent.risk, base_sha=intent.base_sha)
        if not check_only:
            state['last'] = result
            if terminal and key:
                state['records'][key] = result
            save_state(paths, state)
        return result

    try:
        requests = get(API + '/deployments?task=deploy%3Amobile&environment=production&per_page=100')
        require(isinstance(requests, list) and all(isinstance(item, dict) and positive_id(item.get('id'))
                                                  for item in requests), 'Invalid deployment listing')
        if not requests:
            require(state['latest_id'] == 0, 'Previously observed latest deployment is missing')
            return finish('queued', 'No request; production may be awaiting owner approval')
        request = max(requests, key=lambda item: item['id'])
        require(request['id'] >= state['latest_id'], 'Out-of-order/missing latest deployment')
        state['latest_id'] = request['id']
        payload = request.get('payload')
        key = (f"{request.get('sha')}:{payload['approval_run_id']}"
               if isinstance(payload, dict) and positive_id(payload.get('approval_run_id'))
               else f"invalid-deployment:{request['id']}")
        if key in state['records']:
            return finish('duplicate', 'Consumed intent: ' + state['records'][key]['status'])
        intent = validate_intent(request, get, now=now)
        # This precedes even fetch/check execution and never closes admission.
        require_idle(paths.database)
        if check_only:
            from deploy.git_source import _check_checkout
            _check_checkout(paths.source)
            enforce_policy(paths, intent, classify_diff=False)
            return finish('ready', 'Read-only evidence/preflight passed; no sync, checks or activation performed'
                          + ('; host diff classification runs after fetch' if intent.version == 2 else ''))
        require(bool(os.environ.get('INVOCATION_ID')), 'Delivery must run in its own systemd unit')
        # Serialize sync against an existing deployment. The controller obtains this
        # same protected lock itself for final staging/drain/activation serialization.
        with exclusive(paths.controller_state / 'deploy.lock'):
            require_idle(paths.database)
            sync_source(paths, intent.sha, before_merge=lambda: enforce_policy(paths, intent))
        require(current_main(get) == intent.sha, 'Main changed before controller invocation')
        require_idle(paths.database)
        status_path = paths.controller_state / 'status.json'
        before = status_stamp(status_path)
        # Record BEFORE even posting in_progress: interrupted/ambiguous attempts
        # require operator inspection rather than automatic deployment replay.
        finish('running', 'Controller attempt reserved', terminal=True)
        attempted = True
        body = {'state': 'in_progress', 'description': 'Guarded host deployment started', 'auto_inactive': False}
        post(API + f'/deployments/{intent.deployment_id}/statuses', body)
        completed = run([str(paths.source / '.venv/bin/python'), '-m', 'deploy.self_deploy',
                         '--worker', '--hosted-run-id', str(intent.source_run_id)],
                        cwd=paths.source, check=True)
        require(completed.returncode == 0 and status_stamp(status_path) != before,
                'Controller did not produce fresh success evidence')
        status = json.loads(safe_path(status_path).read_text())
        require(status.get('status') == 'succeeded' and status.get('git_sha') == intent.sha,
                'Controller result is not success for the exact approved SHA')
        result = finish('deployed', terminal=True)
        try:
            post(API + f'/deployments/{intent.deployment_id}/statuses',
                 {'state': 'success', 'description': 'Exact approved SHA deployed and verified', 'auto_inactive': False})
        except (RuntimeError, OSError, subprocess.SubprocessError):
            result = finish('deployed', 'GitHub success reporting failed; no automatic replay', terminal=True)
        return result
    except Deferred as exc:
        return finish('approval' if str(exc).startswith('approval:') else 'deferred', str(exc))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = finish('failed' if attempted else 'blocked', str(exc), terminal=bool(key))
        if attempted and intent:
            try:
                post(API + f'/deployments/{intent.deployment_id}/statuses',
                     {'state': 'failure', 'description': 'Guarded deployment failed; inspect host status', 'auto_inactive': False})
            except (RuntimeError, OSError, subprocess.SubprocessError):
                pass  # Durable host status remains authoritative; never claim success.
        return result


class GitHub:
    """Existing gh authentication; explicit host, fixed API paths, bounded calls."""
    def __init__(self, *, run=subprocess.run):
        self.run = run

    def request(self, path, body=None):
        require(path.startswith(API + '/') or path == API, 'API repository is not allowlisted')
        argv = ['gh', 'api', '--hostname', 'github.com', '--method', 'POST' if body is not None else 'GET',
                '-H', 'Accept: application/vnd.github+json', '-H', 'X-GitHub-Api-Version: 2022-11-28']
        kwargs = {'check': True, 'capture_output': True, 'text': True, 'timeout': 60}
        if body is not None:
            argv += ['--input', '-']
            kwargs['input'] = json.dumps(body)
        argv.append(path)
        response = self.run(argv, **kwargs)
        return json.loads(response.stdout)

    def get(self, path):
        return self.request(path)

    def post(self, path, body):
        return self.request(path, body)


def main(argv=None, *, paths=None, client=None, run=subprocess.run, now=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--once', action='store_true', help='Process at most one approved request')
    modes.add_argument('--status', action='store_true', help='Read local delivery status without network/write')
    modes.add_argument('--check-only', '--dry-run', action='store_true',
                       help='Read-only API/idle/source inspection; never sync or deploy')
    args = parser.parse_args(argv)
    paths = paths or Paths()
    client = client or GitHub()
    try:
        require(os.geteuid() != 0, 'Run as the unprivileged application owner, never root')
        if args.status:
            result = load_state(paths)['last']
        else:
            result = poll_once(paths, get=client.get, post=client.post, run=run, now=now,
                               check_only=args.check_only)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
        result = {'status': 'blocked', 'reason': str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 1 if result['status'] in {'failed', 'blocked'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
