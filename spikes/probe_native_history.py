"""Regression probe against installed Hermes; fake only the model loop and DB seam.
Run with /usr/local/lib/hermes-agent/venv/bin/python. No real model/session writes.
Expected acceptance: reload latest durable history even on immediately acquired lease.
"""
import runpy
import sys
from unittest.mock import patch
import inspect
import textwrap

SOURCE='/usr/local/lib/hermes-agent'
sys.path.insert(0,SOURCE)
fixture=runpy.run_path(SOURCE+'/tests/run_agent/test_cross_process_turn_lease.py')
db=fixture['_DB']()
agent=fixture['_agent_with_db'](db)
observed={}

def fake_model_loop(_agent,_message,_system,history,*args,**kwargs):
    observed['history']=history
    return {'final_response':'test loop only','messages':history,'failed':False}

# Candidate is applied in this disposable process ONLY, never to installed files.
if '--candidate' in sys.argv:
    method=fixture['AIAgent'].run_conversation
    source=textwrap.dedent(inspect.getsource(method))
    old='if _lease_waited:\n                latest_session_id'
    assert source.count(old)==1, 'Installed source changed; review candidate rather than guessing'
    candidate=source.replace(old,'if True:  # candidate: always reload after acquiring the durable lease\n                latest_session_id')
    namespace=dict(method.__globals__)
    exec(compile(candidate,'<candidate-turn-history>','exec'),namespace)
    fixture['AIAgent'].run_conversation=namespace['run_conversation']

with patch('agent.conversation_loop.run_conversation',fake_model_loop):
    fixture['AIAgent'].run_conversation(agent,'new turn',conversation_history=[{'role':'user','content':'stale snapshot before another surface wrote'}])
print('Lease/database events:',[event[0] for event in db.events])
print('History reaching model-loop seam:',observed['history'])
assert observed['history']==[{'role':'user','content':'durable latest'}], 'BLOCKER: immediately acquired native lease did not refresh stale cross-surface history'
