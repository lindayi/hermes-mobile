"""Deployment-side contracts: no native imports and no service/DB mutations."""
import copy
import importlib.util

import pytest


WORK = ('pending_admissions', 'inflight_agent_calls', 'active_run_tasks', 'nonterminal_runs',
        'active_run_agents', 'shutdown_agents', 'stopping_runs', 'agent_workers',
        'cancellation_uncertain', 'live_subprocesses', 'active_delegations', 'delegation_executor_threads')


def module():
    assert importlib.util.find_spec('deploy.native_readiness'), 'readiness module missing'
    from deploy import native_readiness
    return native_readiness


def evidence():
    return {'pid': 123, 'gateway_busy': True, 'native_maintenance': {
        'version': 1, 'scope': 'dedicated-listener', 'pid': 123, 'start_ticks': 456,
        'status': 'ok', 'work': {key: 0 for key in WORK},
        'notifications': {'status': 'ok', 'backlog': 78, 'durable_retained': 78, 'unpreserved': 0,
                          'policy': 'retain-durable-no-drain-no-replay'}}}


def test_positive_native_readiness_is_not_notification_backlog_or_shared_busy():
    health = evidence()
    before = copy.deepcopy(health)
    assert module().require_native_readiness(health, expected_pid=123, expected_start_ticks=456, session_delete_version=0)
    assert health == before


@pytest.mark.parametrize('key', WORK)
def test_every_live_work_category_blocks_restart(key):
    health = evidence()
    health['native_maintenance']['work'][key] = 1
    assert module().require_native_readiness(health, expected_pid=123, expected_start_ticks=456, session_delete_version=0) is False


@pytest.mark.parametrize('path,value', [
    (('pid',), True), (('pid',), 124),
    (('native_maintenance',), None),
    (('native_maintenance', 'pid'), 124),
    (('native_maintenance', 'start_ticks'), 457),
    (('native_maintenance', 'start_ticks'), '456'),
    (('native_maintenance', 'version'), True), (('native_maintenance', 'version'), 2),
    (('native_maintenance', 'scope'), 'shared-gateway'),
    (('native_maintenance', 'status'), 'unknown'),
    (('native_maintenance', 'work', 'pending_admissions'), False),
    (('native_maintenance', 'work', 'agent_workers'), -1),
    (('native_maintenance', 'work', 'inflight_agent_calls'), 0.0),
    (('native_maintenance', 'notifications', 'status'), 'unknown'),
    (('native_maintenance', 'notifications', 'backlog'), 79),
    (('native_maintenance', 'notifications', 'durable_retained'), True),
    (('native_maintenance', 'notifications', 'policy'), 'discard'),
])
def test_unknown_malformed_or_wrong_identity_is_never_idle(path, value):
    health = evidence()
    target = health
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match='native maintenance'):
        module().require_native_readiness(health, expected_pid=123, expected_start_ticks=456, session_delete_version=0)


def test_missing_field_and_opaque_notifications_fail_closed():
    health = evidence()
    del health['native_maintenance']['work']['active_run_tasks']
    with pytest.raises(RuntimeError):
        module().require_native_readiness(health, expected_pid=123, expected_start_ticks=456, session_delete_version=0)
    health = evidence()
    health['native_maintenance']['notifications'].update(durable_retained=77, unpreserved=1)
    assert module().require_native_readiness(health, expected_pid=123, expected_start_ticks=456, session_delete_version=0) is False


def legacy():
    return {'pid': 123, 'readiness': {'checks': {'background_queues': {
        'status': 'ok', 'active_api_runs': 0, 'active_delegations': 0, 'process_completions': 0}}}}


def bootstrap_inputs():
    return dict(expected_pid=123, expected_start_ticks=456, exclusive_ingress=True,
                gate_owner='release-a', expected_gate_owner='release-a', local_nonterminal_runs=0,
                required_statuses=['completed'], implementation_attested=True,
                process={'pid': 123, 'start_ticks': 456, 'descendants': 0, 'connected_inet_sockets': 0})


