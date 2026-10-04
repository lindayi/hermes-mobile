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
            question = pending(created_at=1, updated_at=1)
            app.state.orchestrator.clarifications.event(owner, run, question)
            native.items = [question]
            native.adapter = run_controls_adapter(Base)()
            state = native.adapter._state('native-run')
            state['emit'] = lambda event: None
            state['clarifications'] = {
                question['question_id']: {**question, 'signal': threading.Event()}}
            native.adapter._set_run_status('native-run', 'waiting_for_clarification')
            print(json.dumps({'run': run, 'question': question,
                              'temporary': temporary}), flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                print(f"fixture request: {request['method']} {request['path']}",
                      file=sys.stderr, flush=True)
                if request['method'] == 'POST' and request['path'].endswith('/answer'):
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
                response = client.request(
                    request['method'], BASE + request['path'],
                    **({'json': request['body']} if 'body' in request else {}))
                print(json.dumps({'status': response.status_code, 'body': response.json(),
                                  'calls': native.calls}), flush=True)
        finally:
            fixture.close()


if __name__ == '__main__':
    main()
