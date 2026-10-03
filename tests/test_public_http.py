"""Whole HTTP deadline and TLS/credential guard tests for public static checks."""
import gzip
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import socket
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import ssl
from threading import Event, Thread
import pytest


@pytest.fixture
def https_fixture(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Hermes synthetic test CA')])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
    leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(ca_name)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1'))]),
                           critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    cert_path = tmp_path / 'server.pem'
    key_path = tmp_path / 'server.key'
    ca_path = tmp_path / 'ca.pem'
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert_path, key_path)
    requests = []
    cookies = []
    reset_paths = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.client_address[1]))
            cookies.append(self.headers.get('Cookie'))
            if self.path == '/reset-once' and self.path not in reset_paths:
                reset_paths.add(self.path)
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path in ('/redirect-external', '/redirect-external-large'):
                self.send_response(302)
                self.send_header('Location', 'https://127.0.0.1:1/untrusted')
                body = b'x' * 1024 * 1024 if self.path.endswith('-large') else b''
            elif self.path in ('/redirect-same', '/redirect-same-large',
                               '/redirect-same-compressed'):
                self.send_response(302)
                self.send_header('Location', '/canonical')
                body = b'x' * 1024 * 1024 if self.path.endswith('-large') else b''
                if self.path.endswith('-compressed'):
                    body = gzip.compress(bytes(range(256)) * 4096)
                    self.send_header('Content-Encoding', 'gzip')
            elif self.path == '/error':
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            elif self.path == '/cookie-seed':
                self.send_response(200)
                self.send_header('Set-Cookie', 'verification=synthetic')
                body = b'expected bytes'
            elif self.path == '/cookie-gated':
                self.send_response(200)
                body = (b'expected bytes' if self.headers.get('Cookie') ==
                        'verification=synthetic' else b'anonymous bytes')
            else:
                body = b'expected bytes plus extra' if self.path == '/oversized' else (
                    b'wrong bytes' if self.path == '/wrong' else b'expected bytes')
                self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                if self.path.startswith('/redirect-') and self.path != '/redirect-same':
                    try:
                        self.wfile.write(body[:1024])
                        self.wfile.flush()
                        time.sleep(.5)
                        self.wfile.write(body[1024:])
                    except OSError:
                        pass
                else:
                    self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}/', ca_path, requests, cookies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_same_session_reuses_verified_https_connection(https_fixture, monkeypatch):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests, _ = https_fixture
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('https_proxy', 'http://127.0.0.1:1')
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)
        assert public_asset_matches(url + 'cache?deploy=synthetic', b'expected bytes', 3,
                                    session=session)
    assert [path for path, _ in requests] == ['/canonical', '/cache?deploy=synthetic']
    assert len({port for _, port in requests}) == 1


def test_untrusted_https_certificate_fails_closed(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, _, _, _ = https_fixture
    with PublicHTTPSession() as session:
        with pytest.raises(ConnectionError, match='TLS'):
            public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)


