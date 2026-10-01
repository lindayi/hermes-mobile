import importlib.util
from pathlib import Path
import json
import pytest


def test_private_config_loads_subpath_origin_and_keeps_execution_off(tmp_path):
    assert importlib.util.find_spec('backend.configuration') is not None, 'Config loader missing'
    from backend.configuration import load_settings
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'state_dir':str(tmp_path/'state'),'profiles':{'default':str(tmp_path/'hermes')},'origin':'https://lindayi.me','rp_id':'lindayi.me'})); config.chmod(0o600)
    settings=load_settings(config)
    assert settings.origin=='https://lindayi.me'
    assert settings.execution_ready is False
    assert settings.upstream_token==''
    with pytest.raises(ValueError):
        config.write_text(json.dumps({'state_dir':'/var/www/html/hermes/private'}))
        load_settings(config)


def test_deployment_contains_no_credentials_or_broad_proxy():
    root=Path(__file__).resolve().parents[1]
    path=root/'deploy/apache-hermes-mobile.conf'
    assert path.exists(), 'Apache subpath deployment template missing'
    text=path.read_text()
    assert 'ProxyPass /hermes/app-api/' in text
    assert '127.0.0.1:9120/hermes/app-api/' in text
    assert 'ProxyPass / ' not in text
    assert 'SSLCertificate' not in text
    assert 'Options -Indexes' in text
