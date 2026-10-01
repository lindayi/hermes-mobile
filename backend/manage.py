"""Private bootstrap, invoked by the owner at deployment, never by public signup."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from .auth import AuthService, digest
from .app import private_path
from .configuration import load_settings


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


def main():
    parser=argparse.ArgumentParser(description='Initialize private Hermes Mobile state; does not modify Hermes or Apache')
    parser.add_argument('command',choices=['init','owner-code'])
    parser.add_argument('--state-dir',default=str(Path.home()/'.local/share/hermes-mobile'))
    parser.add_argument('--hermes-home',default=str(Path.home()/'.hermes'))
    args=parser.parse_args()
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
