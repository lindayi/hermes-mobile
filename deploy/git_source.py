"""Fail-closed main-only Git provenance for mobile deployment."""
from pathlib import Path
import os
import subprocess

CANONICAL_SOURCE = Path('/home/lindayi/projects/hermes-mobile-git')
REMOTE = 'https://github.com/lindayi/hermes-mobile.git'


def _git(source, *args):
    # Repository identity must come from -C, not a caller's Git process state.
    local_env = {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                 'GIT_NAMESPACE', 'GIT_SHALLOW_FILE', 'GIT_GRAFT_FILE',
                 'GIT_CONFIG', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS'}
    env = {key: value for key, value in os.environ.items()
           if key not in local_env and not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}
    env['GIT_TERMINAL_PROMPT'] = '0'
    try:
        return subprocess.run(
            ['git', '--no-replace-objects', '-C', str(source), *args], check=True, capture_output=True,
            text=True, timeout=60, env=env,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Git source verification failed') from exc


def validate_source(source):
    """Fetch main afresh; return its verified commit (never a cached fallback)."""
    _check_checkout(source)
    _git(source, 'fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main')
    _check_checkout(source)
    sha = _git(source, 'rev-parse', 'HEAD')
    if sha != _git(source, 'rev-parse', 'refs/remotes/origin/main'):
        raise RuntimeError('Git source must be exact freshly fetched origin/main')
    return sha


def _check_checkout(source):
    if (Path(_git(source, 'rev-parse', '--show-toplevel')) != Path(source).resolve()
            or _git(source, 'symbolic-ref', '--quiet', 'HEAD') != 'refs/heads/main'):
        raise RuntimeError('Git source must be a main checkout root')
    if _git(source, 'remote', 'get-url', '--all', 'origin') != REMOTE:
        raise RuntimeError('Git source origin is not the approved remote')
    if _git(source, 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Git source must be clean; dirty/untracked files found')
    if any(entry and entry[0] != 'H' for entry in _git(source, 'ls-files', '-vz').split('\0')):
        raise RuntimeError('Git source must not hide tracked changes with index flags')
    from deploy.self_deploy import SOURCE_TREES, SOURCE_FILES, SKIP
    for name in _git(source, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split('\0'):
        parts = Path(name).parts
        if parts and parts[0] in (*SOURCE_TREES, *SOURCE_FILES) and not set(parts) & SKIP:
            raise RuntimeError('Git source contains ignored untracked deployable files')



def preflight(paths, *, service_run=None, extra_paths=()):
    """Exempt redirected fixtures only when no real service runner is enabled."""
    from deploy.self_deploy import Paths
    from deploy.native_controls_release import NATIVE_DROPIN
    defaults = Paths()
    protected = (CANONICAL_SOURCE, Path('/home/lindayi/projects/hermes-mobile'),
                 defaults.state, defaults.webroot, defaults.database.parent,
                 defaults.dropin.parent, NATIVE_DROPIN.parent)
    production = any(
        path.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(path.resolve())
        for path in (paths.source, paths.state, paths.webroot, paths.database, paths.dropin, *extra_paths)
        for root in protected
    )
    if production and paths.source != CANONICAL_SOURCE:
        raise RuntimeError('Git source must use the canonical production checkout')
    if production or (paths.source / '.git').exists():
        return validate_source(paths.source)
    if service_run is subprocess.run:
        raise RuntimeError('Git source provenance required for real service commands')
    return None



def verify_stage(source, stage, sha):
    """Bind the copied stage to immutable Git objects, not mutable worktree hashes."""
    import hashlib
    from deploy.assets import checked_path
    from deploy.self_deploy import SOURCE_TREES, SOURCE_FILES, SKIP
    _check_checkout(source)
    if any(_git(source, 'rev-parse', ref) != sha for ref in ('HEAD', 'refs/remotes/origin/main')):
        raise RuntimeError('Git source changed while staging')
    expected = {}
    for record in _git(source, 'ls-tree', '-rz', sha).split('\0'):
        if not record:
            continue
        metadata, name = record.split('\t', 1)
        mode, kind, oid = metadata.split()
        parts = Path(name).parts
        if parts[0] not in (*SOURCE_TREES, *SOURCE_FILES) or set(parts) & SKIP:
            continue
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise RuntimeError('Git stage requires regular tracked files')
        expected[name] = (mode, oid)
    actual = {}
    for file in stage.rglob('*'):
        file = checked_path(file)
        if file.is_file():
            data = file.read_bytes()
            algorithm = 'sha256' if len(sha) == 64 else 'sha1'
            oid = hashlib.new(algorithm, b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            mode = '100755' if file.stat().st_mode & 0o111 else '100644'
            actual[str(file.relative_to(stage))] = (mode, oid)
    if actual != expected:
        raise RuntimeError('Git stage differs from pinned main tracked files')
