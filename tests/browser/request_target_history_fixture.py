"""Real SQLite/native+journal history pages for generated notification routing."""
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'tests')]

from test_native_catalog import create_native_db
from backend.chat_snapshot import conversation_snapshot
from backend.native_catalog import NativeCatalog
from backend.runs import RunJournal


with TemporaryDirectory(prefix='hermes-request-target-') as directory:
    home = Path(directory)
    native_path = home / 'state.db'
    create_native_db(native_path)
    catalog = NativeCatalog({'default': home})
    journal = RunJournal(home / 'runs.sqlite')
    user = {'id': 'fixture-owner', 'profile': 'default'}

    def persist(rows):
        with sqlite3.connect(native_path) as db:
            db.executemany(
                'INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES(?,?,?,?,?)',
                [('wa-1', role, content, calls, 100) for role, content, calls in rows])

    old, _ = journal.submit(user['id'], 'default', 'wa-1', 'Old notification request',
                            'old-notification', history_anchor=lambda: catalog.history_anchor('default', 'wa-1'))
    persist([('user', 'Old notification request', None),
             ('assistant', 'Old notification result', None)])
    journal.finish(user['id'], old['id'], 'completed', output='Old notification result')
    middle = []
    for index in range(125):
        middle.extend((('user', f'Unrelated request {index}', None),
                       ('assistant', f'Unrelated result {index}', None)))
    persist(middle)
    newer, _ = journal.submit(user['id'], 'default', 'wa-1', 'New active request',
                              'new-active', history_anchor=lambda: catalog.history_anchor('default', 'wa-1'))
    persist([('user', 'New active request', None)])

    latest = conversation_snapshot(catalog, journal, user, 'wa-1', limit=100,
                                   latest=True, turn_boundary=True)
    pages = {}
    offset = latest['offset']
    while offset > 0:
        limit = min(100, offset)
        page = conversation_snapshot(catalog, journal, user, 'wa-1', limit=limit,
                                     offset=offset - limit, turn_boundary=True)
        pages[str(offset - limit)] = page
        offset = page['offset']
    print(json.dumps({
        'session_id': 'wa-1',
        'old_run_id': old['id'],
        'new_run_id': newer['id'],
        'latest': latest,
        'pages': pages,
        'runs': {
            old['id']: journal.get(user['id'], old['id']),
            newer['id']: journal.get(user['id'], newer['id']),
        },
    }))
