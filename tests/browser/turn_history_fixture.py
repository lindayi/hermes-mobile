"""Temporary native SQLite -> actual turn-aware snapshot, no model calls."""
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root),str(root/'tests')]
from test_native_catalog import create_native_db
from backend.native_catalog import NativeCatalog
from backend.runs import RunJournal
from backend.chat_snapshot import conversation_snapshot
with TemporaryDirectory(prefix='hermes-turn-browser-') as d:
    home=Path(d);path=home/'state.db';create_native_db(path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM messages');db.execute('ALTER TABLE messages ADD COLUMN codex_message_items TEXT')
        rows=[]
        for turn,n in [('Older',620),('Latest',140)]:
            rows.append(('user',turn+' question',None))
            if turn=='Older':
                parts=[{'type':'message','phase':phase,'content':[{'type':'output_text','text':text}]} for phase,text in [('analysis','PRIVATE ANALYSIS'),('commentary','I’ll inspect the earlier results — public progress.')]]
                rows.append(('assistant','',json.dumps(parts)))
            rows.extend(('tool',json.dumps({'exit_code':0,'output':'done'}),None) for _ in range(n))
            rows.append(('assistant',turn+' answer\n\n'+('Readable final response. '*100),None))
        db.executemany('INSERT INTO messages(session_id,role,content,codex_message_items) VALUES(\'wa-1\',?,?,?)',rows)
    cat=NativeCatalog({'default':home});journal=RunJournal(home/'runs.sqlite');user={'id':'u','profile':'default'}
    initial=conversation_snapshot(cat,journal,user,'wa-1',latest=True,turn_boundary=True)
    offset=initial['offset'];limit=min(100,offset)
    older=conversation_snapshot(cat,journal,user,'wa-1',limit=limit,offset=offset-limit,turn_boundary=True)
    print(json.dumps({'initial':initial,'older':older}))
