from pathlib import Path

import pytest

from backend.model_controls import _CONTROL_HASHES
from deploy import native_controls_release as release


def test_native_dependency_drift_rejected_before_service_queries(tmp_path, monkeypatch):
    dependency = tmp_path / 'dependency.py'
    dependency.write_text('changed')
    monkeypatch.setattr(release, 'NATIVE_DEPENDENCIES', {dependency: '0' * 64}, raising=False)
    probe = object.__new__(release.NativeProbe)
    probe.config = tmp_path / 'config'
    probe.config.write_bytes(b'{}')
    probe.config_bytes = b'{}'
    probe.run = lambda *a, **k: pytest.fail('Dependency drift must fail before service queries')
    with pytest.raises(RuntimeError, match='native dependency'):
        probe.attest(tmp_path, legacy=True)


def test_candidate_native_hashes_match_production_approval_set():
    assert release.approved_controls(Path(__file__).resolve().parents[1]) == _CONTROL_HASHES
