"""Browser fixture from real snapshot composition and temporary SQLite, no live state."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory

root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'tests'),str(root)]
from test_chat_snapshot import chat, admit, persist, latest
from test_reopen_native_rewrite import compact

with TemporaryDirectory(prefix='hermes-browser-snapshot-') as directory:
    fixture=chat.__wrapped__(Path(directory))
    context=next(fixture)
    try:
        app,client,user,db=context
        run=admit(context,text='Isolated fixture input.')
        app.state.journal.set_upstream(user['id'],run['id'],'fixture-upstream')
        compact(context)
        persist(context,[('user','Isolated fixture input.',None),
                         ('assistant',None,'[{"id":"t1","function":{"name":"web_search","arguments":"{}"}}]'),
                         ('tool','Frozen fixture tool result',None)])
        print(json.dumps(latest(context)))
    finally:
        try:next(fixture)
        except StopIteration:pass
