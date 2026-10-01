import importlib.util
import pytest
from test_auth import BOOTSTRAP


def test_production_factory_requires_explicit_private_configuration(monkeypatch,tmp_path):
    assert importlib.util.find_spec('backend.serve') is not None, 'Production factory not implemented'
    from backend.serve import create_app
    monkeypatch.delenv('HERMES_MOBILE_CONFIG',raising=False)
    with pytest.raises(RuntimeError):create_app()
    from backend.manage import initialize
    path,code=initialize(tmp_path/'state',tmp_path/'hermes')
    monkeypatch.setenv('HERMES_MOBILE_CONFIG',str(path))
    app=create_app()
    assert app.state.settings.execution_ready is False
    assert app.state.auth.store.path.parent == tmp_path/'state'
