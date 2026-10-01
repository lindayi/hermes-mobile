"""Main-only deployment specification (offline real-Git acceptance tests).

* Production source defaults to /home/lindayi/projects/hermes-mobile-git.
* Any production destination/source requires that canonical checkout. No env bypass.
* Main branch, exact approved origin, fresh successful main fetch, matching HEAD,
  and no dirty/untracked deployable bytes are required before scheduling/staging.
* Staging binds copied bytes to the pinned commit and rechecks checkout identity;
  a task branch cannot deploy even when its tip equals main.
* Successful/rolled-back release records retain the verified Git SHA.
* Purely redirected non-Git legacy fixtures remain offline. Real Git fixtures use
  a local bare origin via Git's test-only URL rewrite, never the public network.
* Existing native protections, stage fingerprints, gates and rollback stay intact.
"""
from pathlib import Path

from deploy import self_deploy


def test_default_source_is_canonical_main_checkout():
    assert self_deploy.Paths().source == Path('/home/lindayi/projects/hermes-mobile-git')


import importlib
import subprocess
import pytest


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(origin)],
                   check=True, capture_output=True)
    source = tmp_path / 'source'
    subprocess.run(['git', 'clone', str(origin), str(source)], check=True, capture_output=True)
    git(source, 'config', 'user.name', 'Offline Test')
    git(source, 'config', 'user.email', 'test@example.invalid')
    (source / 'frontend').mkdir()
    (source / 'frontend/index.html').write_text('<h1>tracked</h1>')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'fixture')
    git(source, 'push', 'origin', 'main')
    return source, origin


def guard_for(monkeypatch, origin):
    spec = importlib.util.find_spec('deploy.git_source')
    assert spec is not None, 'the Git provenance guard is missing'
    guard = importlib.import_module('deploy.git_source')
    monkeypatch.setattr(guard, 'REMOTE', str(origin))
    return guard


