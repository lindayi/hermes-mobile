"""Public byte checks over a bounded, reusable strict HTTP connection session."""
import multiprocessing
import time
from urllib.parse import urljoin, urlsplit


_REDIRECTS = {301, 302, 303, 307, 308}


def _origin(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise RuntimeError('Public HTTP URL is invalid')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return parts.scheme, parts.hostname.lower(), port


def _request_matches(session, url, expected):
    import requests

    origin = _origin(url)
    current = url
    for redirect_count in range(6):
        response = session.get(
            current,
            headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'},
            allow_redirects=False,
            stream=True,
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
        while True:
            request = connection.recv()
            if request is None:
                break
            url, expected = request
            try:
                result = ('match', _request_matches(session, url, expected))
            except requests.exceptions.SSLError:
                result = ('tls', 'Public TLS validation or connection failed')
            except requests.exceptions.Timeout:
                result = ('timeout', 'Public HTTP deadline exceeded')
            except requests.exceptions.RequestException:
                result = ('connection', f'Public HTTP transport failed: {urlsplit(url).path}')
            except RuntimeError as error:
                result = ('runtime', str(error))
            connection.send(result)
    except (EOFError, OSError):
        pass
    finally:
        session.close()
        connection.close()


class PublicHTTPSession:
    """Own one isolated worker and its connection pool for a complete verification."""

    def __init__(self, *, ca_bundle=None):
        self._ca_bundle = str(ca_bundle) if ca_bundle is not None else None
        self._context = multiprocessing.get_context('spawn')
        self._connection = None
        self._process = None
        self._closed = False

    def __enter__(self):
        if self._closed:
            raise RuntimeError('Public HTTP session is closed')
        self._start()
        return self

    def _start(self):
        if self._process is not None and self._process.is_alive():
            return
        self._stop()
        parent, child = self._context.Pipe()
        process = self._context.Process(
            target=_session_worker, args=(child, self._ca_bundle), daemon=True)
        process.start()
        child.close()
        self._connection = parent
        self._process = process

    def _stop(self, *, graceful=False):
        connection, process = self._connection, self._process
        self._connection = self._process = None
        if connection is not None and graceful and process is not None and process.is_alive():
            try:
                connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(timeout=0.5)
        if process is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
        if connection is not None:
            connection.close()

    def matches(self, url, expected, timeout):
        if self._closed:
            raise RuntimeError('Public HTTP session is closed')
        timeout = float(timeout)
        if timeout <= 0:
            raise TimeoutError('Public HTTP deadline exceeded')
        self._start()
        deadline = time.monotonic() + timeout
        try:
            self._connection.send((url, expected))
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._connection.poll(remaining):
                self._stop()
                raise TimeoutError('Public HTTP deadline exceeded')
            kind, value = self._connection.recv()
        except (EOFError, BrokenPipeError, OSError) as error:
            self._stop()
            raise ConnectionError(f'Public HTTP transport failed: {urlsplit(url).path}') from error
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
