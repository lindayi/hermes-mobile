import httpx
import pytest


@pytest.mark.asyncio
async def test_native_history_loader_preserves_tool_pairs_without_exposing_reasoning():
    from backend.hermes_client import GatewayClient
    async def handler(request):
        assert request.url.params['order']=='oldest'
        return httpx.Response(200,json={'session_id':'native','data':[
            {'role':'user','content':'Read a file','reasoning':'private'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_file','arguments':'{}'}}]},
            {'role':'tool','content':'file result','tool_call_id':'call1','tool_name':'read_file'}]})
    client=GatewayClient('http://localhost:8642','secret',transport=httpx.MockTransport(handler))
    context=await client.history('default','native')
    assert context['complete'] is True
    assert context['history'][-1]['tool_call_id']=='call1'
    assert 'reasoning' not in str(context)
    await client.close()


@pytest.mark.asyncio
async def test_native_history_changed_lineage_is_not_silently_claimed_complete():
    from backend.hermes_client import GatewayClient,IntegrationUnavailable
    async def handler(request):
        return httpx.Response(200,json={'session_id':'different-tip','data':[]})
    client=GatewayClient('http://localhost:8642','secret',transport=httpx.MockTransport(handler))
    with pytest.raises(IntegrationUnavailable):
        await client.history('default','old-session')
    await client.close()
