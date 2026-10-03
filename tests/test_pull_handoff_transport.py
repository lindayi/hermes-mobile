"""Exercise shared linkage through both production adapters, without live calls."""
import copy
import json
import subprocess

import pytest

from deploy.cloud_coordinator import GhApi as ConsumerApi
from deploy.issue_starter import GhApi as ProducerApi, _BoundedApi
from deploy.pull_handoff_binding import _closing_issue_linked as closing_issue_linked
from test_issue_starter import (
    REPOSITORY, FakeApi, closing_issue_response, completed_task, issue_reference,
    make_coordinator, pull_request,
)


@pytest.mark.parametrize('adapter', [ConsumerApi, ProducerApi])
@pytest.mark.parametrize('paginated', [False, True])
@pytest.mark.parametrize('missing', [False, True])
def test_shared_linkage_preserves_first_page_through_real_adapters(adapter, paginated, missing, monkeypatch):
    pull = pull_request(draft=False)
    requests = []
    reads = []

    def transport(command, **kwargs):
        assert command[:4] == ['gh', 'api', '--hostname', 'github.com']
        assert kwargs['env']['GH_PROMPT_DISABLED'] == '1'
        if 'graphql' in command:
            if '--input' in command:
                payload = json.loads(kwargs['input'])
                variables = payload['variables']
            else:
                variables = {}
                for index, argument in enumerate(command):
                    if argument == '-F':
                        key, value = command[index + 1].split('=', 1)
                        try:
                            variables[key] = json.loads(value)
                        except ValueError:
                            variables[key] = value
            requests.append(copy.deepcopy(variables))
            assert variables['number'] == pull['number']
            cursor = variables.get('after')
            # GitHub treats the literal CLI value "None" as an opaque cursor,
            # not JSON null. A read-only live probe returned an empty connection.
            if cursor == 'None':
                response = closing_issue_response(pull, nodes=[])
            elif cursor is None and paginated:
                response = closing_issue_response(
                    pull, nodes=[issue_reference(27)],
                    page_info={'hasNextPage': True, 'endCursor': 'page-two'},
                )
            else:
                assert cursor == ('page-two' if paginated else None)
                response = closing_issue_response(
                    pull, nodes=[] if missing else [issue_reference(28)],
                )
        else:
            assert command[-1] == f'repos/{REPOSITORY}/pulls/{pull["number"]}'
            if '--method' in command:
                assert command[command.index('--method') + 1] == 'GET'
            else:
                assert not any(flag in command for flag in ('--input', '-F', '-f'))
            reads.append(command[-1])
            response = copy.deepcopy(pull)
        return subprocess.CompletedProcess(command, 0, json.dumps(response), '')

    if adapter is ProducerApi:
        monkeypatch.setattr('deploy.issue_starter.subprocess.run', transport)
        api = adapter()
    else:
        api = adapter(run=transport)
    assert closing_issue_linked(api, pull, 28) is (not missing)
    assert requests[0].get('after') is None
    assert len(requests) == (2 if paginated else 1)
    if paginated:
        assert requests[1]['after'] == 'page-two'
    assert len(reads) == (0 if missing else 1)


def test_starter_closing_mutation_uses_real_bounded_json_transport(tmp_path, monkeypatch):
    # Synthetic responses only; exercise the coordinator's actual mutation query,
    # _BoundedApi accounting and GhApi serialization, intercepting all subprocesses.
    backend = FakeApi(pulls=[pull_request(draft=False)])
    backend.closing_issues = []
    requests = []

    def transport(command, **kwargs):
        assert command[:4] == ['gh', 'api', '--hostname', 'github.com']
        assert kwargs['env']['GH_PROMPT_DISABLED'] == '1'
        assert kwargs['env']['GH_NO_UPDATE_NOTIFIER'] == '1'
        assert kwargs['capture_output'] is True
        assert kwargs['text'] is True
        assert kwargs['check'] is False
        assert kwargs['timeout'] == 45
        payload = None
        if '--method' in command:
            assert command[-5:] == ['--method', 'POST', command[-3], '--input', '-']
            assert not any(flag in command for flag in ('-F', '-f'))
            payload = json.loads(kwargs['input'])
            response = backend.post(command[-3], payload)
        else:
            assert kwargs['input'] is None
            response = backend.get(command[-1])
        requests.append((command, payload, coordinator.api.reads, coordinator.api.writes))
        return subprocess.CompletedProcess(command, 0, json.dumps(response), '')

    monkeypatch.setattr('deploy.issue_starter.subprocess.run', transport)
    coordinator = make_coordinator(tmp_path, ProducerApi())
    assert isinstance(coordinator.api, _BoundedApi)
    assert isinstance(coordinator.api.api, ProducerApi)
    assert coordinator.run(apply=True)['dispatched'] == 1
    backend.task_detail = completed_task()
    requests.clear()

    assert coordinator.run(apply=True)['handed_off'] == 1

    mutations = [
        index for index, (_, payload, _, _) in enumerate(requests)
        if payload and 'addCloseIssueReferences' in payload.get('query', '')
    ]
    assert len(mutations) == 1
    index = mutations[0]
    command, payload, reads, writes = requests[index]
    assert command[-5:] == ['--method', 'POST', 'graphql', '--input', '-']
    assert set(payload) == {'query', 'variables'}
    assert payload['variables'] == {
        'issueId': 'I_kwDOIssue28',
        'pullRequestIds': ['PR_kwDO123'],
        'clientMutationId': 'hermes-issue-starter:28:9001:close-link',
    }
    # Match only the documented input/payload fields; no invented CAS fields.
    assert ' '.join(payload['query'].split()) == (
        'mutation AddCloseIssueReferences( '
        '$issueId: ID!, $pullRequestIds: [ID!]!, $clientMutationId: String ) { '
        'addCloseIssueReferences(input: { issueId: $issueId, '
        'pullRequestIds: $pullRequestIds, clientMutationId: $clientMutationId }) { '
        'clientMutationId issue { id number repository { id nameWithOwner } } } }'
    )
    assert 'mutation' not in payload['variables']
    assert index > 0
    assert reads == requests[index - 1][2]
    assert writes == requests[index - 1][3] + 1
    assert backend.closing_issues == [issue_reference()]
    assert backend.patches == []