def test_reset_is_not_accepted_and_same_session_can_retry(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        with pytest.raises(ConnectionError):
            public_asset_matches(url + 'reset-once', b'expected bytes', 3, session=session)
        assert public_asset_matches(url + 'reset-once', b'expected bytes', 3, session=session)
    assert len(requests) == 2
    assert requests[0][1] != requests[1][1]


def test_wrong_and_oversized_https_bodies_never_match(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert not public_asset_matches(url + 'wrong', b'expected bytes', 3, session=session)
        assert not public_asset_matches(url + 'oversized', b'expected bytes', 3, session=session)


def test_http_errors_and_cross_origin_redirects_fail_closed(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        with pytest.raises(RuntimeError, match='Public HTTP 403'):
            public_asset_matches(url + 'error', b'', 3, session=session)
        with pytest.raises(RuntimeError, match='redirect rejected'):
            public_asset_matches(url + 'redirect-external', b'expected bytes', 3,
                                 session=session)


def test_same_origin_https_redirect_is_followed(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'redirect-same', b'expected bytes', 3, session=session)
    assert [path for path, _ in requests] == ['/redirect-same', '/canonical']


@pytest.mark.parametrize('path', [
    'redirect-same-large',
    'redirect-same-compressed',
    'redirect-external-large',
])
def test_redirect_bodies_are_not_buffered_before_policy_check(
        https_fixture, path):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        # Startup/import/TLS have their own budget; the .3s proof below isolates
        # redirect-body consumption against the fixture's unchanged .5s pause.
        assert public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)
        requests.clear()
        if path == 'redirect-external-large':
            with pytest.raises(RuntimeError, match='redirect rejected'):
                public_asset_matches(url + path, b'expected bytes', .3, session=session)
        else:
            assert public_asset_matches(url + path, b'expected bytes', .3, session=session)
    assert [requested for requested, _ in requests] == (
        ['/' + path] if path == 'redirect-external-large' else ['/' + path, '/canonical'])


def test_reused_session_never_replays_server_cookies(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _, cookies = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'cookie-seed', b'expected bytes', 3, session=session)
        assert not public_asset_matches(url + 'cookie-gated', b'expected bytes', 3,
                                        session=session)
    assert cookies == [None, None]


def test_large_ipc_send_is_bounded_when_worker_stalls(https_fixture):
    if not hasattr(signal, 'SIGSTOP'):
        pytest.skip('requires POSIX process signals')
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _, _ = https_fixture
    released = Event()
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)
        process = session._process
        os.kill(process.pid, signal.SIGSTOP)

        def resume_after_watchdog():
            if not released.wait(1):
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGCONT)

        watchdog = Thread(target=resume_after_watchdog, daemon=True)
        watchdog.start()
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                session.matches(url + 'canonical', b'x' * (8 * 1024 * 1024), .15)
        finally:
            released.set()
            watchdog.join(1.5)
        assert time.monotonic() - started < .75
        assert session._process is None


def test_stalled_bootstrap_does_not_transfer_parent_preparation(tmp_path):
    if not hasattr(signal, 'SIGSTOP'):
        pytest.skip('requires POSIX process signals')
    pid_file = tmp_path / 'worker.pid'
    executable = tmp_path / 'paused-python'
    # A real executable stops before Python's multiprocessing bootstrap can read
    # its pipe (or before the isolated worker can read its socket). Resource
    # tracker startup is left alone. Only the private subprocess tree is affected.
    executable.write_text(f'#!{sys.executable}\n' + textwrap.dedent(f'''
        import os, signal, sys
        if not any('resource_tracker' in arg for arg in sys.argv):
            with open({str(pid_file)!r}, 'w') as handle:
                handle.write(str(os.getpid()))
            os.kill(os.getpid(), signal.SIGSTOP)
        os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
    '''))
    executable.chmod(0o700)
    probe = textwrap.dedent(f'''
        import faulthandler, json, multiprocessing, sys, time
        from deploy.public_http import PublicHTTPSession
        sys.path.append('x' * (8 * 1024 * 1024))
        sys.executable = {str(executable)!r}
        multiprocessing.set_executable(sys.executable)
        faulthandler.dump_traceback_later(.6)
        started = time.monotonic()
        with PublicHTTPSession() as session:
            try:
                # No HTTP request can be reached while the child is stopped.
                session.matches('https://127.0.0.1:1/', b'', .15)
            except TimeoutError:
                pass
            else:
                raise AssertionError('stopped startup must time out')
            assert session._process is None
        faulthandler.cancel_dump_traceback_later()
        print(json.dumps({{'elapsed': time.monotonic() - started}}), flush=True)
    ''')
    process = subprocess.Popen(
        [sys.executable, '-c', probe], cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            # Bounded independent watchdog: release the old blocking start so
            # it can reap its own worker; an outer process-group kill is backup.
            assert pid_file.exists(), 'startup executable was not reached'
            os.kill(int(pid_file.read_text()), signal.SIGCONT)
            stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stderr
        assert pid_file.exists(), 'the actual child must have been stopped'
        worker_pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        assert json.loads(stdout)['elapsed'] < .75, stderr
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def test_expired_startup_budget_does_not_leave_a_worker(https_fixture):
    from deploy.public_http import PublicHTTPSession

    url, ca_bundle, _, _ = https_fixture
    session = PublicHTTPSession(ca_bundle=ca_bundle)
    with pytest.raises(TimeoutError):
        session.matches(url + 'canonical', b'expected bytes', 1e-6)
    assert session._process is None or session._process.poll() is not None
    session.close()


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