def test_narrow_legacy_bootstrap_needs_explicit_positive_external_proof():
    assert module().require_legacy_bootstrap(legacy(), **bootstrap_inputs()) is True


@pytest.mark.parametrize('field,value', [('exclusive_ingress', False), ('exclusive_ingress', 1),
    ('gate_owner', 'other'), ('gate_owner', None), ('implementation_attested', False),
    ('local_nonterminal_runs', False), ('required_statuses', ['not-a-status']),
    ('process', {'pid': 123, 'start_ticks': 457, 'descendants': 0, 'connected_inet_sockets': 0})])
def test_legacy_unknown_authority_and_identity_are_rejected(field, value):
    inputs = bootstrap_inputs()
    inputs[field] = value
    with pytest.raises(RuntimeError, match='legacy bootstrap'):
        module().require_legacy_bootstrap(legacy(), **inputs)


@pytest.mark.parametrize('kind', ['local_work', 'native_run', 'native_delegation', 'descendant', 'connection', 'required_run'])
def test_legacy_observed_activity_blocks_restart(kind):
    health, inputs = legacy(), bootstrap_inputs()
    if kind == 'local_work':
        inputs['local_nonterminal_runs'] = 1
    elif kind == 'native_run':
        health['readiness']['checks']['background_queues']['active_api_runs'] = 1
    elif kind == 'native_delegation':
        health['readiness']['checks']['background_queues']['active_delegations'] = 1
    elif kind == 'descendant':
        inputs['process']['descendants'] = 1
    elif kind == 'connection':
        inputs['process']['connected_inet_sockets'] = 1
    else:
        inputs['required_statuses'] = ['stopping']
    assert module().require_legacy_bootstrap(health, **inputs) is False


def test_legacy_78_opaque_entries_cannot_be_declared_durable_by_row_count():
    health = legacy()
    health['readiness']['checks']['background_queues']['process_completions'] = 78
    health['durable_pending_rows'] = 78  # Equal cardinality proves no payload identity.
    with pytest.raises(RuntimeError, match='opaque completion backlog.*preservation'):
        module().require_legacy_bootstrap(health, **bootstrap_inputs())


def fake_proc(tmp_path):
    root = tmp_path / 'proc'
    proc = root / '123'
    (proc / 'task' / '123').mkdir(parents=True)
    (proc / 'task' / '124').mkdir()
    (proc / 'fd').mkdir()
    (proc / 'net').mkdir()
    # Kernel field 22, parsed after the final close parenthesis (comm may contain one).
    (proc / 'stat').write_text('123 (native worker)) S ' + ' '.join(['0'] * 18 + ['456'] + ['0'] * 5))
    (proc / 'task' / '123' / 'children').write_text('')
    (proc / 'task' / '124' / 'children').write_text('')
    for table in ('tcp', 'tcp6', 'udp', 'udp6'):
        (proc / 'net' / table).write_text('header\n')
    (proc / 'fd' / '1').symlink_to('socket:[77]')
    (proc / 'net' / 'tcp').write_text('header\n 0: 0100007F:48D2 00000000:0000 0A 0 0 0 0 0 77\n')
    return root, proc


def test_read_only_os_observation_includes_all_thread_children_and_owned_connections(tmp_path):
    root, proc = fake_proc(tmp_path)
    expected = {'pid': 123, 'start_ticks': 456, 'descendants': 0, 'connected_inet_sockets': 0}
    assert module().observe_process(123, proc_root=root) == expected
    (proc / 'task' / '124' / 'children').write_text('456 457 ')
    (proc / 'fd' / '2').symlink_to('socket:[88]')
    (proc / 'net' / 'tcp').write_text('header\n 0: 0100007F:48D2 0100007F:1234 01 0 0 0 0 0 88\n')
    result = module().observe_process(123, proc_root=root)
    assert result['descendants'] == 2 and result['connected_inet_sockets'] == 1
    (proc / 'net' / 'udp').unlink()
    with pytest.raises(RuntimeError, match='process observation'):
        module().observe_process(123, proc_root=root)
