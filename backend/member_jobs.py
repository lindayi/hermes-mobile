"""Console-only FAMILY delivery preparation and real receipt verification.

No native modules may be imported here before member_scheduler isolation.
"""
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from contextlib import contextmanager

from . import profiles
from .member_runtime import private_file, atomic_config, _connect, ActivationError
from .runtime_binding import fingerprint

TARGET = 'mobile_delivery:member'


@contextmanager
def activation_lock(config_path):
    path = private_file(config_path)
    fd = os.open(str(path)+'.activation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path
    finally:
        os.close(fd)


def member_context(config_path, member_id, profile):
    config = json.loads(private_file(config_path).read_text())
    with _connect(private_file(Path(config['state_dir'])/'auth.sqlite')) as db:
        member = profiles.ProfileProvisioner._member(db, member_id)
        state = db.execute('SELECT value FROM settings WHERE key=?', ('provisioning:'+member_id,)).fetchone()
        proof = db.execute('SELECT value FROM settings WHERE key=?', ('runtime_activation:'+member_id,)).fetchone()
    if member['status'] != 'ready' or member['profile'] != profile or not state or state[0] != 'provisioned':
        raise ActivationError('Explicit activated, provisioned member required')
    home = profiles.PROFILE_ROOT/profile
    if not profiles.ProfileProvisioner._complete(home) or home.resolve() != home:
        raise ActivationError('Canonical member home required')
    for name in ('.env', 'config.yaml', 'SOUL.md'):
        private_file(home/name)
    if any(key != profile and Path(value).resolve() == home for key,value in config['profiles'].items()):
        raise ActivationError('Member home aliases another configured identity')
    gateway = config.get('gateway_profiles', {}).get(profile, {})
    if config['profiles'].get(profile) != str(home) or gateway.get('execution_ready') is not True:
        raise ActivationError('Activated member mapping required')
    record = json.loads(proof[0]) if proof else {}
    expected = fingerprint(member_id, profile, home, gateway.get('url', ''), gateway.get('token', ''))
    if record.get('version') != 1 or not hmac.compare_digest(str(record.get('binding', '')), expected):
        raise ActivationError('Member activation proof does not match runtime')
    return config, home


def prepare(config_path, member_id, profile):
    """Stage immutable private credentials, then atomically publish both maps.

    The app config replacement is the commit point. A crash before it leaves
    only an inert credential directory; rerun safely reuses it. No services,
    auth readiness, native config, or existing jobs are changed.
    """
    with activation_lock(config_path) as path:
        config, home = member_context(path, member_id, profile)
        directory = home/'.mobile-jobs'
        descriptor = {'member_id':member_id, 'profile':profile, 'hermes_home':str(home),
                      'bindings':{'member':'profile'}, 'token_file':'.mobile-jobs/token'}
        if not directory.exists():
            staging = Path(tempfile.mkdtemp(prefix='.mobile-jobs-', dir=home))
            try:
                atomic_config(staging/'runtime.json', descriptor)
                token = secrets.token_urlsafe(48)
                fd = os.open(staging/'token', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(token+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.rename(staging, directory)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        if json.loads(private_file(directory/'runtime.json').read_text()) != descriptor:
            raise ActivationError('Existing member delivery descriptor differs')
        token = private_file(directory/'token').read_text().strip()
        if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ActivationError('Invalid private delivery credential')
        for key, value in (('delivery_tokens', token), ('job_delivery_targets', TARGET)):
            mapping = config.setdefault(key, {})
            if profile in mapping and mapping[profile] != value:
                raise ActivationError('Existing delivery mapping differs; reconcile manually')
            mapping[profile] = value
        other_tokens = [value for key,value in config['delivery_tokens'].items() if key != profile]
        other_tokens += [config.get('upstream_token')]
        other_tokens += [entry.get('token') for entry in config.get('gateway_profiles', {}).values()]
        if token in other_tokens:
            raise ActivationError('Independent member delivery credential required')
        atomic_config(path, config)
    return {'profile':profile, 'prepared':True, 'service_started':False}


def delivery_binding(config, member_id, profile, home):
    import hashlib
    gateway = config['gateway_profiles'][profile]
    runtime = fingerprint(member_id,profile,home,gateway['url'],gateway['token'])
    return hashlib.sha256(json.dumps([runtime, config['delivery_tokens'][profile], TARGET]).encode()).hexdigest()


def delivery_settings(config_path, member_id, profile, *, require_verified=True):
    config, home = member_context(config_path, member_id, profile)
    descriptor = json.loads(private_file(home/'.mobile-jobs/runtime.json').read_text())
    expected = {'member_id':member_id, 'profile':profile, 'hermes_home':str(home),
                'bindings':{'member':'profile'}, 'token_file':'.mobile-jobs/token'}
    token = private_file(home/'.mobile-jobs/token').read_text().strip()
    if (descriptor != expected or len(token) < 32
            or config.get('delivery_tokens', {}).get(profile) != token
            or config.get('job_delivery_targets', {}).get(profile) != TARGET):
        raise ActivationError('Member delivery configuration does not match prepared credentials')
    if token in [value for key,value in config['delivery_tokens'].items() if key != profile]:
        raise ActivationError('Shared delivery credential refused')
    if require_verified:
        proof = json.loads(private_file(home/'.mobile-jobs/verified.json').read_text())
        if proof.get('version') != 1 or proof.get('binding') != delivery_binding(config,member_id,profile,home):
            raise ActivationError('Current member delivery has not been verified')
        with _connect(private_file(Path(config['state_dir'])/'notifications.sqlite')) as db:
            receipt = db.execute('SELECT 1 FROM inbox WHERE id=? AND user_id=? AND delivery_id=?',
                                 (proof['receipt_id'],member_id,proof['job_id']+':'+proof['run_id'])).fetchone()
        if not receipt:
            raise ActivationError('Verified member inbox receipt missing')
    return config, home, descriptor


def verify(config_path, member_id, profile, *, timeout=60, transport=None, attempt=None):
    """One future script-only job, real native execution and durable inbox proof.

    transport is an HTTP boundary injection for isolated tests only. There is
    no fake-success path. Existing jobs are never dispatched by this mode.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone
    import threading
    import time
    import httpx
    from .member_scheduler import load_native, scheduler_lock, guarded_tick
    if type(timeout) is not int or not 10 <= timeout <= 120:
        raise ActivationError('Verification timeout must be 10..120 seconds')
    config, home, descriptor = delivery_settings(config_path,member_id,profile,require_verified=False)
    binding = delivery_binding(config,member_id,profile,home)
    deadline = time.monotonic()+timeout
    cancel = threading.Event()
    timer = threading.Timer(timeout, cancel.set)
    timer.daemon = True
    def authorized():
        current, _, _ = delivery_settings(config_path,member_id,profile,require_verified=False)
        return not cancel.is_set() and delivery_binding(current,member_id,profile,home) == binding
    with scheduler_lock(home):
        timer.start()
        sdk, adapter = load_native(home,descriptor)
        from cron.jobs import create_job, get_job, remove_job
        scripts = home/'scripts'
        scripts.mkdir(mode=0o700,exist_ok=True)
        if scripts.resolve() != scripts:
            raise ActivationError('Unsafe member scripts directory')
        challenge = 'FAMILY_DELIVERY_'+secrets.token_hex(24)
        import re
        attempt = attempt or secrets.token_hex(16)
        if not re.fullmatch(r'[a-f0-9]{32}',attempt):
            raise ActivationError('Invalid verification attempt')
        script = scripts/('family_verify_'+attempt+'.py')
        job = None
        try:
            # Exclusive creation and exact content; no model or arbitrary tools.
            fd = os.open(script,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                stream.write('print('+repr(challenge)+')\n')
            asyncio.run(adapter.connect())
            job = create_job(prompt=None, schedule=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat(),
                             name='FAMILY delivery verification', repeat=1, deliver=TARGET,
                             script=script.name, no_agent=True)
            # Authenticate against the already-running BFF's loaded token and
            # runtime/job ownership maps. Silent probe stores no notification.
            with httpx.Client(timeout=5,trust_env=False,follow_redirects=False,transport=transport) as client:
                response = client.post('http://127.0.0.1:9120/hermes/app-api/internal/deliver',
                    headers={'Authorization':'Bearer '+adapter.token,'Origin':'https://lindayi.me'},
                    json={'profile':profile,'job_id':job['id'],'run_id':'preflight','body':''})
                if response.status_code != 200 or response.json() != {'silent':True}:
                    raise ActivationError('Reload and verify the mobile backend mapping before scheduling')
            inbox = private_file(Path(config['state_dir'])/'notifications.sqlite')
            while time.monotonic() < deadline and authorized():
                guarded_tick(sdk,adapter,authorized,only_job=job['id'],cancel_event=cancel)
                with _connect(inbox) as db:
                    rows = db.execute('SELECT id,delivery_id,body FROM inbox WHERE user_id=? AND delivery_id LIKE ?',
                                      (member_id,job['id']+':%')).fetchall()
                matches = [row for row in rows if row['body'].strip() == challenge]
                if len(matches) == 1:
                    receipt = matches[0]
                    run_id = receipt['delivery_id'][len(job['id'])+1:]
                    # Publish a credential-bound verification marker only after
                    # receipt and revalidation; never auth readiness or services.
                    with activation_lock(config_path):
                        if not authorized():
                            raise ActivationError('Member configuration changed during verification')
                        atomic_config(home/'.mobile-jobs/verified.json',
                            {'version':1,'binding':binding,'receipt_id':receipt['id'],'job_id':job['id'],'run_id':run_id})
                    return {'profile':profile,'verified':True,'receipt_id':receipt['id'],'job_id':job['id'],'run_id':run_id}
                time.sleep(min(0.2,max(0,deadline-time.monotonic())))
            raise ActivationError('Native member inbox receipt was not confirmed within the deadline')
        finally:
            cancel.set()
            timer.cancel()
            try:
                if job is not None:
                    current = get_job(job['id'])
                    if current is not None and current.get('script') == script.name and current.get('no_agent') is True:
                        remove_job(job['id'])
            finally:
                script.unlink(missing_ok=True)
                asyncio.run(adapter.disconnect())


@contextmanager
def quiet_native():
    """CLI output is allowlisted; native/provider diagnostics may hold secrets."""
    import contextlib
    import logging
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            yield
    finally:
        logging.disable(previous)


def run_bounded(command, timeout, env):
    """Watchdog for the native verify worker; kill and reap its whole group."""
    import signal
    import subprocess
    process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               text=True,env=env,start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=timeout)
        if process.returncode != 0:
            raise ActivationError('Verification worker failed')
        return json.loads(stdout)
    except BaseException:
        try:
            os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise


def cleanup_attempt(config_path, member_id, profile, attempt):
    """Fresh-process crash cleanup; exact parent-owned nonce, never all jobs."""
    import re
    from .member_scheduler import load_native, scheduler_lock
    if not re.fullmatch(r'[a-f0-9]{32}',attempt):
        raise ActivationError('Invalid verification attempt')
    _, home, descriptor = delivery_settings(config_path,member_id,profile,require_verified=False)
    with scheduler_lock(home):
        sdk, _ = load_native(home,descriptor)
        from cron.jobs import remove_job
        name = 'family_verify_'+attempt+'.py'
        for job in sdk.load_jobs():
            if job.get('script') == name and job.get('no_agent') is True and job.get('name') == 'FAMILY delivery verification':
                remove_job(job['id'])
        script = home/'scripts'/name
        if script.exists() or script.is_symlink():
            private_file(script).unlink()
    return {'cleaned':True}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('prepare','verify'):
        command = sub.add_parser(name)
        command.add_argument('--config',required=True,type=Path)
        command.add_argument('--member-id',required=True)
        command.add_argument('--profile',required=True,help='Explicit existing canonical member profile')
        if name == 'verify': command.add_argument('--timeout',type=int,default=60,help='Receipt deadline in seconds (10..120)')
    args = parser.parse_args(argv)
    try:
        with quiet_native():
            if args.command == 'prepare':
                result = prepare(args.config,args.member_id,args.profile)
            else:
                import sys
                if not 10 <= args.timeout <= 120:
                    raise ActivationError('Verification timeout must be 10..120 seconds')
                attempt = os.environ.get('HERMES_FAMILY_VERIFY_ATTEMPT')
                if attempt:
                    if os.environ.get('HERMES_FAMILY_VERIFY_CLEANUP') == '1':
                        result = cleanup_attempt(args.config,args.member_id,args.profile,attempt)
                    else:
                        result = verify(args.config,args.member_id,args.profile,timeout=args.timeout,attempt=attempt)
                else:
                    delivery_settings(args.config,args.member_id,args.profile,require_verified=False)
                    env = {**os.environ,'HERMES_FAMILY_VERIFY_ATTEMPT':secrets.token_hex(16)}
                    env.pop('HERMES_FAMILY_VERIFY_CLEANUP',None)
                    command = [sys.executable,'-m','backend.member_jobs','verify','--config',str(args.config),
                               '--member-id',args.member_id,'--profile',args.profile,'--timeout',str(args.timeout)]
                    try:
                        result = run_bounded(command,args.timeout+5,env)
                    finally:
                        run_bounded(command,10,{**env,'HERMES_FAMILY_VERIFY_CLEANUP':'1'})
    except Exception:
        parser.exit(1,'Family jobs failed closed. Check private configuration, activation, loaded backend mapping and member runtime; no credentials printed.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
