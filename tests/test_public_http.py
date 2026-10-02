"""Whole HTTP deadline and TLS/credential guard tests for public static checks."""
import subprocess
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
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.client_address[1]))
            body = b'expected bytes'
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


def test_same_session_reuses_verified_https_connection(https_fixture):
    from deploy.public_http import PublicHTTPSession, public_asset_matches

    url, ca_bundle, requests = https_fixture
    with PublicHTTPSession(ca_bundle=ca_bundle) as session:
        assert public_asset_matches(url + 'canonical', b'expected bytes', 3, session=session)
        assert public_asset_matches(url + 'cache?deploy=synthetic', b'expected bytes', 3,
                                    session=session)
    assert [path for path, _ in requests] == ['/canonical', '/cache?deploy=synthetic']
    assert len({port for _, port in requests}) == 1


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
