"""Verify installed approval endpoint forwards the action identity; no tools execute."""
import asyncio
import inspect
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,'/usr/local/lib/hermes-agent')
from gateway.platforms.api_server import APIServerAdapter

seen={}

def resolve(key,choice,**kwargs):
    seen.update(kwargs)
    return 1

class Request:
    match_info={'run_id':'test-run'}
    async def json(self):
        return {'choice':'once','request_id':'reviewed-action-only'}

adapter=SimpleNamespace(_check_auth=lambda request:None,
    _run_statuses={'test-run':{'status':'waiting_for_approval'}},
    _run_approval_sessions={'test-run':'test-run'},_run_streams={},
    _set_run_status=lambda *args,**kwargs:None)
method=APIServerAdapter._handle_run_approval
if '--candidate' in sys.argv:
    source=textwrap.dedent(inspect.getsource(method))
    old='resolve_all=resolve_all,\n'
    assert source.count(old)==1
    source=source.replace(old,old+'            request_id=str(body.get("request_id", "")).strip() or None,\n')
    namespace=dict(method.__globals__)
    exec(compile(source,'<candidate-approval-identity>','exec'),namespace)
    method=namespace['_handle_run_approval']
with patch('tools.approval.resolve_gateway_approval',resolve):
    response=asyncio.run(method(adapter,Request()))
print('HTTP status:',response.status)
print('Resolver request_id:',seen.get('request_id'))
assert seen.get('request_id')=='reviewed-action-only', 'BLOCKER: approval API drops the reviewed action identity'
