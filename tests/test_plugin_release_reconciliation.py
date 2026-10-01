"""Offline reconciliation fixtures: never read installed plugin or private releases."""
from pathlib import Path
import hashlib
import json
import shutil

import pytest

from deploy import self_deploy as deploy

ROOT = Path(__file__).resolve().parents[1]
OLD_SHA = '3c5ae90cd19fa6b62fa98d45f1ac53ceea67d984c8724d9dc299852f0e42c56f'
NEW_SHA = '66b6ab320e8375459d40eb0b63510d07df3b03468b9efb03b0e6b5fa2af38778'
MANIFEST_SHA = '7db60cfd51284c9b9fc37cc38ab4fbe2196c5c7399b73ed64e63e35f7ed407a7'
PREIMAGE_NAME = 'mobile-delivery-before-cron-metadata-20260928.py'
PLUGIN_FILE = 'hermes-plugin/mobile_delivery/__init__.py'


@pytest.fixture
def repair(tmp_path, monkeypatch):
    new = (ROOT / PLUGIN_FILE).read_bytes()
    metadata = b"        cron_deliver_env_var='HERMES_MOBILE_HOME_CHANNEL',\n"
    assert new.count(metadata) == 1
    old = new.replace(metadata, b'')
    manifest = (ROOT / 'hermes-plugin/mobile_delivery/plugin.yaml').read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    assert hashlib.sha256(new).hexdigest() == NEW_SHA
    assert hashlib.sha256(manifest).hexdigest() == MANIFEST_SHA
    paths = deploy.Paths(source=tmp_path / 'source', state=tmp_path / 'private',
                         webroot=tmp_path / 'web', database=tmp_path / 'live/runs.sqlite',
                         dropin=tmp_path / 'systemd/override.conf')
    for name, data in {
        PLUGIN_FILE: new, 'hermes-plugin/mobile_delivery/plugin.yaml': manifest,
        'frontend/index.html': b'<h1>new</h1>', 'requirements.lock': b'fixture==1\n',
        'backend/native_controls_service.py': b'# unchanged native\n',
        'patches/native.patch': b'unchanged patch',
    }.items():
        file = paths.source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    previous = deploy.stage_release(paths.source, paths.state / 'releases/old')
    (previous / PLUGIN_FILE).write_bytes(old)
    (paths.state / 'current').symlink_to(previous, target_is_directory=True)
    preimage = paths.state / PREIMAGE_NAME
    preimage.write_bytes(old)
    installed = tmp_path / 'installed/mobile_delivery'
    shutil.copytree(paths.source / 'hermes-plugin/mobile_delivery', installed)
    monkeypatch.setattr(deploy, 'INSTALLED_MOBILE_PLUGIN', installed, raising=False)
    paths.webroot.mkdir()
    (paths.webroot / 'index.html').write_text('<h1>old</h1>')
    return paths, previous, installed, preimage


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def no_services(*args, **kwargs):
    pytest.fail('no service operation permitted')


def test_frontend_accepts_only_already_installed_exact_repair_without_mutating_native(repair):
    paths, previous, installed, preimage = repair
    before = [snapshot(p) for p in (previous, installed, paths.source)]
    evidence = preimage.read_bytes()
    checked = []
    verified = []
    stage = deploy.deploy(paths, frontend_only=True, checks=lambda stage: checked.append(stage),
                          verify=lambda stage, backend: verified.append((stage, backend)), run=no_services)
    assert checked == [stage]
    assert verified == [(stage, False)]
    assert (paths.webroot / 'index.html').read_text() == '<h1>new</h1>'
    assert (stage / PLUGIN_FILE).read_bytes() == (installed / '__init__.py').read_bytes()
    assert [snapshot(p) for p in (previous, installed, paths.source)] == before
    assert preimage.read_bytes() == evidence
    assert (paths.state / 'current').resolve() == previous
    assert not paths.database.exists()
    assert not paths.dropin.exists()


