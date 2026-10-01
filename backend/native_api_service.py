"""Dedicated loopback web-request adapter; no gateway runner or cron scheduler."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys


import re

OWNER_HOME = Path('/home/lindayi/.hermes')
PROFILE_ROOT = OWNER_HOME/'profiles'


def _private_file(path):
    if (not path.is_absolute() or path.resolve() != path or not path.is_file()
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
            or path.stat().st_nlink != 1):
        raise ValueError('Owned private regular file required')
    return path


def member_environment(home):
    # Never inherit the shell/systemd manager's provider or tool credentials.
    return {'HOME':str(home), 'HERMES_HOME':str(home),
            'HERMES_SHARED_AUTH_DIR':str(home/'shared'),
            'XDG_CONFIG_HOME':str(home/'.config'),
            'XDG_DATA_HOME':str(home/'.local/share'),
            'XDG_CACHE_HOME':str(home/'.cache'),
            'PATH':'/usr/local/bin:/usr/bin:/bin', 'LANG':'C.UTF-8'}


def listener_settings(config):
    if not isinstance(config.get('upstream_token'),str) or not config['upstream_token']:
        raise ValueError('Private API key missing')
    if 'hermes_home' not in config:
        if 'port' in config:
            raise ValueError('Member port requires an explicit member home')
        return {'host':'127.0.0.1','port':18642,'key':config['upstream_token']}
    if set(config) != {'hermes_home', 'port', 'upstream_token'}:
        raise ValueError('Unexpected member listener configuration')
    home = Path(config['hermes_home'])
    if (home.parent != PROFILE_ROOT or not re.fullmatch(r'member_[a-z0-9_-]{1,57}', home.name)
            or home.resolve() != home or not home.is_dir()):
        raise ValueError('Canonical generated member home required')
    for name in ('.env', 'config.yaml', 'SOUL.md'):
        _private_file(home/name)
    for name in ('auth.json', 'state.db'):
        if (home/name).exists() or (home/name).is_symlink():
            _private_file(home/name)
    port = config['port']
    if type(port) is not int or not 1024 <= port <= 65535 or port == 18642:
        raise ValueError('Dedicated unprivileged member port required')
    if len(config['upstream_token']) < 32:
        raise ValueError('Member API token must contain at least 32 characters')
    return {'host':'127.0.0.1','port':port,'key':config['upstream_token']}


def member_adapter(base, home):
    class MemberAdapter(base):
        def _create_agent(self, ephemeral_system_prompt=None, session_id=None, **kwargs):
            if not str(session_id or '').startswith('mobile_activation_'):
                return super()._create_agent(ephemeral_system_prompt=ephemeral_system_prompt,
                                             session_id=session_id, **kwargs)
            # A real native model turn, but no tools, fallbacks, memory updates,
            # or request-selected provider. Do not mutate shared native globals:
            # other concurrent conversations retain their configured tools.
            from gateway.run import _load_gateway_config
            from gateway.platforms.api_server import _resolve_request_runtime_agent_kwargs
            from run_agent import AIAgent
            model = _load_gateway_config().get('model') or {}
            provider, name = model.get('provider'), model.get('default')
            if not provider or provider == 'auto' or not name:
                raise ValueError('Explicit member provider/model required for smoke turn')
            options = _resolve_request_runtime_agent_kwargs(provider, target_model=name)
            options.update(model=name, enabled_toolsets=[], skip_memory=True,
                           skip_background_review=True, skip_context_files=True,
                           max_iterations=1, fallback_model=None, quiet_mode=True,
                           session_id=session_id, session_db=self._ensure_session_db(),
                           platform='api_server', ephemeral_system_prompt='Reply literally to the verification request. Do not use tools.')
            return AIAgent(**options)

        async def _handle_capabilities(self, request):
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            from aiohttp import web
            data = json.loads(response.text)
            data['mobile_runtime'] = {
                'home':str(home), 'credential_policy':'member-only-v1',
                'smoke_policy':'reserved-no-tools-v1'}
            return web.json_response(data)
    return MemberAdapter


async def serve(config_path):
    config = json.loads(_private_file(Path(config_path)).read_text())
    settings = listener_settings(config)
    member = 'hermes_home' in config
    home = Path(config['hermes_home']) if member else OWNER_HOME
    if member:
        os.environ.clear()
        os.environ.update(member_environment(home))
        os.umask(0o077)
    else:
        os.environ['HERMES_HOME'] = str(home)
    sys.path.insert(0,'/usr/local/lib/hermes-agent')
    if member:
        # Native profiles deliberately share root OAuth by default. A FAMILY
        # sidecar must not. Pin the process-local root BEFORE importing auth,
        # provider pools, plugins or gateway modules that cache its value.
        import hermes_constants
        hermes_constants.get_default_hermes_root = lambda: home
    from dotenv import load_dotenv
    load_dotenv(home/'.env', override=False)
    if member:
        os.environ.update(member_environment(home))
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    adapter_class = member_adapter(APIServerAdapter, home) if member else APIServerAdapter
    adapter=adapter_class(PlatformConfig(enabled=True,extra=settings))
    stopped=asyncio.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
    try:
        if not await adapter.connect():raise RuntimeError('Native web listener failed to start')
        await stopped.wait()
    finally:
        await adapter.disconnect()


if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING)
    path=os.environ.get('HERMES_MOBILE_CONFIG')
    if not path:raise RuntimeError('Explicit private config path required')
    asyncio.run(serve(path))
