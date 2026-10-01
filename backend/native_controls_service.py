"""Owner-only native controls entrypoint; legacy/member launcher stays unchanged."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys

# Direct systemd script execution and package tests share the same local imports.
if __package__:
    from .native_api_service import OWNER_HOME, PROFILE_ROOT, _private_file, listener_settings, member_adapter
    from .native_run_controls import run_controls_adapter
    from .native_maintenance import maintenance_adapter
    from .native_session_deletion import session_deletion_adapter
    from .native_notifications import notification_adapter
else:
    from native_api_service import OWNER_HOME, PROFILE_ROOT, _private_file, listener_settings, member_adapter
    from native_run_controls import run_controls_adapter
    from native_maintenance import maintenance_adapter
    from native_session_deletion import session_deletion_adapter
    from native_notifications import notification_adapter


def listener_adapter(base, home, *, member, state_dir=None):
    if member:
        return member_adapter(base, home)
    if state_dir is None:
        raise ValueError('Explicit owner state_dir required')
    return session_deletion_adapter(notification_adapter(
        maintenance_adapter(run_controls_adapter(base)), home,
        state_dir=Path(state_dir)), home)


async def serve(config_path):
    config = json.loads(_private_file(Path(config_path)).read_text())
    if 'hermes_home' in config:
        raise ValueError('Owner controls entrypoint cannot launch member profiles')
    settings = listener_settings(config)
    os.environ['HERMES_HOME'] = str(OWNER_HOME)
    sys.path.insert(0, '/usr/local/lib/hermes-agent')
    from dotenv import load_dotenv
    load_dotenv(OWNER_HOME/'.env', override=False)
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    adapter = listener_adapter(APIServerAdapter, OWNER_HOME, member=False,
                               state_dir=config['state_dir'])(PlatformConfig(enabled=True, extra=settings))
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    try:
        if not await adapter.connect():
            raise RuntimeError('Native controls listener failed to start')
        await stopped.wait()
    finally:
        await adapter.disconnect()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    path = os.environ.get('HERMES_MOBILE_CONFIG')
    if not path:
        raise RuntimeError('Explicit private config path required')
    asyncio.run(serve(path))
