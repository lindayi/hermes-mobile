import importlib.util
import pytest


def test_web_native_listener_is_loopback_and_requires_secret():
    assert importlib.util.find_spec('backend.native_api_service') is not None, 'Isolated web admission listener missing'
    from backend.native_api_service import listener_settings
    with pytest.raises(ValueError): listener_settings({})
    settings=listener_settings({'upstream_token':'test-only','host':'0.0.0.0'})
    assert settings=={'host':'127.0.0.1','port':18642,'key':'test-only'}


def test_member_listener_is_explicit_and_profile_scoped(tmp_path, monkeypatch):
    from backend import native_api_service as service
    root = tmp_path/'profiles'
    home = root/'member_abc123'
    home.mkdir(parents=True)
    monkeypatch.setattr(service, 'PROFILE_ROOT', root, raising=False)
    for name, value in [('.env',''), ('SOUL.md','Member'), ('config.yaml','model: {provider: openai, default: test-model}\nplatform_toolsets: {api_server: []}\n')]:
        (home/name).write_text(value)
        (home/name).chmod(0o600)
    config = {'hermes_home':str(home), 'port':18643, 'upstream_token':'x'*48}
    assert service.listener_settings(config) == {'host':'127.0.0.1', 'port':18643,'key':'x'*48}
    assert hasattr(service, 'member_environment'), 'Credential-isolating member environment missing'
    env = service.member_environment(home)
    assert env['HOME'] == env['HERMES_HOME'] == str(home)
    assert env['HERMES_SHARED_AUTH_DIR'] == str(home/'shared')
    assert 'OPENAI_API_KEY' not in env
    for change in ({'hermes_home':str(tmp_path)}, {'port':18642}, {'port':True}, {'port':80}, {'port':65536}, {'host':'0.0.0.0'}, {'upstream_token':'short'}):
        with pytest.raises(ValueError):
            service.listener_settings(dict(config, **change))


def test_partial_member_descriptor_never_falls_back_to_owner():
    from backend.native_api_service import listener_settings
    with pytest.raises(ValueError):
        listener_settings({'port':18643,'upstream_token':'x'*48})


