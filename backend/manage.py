"""Private bootstrap, invoked by the owner at deployment, never by public signup."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import stat
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from .auth import AuthService, digest
from .app import private_path
from .configuration import load_settings
from .photo_policy import PhotoPolicy


def private_write(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(data)


def initialize(state_dir,hermes_home):
    root=private_path(state_dir)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    root.chmod(0o700)
    config=root/'config.json'
    if config.exists() or (root/'auth.sqlite').exists():
        raise FileExistsError('Already initialized: refusing to replace credentials')
    vapid=Vapid(); vapid.generate_keys()
    private=root/'vapid.pem'
    private_write(private,vapid.private_pem().decode())
    public=base64.urlsafe_b64encode(vapid.public_key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
    secret=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
    AuthService(root/'auth.sqlite',bootstrap_secret=secret)
    private_write(config,json.dumps({'state_dir':str(root),'profiles':{'default':str(Path(hermes_home).resolve())},'origin':'https://lindayi.me','rp_id':'lindayi.me','execution_ready':False,'vapid_private_key':str(private),'vapid_public_key':public},indent=2)+'\n')
    return config,secret


def issue_owner_code(config_path):
    """Explicit console-only refresh, forbidden after an owner is enrolled."""
    settings=load_settings(config_path)
    auth=AuthService(settings.state_dir/'auth.sqlite',origin=settings.origin,rp_id=settings.rp_id)
    secret=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
    with auth.store.transaction() as db:
        if db.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
            raise ValueError('Owner already registered; use passkey recovery instead')
        db.execute("INSERT OR REPLACE INTO settings VALUES('bootstrap_hash',?)",(digest(secret),))
        db.execute("INSERT OR REPLACE INTO settings VALUES('bootstrap_expiry',?)",(str(auth.clock()+900),))
        db.execute("DELETE FROM challenges WHERE kind='register'")
    return secret


def _private_operator_path(path, label, *, directory=False):
    path = Path(os.path.abspath(os.fspath(path)))
    components = [*reversed(path.parents), path]
    for component in components:
        try:
            info = component.lstat()
        except OSError as exc:
            raise ValueError(f'{label} path is unavailable.') from exc
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f'{label} path must not contain symlinks.')
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise ValueError(f'{label} has an unsafe file type.')
    if info.st_uid != os.geteuid():
        raise ValueError(f'{label} has the wrong owner.')
    if info.st_mode & 0o077:
        raise ValueError(f'{label} must be private (mode 700 for directories, 600 for files).')
    return path


def photo_policy_for_operator(config_path):
    config = _private_operator_path(config_path, 'Photo configuration')
    parent = config.parent.lstat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError('Photo configuration directory must be privately owned.')
    data = json.loads(config.read_text())
    if not isinstance(data, dict):
        raise ValueError('Photo configuration must be an object.')
    configured_enabled = data.get('photos_enabled', True)
    if type(configured_enabled) is not bool:
        raise ValueError('Photo feature setting must be boolean.')
    configured_state = data.get('state_dir', str(Path.home()/'.local/share/hermes-mobile'))
    if not isinstance(configured_state, str) or not configured_state:
        raise ValueError('Private state path is invalid.')
    state_path = Path(configured_state).expanduser()
    if not state_path.is_absolute():
        state_path = Path.cwd() / state_path
    state = _private_operator_path(state_path, 'Private state directory', directory=True)
    settings = load_settings(config)
    if settings.state_dir != state or type(settings.photos_enabled) is not bool:
        raise ValueError('Photo configuration is malformed.')
    database = _private_operator_path(state/'runs.sqlite', 'Photo policy database')
    return PhotoPolicy(database)


def main():
    parser=argparse.ArgumentParser(description='Initialize private Hermes Mobile state; does not modify Hermes or Apache')
    parser.add_argument('command',choices=['init','owner-code','photo-status','photo-disable','photo-enable'])
    parser.add_argument('--state-dir',default=str(Path.home()/'.local/share/hermes-mobile'))
    parser.add_argument('--hermes-home',default=str(Path.home()/'.hermes'))
    parser.add_argument('--config')
    parser.add_argument('--confirm',action='store_true')
    args=parser.parse_args()
    if args.command.startswith('photo-'):
        if not args.config:
            parser.error('--config is required for photo policy commands')
        if args.command != 'photo-status' and not args.confirm:
            parser.error('--confirm is required for photo policy changes')
        if args.command == 'photo-status' and args.confirm:
            parser.error('--confirm is only used for photo policy changes')
        try:
            policy = photo_policy_for_operator(args.config)
            if args.command == 'photo-disable':
                policy.set_enabled(False)
            elif args.command == 'photo-enable':
                policy.set_enabled(True)
            print(json.dumps(policy.status(), sort_keys=True))
        except (OSError, ValueError) as exc:
            parser.exit(1, f'Photo policy operation failed: {exc}\n')
        return
    if args.command=='init':
        path,code=initialize(args.state_dir,args.hermes_home)
    else:
        path=Path(args.state_dir)/'config.json'
        code=issue_owner_code(path)
    print('Private configuration:',path)
    print('Owner bootstrap code (one use; expires in 15 minutes):',code)
    print('Enter at https://lindayi.me/hermes/ using the invitation form. Do not share it.')


if __name__=='__main__':
    main()
