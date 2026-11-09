"""Synthetic deployment ownership: independent routes are not mobile assets."""
from pathlib import Path
from types import SimpleNamespace
import json
import hashlib

import pytest
from deploy import assets
from deploy.self_deploy import verify_release


@pytest.mark.parametrize('external', ['finance', 'tmp'])
def test_publish_and_restore_do_not_read_or_modify_external_tree(tmp_path, monkeypatch, external):
    source, web, backup = (tmp_path / name for name in ('source', 'web', 'backup'))
    source.mkdir(); web.mkdir()
    (source / 'index.html').write_text('new')
    (web / 'index.html').write_text('old')
    outside = web / external
    outside.mkdir()
    (outside / 'index.html').write_text('independent')
    original_iterdir = Path.iterdir
    def no_traversal(path):
        assert path != outside, 'mobile traversed externally owned subtree'
        return original_iterdir(path)
    monkeypatch.setattr(Path, 'iterdir', no_traversal)
    assets.publish_assets(source, web, backup)
    assert (outside / 'index.html').read_text() == 'independent'
    manifest = json.loads((backup / 'manifest.json').read_text())
    assert manifest['old'] == {'index.html': hashlib.sha256(b'old').hexdigest()}
    (outside / 'index.html').write_text('independent update')
    assets.restore_assets(web, backup)
    assert (web / 'index.html').read_text() == 'old'
    assert (outside / 'index.html').read_text() == 'independent update'


@pytest.mark.parametrize('external', ['finance', 'tmp'])
@pytest.mark.parametrize('populated', [False, True])
def test_candidate_cannot_publish_external_namespace(tmp_path, external, populated):
    source, web, backup = (tmp_path / name for name in ('source', 'web', 'backup'))
    source.mkdir(); web.mkdir()
    (source / 'index.html').write_text('new')
    (source / external).mkdir()
    if populated:
        (source / external / 'index.html').write_text('collision')
    (web / 'index.html').write_text('old')
    with pytest.raises(ValueError, match='Externally owned'):
        assets.publish_assets(source, web, backup)
    assert (web / 'index.html').read_text() == 'old'
    assert not backup.exists()


@pytest.mark.parametrize('hazard', ['symlink', 'file'])
def test_external_boundary_must_be_real_directory(tmp_path, hazard):
    source, web, backup = (tmp_path / name for name in ('source', 'web', 'backup'))
    source.mkdir(); web.mkdir()
    (source / 'index.html').write_text('new')
    (web / 'index.html').write_text('old')
    if hazard == 'symlink':
        (web / 'tmp').symlink_to(source, target_is_directory=True)
    else:
        (web / 'tmp').write_text('not a directory')
    with pytest.raises(ValueError):
        assets.publish_assets(source, web, backup)
    assert (web / 'index.html').read_text() == 'old'
    assert not backup.exists()


def test_verifier_uses_same_owned_scope_for_legacy_and_live_tree(tmp_path, monkeypatch):
    import deploy.public_http as http
    stage, web = tmp_path / 'stage', tmp_path / 'web'
    old = stage / 'public'
    old.mkdir(parents=True); web.mkdir()
    for root in (old, web):
        (root / 'index.html').write_text('mobile')
        (root / 'finance').mkdir()
    (old / 'finance' / 'index.html').write_text('retired')
    (web / 'finance' / 'index.html').write_text('independent')
    (web / 'tmp').mkdir()
    (web / 'tmp' / 'preview.html').write_text('preview')
    seen = []
    def matches(url, data, *args, **kwargs):
        assert '/finance/' not in url and '/tmp/' not in url
        seen.append((url, data))
        return True
    monkeypatch.setattr(http, 'public_asset_matches', matches)
    verify_release(SimpleNamespace(webroot=web), stage, False)
    assert seen and all(data == b'mobile' for _, data in seen)
    (web / 'index.html').write_text('wrong')
    with pytest.raises(RuntimeError, match='Published assets differ'):
        verify_release(SimpleNamespace(webroot=web), stage, False)


@pytest.mark.parametrize('version', ['old', 'new'])
def test_rollback_manifest_cannot_claim_external_namespace(tmp_path, version):
    source, web, backup = (tmp_path / name for name in ('source', 'web', 'backup'))
    source.mkdir(); web.mkdir()
    (source / 'index.html').write_text('new')
    (web / 'index.html').write_text('old')
    assets.publish_assets(source, web, backup)
    (web / 'tmp').mkdir()
    target = web / 'tmp' / 'page.html'
    target.write_text('independent')
    manifest = json.loads((backup / 'manifest.json').read_text())
    manifest[version]['tmp/page.html'] = hashlib.sha256(b'independent').hexdigest()
    (backup / 'manifest.json').write_text(json.dumps(manifest))
    (backup / 'files' / 'tmp').mkdir()
    (backup / 'files' / 'tmp' / 'page.html').write_text('independent')
    with pytest.raises(ValueError, match='Externally owned'):
        assets.restore_assets(web, backup)
    assert target.read_text() == 'independent'
    assert (web / 'index.html').read_text() == 'new'
