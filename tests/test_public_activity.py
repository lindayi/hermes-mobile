"""Public text channel selection; never forward private reasoning into activity."""
import pytest
from test_orchestration import runtime_at, USER, BODY, until


@pytest.mark.asyncio
async def test_public_commentary_preserves_event_order_and_excludes_private_reasoning(tmp_path):
    runtime,journal,gateway=runtime_at(tmp_path)
    try:
        run=await runtime.submit(USER,BODY)
        await gateway.started.wait()
        for event in [
            {'event':'message.commentary','text':'Checking the public sources.','reasoning_content':'PRIVATE'},
            {'event':'tool.started','tool':'web_search','preview':'Sources'},
            {'event':'message.delta','channel':'commentary','delta':'Comparing their dates.'},
            {'event':'reasoning.available','text':'PRIVATE'},
            {'event':'message.delta','channel':'analysis','delta':'PRIVATE'},
            {'event':'message.delta','phase':'analysis','delta':'PRIVATE'},
            {'event':'message.delta','channel':'reasoning','delta':'PRIVATE'},
            {'event':'message.delta','channel':'thinking','delta':'PRIVATE'},
            {'event':'message.delta','phase':'internal','delta':'PRIVATE'},
            {'event':'message.commentary','channel':'analysis','text':'PRIVATE'},
            {'event':'message.delta','delta':'Public answer.','reasoning_content':'PRIVATE'},
            {'event':'run.completed','run_id':'upstream','output':'Public answer.'},
        ]:
            await gateway.queue.put(event)
        await until(lambda:journal.get(USER['id'],run['id'])['status']=='completed')
        events=journal.events(USER['id'],run['id'])
        selected=[(e['name'],e['data']) for e in events if e['name'] not in ('status','done')]
        assert [e[0] for e in selected]==['commentary','tool','commentary','delta']
        assert selected[0][1]=={'text':'Checking the public sources.'}
        assert selected[2][1]=={'text':'Comparing their dates.'}
        assert selected[3][1]=={'text':'Public answer.'}
        assert 'PRIVATE' not in str(events)
    finally:
        await runtime.close()
