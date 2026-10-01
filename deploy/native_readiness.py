"""Fail-closed validators for dedicated native maintenance observations.

No service operations, native imports, gate writes or queue consumption.
A successful observation is not an atomic drain: caller must hold the owned
bridge gate and separately attest exclusive ingress and listener binding.
"""

from pathlib import Path
import os

WORK = ('pending_admissions', 'inflight_agent_calls', 'active_run_tasks', 'nonterminal_runs',
        'active_run_agents', 'shutdown_agents', 'stopping_runs', 'agent_workers',
        'cancellation_uncertain', 'live_subprocesses', 'active_delegations', 'delegation_executor_threads')


def require_native_readiness(health, *, expected_pid, expected_start_ticks, session_delete_version=1, notification_version=0):
    """Version is selected by source attestation, never inferred from missing work.

    True means observed idle and notifications durably retained, not drained.
    Version 0 is only for the explicitly attested previous native source set.
    """
    try:
        if (type(session_delete_version) is not int or session_delete_version not in (0, 1)
                or type(notification_version) is not int or notification_version not in (0, 1)):
            raise ValueError()
        required_work = WORK + (('session_deletion_workers',) if session_delete_version == 1 else ())
        required_work += ('notification_workers','notification_lifecycle_uncertain') if notification_version == 1 else ()
        evidence = health['native_maintenance']
        identities = (expected_pid, expected_start_ticks, health['pid'], evidence['pid'], evidence['start_ticks'])
        if (any(type(v) is not int or v <= 0 for v in identities)
                or health['pid'] != expected_pid or evidence['pid'] != expected_pid
                or evidence['start_ticks'] != expected_start_ticks
                or type(evidence['version']) is not int or evidence['version'] != 1
                or evidence['scope'] != 'dedicated-listener' or evidence['status'] != 'ok'):
            raise ValueError()
        work = evidence['work']
        notifications = evidence['notifications']
        if set(work) != set(required_work):
            raise ValueError()
        counts = [work[key] for key in required_work]
        backlog, retained, unpreserved = [notifications[key] for key in ('backlog', 'durable_retained', 'unpreserved')]
        if notification_version == 1:
            delivery = [notifications[key] for key in ('web_pending','quarantined','foreign_retained','web_delivered','shutdown_publications')]
            if (any(type(value) is not int or value < 0 for value in delivery)
                    or delivery[0] + delivery[1] > retained
                    or work['notification_lifecycle_uncertain'] != int(delivery[4] > 0)):
                raise ValueError()
        if (any(type(v) is not int or v < 0 for v in [*counts, backlog, retained, unpreserved])
                or backlog != retained + unpreserved or notifications['status'] != 'ok'
                or notifications['policy'] != 'retain-durable-no-drain-no-replay'):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Unknown or mismatched native maintenance evidence') from None
    return all(value == 0 for value in counts) and unpreserved == 0


