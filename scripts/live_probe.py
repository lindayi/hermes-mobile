"""Live deployment checks in an explicitly selected, already-verified test session.

Native session creation currently drops agent_test provenance. Do not create a
new unmarked session; require --test-session-id and verify origin before runs.
"""
import asyncio
import json
import secrets
from pathlib import Path
import sys
import time
import httpx
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.session_visibility import VERIFIED_TEST_SESSION_IDS

async def main(test_session_id=None):
    if not test_session_id:
        raise RuntimeError('Pass --test-session-id for an existing verified testing session; unmarked creation is disabled')
    config=json.loads(Path('/home/lindayi/.local/share/hermes-mobile-live/config.json').read_text())
    async with httpx.AsyncClient(base_url=config['upstream_url'],headers={'Authorization':'Bearer '+config['upstream_token']},timeout=180,trust_env=False) as c:
        caps=(await c.get('/v1/capabilities'));caps.raise_for_status();caps=caps.json()
        assert caps['features'].get('run_approval_request_id') is True,'Action-bound approval capability missing'
        checked=await c.get('/api/sessions/'+quote(test_session_id,safe=''))
        checked.raise_for_status();session=checked.json()['session']
        if session.get('id') != test_session_id or not (
            session.get('source') == 'agent_test' or
            (session.get('source') == 'api_server' and test_session_id in VERIFIED_TEST_SESSION_IDS)
        ):
            raise RuntimeError('Refusing to run outside a verified testing session')
        sid=test_session_id
        history_path='/api/sessions/'+quote(sid,safe='')+'/messages'
        # Native latest pages are chronological, with monotonic insertion IDs.
        # Capture the newest ID before either run, never accept older evidence.
        before=await c.get(history_path,params={'limit':1,'order':'latest'})
        before.raise_for_status();before=before.json()
        canonical_id=before.get('session_id')
        if not isinstance(canonical_id,str) or not canonical_id or (
            canonical_id!=sid and before.get('requested_session_id')!=sid
        ):
            raise RuntimeError('Native history is not bound to the selected session')
        boundary=max((m['id'] for m in before['data']),default=0)
        results=[]
        marker='HERMES_MOBILE_LIVE_TOOL_OK_'+secrets.token_hex(16)
        prompts=[
            f'This is a deployment smoke test. Do not update memory, files, settings, jobs or send external messages. Use the terminal tool exactly once to run: printf {marker} . Then report the exact marker it returned.',
            'Continue the deployment test without using tools or changing memory. What exact marker did the terminal return in the preceding turn? Reply with that marker only.'
        ]
        for prompt in prompts:
            response=await c.post('/v1/runs',json={'session_id':sid,'input':prompt})
            response.raise_for_status();rid=response.json()['run_id']
            deadline=time.monotonic()+240
            while True:
                state=await c.get('/v1/runs/'+rid);state.raise_for_status();state=state.json()
                if state['status'] in ('completed','failed','cancelled'):
                    break
                if state['status']=='waiting_for_approval':
                    raise RuntimeError('Smoke test awaits a human approval; not auto-approving')
                if time.monotonic()>deadline:raise TimeoutError('Native run still active; do not rerun automatically')
                await asyncio.sleep(1)
            results.append({'run_id':rid,'status':state['status'],'output':state.get('output'),'error':state.get('error')})
            if state['status']!='completed':break
        report={'session_id':sid,'action_bound_approval':True,'results':results}
        print(json.dumps(report,indent=2))
        assert len(results)==2 and all(r['status']=='completed' and marker in str(r['output']) for r in results),'Live execution/continuation check failed'
        found=False
        offset=0
        previous_oldest=None
        # Walk back to the pre-run boundary, not just the oldest/latest 500 rows.
        while True:
            history=await c.get(history_path,params={'limit':500,'offset':offset,'order':'latest'})
            history.raise_for_status();history=history.json()
            if history.get('session_id')!=canonical_id or (
                canonical_id!=sid and history.get('requested_session_id')!=sid
            ):
                raise RuntimeError('Native history session changed during probe; evidence is inconclusive')
            rows=history['data']
            if rows and previous_oldest is not None and max(m['id'] for m in rows)>=previous_oldest:
                raise RuntimeError('Native history pagination did not advance; evidence is inconclusive')
            found=any(m['id']>boundary and m.get('role')=='tool' and marker in str(m.get('content')) for m in rows)
            if found or len(rows)<500 or any(m['id']<=boundary for m in rows):
                break
            previous_oldest=min(m['id'] for m in rows)
            offset+=len(rows)
        assert found,'No new native persisted tool result'
        print('PASS: live model, terminal result, native persistence, and second-turn context.')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-session-id', required=True,
                        help='Explicit consent to append probe turns to an existing verified testing session')
    asyncio.run(main(parser.parse_args().test_session_id))
