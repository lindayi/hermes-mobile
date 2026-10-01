"""Console-only FAMILY activation. Never creates profiles, copies auth, or starts services."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from urllib.parse import urlparse

import yaml
import httpx

from . import profiles
from .runtime_binding import activation_record


class ActivationError(RuntimeError):
    """Safe operator-facing failure (never includes upstream response bodies)."""


def private_file(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or not path.is_file()
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
            or path.stat().st_nlink != 1):
        raise ActivationError('Expected an owned, private, regular non-symlink file')
    return path


def atomic_config(path, data):
    fd, name = tempfile.mkstemp(prefix='.activation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _member(db, member_id):
    member = profiles.ProfileProvisioner._member(db, member_id)
    if member['status'] != 'pending':
        raise ActivationError('A pending member is required')
    row = db.execute('SELECT value FROM settings WHERE key=?', ('provisioning:'+member_id,)).fetchone()
    if not row or row[0] != 'provisioned':
        raise ActivationError('A successfully provisioned profile is required')
    return member


@contextmanager
def _connect(path, mode='ro'):
    # URI mode=rw/ro never creates missing DBs or migrates a live auth store.
    db = sqlite3.connect(Path(path).as_uri()+'?mode='+mode, uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _proof(home, owner, sid, title, answer=None):
    with _connect(private_file(home/'state.db')) as db:
        row = db.execute('SELECT title FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row or row[0] != title:
            raise ActivationError('Native session was not persisted in this member database')
        if answer is not None:
            rows = db.execute("SELECT content FROM messages WHERE session_id=? AND role='assistant'", (sid,)).fetchall()
            if not any(row[0].strip() == answer for row in rows):
                raise ActivationError('Model response was not persisted in this member database')
    with _connect(private_file(owner/'state.db')) as db:
        if db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone():
            raise ActivationError('Native runtime wrote the challenge into the owner database')


def activate(config_path, member_id, runtime_path, *, transport=None):
    """Verify a running sidecar, then publish config before the auth ready bit.

    A crash after config publication leaves the user pending (safe); an operator
    can rerun verification. No slow network call holds the auth write lock.
    """
    config_path = private_file(config_path)
    runtime_path = private_file(runtime_path)
    # Separate stable lock inode: replacing config must not drop serialization.
    lock_fd = os.open(str(config_path)+'.activation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = config_path.read_bytes()
        config = json.loads(original)
        runtime = json.loads(runtime_path.read_text())
        auth_path = private_file(Path(config['state_dir'])/'auth.sqlite')
        with _connect(auth_path) as db:
            member = _member(db, member_id)
        profile = member['profile']
        home = profiles.PROFILE_ROOT/profile
        if not profiles.ProfileProvisioner._complete(home):
            raise ActivationError('Canonical member profile is missing or unsafe')
        for name in ('.env', 'config.yaml'):
            private_file(home/name)
        member_config = yaml.safe_load((home/'config.yaml').read_text()) or {}
        model = member_config.get('model') or {}
        if (not isinstance(model, dict) or not isinstance(model.get('provider'), str)
                or model['provider'].strip() in ('', 'auto')
                or not isinstance(model.get('default'), str) or not model['default'].strip()):
            raise ActivationError('Explicit member provider and model configuration required')
        if set(runtime) != {'hermes_home', 'port', 'upstream_token'}:
            raise ActivationError('Unexpected runtime configuration; only a dedicated loopback listener is supported')
        owner = Path(config['profiles']['default'])
        private_file(owner/'state.db')
        if runtime.get('hermes_home') != str(home):
            raise ActivationError('Listener home does not match generated member profile')
        port = runtime.get('port')
        token = runtime.get('upstream_token')
        if type(port) is not int or not 1024 <= port <= 65535 or port == 18642:
            raise ActivationError('A dedicated unprivileged member port is required')
        if not isinstance(token, str) or len(token) < 32 or token == config.get('upstream_token'):
            raise ActivationError('A separate private member listener token is required')
        url = f'http://127.0.0.1:{port}'
        entry = {'url':url, 'token':token, 'execution_ready':True}
        if (config['profiles'].get(profile, str(home)) != str(home)
                or config.get('gateway_profiles', {}).get(profile, entry) != entry):
            raise ActivationError('Existing member mapping differs; reconcile before activation')
        if any(profile in config.get(key, {}) for key in ('delivery_tokens', 'job_delivery_targets')):
            raise ActivationError('Unverified member scheduler/delivery mapping must be reconciled first')
        other_gateways = [entry for key, entry in config.get('gateway_profiles', {}).items() if key != profile]
        other_gateways.append({'url':config.get('upstream_url', 'http://127.0.0.1:8642'), 'token':config.get('upstream_token')})
        if any(urlparse(entry['url']).port == port or entry.get('token') == token for entry in other_gateways):
            raise ActivationError('Member listener endpoint/token is already used by another runtime')
        if any(key != profile and Path(value).resolve() == home for key, value in config['profiles'].items()):
            raise ActivationError('Member home is already mapped to another identity')
        with httpx.Client(base_url=url, headers={'Authorization':'Bearer '+token},
                          timeout=httpx.Timeout(15, read=180), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            def request(method, path, **kwargs):
                try:
                    response = client.request(method, path, **kwargs)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ActivationError('Native runtime verification failed; member remains pending') from exc
            caps = request('GET', '/v1/capabilities')
            required = ('run_submission','run_events_sse','run_stop','run_approval_request_id','session_resources','session_chat')
            if (caps.get('object') != 'hermes.api_server.capabilities'
                    or caps.get('auth', {}).get('required') is not True
                    or any(caps.get('features', {}).get(key) is not True for key in required)
                    or caps.get('mobile_runtime') != {'home':str(home), 'credential_policy':'member-only-v1', 'smoke_policy':'reserved-no-tools-v1'}):
                raise ActivationError('Isolated native capability or tool-free smoke policy missing')
            sid = 'mobile_activation_'+secrets.token_hex(20)
            title = 'FAMILY activation '+secrets.token_hex(20)
            request('POST', '/api/sessions', json={'id':sid, 'title':title})
            _proof(home, owner, sid, title)
            answer = 'FAMILY_OK_'+secrets.token_hex(16)
            result = request('POST', '/api/sessions/'+sid+'/chat',
                             json={'message':'Do not use tools. Reply with exactly this text: '+answer})
            if (result.get('session_id') != sid
                    or result.get('message', {}).get('content', '').strip() != answer):
                raise ActivationError('Real model smoke turn did not return the challenge')
            _proof(home, owner, sid, title, answer)
        with _connect(auth_path, 'rw') as db:
            db.execute('BEGIN IMMEDIATE')
            _member(db, member_id)  # A concurrent revocation always wins.
            if config_path.read_bytes() != original:
                raise ActivationError('Configuration changed during verification; retry')
            config.setdefault('profiles', {})[profile] = str(home)
            config.setdefault('gateway_profiles', {})[profile] = {'url':url, 'token':token, 'execution_ready':True}
            atomic_config(config_path, config)
            db.execute("UPDATE users SET status='ready' WHERE id=? AND status='pending'", (member_id,))
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('runtime_activation:'+member_id, activation_record(member_id, profile, home, url, token, sid)))
        return {'id':member_id, 'profile':profile, 'status':'ready', 'smoke_session':sid, 'jobs_ready':False}
    finally:
        os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--member-id', required=True)
    parser.add_argument('--runtime-config', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = activate(args.config, args.member_id, args.runtime_config)
    except Exception:
        parser.exit(1, 'Activation failed closed. Check member status, private paths, listener/provider setup and logs; no credentials were printed.\n')
    print(json.dumps(result))
    print('Reload/restart only the mobile backend to load the new mapping. Member scheduling/delivery is NOT enabled.')


if __name__ == '__main__':
    main()
