"""FAMILY scheduler only: one native ticker, one outbound mobile adapter.

Importing this module does not import any native SDK/auth modules.
"""
import asyncio
from contextlib import contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import re
import sys

from .native_api_service import member_environment
from .member_runtime import private_file


@contextmanager
def scheduler_lock(home):
    directory = home/'cron'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.resolve() != directory:
        raise ActivationError('Unsafe member cron directory')
    fd = os.open(directory/'.family-scheduler.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def audit_store(home):
    """Reject unsafe/corrupt native store before its loader can auto-repair it."""
    import json
    for name in ('cron','cron/output','scripts','.mobile-jobs'):
        path = home/name
        if (path.exists() or path.is_symlink()) and (path.resolve() != path or not path.is_dir()):
            raise ActivationError('Unsafe member scheduler directory')
    for name in ('cron/jobs.json','cron/.tick.lock','cron/executions.db'):
        path = home/name
        if path.exists() or path.is_symlink():
            private_file(path)
    path = home/'cron/jobs.json'
    if path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):
            raise ActivationError('Invalid member jobs store')
        for job in data['jobs']:
            validate_job(job)


def load_native(home, descriptor):
    """Dedicated fresh-process bootstrap; never reuse owner SDK/auth caches."""
    if any(name == 'cron' or name.startswith(('cron.', 'gateway.', 'agent.', 'hermes_cli.')) for name in sys.modules):
        raise ActivationError('Fresh scheduler process required before native imports')
    for name in ('.env','config.yaml','auth.json','state.db'):
        if (home/name).exists() or (home/name).is_symlink():
            private_file(home/name)
    audit_store(home)
    os.environ.clear()
    os.environ.update(member_environment(home))
    os.umask(0o077)
    os.chdir(home)
    sys.path.insert(0, '/usr/local/lib/hermes-agent')
    import hermes_constants
    hermes_constants.get_default_hermes_root = lambda: home
    from dotenv import load_dotenv
    load_dotenv(home/'.env', override=False)
    os.environ.update(member_environment(home))
    os.environ['HERMES_CRON_MAX_PARALLEL'] = '1'
    from cron import scheduler
    from cron.jobs import load_jobs
    # tick's namespace does not necessarily export load_jobs on every SDK.
    scheduler.load_jobs = load_jobs
    from gateway.config import PlatformConfig
    from gateway.platform_registry import platform_registry, PlatformEntry
    plugin_path = Path(__file__).resolve().parents[1]/'hermes-plugin/mobile_delivery/__init__.py'
    spec = importlib.util.spec_from_file_location('family_mobile_delivery', plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    platform_registry.register(PlatformEntry(name='mobile_delivery', label='Mobile inbox',
        adapter_factory=plugin.MobileDeliveryAdapter, check_fn=lambda:True))
    adapter = plugin.MobileDeliveryAdapter(PlatformConfig(enabled=True, extra=descriptor))
    return scheduler, adapter

from .member_jobs import TARGET
from .member_runtime import ActivationError


def validate_job(job):
    if (not isinstance(job, dict) or not re.fullmatch(r'[a-f0-9]{12}', str(job.get('id','')))
            or job.get('deliver') != TARGET):
        raise ActivationError('Family jobs require the explicit mobile member destination')


def guarded_tick(sdk, adapter, authorized, *, only_job=None, cancel_event=None):
    """Keep native tick lock, CAS/fire claims, heartbeat and execution ledger.

    Override ONLY this process's dispatch/delivery boundary, restoring it after
    synchronous completion. No SDK default router/standalone fallback is used.
    """
    if isinstance(getattr(adapter,'home',None),Path):
        audit_store(adapter.home)
    for job in sdk.load_jobs():
        validate_job(job)
    native_run = sdk.run_one_job
    native_deliver = sdk._deliver_result
    native_due = getattr(sdk, 'get_due_jobs', None)
    if only_job is not None:
        def only_due():
            # Verification must not claim/advance any pre-existing job. Native
            # tick still owns its lock, execution ledger and CAS fire claim.
            from datetime import datetime, timezone
            jobs = [j for j in sdk.load_jobs() if j['id'] == only_job]
            return [j for j in jobs if j.get('enabled', True) and j.get('state') != 'completed'
                    and j.get('next_run_at') and datetime.fromisoformat(j['next_run_at']) <= datetime.now(timezone.utc)]
        sdk.get_due_jobs = only_due

    def run(job, **kwargs):
        validate_job(job)  # exact CAS-claimed version, not the earlier snapshot
        if not authorized() or (only_job is not None and job['id'] != only_job):
            raise ActivationError('Member dispatch not authorized')
        if not isinstance(job.get('execution_id'), str) or not job['execution_id']:
            raise ActivationError('Native execution identity required')
        if cancel_event is not None:
            kwargs['cancel_event'] = cancel_event
        return native_run(job, **kwargs)

    def deliver(job, content, adapters=None, loop=None):
        validate_job(job)
        if not authorized() or not job.get('execution_id'):
            return 'Member delivery not authorized'
        # Deliberately bypass native platform discovery, default targets and
        # standalone fallback. Native execution identity is passed unchanged.
        result = asyncio.run(adapter.send('member', content, metadata={
            'job_id':job['id'], 'run_id':job['execution_id']}))
        return None if result.success else 'Member mobile receipt unconfirmed'

    sdk.run_one_job, sdk._deliver_result = run, deliver
    try:
        return sdk.tick(verbose=False, adapters={'mobile_delivery':adapter},
                        sync=True, can_dispatch=authorized)
    finally:
        sdk.run_one_job, sdk._deliver_result = native_run, native_deliver
        if only_job is not None:
            sdk.get_due_jobs = native_due


def serve(config_path, member_id, profile, *, stopped=None):
    import threading
    from .member_jobs import delivery_settings, delivery_binding
    config, home, descriptor = delivery_settings(config_path,member_id,profile)
    binding = delivery_binding(config,member_id,profile,home)
    stopped = stopped or threading.Event()
    def authorized():
        current, _, _ = delivery_settings(config_path,member_id,profile)
        return not stopped.is_set() and delivery_binding(current,member_id,profile,home) == binding
    with scheduler_lock(home):
        sdk, adapter = load_native(home,descriptor)
        try:
            if not asyncio.run(adapter.connect()):
                raise ActivationError('Member mobile adapter failed to connect')
            while not stopped.is_set():
                if not authorized():
                    raise ActivationError('Member scheduler authorization changed')
                guarded_tick(sdk,adapter,authorized,cancel_event=stopped)
                stopped.wait(30)
        finally:
            asyncio.run(adapter.disconnect())


def main(argv=None):
    import argparse
    import signal
    import threading
    from .member_jobs import quiet_native
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--member-id',required=True)
    parser.add_argument('--profile',required=True)
    args = parser.parse_args(argv)
    stopped = threading.Event()
    previous = {sig:signal.signal(sig,lambda *args:stopped.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        with quiet_native():
            serve(args.config,args.member_id,args.profile,stopped=stopped)
    except Exception:
        parser.exit(1,'Family scheduler stopped closed. Check activation, verified delivery, member job destinations and exclusive scheduler ownership; no credentials printed.\n')
    finally:
        for sig, handler in previous.items(): signal.signal(sig,handler)


if __name__ == '__main__':
    main()
