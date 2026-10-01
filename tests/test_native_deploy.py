from pathlib import Path
import pytest


def test_native_prep_preserves_existing_key_and_matches_cli_tools():
    from deploy.configure_native import configuration_changes
    changes, token=configuration_changes({'platform_toolsets':{'cli':['terminal','file']}},'existing-secret')
    assert token=='existing-secret'
    # Web has a dedicated listener; gateway wakeups must not occupy its sole slot.
    assert changes['gateway.api_server.enabled']=='false'
    assert changes['gateway.api_server.host']=='127.0.0.1'
    assert changes['gateway.api_server.max_concurrent_runs']=='1'
    assert changes['platform_toolsets.api_server']=='["terminal", "file"]'
    assert changes['API_SERVER_KEY']=='existing-secret'


def test_core_installer_rejects_unexpected_patch_targets():
    from deploy.install_core import patch_targets
    assert patch_targets('--- a/run_agent.py\n+++ b/run_agent.py\n')=={'run_agent.py'}
    with pytest.raises(ValueError):patch_targets('--- a/../../etc/passwd\n+++ b/../../etc/passwd\n')


def test_core_installer_checks_exact_source_fingerprint(tmp_path):
    import hashlib
    from deploy.install_core import validate_baselines
    source=tmp_path/'run_agent.py';source.write_text('original')
    hashes={'run_agent.py':{'installed_sha256':hashlib.sha256(b'original').hexdigest(),'staged_sha256':hashlib.sha256(b'patched').hexdigest()}}
    validate_baselines(tmp_path,hashes)
    source.write_text('unexpected upgrade')
    with pytest.raises(ValueError):validate_baselines(tmp_path,hashes)