# Characterize the preserved protection: the exception must never cover another delta.
@pytest.mark.parametrize('name', [name for name in deploy.PROTECTED if name != 'hermes-plugin'])
@pytest.mark.parametrize('operation', ['add', 'change', 'delete'])
def test_every_other_protected_difference_still_blocks(repair, name, operation):
    paths, previous, installed, preimage = repair
    relative = name + '/extra.py' if name == 'patches' else name
    source, baseline = paths.source / relative, previous / relative
    for file in (source, baseline):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.unlink(missing_ok=True)
        if operation != 'add':
            file.write_text('original')
    if operation == 'delete':
        source.unlink()
    else:
        source.write_text('unapproved change')
    with pytest.raises(RuntimeError, match='dependency/native'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert (paths.state / 'current').resolve() == previous


@pytest.mark.parametrize('location', ['installed', 'source', 'previous', 'preimage'])
@pytest.mark.parametrize('operation', ['change', 'delete'])
def test_exact_repair_bytes_are_required_everywhere(repair, location, operation):
    paths, previous, installed, preimage = repair
    file = {'installed': installed / '__init__.py', 'source': paths.source / PLUGIN_FILE,
            'previous': previous / PLUGIN_FILE, 'preimage': preimage}[location]
    if operation == 'delete':
        file.unlink()
    else:
        file.write_bytes(file.read_bytes() + b'\n# unapproved\n')
    with pytest.raises(RuntimeError, match='maintenance|evidence'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)


@pytest.mark.parametrize('operation', ['manifest', 'extra', 'missing_manifest'])
def test_source_and_installed_matching_each_other_is_not_authorization(repair, operation):
    paths, previous, installed, preimage = repair
    for root in (paths.source / 'hermes-plugin/mobile_delivery', installed):
        if operation == 'manifest':
            (root / 'plugin.yaml').write_text('unapproved manifest')
        elif operation == 'extra':
            (root / 'unapproved.py').write_text('unapproved code')
        else:
            (root / 'plugin.yaml').unlink()
    with pytest.raises(RuntimeError, match='maintenance|evidence'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)


def test_reverse_transition_cannot_remove_installed_fix(repair):
    paths, previous, installed, preimage = repair
    (previous / PLUGIN_FILE).write_bytes((installed / '__init__.py').read_bytes())
    (paths.source / PLUGIN_FILE).write_bytes(preimage.read_bytes())
    with pytest.raises(RuntimeError, match='maintenance'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)


@pytest.mark.parametrize('kind', ['hardlink', 'fifo', 'directory'])
@pytest.mark.parametrize('location', ['preimage', 'installed'])
def test_evidence_is_regular_and_unaliased(repair, kind, location):
    import os
    paths, previous, installed, preimage = repair
    file = preimage if location == 'preimage' else installed / '__init__.py'
    if kind == 'hardlink':
        os.link(file, paths.source.parent / 'hardlinked')
    else:
        file.unlink()
        if kind == 'fifo':
            os.mkfifo(file)
        else:
            file.mkdir()
    with pytest.raises(RuntimeError, match='maintenance|evidence'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)


@pytest.mark.parametrize('location', ['installed', 'source'])
@pytest.mark.parametrize('kind', ['regular', 'hardlink', 'fifo', 'symlink_file', 'symlink_directory'])
def test_cache_name_does_not_exempt_non_directory_evidence(repair, location, kind):
    import os
    paths, previous, installed, preimage = repair
    root = installed if location == 'installed' else paths.source / 'hermes-plugin/mobile_delivery'
    cache = root / '__pycache__'
    target = paths.source.parent / 'cache-alias-target'
    if kind == 'regular':
        cache.write_text('unapproved non-cache file')
    elif kind == 'hardlink':
        target.write_text('unapproved aliased file')
        os.link(target, cache)
    elif kind == 'fifo':
        os.mkfifo(cache)
    else:
        if kind == 'symlink_directory':
            target.mkdir()
        else:
            target.write_text('unapproved symlink target')
        cache.symlink_to(target, target_is_directory=target.is_dir())
    checked, verified = [], []
    with pytest.raises((RuntimeError, ValueError), match='evidence|Symlink|symlink'):
        deploy.deploy(paths, frontend_only=True, checks=lambda stage: checked.append(stage),
                      verify=lambda *args: verified.append(args), run=no_services)
    assert checked == verified == []
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert (paths.state / 'current').resolve() == previous
    assert not paths.database.exists()
    assert not paths.dropin.exists()


def test_actual_cache_directories_remain_excluded(repair):
    paths, previous, installed, preimage = repair
    for root in (installed, paths.source / 'hermes-plugin/mobile_delivery',
                 previous / 'hermes-plugin/mobile_delivery'):
        cache = root / '__pycache__'
        cache.mkdir()
        (cache / '__init__.cpython-312.pyc').write_bytes(b'ignored bytecode')
    deploy.deploy(paths, frontend_only=True, checks=lambda _: None,
                  verify=lambda *_: None, run=no_services)
    assert (paths.webroot / 'index.html').read_text() == '<h1>new</h1>'


@pytest.mark.parametrize('location', ['installed', 'previous', 'source', 'stage'])
def test_unreadable_non_cache_subtree_rejects_incomplete_evidence(repair, location):
    import os
    if os.geteuid() == 0:
        pytest.skip('requires an unprivileged filesystem permission check')
    paths, previous, installed, preimage = repair
    stage = deploy.stage_release(paths.source, paths.state / 'releases/candidate')
    root = {'installed': installed, 'previous': previous / 'hermes-plugin/mobile_delivery',
            'source': paths.source / 'hermes-plugin/mobile_delivery',
            'stage': stage / 'hermes-plugin/mobile_delivery'}[location]
    extra = root / 'unapproved'
    extra.mkdir()
    (extra / 'native.py').write_text('unapproved native code')
    original_mode = extra.stat().st_mode & 0o777
    extra.chmod(0)
    checked, verified = [], []
    try:
        # Prove the fixture really is unreadable; do not simulate a permission error.
        with pytest.raises(PermissionError):
            with os.scandir(extra) as entries:
                list(entries)
        with pytest.raises(RuntimeError, match='evidence unavailable or unsafe') as error:
            if location in ('installed', 'previous'):
                deploy.deploy(paths, frontend_only=True, checks=lambda item: checked.append(item),
                              verify=lambda *args: verified.append(args), run=no_services)
            else:
                # Stage first so source-copy refusal cannot mask inventory behavior.
                deploy.check_protected_release(paths, stage, previous)
        assert isinstance(error.value.__cause__, PermissionError)
    finally:
        extra.chmod(original_mode)
    assert checked == verified == []
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert (paths.state / 'current').resolve() == previous
    assert not paths.database.exists()
    assert not paths.dropin.exists()


def test_normal_identical_releases_do_not_require_historical_evidence(repair):
    paths, previous, installed, preimage = repair
    (previous / PLUGIN_FILE).write_bytes((paths.source / PLUGIN_FILE).read_bytes())
    preimage.unlink()
    shutil.rmtree(installed)
    deploy.deploy(paths, frontend_only=True, checks=lambda _: None, verify=lambda *_: None, run=no_services)
    assert (paths.webroot / 'index.html').read_text() == '<h1>new</h1>'


@pytest.mark.parametrize('fail_verification', [False, True])
def test_bridge_reconciliation_preserves_native_bytes_on_success_and_rollback(repair, fail_verification):
    from backend.runs import RunJournal, RunConflict
    paths, previous, installed, preimage = repair
    journal = RunJournal(paths.database)
    before = [snapshot(p) for p in (previous, installed, paths.source)]
    evidence = preimage.read_bytes()
    commands, verified = [], []
    def verify(stage, backend, *, assets=None):
        assert backend
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'default', 'blocked', 'hello', 'blocked')
        verified.append(stage)
        if fail_verification and assets is None:
            raise RuntimeError('fixture unhealthy')
    def release():
        return deploy.deploy(paths, checks=lambda _: None, verify=verify,
                             run=lambda command, **kw: commands.append(command))
    if fail_verification:
        with pytest.raises(RuntimeError, match='fixture unhealthy'):
            release()
        assert (paths.state / 'current').resolve() == previous
        assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
        assert verified[-1] == previous
    else:
        stage = release()
        assert (paths.state / 'current').resolve() == stage
        assert (stage / PLUGIN_FILE).read_bytes() == (installed / '__init__.py').read_bytes()
        assert (paths.webroot / 'index.html').read_text() == '<h1>new</h1>'
        assert verified == [stage]
    assert commands and all(command in (
        ['systemctl', '--user', 'daemon-reload'],
        ['systemctl', '--user', 'restart', 'hermes-mobile.service'],
    ) for command in commands)
    assert [snapshot(p) for p in (previous, installed, paths.source)] == before
    assert preimage.read_bytes() == evidence
    assert journal.submit('u', 'default', 's', 'hello', 'after')[1]


