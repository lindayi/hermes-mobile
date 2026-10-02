"""Exercise shared linkage through both production adapters, without live calls."""
import copy
import json
import subprocess

import pytest

from deploy.cloud_coordinator import GhApi as ConsumerApi
from deploy.issue_starter import GhApi as ProducerApi
from deploy.pull_handoff_binding import _closing_issue_linked as closing_issue_linked
from test_issue_starter import (
    REPOSITORY, closing_issue_response, issue_reference, pull_request,
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
