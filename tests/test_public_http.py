"""Whole HTTP deadline and TLS/credential guard tests for public static checks."""
import socket
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import ssl
from threading import Thread
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
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert_path, key_path)
    requests = []
    reset_paths = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.client_address[1]))
            if self.path == '/reset-once' and self.path not in reset_paths:
                reset_paths.add(self.path)
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path == '/redirect-external':
                self.send_response(302)
                self.send_header('Location', 'https://127.0.0.1:1/untrusted')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if self.path == '/redirect-same':
                self.send_response(302)
                self.send_header('Location', '/canonical')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if self.path == '/error':
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            body = b'expected bytes plus extra' if self.path == '/oversized' else (
                b'wrong bytes' if self.path == '/wrong' else b'expected bytes')
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}/', ca_path, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_same_session_reuses_verified_https_connection(https_fixture, monkeypatch):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests = https_fixture
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

    url, _, _ = https_fixture
    with PublicHTTPSession() as session:
        with pytest.raises(ConnectionError, match='TLS'):
            public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)


def test_reset_is_not_accepted_and_same_session_can_retry(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        with pytest.raises(ConnectionError):
            public_asset_matches(url + 'reset-once', b'expected bytes', 3, session=session)
        assert public_asset_matches(url + 'reset-once', b'expected bytes', 3, session=session)
    assert len(requests) == 2
    assert requests[0][1] != requests[1][1]


def test_wrong_and_oversized_https_bodies_never_match(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert not public_asset_matches(url + 'wrong', b'expected bytes', 3, session=session)
        assert not public_asset_matches(url + 'oversized', b'expected bytes', 3, session=session)


def test_http_errors_and_cross_origin_redirects_fail_closed(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, _ = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        with pytest.raises(RuntimeError, match='Public HTTP 403'):
            public_asset_matches(url + 'error', b'', 3, session=session)
        with pytest.raises(RuntimeError, match='redirect rejected'):
            public_asset_matches(url + 'redirect-external', b'expected bytes', 3,
                                 session=session)


def test_same_origin_https_redirect_is_followed(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'redirect-same', b'expected bytes', 3, session=session)
    assert [path for path, _ in requests] == ['/redirect-same', '/canonical']


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