def test_clean_main_fetches_and_returns_exact_sha(repository, monkeypatch):
    source, origin = repository
    guard = guard_for(monkeypatch, origin)
    git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    assert guard.validate_source(source) == git(source, 'rev-parse', 'HEAD')
    assert git(source, 'rev-parse', 'origin/main') == git(source, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('change', ['dirty', 'staged', 'untracked', 'branch', 'detached',
                                  'ahead', 'behind', 'wrong-remote', 'missing-origin'])
def test_source_rejects_any_non_main_provenance(repository, monkeypatch, tmp_path, change):
    source, origin = repository
    guard = guard_for(monkeypatch, origin)
    file = source / 'frontend/index.html'
    if change in ('dirty', 'staged', 'ahead'):
        file.write_text('not main')
        if change in ('staged', 'ahead'):
            git(source, 'add', '.')
        if change == 'ahead':
            git(source, 'commit', '-m', 'not pushed')
    elif change == 'untracked':
        (source / 'untracked').write_text('not main')
    elif change == 'branch':
        git(source, 'checkout', '-b', 'task/unmerged')
    elif change == 'detached':
        git(source, 'checkout', '--detach')
    elif change == 'behind':
        old = git(source, 'rev-parse', 'HEAD')
        file.write_text('new main')
        git(source, 'add', '.')
        git(source, 'commit', '-m', 'advance main')
        git(source, 'push', 'origin', 'main')
        git(source, 'reset', '--hard', old)
        git(source, 'update-ref', 'refs/remotes/origin/main', old)
    elif change == 'wrong-remote':
        other = tmp_path / 'other.git'
        subprocess.run(['git', 'clone', '--bare', str(origin), str(other)],
                       check=True, capture_output=True)
        git(source, 'remote', 'set-url', 'origin', str(other))
    else:
        git(source, 'remote', 'remove', 'origin')
    with pytest.raises(RuntimeError, match='Git source'):
        guard.validate_source(source)



def isolated_paths(tmp_path, source):
    return self_deploy.Paths(source=source, state=tmp_path / 'private',
                             webroot=tmp_path / 'web', database=tmp_path / 'db/runs.sqlite',
                             dropin=tmp_path / 'systemd/override.conf')


@pytest.mark.parametrize('entry', ['schedule', 'deploy'])
def test_unmerged_git_source_rejected_before_side_effects(repository, monkeypatch, tmp_path, entry):
    source, origin = repository
    guard_for(monkeypatch, origin)
    git(source, 'checkout', '-b', 'task/unmerged')
    paths = isolated_paths(tmp_path, source)
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('old')
    monkeypatch.setattr(self_deploy.os, 'geteuid', lambda: 1000)
    calls = []
    with pytest.raises(RuntimeError, match='Git source'):
        if entry == 'schedule':
            self_deploy.main([], paths=paths, run=lambda *a, **k: calls.append(a))
        else:
            self_deploy.deploy(paths, frontend_only=True, checks=lambda *a: calls.append(a),
                               verify=lambda *a: calls.append(a))
    assert not calls
    assert not paths.state.exists()


@pytest.mark.parametrize('field', ['source', 'state', 'webroot', 'database', 'dropin'])
def test_any_default_production_path_rejects_noncanonical_source(tmp_path, monkeypatch, field):
    from dataclasses import replace
    source = tmp_path / 'source'
    source.mkdir()
    paths = isolated_paths(tmp_path, source)
    defaults = self_deploy.Paths()
    value = (Path('/home/lindayi/projects/hermes-mobile') if field == 'source'
             else getattr(defaults, field))
    paths = replace(paths, **{field: value})
    monkeypatch.setattr(self_deploy.os, 'geteuid', lambda: 1000)
    with pytest.raises(RuntimeError, match='canonical'):
        self_deploy.main([], paths=paths, run=lambda *a, **k: None)
    assert not (tmp_path / 'private').exists()


def test_fetch_failure_never_uses_cached_main(repository, monkeypatch):
    source, origin = repository
    guard = guard_for(monkeypatch, origin)
    origin.rename(origin.with_name('unavailable.git'))
    with pytest.raises(RuntimeError, match='Git source verification failed'):
        guard.validate_source(source)


@pytest.mark.parametrize('race', ['copied-bytes', 'branch', 'new-main', 'ignored-file', 'hidden-dirty'])
def test_staging_rejects_source_not_matching_pinned_git_tree(repository, monkeypatch, tmp_path, race):
    source, origin = repository
    guard_for(monkeypatch, origin)
    if race == 'ignored-file':
        (source / '.git/info/exclude').write_text('frontend/ignored.js\n')
        (source / 'frontend/ignored.js').write_text('not committed')
    if race == 'hidden-dirty':
        git(source, 'update-index', '--assume-unchanged', 'frontend/index.html')
        (source / 'frontend/index.html').write_text('hidden from git status')
    copy = self_deploy.shutil.copytree

    def copying(original, destination, **kwargs):
        result = copy(original, destination, **kwargs)
        if Path(original) == source / 'frontend':
            if race == 'copied-bytes':
                # A transient edit can evade before/after source status checks.
                (Path(destination) / 'index.html').write_text('raced bytes')
            elif race == 'branch':
                git(source, 'checkout', '-b', 'task/race')
            elif race == 'new-main':
                (source / 'frontend/index.html').write_text('new main')
                git(source, 'add', '.')
                git(source, 'commit', '-m', 'racing update')
                git(source, 'push', 'origin', 'main')
        return result

    monkeypatch.setattr(self_deploy.shutil, 'copytree', copying)
    with pytest.raises(RuntimeError, match='Git source|Git stage'):
        self_deploy.stage_release(source, tmp_path / 'stage')


def test_stage_clean_main_omits_ignored_external_venv(repository, monkeypatch, tmp_path):
    source, origin = repository
    guard_for(monkeypatch, origin)
    dependency = tmp_path / 'external-venv'
    dependency.mkdir()
    (dependency / 'token').write_text('never copy')
    (source / '.venv').symlink_to(dependency, target_is_directory=True)
    (source / '.git/info/exclude').write_text('.venv\n')
    stage = self_deploy.stage_release(source, tmp_path / 'stage')
    assert (stage / 'frontend/index.html').read_bytes() == (source / 'frontend/index.html').read_bytes()
    assert not (stage / '.git').exists()
    assert not (stage / '.venv').exists()


@pytest.mark.parametrize('outcome', ['succeeded', 'rolled_back', 'rollback_failed', 'failed'])
def test_release_record_keeps_pinned_git_sha(repository, monkeypatch, tmp_path, outcome):
    import json
    source, origin = repository
    guard_for(monkeypatch, origin)
    sha = git(source, 'rev-parse', 'HEAD')
    paths = isolated_paths(tmp_path, source)
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('old')

    def checks(stage):
        assert (stage / 'frontend/index.html').read_text() == '<h1>tracked</h1>'
        if outcome == 'failed':
            raise RuntimeError('checks failed')
        # The release is frozen; do not derive provenance from a later HEAD.
        (source / 'frontend/index.html').write_text('later source')
        git(source, 'add', '.')
        git(source, 'commit', '-m', 'after freeze')

    def verify(stage, backend, *, assets=None):
        if outcome == 'rollback_failed' or (outcome == 'rolled_back' and assets is None):
            raise RuntimeError('verification failed')

    if outcome == 'succeeded':
        self_deploy.deploy(paths, frontend_only=True, checks=checks, verify=verify)
    else:
        with pytest.raises(RuntimeError):
            self_deploy.deploy(paths, frontend_only=True, checks=checks, verify=verify)
    status = json.loads((paths.state / 'status.json').read_text())
    assert status['status'] == outcome
    assert status.get('git_sha') == sha


@pytest.mark.parametrize('hidden', ['assume-unchanged', 'skip-worktree', 'ignored-source'])
def test_scheduling_rejects_git_hidden_source_changes(repository, monkeypatch, tmp_path, hidden):
    source, origin = repository
    guard_for(monkeypatch, origin)
    if hidden == 'ignored-source':
        (source / '.git/info/exclude').write_text('frontend/untracked.js\n')
        (source / 'frontend/untracked.js').write_text('not main')
    else:
        git(source, 'update-index', '--' + hidden, 'frontend/index.html')
        (source / 'frontend/index.html').write_text('not main')
    paths = isolated_paths(tmp_path, source)
    monkeypatch.setattr(self_deploy.os, 'geteuid', lambda: 1000)
    calls = []
    with pytest.raises(RuntimeError, match='Git source'):
        self_deploy.main([], paths=paths, run=lambda *a, **k: calls.append(a))
    assert not calls