@pytest.mark.parametrize('changed', ['installed', 'preimage', 'source', 'previous'])
def test_repair_evidence_is_rechecked_after_tests_before_publication(repair, changed):
    paths, previous, installed, preimage = repair
    def checks(stage):
        file = {'installed': installed / '__init__.py', 'preimage': preimage,
                'source': paths.source / PLUGIN_FILE, 'previous': previous / PLUGIN_FILE}[changed]
        file.write_bytes((stage / PLUGIN_FILE).read_bytes() if changed == 'previous' else b'changed')
    with pytest.raises(RuntimeError, match='maintenance|evidence|baseline'):
        deploy.deploy(paths, frontend_only=True, checks=checks, verify=lambda *_: None, run=no_services)
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert (paths.state / 'current').resolve() == previous
    assert not paths.database.exists()


@pytest.mark.parametrize('changed', ['installed', 'preimage', 'previous'])
def test_repair_evidence_is_rechecked_after_bridge_drain(repair, changed):
    from backend.runs import RunJournal, RunConflict
    paths, previous, installed, preimage = repair
    journal = RunJournal(paths.database)
    active, _ = journal.submit('u', 'default', 's', 'hello', 'active')
    commands = []
    def sleep(_):
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'default', 'blocked', 'hello', 'blocked')
        file = {'installed': installed / '__init__.py', 'preimage': preimage,
                'previous': previous / PLUGIN_FILE}[changed]
        file.write_bytes((paths.source / PLUGIN_FILE).read_bytes() if changed == 'previous' else b'changed')
        journal.finish('u', active['id'], 'completed')
    expected = 'admission state is unverified' if changed == 'previous' else 'maintenance|evidence|baseline'
    with pytest.raises(RuntimeError, match=expected) as failure:
        deploy.deploy(paths, checks=lambda _: None, verify=lambda *_: None,
                      run=lambda command, **kw: commands.append(command), sleep=sleep)
    assert commands == []
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'
    assert (paths.state / 'current').resolve() == previous
    assert not paths.dropin.exists()
    if changed == 'previous':
        # The baseline itself changed while draining. The strengthened abort
        # guard must retain admission, not call this a verified rollback.
        assert 'Abort baseline changed' in str(failure.value.__cause__)
        status = json.loads((paths.state / 'status.json').read_text())
        assert status['status'] == 'rollback_failed'
        with journal.connect() as connection:
            assert [tuple(row) for row in connection.execute('SELECT singleton,owner FROM deployment_gate')] == [(1, status['release'])]
        with pytest.raises(RunConflict, match='deploy'):
            journal.submit('u', 'default', 's', 'hello', 'after')
    else:
        assert journal.submit('u', 'default', 's', 'hello', 'after')[1]


