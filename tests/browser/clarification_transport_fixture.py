"""Synthetic authenticated API/native transport for generated-browser reconciliation."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.native_run_controls import run_controls_adapter
from test_auth import BASE
from test_clarifications import authenticated_clarification_app, pending
from test_native_run_controls import Base


def main():
    with tempfile.TemporaryDirectory(prefix='clarification-transport-') as temporary:
        fixture = authenticated_clarification_app.__wrapped__(Path(temporary))
        app, client, owner, run, native = next(fixture)
        try:
            scenario = sys.argv[1] if len(sys.argv) > 1 else 'conflict'
            shape = sys.argv[2] if len(sys.argv) > 2 else 'single'
            question = pending(created_at=1, updated_at=1)
            if shape == 'multi':
                question['multi_select'] = True
            elif shape == 'open':
                question['choices'] = None
            app.state.orchestrator.clarifications.event(owner, run, question)
            native.items = [question]
            native.adapter = run_controls_adapter(Base)()
            state = native.adapter._state('native-run')
            state['emit'] = lambda event: None
            state['clarifications'] = {
                question['question_id']: {**question, 'signal': threading.Event()}}
            native.adapter._set_run_status('native-run', 'waiting_for_clarification')
            print(json.dumps({'run': run, 'question': question,
                              'cursor': app.state.journal.events(owner['id'], run['id'])[-1]['id'],
                              'temporary': temporary}), flush=True)
            submissions = 0
            answer_bodies = []
            for line in sys.stdin:
                request = json.loads(line)
                print(f"fixture request: {request['method']} {request['path']}",
                      file=sys.stderr, flush=True)
                if request['path'] == '/fixture/observe':
                    event = request['body']
                    runtime = app.state.orchestrator
                    if event['event'] == 'run.completed':
                        client.portal.call(runtime._observe_terminal_event, owner, run['id'], event)
                    else:
                        client.portal.call(runtime._observe_clarification_event, owner, run['id'], event)
                    print(json.dumps({'events': app.state.journal.events(owner['id'], run['id'])}),
                          flush=True)
                    continue
                if request['method'] == 'POST' and request['path'].endswith('/answer'):
                    submissions += 1
                    answer_bodies.append(request['body'])
                    if scenario == 'conflict':
                        # Another native caller wins just before this mobile dispatch.
                        receipt = client.portal.call(
                            native.adapter._handle_clarification_answer, SimpleNamespace(
                                match_info={'run_id': 'native-run',
                                            'question_id': question['question_id']},
                                body={'answer': 'Keep current', 'other': False}))
                        assert receipt.status == 200
                        native.items = [
                            native.adapter._clarification_view(
                                state['clarifications'][question['question_id']]),
                            pending('b' * 32, created_at=3, updated_at=3),
                        ]
                    elif scenario == 'rejected' and submissions == 1:
                        native.answer_response = (409, {
                            'object': 'hermes.run.clarification', 'run_id': 'native-run',
                            'question_id': question['question_id'], 'status': 'rejected',
                            'error': {'code': 'clarification_stale'}})
                    else:
                        native.answer_response = 'timeout' if scenario == 'unknown' else None
                response = client.request(
                    request['method'], BASE + request['path'],
                    **({'json': request['body']} if 'body' in request else {}))
                print(json.dumps({'status': response.status_code, 'body': response.json(),
                                  'calls': native.calls,
                                  'answer_bodies': answer_bodies,
                                  'events': app.state.journal.events(owner['id'], run['id']),
                                  'native_question': native.adapter._clarification_view(
                                      state['clarifications'][question['question_id']])}), flush=True)
        finally:
            fixture.close()


if __name__ == '__main__':
    main()
