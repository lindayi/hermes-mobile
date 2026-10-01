"""Public byte checks with a process-enforced whole-request deadline.

curl's total timeout plus subprocess timeout bounds DNS, TLS, headers and slow
bodies. --disable ignores user curlrc; no proxies or TLS bypass are permitted.
"""
import subprocess
from urllib.parse import urlsplit


def public_asset_matches(url, expected, timeout):
    command=['/usr/bin/curl','--disable','--silent','--show-error','--location',
             '--max-redirs','5','--proto','=http,https','--proto-redir','=https',
             '--noproxy','*','--max-time',str(timeout),
             '--max-filesize',str(max(1,len(expected))),
             '--header','Cache-Control: no-cache','--write-out','\n%{http_code}',url]
    try:
        result=subprocess.run(command,capture_output=True,timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('Public HTTP deadline exceeded') from error
    if result.returncode==35:
        raise ConnectionError('Public TLS handshake interrupted')
    if result.returncode==28:
        raise TimeoutError('Public HTTP deadline exceeded')
    body, _, status=result.stdout.rpartition(b'\n')
    code=int(status) if len(status)==3 and status.isdigit() else 0
    if code>=400:
        raise RuntimeError(f'Public HTTP {code}: {urlsplit(url).path}')
    if result.returncode==63 and 200<=code<300:
        return False  # Oversize is a mismatch, never a successful check.
    if result.returncode:
        raise RuntimeError(f'Public HTTP transport failed ({result.returncode}): {urlsplit(url).path}')
    if not 200<=code<300:
        raise RuntimeError(f'Public HTTP {code}: {urlsplit(url).path}')
    return body==expected