def test_member_serve_sanitizes_credentials_and_native_root_before_connect(tmp_path, monkeypatch):
    import asyncio
    import json
    import os
    import sys
    import types
    from backend import native_api_service as service
    home = tmp_path/'profiles/member_abc123'
    home.mkdir(parents=True)
    monkeypatch.setattr(service, 'PROFILE_ROOT', home.parent)
    for name, content in [('.env','OPENAI_API_KEY=member-key'), ('SOUL.md','Member'), ('config.yaml','model: {provider: openai, default: test-model}\nplatform_toolsets: {api_server: []}\n')]:
        (home/name).write_text(content)
        (home/name).chmod(0o600)
    path = tmp_path/'member.json'
    path.write_text(json.dumps({'hermes_home':str(home),'port':18643,'upstream_token':'x'*48}))
    path.chmod(0o600)
    monkeypatch.setenv('OPENAI_API_KEY','OWNER-DO-NOT-INHERIT')
    monkeypatch.setenv('ANTHROPIC_API_KEY','OWNER-DO-NOT-INHERIT')
    monkeypatch.setenv('HERMES_HOME','/owner')
    original = dict(os.environ)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(service.os, 'umask', lambda value: 0o077)
    constants = types.ModuleType('hermes_constants')
    constants.get_default_hermes_root = lambda: tmp_path/'owner'
    monkeypatch.setitem(sys.modules,'hermes_constants',constants)
    observed = {}
    def load_dotenv(path, override=False):
        assert str(path) == str(home/'.env')
        assert 'OPENAI_API_KEY' not in os.environ
        os.environ['OPENAI_API_KEY'] = 'member-key'
        # A dotenv cannot override the isolation anchors.
        os.environ['HOME'] = '/owner'
    monkeypatch.setitem(sys.modules,'dotenv',types.SimpleNamespace(load_dotenv=load_dotenv))
    monkeypatch.setitem(sys.modules,'gateway.config',types.SimpleNamespace(PlatformConfig=lambda **kwargs: kwargs))
    class Adapter:
        def __init__(self, config):
            observed['config'] = config
        async def connect(self):
            observed['env'] = dict(os.environ)
            observed['root'] = constants.get_default_hermes_root()
            observed['caps'] = json.loads((await self._handle_capabilities(None)).text)
            return True
        async def disconnect(self):
            observed['disconnected'] = True
        async def _handle_capabilities(self, request):
            return types.SimpleNamespace(status=200,text=json.dumps({'features':{}}))
    monkeypatch.setitem(sys.modules,'gateway.platforms.api_server',types.SimpleNamespace(APIServerAdapter=Adapter))
    monkeypatch.setitem(sys.modules,'gateway.run',types.SimpleNamespace(_load_gateway_config=lambda: {}))
    monkeypatch.setitem(sys.modules,'hermes_cli.tools_config',types.SimpleNamespace(_get_platform_tools=lambda cfg,platform: set()))
    monkeypatch.setitem(sys.modules,'aiohttp',types.SimpleNamespace(web=types.SimpleNamespace(json_response=lambda data:types.SimpleNamespace(text=json.dumps(data)))))
    class Stopped:
        async def wait(self): pass
        def set(self): pass
    monkeypatch.setattr(service.asyncio,'Event',Stopped)
    try:
        asyncio.run(service.serve(path))
    finally:
        os.environ.clear()
        os.environ.update(original)
    assert observed['config']['extra']['port'] == 18643
    assert observed['env']['OPENAI_API_KEY'] == 'member-key'
    assert 'ANTHROPIC_API_KEY' not in observed['env']
    assert observed['env']['HOME'] == observed['env']['HERMES_HOME'] == str(home)
    assert observed['root'] == home
    assert observed['caps']['mobile_runtime'] == {'home':str(home),'credential_policy':'member-only-v1','smoke_policy':'reserved-no-tools-v1'}
    assert observed['disconnected']


def test_only_reserved_smoke_sessions_disable_tools(tmp_path, monkeypatch):
    import sys
    import types
    from backend import native_api_service as service
    assert hasattr(service, 'member_adapter'), 'Reserved-session smoke adapter missing'
    home = tmp_path/'member_abc'
    calls = []
    class Base:
        def _create_agent(self, **kwargs):
            calls.append(kwargs)
            return 'normal-native-agent-with-configured-tools'
        def _ensure_session_db(self): return 'member-db'
    captured = {}
    def agent(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace()
    monkeypatch.setitem(sys.modules,'run_agent',types.SimpleNamespace(AIAgent=agent))
    monkeypatch.setitem(sys.modules,'gateway.run',types.SimpleNamespace(_load_gateway_config=lambda: {'model':{'provider':'openai','default':'member-model'}}))
    monkeypatch.setitem(sys.modules,'gateway.platforms.api_server',types.SimpleNamespace(_resolve_request_runtime_agent_kwargs=lambda provider,target_model: {'api_key':'member-key','provider':provider}))
    adapter = service.member_adapter(Base, home)()
    assert adapter._create_agent(session_id='normal_session', requested_model='chosen-model') == 'normal-native-agent-with-configured-tools'
    assert calls[0]['requested_model'] == 'chosen-model'
    adapter._create_agent(session_id='mobile_activation_random', requested_model='untrusted-override')
    assert len(calls) == 1
    assert captured['enabled_toolsets'] == []
    assert captured['skip_memory'] is True
    assert captured['skip_background_review'] is True
    assert captured['skip_context_files'] is True
    assert captured['model'] == 'member-model'
    assert captured['api_key'] == 'member-key'
    assert captured['fallback_model'] is None
    assert captured['session_db'] == 'member-db'
