from pathlib import Path
import hashlib
import backend.model_controls as controls


def test_native_controls_root_is_only_legacy_or_canonical_private_release(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();releases=tmp_path/'releases';releases.mkdir()
    monkeypatch.setattr(controls,'_OWNER_SOURCE',source,raising=False)
    monkeypatch.setattr(controls,'_OWNER_RELEASES',releases,raising=False)
    candidate=releases/('a'*32);candidate.mkdir()
    assert controls._runtime_root_allowed(source)
    assert controls._runtime_root_allowed(candidate)
    assert not controls._runtime_root_allowed(tmp_path)
    bad=releases/'anything';bad.mkdir();assert not controls._runtime_root_allowed(bad)
    link=releases/('b'*32);link.symlink_to(candidate,target_is_directory=True)
    assert not controls._runtime_root_allowed(link)


def test_new_native_controls_attestation_pins_all_executable_local_sources(tmp_path,monkeypatch):
    root=tmp_path;files={'backend/native_controls_service.py':'entry','backend/native_run_controls.py':'compat','backend/native_api_service.py':'legacy helpers'}
    hashes={k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}
    monkeypatch.setattr(controls,'_CONTROL_HASHES',hashes,raising=False)
    for name,content in files.items():
        p=root/name;p.parent.mkdir(exist_ok=True);p.write_text(content)
    assert controls._control_sources_match(root)
    (root/'backend/native_run_controls.py').write_text('changed')
    assert not controls._control_sources_match(root)
    (root/'backend/native_run_controls.py').unlink()
    outside=tmp_path/'outside';outside.write_text('compat');(root/'backend/native_run_controls.py').symlink_to(outside)
    assert not controls._control_sources_match(root)
