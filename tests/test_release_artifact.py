"""Offline bundle contract; all source/assets/network evidence is synthetic."""
import hashlib
import importlib
import json
import io
import tarfile
import zipfile
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def artifact():
    return importlib.import_module('deploy.release_artifact')


def fixture(tmp_path):
    source = tmp_path / 'source'
    (source / 'frontend').mkdir(parents=True)
    (source / 'frontend/index.html').write_text('<h1>source</h1>')
    (source / 'README.md').write_text('public source')
    (source / 'config.json').write_text('PRIVATE')
    public = tmp_path / 'generated'
    public.mkdir()
    (public / 'index.html').write_text('<h1>built once</h1>')
    return source, public


def test_bundle_roundtrip_binds_full_source_and_generated_bytes(tmp_path):
    module = artifact()
    source, public = fixture(tmp_path)
    bundle = tmp_path / 'release.tar'
    sha = 'a' * 40
    module.create_bundle(source, public, bundle, sha)
    manifest = module.unpack_bundle(bundle, source, tmp_path / 'unpacked', sha)
    assert manifest['schema'] == 1
    assert manifest['source_sha'] == sha
    assert manifest['source_files'] == {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in ['frontend/index.html', 'README.md']}
    assert manifest['public_files'] == {
        'index.html': hashlib.sha256((public / 'index.html').read_bytes()).hexdigest()}
    assert (tmp_path / 'unpacked/public/index.html').read_bytes() == (public / 'index.html').read_bytes()
    assert b'PRIVATE' not in bundle.read_bytes()



@pytest.mark.parametrize('limit,value', [('MAX_BUNDLE', 100), ('MAX_FILE', 10), ('MAX_FILES', 1)])
def test_unpack_limits_are_enforced_before_writes(tmp_path, monkeypatch, limit, value):
    module = artifact()
    source, public = fixture(tmp_path)
    bundle = tmp_path / 'release.tar'
    module.create_bundle(source, public, bundle, 'a' * 40)
    monkeypatch.setattr(module, limit, value, raising=False)
    with pytest.raises(ValueError, match='limit'):
        module.unpack_bundle(bundle, source, tmp_path / 'out', 'a' * 40)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('name,kind', [('../outside', tarfile.REGTYPE),
    ('/outside', tarfile.REGTYPE), ('public/../outside.js', tarfile.REGTYPE),
    ('public/link.js', tarfile.SYMTYPE), ('public/link.js', tarfile.LNKTYPE),
    ('public/device.js', tarfile.CHRTYPE), ('public/index.html', tarfile.REGTYPE),
    ('private.json', tarfile.REGTYPE), ('public//x.js', tarfile.REGTYPE)])
def test_archive_rejects_unsafe_or_duplicate_members(tmp_path, name, kind):
    module = artifact()
    source, public = fixture(tmp_path)
    bundle = tmp_path / 'release.tar'
    module.create_bundle(source, public, bundle, 'a' * 40)
    with tarfile.open(bundle, 'a') as archive:
        info = tarfile.TarInfo(name)
        info.type = kind
        archive.addfile(info, io.BytesIO(b''))
    with pytest.raises(ValueError):
        module.unpack_bundle(bundle, source, tmp_path / 'out', 'a' * 40)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('change', ['source', 'extra-source', 'public', 'schema', 'sha', 'json-duplicate'])
def test_full_manifest_and_actual_bytes_cannot_diverge(tmp_path, change):
    module = artifact()
    source, public = fixture(tmp_path)
    bundle = tmp_path / 'release.tar'
    module.create_bundle(source, public, bundle, 'a' * 40)
    with tarfile.open(bundle) as archive:
        records = {i.name: archive.extractfile(i).read() for i in archive}
    manifest = json.loads(records['manifest.json'])
    if change == 'source':
        (source / 'README.md').write_text('changed')
    elif change == 'extra-source':
        (source / 'frontend/extra.js').write_text('changed')
    elif change == 'public':
        records['public/index.html'] = b'not tested'
    elif change == 'schema':
        manifest['schema'] = 2
    elif change == 'sha':
        manifest['source_sha'] = 'b' * 40
    records['manifest.json'] = json.dumps(manifest).encode()
    if change == 'json-duplicate':
        records['manifest.json'] = records['manifest.json'].replace(b'{', b'{"schema":1,', 1)
    with tarfile.open(bundle, 'w') as archive:
        for name, data in records.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError):
        module.unpack_bundle(bundle, source, tmp_path / 'out', 'a' * 40)
    assert not (tmp_path / 'out').exists()



