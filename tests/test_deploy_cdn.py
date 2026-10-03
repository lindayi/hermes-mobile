"""CDN stale-while-revalidate fixtures; real HTTP, no external service requests."""
from collections import Counter
from contextlib import contextmanager
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from threading import Thread
from urllib.parse import urlsplit
import shutil
import pytest
from test_self_deploy import deploy_fixture


@contextmanager
def fixture(tmp_path, mode):
    module, paths = deploy_fixture(tmp_path)
    (paths.source/'frontend/sw.js').write_text('current service worker')
    stage=module.stage_release(paths.source,tmp_path/'stage')
    shutil.copytree(stage/'frontend',paths.webroot,dirs_exist_ok=True)
    counts=Counter()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(paths.webroot),**kwargs)
        def log_message(self,*args):pass
        def do_GET(self):
            path=urlsplit(self.path);kind='query' if path.query else 'canonical'
            counts[(path.path,kind)]+=1
            if mode=='reset' and not reset_seen[0]:
                reset_seen[0]=True
                self.connection.shutdown(2);self.connection.close();return
            if path.path=='/sw.js':
                stale=(mode=='permanent' or (mode=='refresh' and sum(v for (p,_),v in counts.items() if p=='/sw.js')==1)
                       or (mode=='canonical_stale' and kind=='canonical'))
                if stale:
                    self.send_response(200);self.send_header('Supersonic-Cache-Status','STALE');self.end_headers();self.wfile.write(b'old worker');return
            if mode=='403':self.send_response(403);self.end_headers();return
            super().do_GET()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    reset_seen=[False]
    try:yield module,paths,stage,'http://127.0.0.1:'+str(server.server_port)+'/',counts
    finally:server.shutdown();server.server_close();thread.join()


def test_matching_final_response_after_aggregate_deadline_is_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import deploy.public_http as transport
    with fixture(tmp_path,'fresh') as (module,paths,stage,url,counts):
        clock=[0]
        sessions=[]
        monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        def late_match(address,expected,timeout,*,session=None):
            sessions.append(session)
            if '/sw.js?' in address:clock[0]=91
            return True
        monkeypatch.setattr(transport,'public_asset_matches',late_match)
        with pytest.raises(RuntimeError,match='timed out'):
            module.verify_release(paths,stage,False,public_url=url,sleep=lambda _:None)
        assert sessions and sessions[0] is not None
        assert len({id(session) for session in sessions}) == 1


def test_transient_connection_reset_is_retried_without_skipping_byte_checks(tmp_path):
    with fixture(tmp_path,'reset') as (module,paths,stage,url,counts):
        delays=[]
        module.verify_release(paths,stage,False,public_url=url,sleep=delays.append)
        assert delays and counts[('/','canonical')]==2
        assert counts[('/sw.js','canonical')]==1


def test_stale_response_is_retried_until_exact_bytes_match(tmp_path):
    with fixture(tmp_path,'refresh') as (module,paths,stage,url,counts):
        delays=[]
        module.verify_release(paths,stage,False,public_url=url,sleep=delays.append)
        assert delays, 'observed stale response must trigger a bounded retry'
        assert counts[('/sw.js','canonical')]>=2
        assert counts[('/sw.js','query')]>=1


@pytest.mark.parametrize('mode',['permanent','canonical_stale'])
def test_permanently_wrong_canonical_asset_never_passes_even_if_query_is_fresh(tmp_path,mode):
    with fixture(tmp_path,mode) as (module,paths,stage,url,counts):
        delays=[]
        with pytest.raises(RuntimeError,match='Public asset verification failed: sw.js'):
            module.verify_release(paths,stage,False,public_url=url,sleep=delays.append)
        assert 1<len(delays)<=5
        assert 2<=counts[('/sw.js','canonical')]<=6


def test_http403_is_not_mistaken_for_eventual_cache_refresh(tmp_path):
    with fixture(tmp_path,'403') as (module,paths,stage,url,counts):
        with pytest.raises(RuntimeError,match='Public HTTP 403: /'):
            module.verify_release(paths,stage,False,public_url=url,sleep=lambda _:pytest.fail('403 is not a byte-cache retry'))
        assert sum(counts.values())==1
