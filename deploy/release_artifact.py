"""Public build bundles and fail-closed hosted release evidence.

Packaging/unpacking is NOT authorization. Only acquire_verified_bundle verifies
live GitHub evidence; controllers must call it themselves, never accept JSON.
"""
from pathlib import Path, PurePosixPath
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import io
import json
import os
import re
import resource
import stat
import subprocess
import tarfile
import tempfile
import zipfile

from deploy.assets import checked_path, public_tree, _asset_name

MAX_BUNDLE = 64 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_FILES = 10000


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _json(data):
    return json.loads(data, object_pairs_hook=_unique)


def _name(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or PurePosixPath(name).is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsafe bundle path')
    return name


def source_mapping(source):
    """Exactly the deployable trees, enumerated without following aliases."""
    from deploy.self_deploy import SOURCE_TREES, SOURCE_FILES, SKIP
    source = checked_path(source)
    result = {}

    def visit(path):
        path = checked_path(path)
        info = path.stat()
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name not in SKIP:
                        visit(Path(entry.path))
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            result[path.relative_to(source).as_posix()] = _sha(path.read_bytes())
        else:
            raise ValueError('Source must contain only regular unaliased files')

    for name in (*SOURCE_TREES, *SOURCE_FILES):
        path = checked_path(source / name)
        if path.exists():
            visit(path)
    return result


def create_bundle(source, public, destination, source_sha):
    if not isinstance(source_sha, str) or not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('Full source SHA required')
    source, public, destination = map(checked_path, (source, public, destination))
    assets = {p.relative_to(public).as_posix(): p.read_bytes() for p in public_tree(public)}
    manifest = {'schema': 1, 'source_sha': source_sha, 'source_files': source_mapping(source),
                'public_files': {name: _sha(data) for name, data in assets.items()}}
    with destination.open('xb') as output, tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [('manifest.json', json.dumps(manifest, sort_keys=True).encode()),
                           *[('public/' + name, data) for name, data in sorted(assets.items())]]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    return manifest


def unpack_bundle(bundle, source, destination, source_sha):
    """Validate complete bytes before writes. CI consumers use this without trust claims."""
    bundle, destination = map(checked_path, (bundle, destination))
    info = bundle.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BUNDLE:
        raise ValueError('Unsafe bundle or size limit exceeded')
    if destination.exists():
        raise ValueError('Bundle destination must not exist')
    records = {}
    with tarfile.open(bundle, mode='r:') as archive:
        for info in archive:
            if len(records) >= MAX_FILES or info.size < 0 or info.size > MAX_FILE:
                raise ValueError('Bundle member limit exceeded')
            name = _name(info.name)
            if name in records or info.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or info.sparse is not None:
                raise ValueError('Duplicate or non-regular bundle member')
            if name != 'manifest.json':
                if not name.startswith('public/'):
                    raise ValueError('Unknown bundle member')
                _asset_name(name.removeprefix('public/'))
            records[name] = archive.extractfile(info).read()
    manifest = _json(records.pop('manifest.json'))
    if (not isinstance(manifest, dict)
            or set(manifest) != {'schema', 'source_sha', 'source_files', 'public_files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['source_sha'] != source_sha
            or manifest['source_files'] != source_mapping(source)):
        raise ValueError('Bundle source/schema mismatch')
    public_files = {name.removeprefix('public/'): _sha(data) for name, data in records.items()}
    if public_files != manifest['public_files'] or 'index.html' not in public_files:
        raise ValueError('Bundle public checksum mismatch')
    destination.mkdir(parents=True, mode=0o700)
    for name, data in records.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
    return manifest



# Installed server policy, NOT values supplied by the release manifest or intent.
REPOSITORY = 'lindayi/hermes-mobile'
REPOSITORY_ID = 1399942965
WORKFLOW = '.github/workflows/ci.yml'
WORKFLOW_ID = 372155405
REF = 'refs/heads/main'
EXPECTED_JOBS = frozenset({'build', 'checks', 'js', 'python (0)', 'python (1)',
                          'browser (0)', 'browser (1)', 'browser (2)', 'browser (3)',
                          'native', 'source-ci', 'integration-tests', 'attest'})

@dataclass(frozen=True)
class VerifiedBundle:
    source_sha: str
    run_id: int
    run_attempt: int
    artifact_id: int
    bundle_sha256: str
    source_files: Mapping[str, str]
    public_files: Mapping[str, str]
    public_path: Path


def _command(command, *, run, limit=4 * 1024 * 1024):
    """Bound stdout AND stderr at the OS boundary, including archive downloads."""
    def bounded():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    env = dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1', GH_PAGER='cat')
    env.pop('GH_DEBUG', None)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            run(command, stdout=output, stderr=errors, check=True, timeout=120,
                preexec_fn=bounded, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError('Hosted evidence command failed; no local fallback') from exc
        if output.tell() > limit:
            raise ValueError('Hosted evidence output limit exceeded')
        output.seek(0)
        return output.read(limit + 1)


def _api(endpoint, *, run):
    return _json(_command(['gh', 'api', '--hostname', 'github.com',
                          'repos/' + REPOSITORY + '/' + endpoint], run=run))


def _listed(endpoint, key, *, run):
    records = []
    total = None
    for page in range(1, 11):
        data = _api(endpoint + f'?per_page=100&page={page}', run=run)
        count = data.get('total_count')
        items = data.get(key)
        if (type(count) is not int or not 0 <= count <= 1000 or not isinstance(items, list)
                or len(items) > 100 or (total is not None and total != count)):
            raise ValueError('Invalid or changing GitHub pagination')
        total = count
        records.extend(items)
        if len(records) == total:
            return records
        if not items or len(records) > total:
            break
    raise ValueError('Incomplete GitHub pagination')


def _run_record(run_id, sha, *, run):
    data = _api(f'actions/runs/{run_id}', run=run)
    expected = {'id': run_id, 'workflow_id': WORKFLOW_ID, 'path': WORKFLOW,
                'event': 'push', 'head_branch': 'main', 'head_sha': sha,
                'status': 'completed', 'conclusion': 'success'}
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError('Source CI run is not trusted successful main')
    for key in ('repository', 'head_repository'):
        repo = data.get(key, {})
        if repo.get('id') != REPOSITORY_ID or repo.get('full_name') != REPOSITORY:
            raise ValueError('Source CI repository identity mismatch')
    attempt = data.get('run_attempt')
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Invalid source CI attempt')
    return attempt


def _check_jobs(run_id, attempt, sha, *, run):
    jobs = _listed(f'actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs', run=run)
    if (len(jobs) != len(EXPECTED_JOBS) or {job.get('name') for job in jobs} != EXPECTED_JOBS
            or len({job.get('id') for job in jobs}) != len(jobs)):
        raise ValueError('Source CI complete job set mismatch')
    for job in jobs:
        expected = {'run_id': run_id, 'run_attempt': attempt, 'head_sha': sha,
                    'status': 'completed', 'conclusion': 'success'}
        if any(job.get(key) != value for key, value in expected.items()):
            raise ValueError('Source CI job did not succeed in exact attempt')


def _attestation(bundle, sha, run_id, attempt, *, run):
    signer = REPOSITORY + '/' + WORKFLOW
    identity = 'https://github.com/' + signer + '@' + REF
    results = _json(_command([
        'gh', 'attestation', 'verify', str(bundle), '--hostname', 'github.com',
        # Exact certificate identity pins both signer repository and workflow.
        # gh forbids combining it with signer-repo or signer-workflow selectors.
        '--repo', REPOSITORY,
        '--cert-identity', identity, '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com',
        '--source-ref', REF, '--source-digest', sha, '--signer-digest', sha,
        '--deny-self-hosted-runners', '--predicate-type', 'https://slsa.dev/provenance/v1',
        '--format', 'json'], run=run))
    expected = {'runInvocationURI': f'https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}',
                'sourceRepositoryIdentifier': str(REPOSITORY_ID),
                'sourceRepositoryURI': 'https://github.com/' + REPOSITORY,
                'sourceRepositoryDigest': sha, 'sourceRepositoryRef': REF,
                'buildSignerURI': identity, 'buildSignerDigest': sha, 'buildTrigger': 'push',
                'runnerEnvironment': 'github-hosted', 'subjectAlternativeName': identity,
                'issuer': 'https://token.actions.githubusercontent.com'}
    # Only certificate extensions authenticated by gh count, never caller-controlled
    # statement/predicate invocation strings. Missing extensions are a blocker.
    if isinstance(results, list):
        for result in results:
            certificate = result.get('verificationResult', {}).get('signature', {}).get('certificate', {})
            if all(certificate.get(key) == value for key, value in expected.items()):
                return
    raise ValueError('Verified certificate lacks exact trusted run invocation')


def acquire_verified_bundle(source: Path, run_id: int, destination: Path, *, run=subprocess.run):
    """Acquire live evidence for canonical current main; no unsigned evidence input."""
    from deploy.git_source import CANONICAL_SOURCE, validate_source
    source, destination = map(checked_path, (source, destination))
    if source != CANONICAL_SOURCE or type(run_id) is not int or run_id <= 0:
        raise ValueError('Canonical source and positive hosted run ID required')
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Separate new verified bundle destination required')
    sha = validate_source(source)
    attempt = _run_record(run_id, sha, run=run)
    _check_jobs(run_id, attempt, sha, run=run)
    artifacts = _listed(f'actions/runs/{run_id}/artifacts', 'artifacts', run=run)
    matches = [a for a in artifacts if a.get('name') == f'release-{run_id}-{attempt}']
    if len(matches) != 1:
        raise ValueError('Unique exact-attempt release artifact required')
    artifact = matches[0]
    artifact_id, size = artifact.get('id'), artifact.get('size_in_bytes')
    if (artifact.get('expired') is not False or type(artifact_id) is not int or artifact_id <= 0
            or type(size) is not int or not 0 < size <= MAX_BUNDLE):
        raise ValueError('Expired or oversized release artifact')
    expected = {'id': run_id, 'head_sha': sha, 'head_branch': 'main',
                'repository_id': REPOSITORY_ID, 'head_repository_id': REPOSITORY_ID}
    if any(artifact.get('workflow_run', {}).get(key) != value for key, value in expected.items()):
        raise ValueError('Release artifact source mismatch')
    data = _command(['gh', 'api', '--hostname', 'github.com',
                     f'repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip'], run=run, limit=MAX_BUNDLE)
    digest = artifact.get('digest')
    if digest is not None and digest != 'sha256:' + _sha(data):
        raise ValueError('Artifact transport digest mismatch')
    # Never extract the transport archive. Only one bounded regular tar is allowed.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) != 1:
            raise ValueError('Unexpected transport archive members')
        info = members[0]
        mode = info.external_attr >> 16
        if (info.filename != 'release.tar' or info.file_size > MAX_BUNDLE or info.flag_bits & 1
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
            raise ValueError('Unsafe transport archive')
        tar = archive.read(info)
    with tempfile.TemporaryDirectory(prefix='hermes-release-') as temporary:
        bundle = Path(temporary) / 'release.tar'
        bundle.write_bytes(tar)
        _attestation(bundle, sha, run_id, attempt, run=run)
        if _run_record(run_id, sha, run=run) != attempt or validate_source(source) != sha:
            raise ValueError('Source/run changed during verification')
        manifest = unpack_bundle(bundle, source, destination, sha)
    return VerifiedBundle(sha, run_id, attempt, artifact_id, _sha(tar),
                          MappingProxyType(manifest['source_files']), MappingProxyType(manifest['public_files']),
                          destination / 'public')


def main(argv=None):
    """CI packaging/consumption only. Does not authorize production deployment."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'unpack'))
    parser.add_argument('--source', type=Path, default=Path.cwd())
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch('[0-9a-f]{40}', sha):
        parser.error('GITHUB_SHA must be a full checkout SHA')
    if args.command == 'build':
        from deploy.frontend_release import build_frontend
        with tempfile.TemporaryDirectory(prefix='hermes-build-') as temporary:
            public = build_frontend(args.source / 'frontend', Path(temporary) / 'public')
            create_bundle(args.source, public, args.bundle, sha)
    else:
        if args.destination is None:
            parser.error('unpack requires --destination')
        unpack_bundle(args.bundle, args.source, args.destination, sha)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