class GitHub:
    """Explicit synthetic API/CLI adapter; never authentic cryptographic evidence."""
    def __init__(self, tmp_path, monkeypatch):
        self.module = artifact()
        self.source, public = fixture(tmp_path)
        from deploy import git_source
        monkeypatch.setattr(git_source, 'CANONICAL_SOURCE', self.source)
        monkeypatch.setattr(git_source, 'validate_source', lambda source: 'a' * 40)
        bundle = tmp_path / 'release.tar'
        self.module.create_bundle(self.source, public, bundle, 'a' * 40)
        self.bundle = bundle.read_bytes()
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, 'w') as archive:
            archive.writestr('release.tar', self.bundle)
        self.zip = zipped.getvalue()
        repo = {'id': 1399942965, 'full_name': 'lindayi/hermes-mobile'}
        self.metadata = {'id': 51, 'workflow_id': 372155405, 'path': '.github/workflows/ci.yml',
            'event': 'push', 'head_branch': 'main', 'head_sha': 'a' * 40, 'run_attempt': 2,
            'repository': dict(repo), 'head_repository': dict(repo), 'status': 'completed',
            'conclusion': 'success'}
        self.jobs = [{'id': n, 'name': name, 'run_id': 51, 'run_attempt': 2,
                      'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success'}
                     for n, name in enumerate(['build', 'checks', 'js', 'python (0)', 'python (1)',
                                              'browser (0)', 'browser (1)', 'browser (2)',
                                              'browser (3)', 'native', 'source-ci', 'attest'], 100)]
        self.record = {'id': 61, 'name': 'release-51-2', 'expired': False,
            'size_in_bytes': len(self.zip), 'digest': 'sha256:' + hashlib.sha256(self.zip).hexdigest(),
            'workflow_run': {'id': 51, 'head_sha': 'a' * 40, 'head_branch': 'main',
                             'repository_id': 1399942965, 'head_repository_id': 1399942965}}
        identity = 'https://github.com/lindayi/hermes-mobile/.github/workflows/ci.yml@refs/heads/main'
        self.certificate = {'runInvocationURI': 'https://github.com/lindayi/hermes-mobile/actions/runs/51/attempts/2',
            'sourceRepositoryIdentifier': '1399942965', 'sourceRepositoryURI': 'https://github.com/lindayi/hermes-mobile',
            'sourceRepositoryDigest': 'a' * 40, 'sourceRepositoryRef': 'refs/heads/main',
            'buildSignerURI': identity, 'buildSignerDigest': 'a' * 40, 'buildTrigger': 'push',
            'runnerEnvironment': 'github-hosted', 'subjectAlternativeName': identity,
            'issuer': 'https://token.actions.githubusercontent.com'}
        self.calls = []
        self.run_reads = 0
        self.final_attempt = None

    def run(self, command, **kwargs):
        self.calls.append(command)
        assert kwargs['check'] is True
        assert kwargs['timeout'] <= 120
        assert callable(kwargs['preexec_fn']), 'download/output must have OS file-size limit'
        if command[1:3] == ['attestation', 'verify']:
            assert Path(command[3]).read_bytes() == self.bundle
            data = [{'verificationResult': {'signature': {'certificate': self.certificate}}}]
        else:
            endpoint = command[command.index('--hostname') + 2]
            if endpoint.endswith('/zip'):
                kwargs['stdout'].write(self.zip)
                return SimpleNamespace(returncode=0)
            if '/jobs?' in endpoint:
                data = {'total_count': len(self.jobs), 'jobs': self.jobs}
            elif '/artifacts?' in endpoint:
                data = {'total_count': 1, 'artifacts': [self.record]}
            else:
                assert endpoint.endswith('/runs/51'), endpoint
                self.run_reads += 1
                data = dict(self.metadata)
                if self.final_attempt and self.run_reads > 1:
                    data['run_attempt'] = self.final_attempt
        kwargs['stdout'].write(json.dumps(data).encode())
        return SimpleNamespace(returncode=0)


