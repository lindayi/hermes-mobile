"""Actual assembled snapshot/journal fixtures; temporary databases and no model calls."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / 'tests'), str(root)]
from test_chat_snapshot import chat, admit, persist, latest

with TemporaryDirectory(prefix='hermes-latest-tools-fixture-') as directory:
    fixture = chat.__wrapped__(Path(directory))
    context = next(fixture)
    try:
        app, _, user, _ = context
        run = admit(context, text='Completed fixture turn')
        app.state.journal.set_upstream(user['id'], run['id'], 'fixture-upstream')
        persist(context, [('user', 'Completed fixture turn', None)])
        app.state.journal.event(user['id'], run['id'], 'commentary', {'text': 'Checking recorded sources'})
        for tool, summary in [('terminal', 'terminal: pytest tests -q'), ('read_file', 'read_file: docs/guide.md')]:
            app.state.journal.event(user['id'], run['id'], 'tool', {'event': 'tool.started', 'tool': tool, 'summary': summary})
            app.state.journal.event(user['id'], run['id'], 'tool', {'event': 'tool.completed', 'tool': tool, 'error': False})
        app.state.journal.finish(user['id'], run['id'], 'completed', output='Completed fixture answer')
        overlay = latest(context)
        persist(context, [('assistant', 'Completed fixture answer', None)])
        history = latest(context)
        print(json.dumps({'overlay': overlay, 'history': history}))
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
