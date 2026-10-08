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


def test_approval_registry_contract_is_pinned():
    from deploy import native_controls_release as release
    dependency = Path('/usr/local/lib/hermes-agent/tools/approval.py')
    assert release.NATIVE_DEPENDENCIES.get(dependency) == (
        '266ce183b1adced097b29df7b2c4a15bde0f86c9a24f6d887339675f4011b903')


def test_clarification_tool_callback_contract_is_pinned():
    from deploy import native_controls_release as release
    dependency = Path('/usr/local/lib/hermes-agent/tools/clarify_tool.py')
    assert release.NATIVE_DEPENDENCIES.get(dependency) == (
        'bbd37f211baa1a1fd1e79cf7ce9a553cbdc92459f5436d37ed04ea8d232dbcd4')


def test_all_photo_hook_dispatch_contracts_are_pinned():
    from deploy import native_controls_release as release
    for name, digest in {
        'agent/turn_context.py': '4a65e543a0795c341fd7598ac42a40eb2d87da6637a3521ecc3a00096eb56896',
        'agent/turn_finalizer.py': '8c6157b55abf936d6f413c3722c919e2a8a0f02439b3c99ba13f6b22063cab7f',
        'hermes_cli/lifecycle.py': 'e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd',
    }.items():
        assert release.NATIVE_DEPENDENCIES[Path('/usr/local/lib/hermes-agent') / name] == digest


def test_approval_registry_drift_rejected_before_service_queries(tmp_path, monkeypatch):
    import pytest
    from deploy import native_controls_release as release
    installed = Path('/usr/local/lib/hermes-agent/tools/approval.py')
    expected = release.NATIVE_DEPENDENCIES.get(installed)
    assert expected is not None
    dependency = tmp_path / 'approval.py'
    dependency.write_text('# changed approval registry contract\n')
    monkeypatch.setattr(release, 'NATIVE_DEPENDENCIES', {dependency: expected})
    probe = object.__new__(release.NativeProbe)
    probe.config = tmp_path / 'config'
    probe.config.write_bytes(b'{}')
    probe.config_bytes = b'{}'
    def unexpected_service_query(*args, **kwargs):
        pytest.fail('Dependency drift must fail before service queries')
    probe.run = unexpected_service_query
    with pytest.raises(RuntimeError, match='native dependency'):
        probe.attest(tmp_path, legacy=True)


def test_actual_candidate_native_hashes_match_both_production_approval_sets():
    root=Path(__file__).resolve().parents[1]
    assert approved_controls(root)==_CONTROL_HASHES