@pytest.mark.parametrize('alias', ['installed_file', 'installed_root', 'preimage', 'previous_file'])
def test_reconciliation_rejects_aliased_evidence_before_checks(repair, alias):
    paths, previous, installed, preimage = repair
    target = {'installed_file': installed / '__init__.py', 'installed_root': installed,
              'preimage': preimage, 'previous_file': previous / PLUGIN_FILE}[alias]
    real = paths.source.parent / 'aliased-evidence'
    target.rename(real)
    target.symlink_to(real, target_is_directory=real.is_dir())
    with pytest.raises((RuntimeError, ValueError), match='Symlink|evidence|maintenance'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: pytest.fail('no publication'), run=no_services)
    assert (paths.webroot / 'index.html').read_text() == '<h1>old</h1>'


@pytest.mark.parametrize('extra', ['config.json', '.env', 'arbitrary.py'])
def test_reconciliation_rejects_unpinned_installed_files_even_staging_exclusions(repair, extra):
    paths, previous, installed, preimage = repair
    (installed / extra).write_text('not part of approved plugin')
    with pytest.raises(RuntimeError, match='maintenance|evidence'):
        deploy.deploy(paths, frontend_only=True, checks=lambda _: pytest.fail('reject before checks'),
                      verify=lambda *_: None, run=no_services)
