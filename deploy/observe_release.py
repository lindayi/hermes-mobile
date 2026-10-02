"""Read-only bounded release observer; optional existing owner Inbox receipt.

Run with python -m deploy.observe_release. Expected JSON maps stage-relative
core file names to SHA256 hex digests. No observer starts automatically.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from contextlib import contextmanager

from deploy.self_deploy import Paths, verify_release


CONFIG = Path('/home/lindayi/.local/share/hermes-mobile-live/config.json')


DEFAULT_TIMEOUT = 2100
MAX_TIMEOUT = 86400

DIAGNOSTIC_PHASES = {
    'observer_setup', 'observer', 'worker_status', 'deployment_status',
    'release_proofs', 'release_checks', 'native_health', 'status_recheck',
}
DIAGNOSTIC_REASONS = {
    'starting', 'waiting', 'checking', 'deployment_failed',
    'verification_failed', 'verified', 'terminal_status_missing', 'timeout',
}


def set_diagnostic(diagnostics, phase, reason):
    if diagnostics is not None:
        diagnostics.update(
            phase=phase if phase in DIAGNOSTIC_PHASES else 'observer',
            reason=reason if reason in DIAGNOSTIC_REASONS else 'verification_failed')


def validate_timeout(timeout):
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f'timeout must be whole seconds in 1..{MAX_TIMEOUT}')
    return timeout


def observe(paths, since_ns, expected, *, clock=time.monotonic, sleep=time.sleep,
            verify=verify_release, timeout=DEFAULT_TIMEOUT, worker_state=None,
            diagnostics=None):
    timeout = validate_timeout(timeout)
    deadline = clock() + timeout
    seen_worker = False
    while clock() < deadline:
        result = classify(paths, since_ns, expected, verify=verify, diagnostics=diagnostics)
        if result is None and worker_state is not None:
            state = worker_state()
            seen_worker = seen_worker or state == 'active'
            if state == 'failed' or (seen_worker and state in ('inactive', 'missing')):
                # The worker may publish its final status between our two reads.
                result = classify(paths, since_ns, expected, verify=verify,
                                  diagnostics=diagnostics)
                if result is None:
                    set_diagnostic(diagnostics, 'worker_status', 'terminal_status_missing')
                    result = 'verification_failed'
        if clock() >= deadline:
            break
        if result is not None:
            return result
        sleep(min(2, deadline - clock()))
    set_diagnostic(diagnostics, 'observer', 'timeout')
    return 'timeout'


def private_existing(path, *, directory=False):
    path = Path(path).absolute()
    info = path.stat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (path.resolve(strict=True) != path or not kind(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValueError('Existing canonical owned private path required')
    return path


def safe_unit(unit):
    if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', unit):
        raise ValueError('Invalid unit')
    return unit


def read_worker_state(unit, *, run=subprocess.run):
    """Read lifecycle only; Result=success is also a missing unit's default."""
    unit = safe_unit(unit)
    service = unit if unit.endswith('.service') else unit + '.service'
    try:
        result = run(['systemctl', '--user', 'show', service,
                      '--property=LoadState', '--property=ActiveState'],
                     check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    load, active = properties.get('LoadState'), properties.get('ActiveState')
    if load == 'not-found' and active == 'inactive':
        return 'missing'
    if result.returncode != 0 or load != 'loaded':
        return None
    if active in ('active', 'activating', 'deactivating'):
        return 'active'
    if active in ('failed', 'inactive'):
        return active
    return None


def notify_owner(unit, outcome, *, config_path=CONFIG):
    """Use existing Inbox only: no constructors/migrations, push, or sessions."""
    from backend.configuration import load_settings
    from backend.notifications import NotificationService
    safe_unit(unit)
    messages = {'succeeded': 'Release completed and verification passed.',
                'failed': 'The release did not complete successfully.',
                'verification_failed': 'The release could not be verified. The update may already be running. Check deployment status before retrying.',
                'timeout': 'Release observation timed out. Operator review is required.'}
    body = messages[outcome]
    config_path = private_existing(config_path)
    # Validate the original spelling before load_settings resolves symlinks.
    configured_state = json.loads(config_path.read_text()).get('state_dir')
    if not isinstance(configured_state, str):
        raise ValueError('Explicit existing state directory required')
    private_existing(configured_state, directory=True)
    settings = load_settings(config_path)
    state = private_existing(settings.state_dir, directory=True)
    auth = private_existing(state / 'auth.sqlite')
    inbox = private_existing(state / 'notifications.sqlite')
    with closing(sqlite3.connect(auth.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        owners = db.execute("SELECT id FROM users WHERE role='owner' AND status='ready' AND profile='default'").fetchall()
    if len(owners) != 1:
        raise ValueError('Exactly one ready default owner required')

    class ExistingInbox(NotificationService):
        # Parent constructor creates DB/schema and changes modes: do not call it.
        def __init__(self):
            self.clock = time.time

        @contextmanager
        def _db(self):
            private_existing(inbox)
            with closing(sqlite3.connect(inbox.as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                with db:
                    yield db

    delivery_id = 'release-observer:' + hashlib.sha256(unit.encode()).hexdigest()
    return ExistingInbox().ingest(owners[0][0], delivery_id, 'Release operation report',
                                  body, silent=True)['id']


def classify(paths, since_ns, expected, *, verify=verify_release, diagnostics=None):
    """Return None (pending), failed, verification_failed, or verified succeeded."""
    status_path = paths.state / 'status.json'
    try:
        with status_path.open() as handle:
            stamp = os.fstat(handle.fileno()).st_mtime_ns
            if stamp < since_ns:
                return None
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get('status') in ('failed', 'rolled_back', 'rollback_failed'):
        set_diagnostic(diagnostics, 'deployment_status', 'deployment_failed')
        return 'failed'
    if record.get('status') != 'succeeded':
        return None
    try:
        set_diagnostic(diagnostics, 'release_proofs', 'checking')
        release = record.get('release')
        if not isinstance(release, str) or not re.fullmatch('[0-9a-f]{32}', release):
            set_diagnostic(diagnostics, 'release_proofs', 'verification_failed')
            return 'verification_failed'
        stage = paths.state / 'releases' / release
        if not stage.is_dir() or stage.resolve(strict=True) != stage.absolute():
            set_diagnostic(diagnostics, 'release_proofs', 'verification_failed')
            return 'verification_failed'
        def proofs():
            current = paths.state / 'current'
            if not current.is_symlink() or current.resolve(strict=True) != stage:
                raise ValueError('Release mismatch')
            if not isinstance(expected, dict) or not expected:
                raise ValueError('Expected hashes required')
            for name, digest in expected.items():
                relative = Path(name)
                if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                        or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
                    raise ValueError('Invalid expected hash')
                file = stage / relative
                if file.resolve(strict=True) != file or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                    raise ValueError('Hash mismatch')
            with closing(sqlite3.connect(paths.database.absolute().as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('SELECT 1 FROM deployment_gate LIMIT 1').fetchone():
                    raise ValueError('Deployment gate is closed')
        proofs()
        set_diagnostic(diagnostics, 'release_checks', 'checking')
        verify(paths, stage, True)
        set_diagnostic(diagnostics, 'release_proofs', 'checking')
        proofs()
        if status_path.stat().st_mtime_ns != stamp or json.loads(status_path.read_text()) != record:
            set_diagnostic(diagnostics, 'status_recheck', 'verification_failed')
            return 'verification_failed'
        set_diagnostic(diagnostics, 'status_recheck', 'verified')
        return 'succeeded'
    except Exception:
        # Receipts never include exception strings (which may contain secrets).
        phase = diagnostics.get('phase', 'release_checks') if diagnostics is not None else 'release_checks'
        set_diagnostic(diagnostics, phase, 'verification_failed')
        return 'verification_failed'


class ObservationTimeout(BaseException):
    """Deadline must escape verifier retry/exception handlers."""


def main(argv=None, *, paths=None):
    import argparse
    import signal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since-ns', type=int, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--unit', type=safe_unit, required=True)
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                        help=f'Absolute observation budget in seconds (default {DEFAULT_TIMEOUT})')
    parser.add_argument('--watch-worker', action='store_true',
                        help='Also detect terminal worker failure via read-only systemd state')
    parser.add_argument('--native', action='store_true',
                        help='Independently verify dedicated native listener binding/readiness')
    parser.add_argument('--notify-owner', action='store_true',
                        help='Write a silent operation report to the existing owner Inbox')
    args = parser.parse_args(argv)
    if args.since_ns < 0:
        parser.error('--since-ns must be nonnegative')
    try:
        validate_timeout(args.timeout)
    except ValueError as error:
        parser.error(str(error))
    diagnostics = {'phase': 'observer_setup', 'reason': 'starting'}
    receipt = {'unit': args.unit, 'status': 'verification_failed', 'notification': 'disabled'}
    def expired(signum, frame):
        raise ObservationTimeout()
    previous_handler = signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.timeout)
    try:
        expected = json.loads(args.expected.read_text())
        worker_state = (lambda: read_worker_state(args.unit)) if args.watch_worker else None
        def verify_candidate(p, stage, backend):
            set_diagnostic(diagnostics, 'release_checks', 'checking')
            verify_release(p, stage, backend)
            if args.native:
                set_diagnostic(diagnostics, 'native_health', 'checking')
                from deploy.native_controls_release import NativeProbe
                NativeProbe(p.source).verify_operational(stage)
        receipt['status'] = observe(paths or Paths(), args.since_ns, expected,
                                    timeout=args.timeout, worker_state=worker_state,
                                    diagnostics=diagnostics,
                                    **({'verify': verify_candidate} if args.native else {}))
    except ObservationTimeout:
        receipt['status'] = 'timeout'
        set_diagnostic(diagnostics, 'observer', 'timeout')
    except Exception:
        receipt['status'] = 'verification_failed'
        set_diagnostic(diagnostics, 'observer_setup', 'verification_failed')
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    receipt.update(diagnostics)
    if args.notify_owner:
        try:
            receipt['inbox_id'] = notify_owner(args.unit, receipt['status'])
            receipt['notification'] = 'recorded'
        except Exception:
            receipt['notification'] = 'failed'
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['status'] == 'succeeded' and receipt['notification'] != 'failed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
