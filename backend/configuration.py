"""Load explicitly provisioned private settings; no public-directory secret files."""
import json
from pathlib import Path
from urllib.parse import urlparse
from .app import Settings, private_path


def load_settings(path):
    path=private_path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('Config must be private (chmod 600)')
    data=json.loads(path.read_text())
    allowed=set(Settings.__dataclass_fields__)
    if set(data)-allowed:
        raise ValueError('Unknown configuration field')
    state=private_path(data.get('state_dir',str(Path.home()/'.local/share/hermes-mobile')))
    data['state_dir']=state
    origin=data.get('origin','https://lindayi.me')
    u=urlparse(origin)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        raise ValueError('An exact HTTPS origin without a path is required')
    data.setdefault('rp_id',u.hostname)
    if data['rp_id']!=u.hostname:
        raise ValueError('RP identity must match the configured host exactly')
    data.setdefault('execution_ready',False)
    if not isinstance(data['execution_ready'],bool):
        raise ValueError('Execution readiness must be boolean')
    return Settings(**data)
