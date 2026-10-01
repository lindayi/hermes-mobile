from pathlib import Path
from deploy.native_controls_release import approved_controls
from backend.model_controls import _CONTROL_HASHES


def test_readiness_dependency_drift_rejected_before_service_queries(tmp_path,monkeypatch):
    import pytest
    from deploy import native_controls_release as release
    dependency=tmp_path/'dependency.py';dependency.write_text('changed')
    monkeypatch.setattr(release,'NATIVE_DEPENDENCIES',{dependency:'0'*64},raising=False)
    probe=object.__new__(release.NativeProbe)
    probe.config=tmp_path/'config';probe.config.write_bytes(b'{}');probe.config_bytes=b'{}'
    with pytest.raises(RuntimeError,match='native dependency'):
        probe.attest(tmp_path,legacy=True)


def test_actual_candidate_native_hashes_match_both_production_approval_sets():
    root=Path(__file__).resolve().parents[1]
    assert approved_controls(root)==_CONTROL_HASHES
