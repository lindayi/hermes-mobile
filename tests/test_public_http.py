"""Whole HTTP deadline and TLS/credential guard tests for public static checks."""
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest


@pytest.mark.parametrize('phase',['headers','body'])
def test_trickling_headers_and_body_cannot_outlive_request_deadline(phase):
    from deploy.public_http import public_asset_matches
    class Slow(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            try:
                if phase=='headers':self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                else:self.send_response(200);self.end_headers()
                for _ in range(30):self.wfile.write(b'x');self.wfile.flush();time.sleep(.05)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Slow);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        started=time.monotonic()
        with pytest.raises(TimeoutError):public_asset_matches(f'http://127.0.0.1:{server.server_port}/',b'x'*30,.2)
        assert time.monotonic()-started<1.0
    finally:server.shutdown();server.server_close();thread.join()


def test_temporary_tls_handshake_failure_is_retryable_but_not_accepted(monkeypatch):
    from deploy.public_http import public_asset_matches
    monkeypatch.setattr(subprocess,'run',lambda command,**kw:subprocess.CompletedProcess(command,35,b'\n000',b'handshake interrupted'))
    with pytest.raises(ConnectionError):public_asset_matches('https://example.invalid/sw.js',b'expected',3)


def test_public_fetch_keeps_tls_checks_ignores_user_config_and_bounds_process(monkeypatch):
    from deploy.public_http import public_asset_matches
    seen=[]
    def run(command,**kwargs):seen.append((command,kwargs));return subprocess.CompletedProcess(command,60,b'',b'certificate failed')
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(RuntimeError,match='transport failed.*60'):public_asset_matches('https://example.invalid/sw.js',b'expected',3)
    command,options=seen[0]
    assert command[:2]==['/usr/bin/curl','--disable']
    assert '--insecure' not in command and '-k' not in command
    assert command[command.index('--noproxy')+1]=='*'
    assert options['timeout']==3 and options.get('shell',False) is False