def test_attestation_uses_one_exact_identity_selector(tmp_path, monkeypatch):
    # gh rejects combining any of these four mutually exclusive selectors.
    fake = GitHub(tmp_path, monkeypatch)
    fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'verified', run=fake.run)
    command = next(c for c in fake.calls if c[1:3] == ['attestation', 'verify'])
    selectors = {'--cert-identity', '--cert-identity-regex', '--signer-repo', '--signer-workflow'}
    assert selectors.intersection(command) == {'--cert-identity'}
    assert command[command.index('--cert-identity') + 1] == (
        'https://github.com/lindayi/hermes-mobile/.github/workflows/ci.yml@refs/heads/main'
    )
    assert command[command.index('--cert-oidc-issuer') + 1] == 'https://token.actions.githubusercontent.com'


def test_acquire_verifies_live_success_and_signer_before_unpack(tmp_path, monkeypatch):
    fake = GitHub(tmp_path, monkeypatch)
    evidence = fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'verified', run=fake.run)
    assert evidence.source_sha == 'a' * 40
    assert evidence.run_id == 51 and evidence.run_attempt == 2 and evidence.artifact_id == 61
    assert evidence.bundle_sha256 == hashlib.sha256(fake.bundle).hexdigest()
    assert (evidence.public_path / 'index.html').read_text() == '<h1>built once</h1>'
    assert fake.run_reads == 2, 'run must still be the same successful latest attempt after verification'
    command = next(c for c in fake.calls if c[1:3] == ['attestation', 'verify'])
    for flag, value in {'--repo': 'lindayi/hermes-mobile',
                       '--cert-identity': 'https://github.com/lindayi/hermes-mobile/.github/workflows/ci.yml@refs/heads/main',
                       '--cert-oidc-issuer': 'https://token.actions.githubusercontent.com',
                       '--source-ref': 'refs/heads/main', '--source-digest': 'a' * 40,
                       '--signer-digest': 'a' * 40, '--format': 'json'}.items():
        assert command[command.index(flag) + 1] == value
    assert '--deny-self-hosted-runners' in command



@pytest.mark.parametrize('field,value', [('id', 52), ('workflow_id', 1), ('path', '.github/workflows/evil.yml'),
    ('event', 'pull_request'), ('event', 'workflow_dispatch'), ('head_branch', 'topic'),
    ('head_sha', 'b' * 40), ('status', 'in_progress'), ('conclusion', 'failure'), ('run_attempt', 0),
    ('repository', {'id': 1, 'full_name': 'lindayi/hermes-mobile'}),
    ('head_repository', {'id': 1399942965, 'full_name': 'fork/mobile'})])
def test_acquire_rejects_untrusted_run(tmp_path, monkeypatch, field, value):
    fake = GitHub(tmp_path, monkeypatch)
    fake.metadata[field] = value
    with pytest.raises(ValueError):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()
    assert not any(c[1:3] == ['attestation', 'verify'] for c in fake.calls)


