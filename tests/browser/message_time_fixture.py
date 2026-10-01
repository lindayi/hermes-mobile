"""Synthetic persisted native history for generated-browser timestamp proof."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.native_catalog import NativeCatalog

if len(sys.argv) > 1 and sys.argv[1] == 'reminder':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_task_reminder_projection import fixture, INPUT, SUFFIX
    with tempfile.TemporaryDirectory() as temporary:
        catalog, db, state = fixture(Path(temporary))
        before = db.read_bytes()
        page = catalog.messages('default', 's', snapshot=state, limit=500, turn_boundary=True)
        assert db.read_bytes() == before
        print(json.dumps({'page': page, 'question': INPUT, 'suffix': SUFFIX}))
    raise SystemExit

with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary)
    with sqlite3.connect(home / 'state.db') as db:
        db.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY);
            INSERT INTO sessions VALUES ('fixture');
            CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT DEFAULT 'fixture',
                role TEXT,content TEXT,tool_calls TEXT,timestamp REAL,display_kind TEXT,
                display_metadata TEXT,platform_message_id TEXT,
                active INTEGER DEFAULT 1,compacted INTEGER DEFAULT 0);
        ''')
        rows = [
            (1, 'system', 'PRIVATE SYSTEM PROMPT MUST NOT APPEAR', 1762061400, None),
            (2, 'user', 'Original timed question', 1762061400, None),
            (3, 'assistant', 'Recorded timed answer', 1762065000, None),
            (4, 'user', '[CONTEXT SUMMARY]:\nSynthetic retained process information.', 1762065060, 'hidden'),
            (5, 'user', 'Legacy question without a recorded time', None, None),
            (6, 'assistant', 'Legacy answer without a recorded time', None, None),
        ]
        db.executemany('INSERT INTO messages(id,role,content,timestamp,display_kind) VALUES(?,?,?,?,?)', rows)
    before = (home / 'state.db').read_bytes()
    page = NativeCatalog({'default': home}).messages('default', 'fixture', limit=500, latest=True, turn_boundary=True)
    assert (home / 'state.db').read_bytes() == before
    print(json.dumps({'page': dict(page, run=None)}))