def require_legacy_bootstrap(health, *, expected_pid, expected_start_ticks,
                             exclusive_ingress, gate_owner, expected_gate_owner,
                             local_nonterminal_runs, required_statuses,
                             implementation_attested, process, accepted_backlog=None):
    """Narrow trusted-ingress bootstrap, never a substitute for native v1.

    Caller must obtain each prerequisite afresh under its deployment lock.
    Opaque nonempty legacy queues cannot be proven preserved with this API.
    """
    terminal = {'completed', 'failed', 'cancelled'}
    try:
        if (exclusive_ingress is not True or implementation_attested is not True
                or not isinstance(gate_owner, str) or not gate_owner
                or gate_owner != expected_gate_owner
                or not isinstance(required_statuses, (list, tuple))):
            raise ValueError()
        identities = (expected_pid, expected_start_ticks, health['pid'], process['pid'], process['start_ticks'])
        if (any(type(v) is not int or v <= 0 for v in identities)
                or health['pid'] != expected_pid or process['pid'] != expected_pid
                or process['start_ticks'] != expected_start_ticks
                or 'native_maintenance' in health):
            raise ValueError()
        counts = health['readiness']['checks']['background_queues']
        backlog = counts['process_completions']
        work = [local_nonterminal_runs, counts['active_api_runs'], counts['active_delegations'],
                process['descendants'], process['connected_inet_sockets']]
        if (counts['status'] != 'ok' or any(type(v) is not int or v < 0 for v in [backlog, *work])
                or any(s not in terminal | {'queued', 'running', 'stopping', 'waiting_for_approval'}
                       for s in required_statuses)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Unknown or unauthorized legacy bootstrap evidence') from None
    if accepted_backlog is not None and (type(accepted_backlog) is not int or accepted_backlog < 0 or accepted_backlog != backlog):
        raise RuntimeError('Legacy notification consent does not match exact backlog')
    if backlog and accepted_backlog is None:
        raise RuntimeError('Legacy opaque completion backlog: preservation cannot be proven; restart blocked')
    return all(v == 0 for v in work) and all(s in terminal for s in required_statuses)


def load_legacy_notice_approval(path, *, backups_root=Path('/home/lindayi/.local/share/hermes-mobile-backups')):
    """Explicit one-process operator exception after verified saved-state backup."""
    import hashlib
    import json
    from backend.native_api_service import _private_file
    from deploy.assets import checked_path
    from deploy.backup import verify
    path = _private_file(checked_path(Path(path)))
    record = json.loads(path.read_bytes())
    fields = {'version', 'pid', 'start_ticks', 'backlog', 'consent', 'snapshot', 'manifest_sha256'}
    if (not isinstance(record, dict) or set(record) != fields
            or type(record['version']) is not int or record['version'] != 1
            or any(type(record[k]) is not int or record[k] <= 0 for k in ('pid', 'start_ticks'))
            or type(record['backlog']) is not int or record['backlog'] < 0
            or record['consent'] != 'accept-legacy-memory-only-notice-loss'
            or not isinstance(record['snapshot'], str)):
        raise RuntimeError('Invalid scoped legacy restart approval')
    snapshot = checked_path(Path(record['snapshot']))
    if snapshot.parent != backups_root or not snapshot.is_dir():
        raise RuntimeError('Restart approval must bind a private backup snapshot')
    verify(snapshot)
    if hashlib.sha256((snapshot / 'manifest.json').read_bytes()).hexdigest() != record['manifest_sha256']:
        raise RuntimeError('Approved backup manifest changed')
    return record


def observe_process(pid, *, proc_root=Path('/proc'), trusted_peer_pid=None, allow_closed_tcp=False):
    """Read-only OS supplement, sampled after probe HTTP sockets are closed.

    `descendants` is a lower bound (immediate children across every thread);
    any child blocks. Reparented/detached or remote work is not disproven.
    INET sockets include TCP and UDP; local asyncio/stdio UNIX socketpairs
    are not evidence of agent network work. This does not replace attestation.
    """
    try:
        if type(pid) is not int or pid <= 0:
            raise ValueError()
        proc = Path(proc_root) / str(pid)
        def start():
            return int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
        started = start()
        threads = list((proc / 'task').iterdir())
        if not threads:
            raise ValueError()
        children = {int(child) for thread in threads
                    for child in (thread / 'children').read_text().split()}
        sockets = {os.readlink(fd) for fd in (proc / 'fd').iterdir()}
        peer_sockets = set()
        if trusted_peer_pid is not None:
            if type(trusted_peer_pid) is not int or trusted_peer_pid <= 0 or trusted_peer_pid == pid:
                raise ValueError()
            peer_sockets = {os.readlink(fd) for fd in (Path(proc_root) / str(trusted_peer_pid) / 'fd').iterdir()}
        connected = set()
        for table in ('tcp', 'tcp6', 'udp', 'udp6'):
            rows = [line.split() for line in (proc / 'net' / table).read_text().splitlines()[1:]]
            for fields in rows:
                if 'socket:[' + fields[9] + ']' not in sockets:
                    continue
                if table.startswith('tcp') and fields[3] == '0A':
                    continue
                if allow_closed_tcp and table.startswith('tcp') and fields[3] == '08':
                    continue  # Peer FIN received; trusted exclusive-ingress bootstrap only.
                if (table == 'tcp' and fields[1] == '0100007F:48D2'
                        and fields[2].startswith('0100007F:') and any(
                            row[1] == fields[2] and row[2] == fields[1]
                            and 'socket:[' + row[9] + ']' in peer_sockets for row in rows)):
                    continue  # The attested bridge is gated; its HTTP pool is not active model work.
                connected.add(fields[9])
        if started <= 0 or start() != started:
            raise ValueError()
        return {'pid': pid, 'start_ticks': started, 'descendants': len(children),
                'connected_inet_sockets': len(connected)}
    except (OSError, IndexError, TypeError, ValueError):
        raise RuntimeError('Unknown native process observation') from None