@pytest.mark.parametrize('change', [
    'missing', 'missing-native', 'duplicate', 'extra', 'skipped', 'wrong-attempt',
    'wrong-sha', 'wrong-run'
])
def test_acquire_requires_every_job_in_same_attempt(tmp_path, monkeypatch, change):
    fake = GitHub(tmp_path, monkeypatch)
    if change == 'missing': fake.jobs.pop()
    elif change == 'missing-native': fake.jobs = [job for job in fake.jobs if job['name'] != 'native']
    elif change == 'duplicate': fake.jobs[-1] = fake.jobs[0]
    elif change == 'extra': fake.jobs.append(dict(fake.jobs[0], id=999, name='other'))
    elif change == 'skipped': fake.jobs[0]['conclusion'] = 'skipped'
    elif change == 'wrong-attempt': fake.jobs[0]['run_attempt'] = 1
    elif change == 'wrong-sha': fake.jobs[0]['head_sha'] = 'b' * 40
    elif change == 'wrong-run': fake.jobs[0]['run_id'] = 52
    with pytest.raises(ValueError):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('field,value', [('name', 'release-51-1'), ('expired', True),
    ('size_in_bytes', 9999999999), ('digest', 'sha256:' + '0' * 64),
    ('workflow_run', {'id': 52}), ('id', '../secret')])
def test_acquire_rejects_bad_artifact_metadata(tmp_path, monkeypatch, field, value):
    fake = GitHub(tmp_path, monkeypatch)
    fake.record[field] = value
    with pytest.raises(ValueError):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('field', ['runInvocationURI', 'sourceRepositoryIdentifier', 'sourceRepositoryDigest',
    'sourceRepositoryRef', 'buildSignerURI', 'buildSignerDigest', 'buildTrigger', 'runnerEnvironment',
    'sourceRepositoryURI', 'subjectAlternativeName', 'issuer'])
def test_verified_certificate_must_authenticate_invocation_not_just_digest(tmp_path, monkeypatch, field):
    fake = GitHub(tmp_path, monkeypatch)
    fake.certificate.pop(field)
    with pytest.raises(ValueError, match='certificate'):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()


def test_rerun_started_during_verification_invalidates_evidence(tmp_path, monkeypatch):
    fake = GitHub(tmp_path, monkeypatch)
    fake.final_attempt = 3
    with pytest.raises(ValueError, match='changed'):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('kind', ['extra', 'traversal', 'symlink', 'oversized'])
def test_transport_zip_is_not_extracted_unsafely(tmp_path, monkeypatch, kind):
    fake = GitHub(tmp_path, monkeypatch)
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, 'w') as archive:
        name = '../release.tar' if kind == 'traversal' else 'release.tar'
        info = zipfile.ZipInfo(name)
        if kind == 'symlink': info.external_attr = 0o120777 << 16
        archive.writestr(info, fake.bundle)
        if kind == 'extra': archive.writestr('evil', b'x')
    fake.zip = zipped.getvalue()
    fake.record['digest'] = 'sha256:' + hashlib.sha256(fake.zip).hexdigest()
    if kind == 'oversized': monkeypatch.setattr(fake.module, 'MAX_BUNDLE', 100)
    with pytest.raises(ValueError):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=fake.run)
    assert not (tmp_path / 'out').exists()


def test_failed_crypto_verifier_does_not_fall_back(tmp_path, monkeypatch):
    fake = GitHub(tmp_path, monkeypatch)
    def run(command, **kwargs):
        if command[1:3] == ['attestation', 'verify']:
            raise subprocess.CalledProcessError(1, command)
        return fake.run(command, **kwargs)
    with pytest.raises(RuntimeError, match='no local fallback'):
        fake.module.acquire_verified_bundle(fake.source, 51, tmp_path / 'out', run=run)
    assert not (tmp_path / 'out').exists()



def test_ci_commands_build_once_then_consume_exact_archive(tmp_path, monkeypatch):
    module = artifact()
    source, _ = fixture(tmp_path)
    bundle = tmp_path / 'ci.tar'
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    assert module.main(['build', '--source', str(source), '--bundle', str(bundle)]) == 0
    from deploy import frontend_release
    monkeypatch.setattr(frontend_release, 'build_frontend', lambda *a: pytest.fail('consumer must not build'))
    assert module.main(['unpack', '--source', str(source), '--bundle', str(bundle),
                        '--destination', str(tmp_path / 'consumer')]) == 0
    assert (tmp_path / 'consumer/public/index.html').read_text() == '<h1>source</h1>'



