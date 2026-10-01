"""Production ASGI factory: requires an explicitly provisioned private config."""
import os
from .configuration import load_settings
from .app import create_app as assemble_app


def create_app():
    path = os.environ.get('HERMES_MOBILE_CONFIG')
    if not path:
        raise RuntimeError('HERMES_MOBILE_CONFIG must name a private configuration file')
    return assemble_app(load_settings(path))
