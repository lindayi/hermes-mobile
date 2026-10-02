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


def test_timeout_recovery_sources_have_exact_matching_release_pins():
    from deploy import native_controls_release as release
    root = Path(__file__).resolve().parents[1]
    assert controls._CONTROL_HASHES == release.APPROVED_CONTROL_HASHES
    for name, digest in controls._CONTROL_HASHES.items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest, name
    assert release.approved_controls(root) == controls._CONTROL_HASHES


def test_timeout_baseline_remains_exactly_attested_for_drain_and_rollback(tmp_path, monkeypatch):
    from deploy import native_controls_release as release
    import pytest
    baseline = getattr(controls, '_TIMEOUT_BASELINE_CONTROL_HASHES', None)
    assert controls._PRE_ROUTING_CONTROL_HASHES == release.PRE_ROUTING_CONTROL_HASHES
    assert baseline is not None, 'The immediate pre-fix listener must remain attestable'
    assert baseline == release.TIMEOUT_BASELINE_CONTROL_HASHES
    assert baseline == {**controls._PRE_ROUTING_CONTROL_HASHES, 'backend/native_run_controls.py':
        '5107e54ed631fe2579efe2fb50c6a8ba1e9e3616c4fcd1d0e8ead2f7f29445d9'}
    # Synthetic source sets exercise attestation without production release reads.
    maps = []
    for version in ('current', 'baseline'):
        root = tmp_path/version
        hashes = {}
        for name in baseline:
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(version+name)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        maps.append(hashes)
    monkeypatch.setattr(controls, '_CONTROL_HASHES', maps[0])
    monkeypatch.setattr(release, 'APPROVED_CONTROL_HASHES', maps[0])
    monkeypatch.setattr(controls, '_TIMEOUT_BASELINE_CONTROL_HASHES', maps[1])
    monkeypatch.setattr(release, 'TIMEOUT_BASELINE_CONTROL_HASHES', maps[1])
    assert release.attested_controls(tmp_path/'baseline') == maps[1]
    assert release.attested_controls(tmp_path/'current') == maps[0]
    name = 'backend/native_run_controls.py'
    (tmp_path/'baseline'/name).write_bytes((tmp_path/'current'/name).read_bytes())
    with pytest.raises(RuntimeError, match='approved'):
        release.attested_controls(tmp_path/'baseline')
