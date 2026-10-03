"""Public byte checks over a bounded, reusable strict HTTP connection session."""
import math
from pathlib import Path
import pickle
import subprocess
import sys
import selectors
import socket
import struct
import time
from urllib.parse import urljoin, urlsplit


_REDIRECTS = {301, 302, 303, 307, 308}
_FRAME_LENGTH = struct.Struct('!Q')
_MAX_RESPONSE_FRAME = 1024 * 1024


def _origin(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise RuntimeError('Public HTTP URL is invalid')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return parts.scheme, parts.hostname.lower(), port


def _send_frame(connection, value, deadline=None):
    payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    header = _FRAME_LENGTH.pack(len(payload))
    if deadline is None:
        connection.sendall(header)
        connection.sendall(payload)
        return
    if time.monotonic() >= deadline:
        raise TimeoutError('Public HTTP deadline exceeded')
    with selectors.DefaultSelector() as selector:
        selector.register(connection, selectors.EVENT_WRITE)
        for data in (header, payload):
            view = memoryview(data)
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('Public HTTP deadline exceeded')
                try:
                    sent = connection.send(view)
                except BlockingIOError:
                    continue
                if not sent:
                    raise ConnectionError('Public HTTP worker disconnected')
                view = view[sent:]


def _read_exact(connection, size, selector=None, deadline=None):
    result = bytearray()
    while len(result) < size:
        if selector is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError('Public HTTP deadline exceeded')
        try:
            chunk = connection.recv(size - len(result))
        except BlockingIOError:
            continue
        if not chunk:
            if not result:
                raise EOFError
            raise ConnectionError('Public HTTP worker disconnected')
        result.extend(chunk)
    return bytes(result)


def _receive_frame(connection, deadline=None, max_size=None):
    if deadline is None:
        header = _read_exact(connection, _FRAME_LENGTH.size)
        selector = None
    else:
        selector = selectors.DefaultSelector()
        selector.register(connection, selectors.EVENT_READ)
        header = _read_exact(connection, _FRAME_LENGTH.size, selector, deadline)
    try:
        length = _FRAME_LENGTH.unpack(header)[0]
        if max_size is not None and length > max_size:
            raise RuntimeError('Public HTTP worker response is too large')
        payload = _read_exact(connection, length, selector, deadline)
        return pickle.loads(payload)
    finally:
        if selector is not None:
            selector.close()


def _request_matches(session, url, expected, deadline):
    import requests

    origin = _origin(url)
    current = url
    for redirect_count in range(6):
        session.cookies.clear()
        request = requests.Request(
            'GET',
            current,
            headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'},
        )
        prepared = session.prepare_request(request)
        prepared.headers.pop('Cookie', None)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout('Public HTTP deadline exceeded')
        adapter = session.get_adapter(current)
        response = adapter.send(
            prepared,
            stream=True,
            timeout=remaining,
            verify=session.verify,
            cert=session.cert,
            proxies={},
        )
        if response.status_code in _REDIRECTS:
            location = response.headers.get('Location')
            response.close()
            if not location or redirect_count == 5:
                raise RuntimeError('Public HTTP redirect limit exceeded')
            destination = urljoin(current, location)
            if _origin(destination) != origin or urlsplit(destination).scheme != 'https':
                raise RuntimeError('Public HTTP redirect rejected')
            current = destination
            continue
        if response.status_code >= 400:
            response.close()
            raise RuntimeError(
                f'Public HTTP {response.status_code}: {urlsplit(url).path}')
        if not 200 <= response.status_code < 300:
            response.close()
            raise RuntimeError(f'Public HTTP {response.status_code}: {urlsplit(url).path}')

        content_length = response.headers.get('Content-Length')
        if content_length is not None:
            try:
                if int(content_length) > len(expected):
                    response.close()
                    return False
            except ValueError:
                response.close()
                raise RuntimeError(f'Public HTTP invalid body size: {urlsplit(url).path}')
        body = bytearray()
        try:
            limit = len(expected) + 1
            while len(body) < limit:
                chunk = response.raw.read(min(65536, limit - len(body)), decode_content=False)
                if not chunk:
                    break
                body.extend(chunk)
        finally:
            response.close()
        return len(body) <= len(expected) and body == expected
    raise RuntimeError('Public HTTP redirect limit exceeded')


def _session_worker(connection, ca_bundle):
    import requests

    session = requests.Session()
    session.trust_env = False
    if ca_bundle is not None:
        session.verify = ca_bundle
    try:
        _send_frame(connection, ('ready', None))
        while True:
            request = _receive_frame(connection)
            if request is None:
                break
            url, expected, deadline = request
            try:
                result = ('match', _request_matches(session, url, expected, deadline))
            except requests.exceptions.SSLError:
                result = ('tls', 'Public TLS validation or connection failed')
            except requests.exceptions.Timeout:
                result = ('timeout', 'Public HTTP deadline exceeded')
            except requests.exceptions.RequestException:
                result = ('connection', f'Public HTTP transport failed: {urlsplit(url).path}')
            except RuntimeError as error:
                result = ('runtime', str(error))
            _send_frame(connection, result)
    except (EOFError, OSError, ConnectionError):
        pass
    finally:
        session.close()
        connection.close()


class PublicHTTPSession:
    """Own one isolated worker and its connection pool for a complete verification."""

    def __init__(self, *, ca_bundle=None):
        self._ca_bundle = str(ca_bundle) if ca_bundle is not None else None
        self._connection = None
        self._process = None
        self._closed = False

    def __enter__(self):
        if self._closed:
            raise RuntimeError('Public HTTP session is closed')
        return self

    def _start(self, deadline):
        if self._process is not None and self._process.poll() is None:
            return
        self._stop()
        if time.monotonic() >= deadline:
            raise TimeoutError('Public HTTP deadline exceeded')
        parent, child = socket.socketpair()
        try:
            # Fixed isolated entrypoint: no parent __main__/sys.path preparation
            # pipe, no shell, no Python preexec_fn (safe from threaded callers).
            # OS exec/scheduling is trusted, not an interruptible real-time API.
            process = subprocess.Popen(
                [sys.executable, '-I', str(Path(__file__).resolve()), str(child.fileno())],
                pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        parent.setblocking(False)
        self._connection = parent
        self._process = process
        try:
            # All variable bootstrap data uses the same bounded socket as HTTP
            # commands. A child stalled during imports cannot block this send.
            _send_frame(parent, self._ca_bundle, deadline)
            kind, value = _receive_frame(parent, deadline, _MAX_RESPONSE_FRAME)
            if time.monotonic() >= deadline:
                raise TimeoutError('Public HTTP deadline exceeded')
            if kind != 'ready':
                raise RuntimeError(value)
        except BaseException:
            self._stop()
            raise

    def _stop(self, *, graceful=False):
        connection, process = self._connection, self._process
        self._connection = self._process = None
        if connection is not None and graceful and process is not None and process.poll() is None:
            try:
                _send_frame(connection, None, time.monotonic() + 0.1)
                process.wait(timeout=0.1)
            except (BrokenPipeError, ConnectionError, EOFError, OSError,
                    TimeoutError, subprocess.TimeoutExpired):
                pass
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        # Do not wait indefinitely on an unresponsive kernel.
                        # Popen retains unreaped children for later collection.
                        pass
        if connection is not None:
            connection.close()

    def matches(self, url, expected, timeout):
        if self._closed:
            raise RuntimeError('Public HTTP session is closed')
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise TimeoutError('Public HTTP deadline exceeded')
        deadline = time.monotonic() + timeout
        try:
            self._start(deadline)
            _send_frame(self._connection, (url, expected, deadline), deadline)
            kind, value = _receive_frame(
                self._connection, deadline, _MAX_RESPONSE_FRAME)
            if time.monotonic() >= deadline:
                raise TimeoutError('Public HTTP deadline exceeded')
        except TimeoutError:
            self._stop()
            raise
        except (EOFError, BrokenPipeError, OSError, ConnectionError) as error:
            self._stop()
            raise ConnectionError(
                f'Public HTTP transport failed: {urlsplit(url).path}') from error
        if kind == 'match':
            return value
        if kind == 'tls':
            raise ConnectionError(value)
        if kind == 'timeout':
            raise TimeoutError(value)
        if kind == 'connection':
            raise ConnectionError(value)
        raise RuntimeError(value)

    def close(self):
        if not self._closed:
            self._stop(graceful=True)
            self._closed = True

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def public_asset_matches(url, expected, timeout, *, session=None):
    """Return whether a bounded public response exactly matches staged bytes."""
    if session is not None:
        return session.matches(url, expected, timeout)
    with PublicHTTPSession() as temporary_session:
        return temporary_session.matches(url, expected, timeout)


if __name__ == '__main__':
    # -I executes this reviewed file directly, not caller-controlled import paths
    # or __main__. The private inherited socket carries only trusted local data.
    with socket.socket(fileno=int(sys.argv[1])) as connection:
        _session_worker(connection, _receive_frame(connection))