def test_workflow_builds_once_browser_consumes_and_main_only_attests():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    assert set(jobs['source-ci']['needs']) == {'build', 'checks', 'js', 'python', 'browser', 'native'}
    assert jobs['browser']['needs'] == 'build'
    browser = '\n'.join(s.get('run', '') for s in jobs['browser']['steps'])
    assert 'build_frontend' not in browser and 'release_artifact unpack' in browser
    assert '--assets "$RUNNER_TEMP/consumed/public"' in browser
    build = '\n'.join(s.get('run', '') for s in jobs['build']['steps'])
    assert 'release_artifact build' in build
    for job in ('build', 'browser', 'attest'):
        steps = jobs[job]['steps']
        transfer = next(s for s in steps if 'artifact@' in s.get('uses', ''))
        assert transfer['with']['name'] == 'release-${{ github.run_id }}-${{ github.run_attempt }}'
    attest = jobs['attest']
    assert attest['needs'] == 'source-ci'
    assert "github.event_name == 'push'" in attest['if']
    assert "github.ref == 'refs/heads/main'" in attest['if']
    assert "github.repository == 'lindayi/hermes-mobile'" in attest['if']
    assert attest['permissions'] == {'contents': 'read', 'id-token': 'write', 'attestations': 'write'}
    assert workflow['permissions'] == {'contents': 'read'}
    for name, job in jobs.items():
        if name != 'attest':
            assert 'id-token' not in job.get('permissions', {})
        for step in job['steps']:
            if '@' in step.get('uses', ''):
                assert len(step['uses'].split('@')[-1]) == 40



def test_tar_sparse_members_are_not_regular_public_files(tmp_path):
    module = artifact()
    source, public = fixture(tmp_path)
    bundle = tmp_path / 'release.tar'
    module.create_bundle(source, public, bundle, 'a' * 40)
    # Sparse type is considered isreg() by tarfile; it must still be rejected.
    with tarfile.open(bundle, 'a') as archive:
        info = tarfile.TarInfo('public/sparse.js')
        info.type = tarfile.GNUTYPE_SPARSE
        archive.addfile(info, io.BytesIO(b''))
    with pytest.raises(ValueError, match='non-regular'):
        module.unpack_bundle(bundle, source, tmp_path / 'out', 'a' * 40)



def test_subprocess_output_limit_is_enforced_by_real_os_boundary():
    import sys
    module = artifact()
    assert module._command([sys.executable, '-c', 'print("ok")'], run=subprocess.run, limit=128) == b'ok\n'
    with pytest.raises(RuntimeError, match='no local fallback'):
        module._command([sys.executable, '-c', 'import os; os.write(1,b"x"*8192); os.write(1,b"x")'],
                        run=subprocess.run, limit=128)


def test_pagination_completeness_and_changing_count(tmp_path, monkeypatch):
    module = artifact()
    pages = iter([{'total_count': 2, 'items': [{'id': 1}]}, {'total_count': 2, 'items': [{'id': 2}]}])
    monkeypatch.setattr(module, '_api', lambda *a, **kw: next(pages))
    assert module._listed('endpoint', 'items', run=None) == [{'id': 1}, {'id': 2}]
    pages = iter([{'total_count': 2, 'items': [{'id': 1}]}, {'total_count': 3, 'items': [{'id': 2}]}])
    with pytest.raises(ValueError, match='pagination'):
        module._listed('endpoint', 'items', run=None)


@pytest.mark.parametrize('alias', ['symlink', 'hardlink'])
def test_bundle_source_mapping_rejects_aliases(tmp_path, alias):
    module = artifact()
    source, public = fixture(tmp_path)
    target = source / 'frontend/alias.html'
    if alias == 'symlink': target.symlink_to(source / 'config.json')
    else: target.hardlink_to(source / 'config.json')
    with pytest.raises(ValueError):
        module.create_bundle(source, public, tmp_path / 'out.tar', 'a' * 40)
