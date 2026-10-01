"""Fail-closed saved PUBLIC commentary; no native/provider/runtime imports.

Presentation only. Raw rows and their pagination coordinates are unchanged.
"""
import json
import re

from urllib.parse import unquote, urlsplit, urlunsplit

# Pure, bounded display redaction; never apply the shell-command allowlist to prose.
_PREFIX = re.compile(r'(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{10,}|(?:gh[pousr]_|github_pat_|hf_|npm_|pypi-|gsk_|xox[baprs]-)[A-Za-z0-9_-]{10,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})')
_ASSIGN = re.compile(
    r'''(?i)(\b(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|credential|client[_-]?secret)["'”’]?[ \t]*[:=]\s*)("[^"]*"|'[^']*'|“[^”]*”|‘[^’]*’|[^\s,;`<>]+)''')
# Short credentials require an explicit auth header; standalone tokens must
# be long and contain credential punctuation/digits, not just an English word.
_AUTH = re.compile(
    r'''(?i)(\b(?:authorization["'”’]?[ \t]*[:=][ \t]*["'“‘]?(?:Bearer|Basic)|(?:Bearer|Basic)(?=[ \t]+[A-Za-z0-9_./+~=-]{12,})(?=[ \t]+[A-Za-z0-9_./+~=-]*[0-9_/+~=-]))[ \t]+)'''
    r'[A-Za-z0-9_./+~=-]+')
_URL = re.compile(r'''(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s<>"'`\)\]]+''')
_PEM = re.compile(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', re.S)
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')


def _redact_public(text):
    def clean_url(match):
        raw = match.group(0)
        # Bounded decoding of URL escapes only, never Unicode/prose generally.
        for _ in range(3):
            decoded = unquote(raw)
            if decoded == raw:
                break
            raw = decoded
        try:
            url = urlsplit(raw)
            if not url.netloc:
                return '[redacted URL]'
            cleaned = urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, '', ''))
            # Preserve untouched public URLs byte-for-byte.
            return cleaned if '@' in url.netloc or url.query or url.fragment else match.group(0)
        except ValueError:
            return '[redacted URL]'

    text = _URL.sub(clean_url, text)
    text = _PEM.sub('[redacted]', text)
    text = _PREFIX.sub('[redacted]', text)
    text = _JWT.sub('[redacted]', text)
    text = _AUTH.sub(lambda m: m.group(1) + '[redacted]', text)
    return _ASSIGN.sub(lambda m: m.group(1) + '[redacted]', text)

MAX_SIDECAR_BYTES = 262144
MAX_ITEMS = 128
MAX_TEXT_CHARS = 10000
MAX_NODES = 4096
_TAG = re.compile(r'<\s*(/?)\s*(think|thinking|reasoning|reasoning_scratchpad|thought)\b[^>]*(?:>|\Z)', re.I)


def _bounded(value):
    """Bound parsed input too, without serializing an arbitrarily large object."""
    stack = [(value, 0)]
    budget = MAX_SIDECAR_BYTES
    nodes = 0
    while stack:
        obj, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > 12:
            return False
        if isinstance(obj, str):
            if len(obj) > budget:
                return False
            budget -= len(obj.encode('utf-8', errors='replace'))
        elif isinstance(obj, (list, dict)):
            if len(obj) > MAX_NODES or len(stack) + len(obj) * 2 > MAX_NODES:
                return False
            children = list(obj.items()) if isinstance(obj, dict) else obj
            for child in children:
                if isinstance(obj, dict):
                    key, val = child
                    if not isinstance(key, str):
                        return False
                    stack.append((key, depth + 1))
                    stack.append((val, depth + 1))
                else:
                    stack.append((child, depth + 1))
        elif obj is not None and not isinstance(obj, (int, float, bool)):
            return False
        budget -= 1
        if budget < 0:
            return False
    return True


def _visible(text):
    # Strip nested and unterminated private blocks; join parts before scanning.
    visible = []
    end = 0
    depth = 0
    for match in _TAG.finditer(text):
        if not depth:
            visible.append(text[end:match.start()])
        if match.group(1):
            if not depth:
                return ''  # Orphan close: preceding text may be private.
            depth -= 1
        else:
            depth += 1
        end = match.end()
    if not depth:
        visible.append(text[end:])
    return _redact_public(''.join(visible))


def public_commentary_items(value):
    """Return sanitized presentation items with original zero-based item_index.

    Accept only the codex_message_items sidecar (JSON string or parsed list).
    Malformed or oversized sidecars fail closed; never use reasoning fields.
    """
    if isinstance(value, str):
        if len(value) > MAX_SIDECAR_BYTES or len(value.encode('utf-8', errors='replace')) > MAX_SIDECAR_BYTES:
            return []
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return []
    if not isinstance(value, list) or len(value) > MAX_ITEMS or not _bounded(value):
        return []
    result = []
    text_budget = MAX_TEXT_CHARS
    for index, item in enumerate(value):
        if not isinstance(item, dict) or item.get('type') != 'message' or item.get('phase') != 'commentary':
            continue
        parts = item.get('content')
        if not isinstance(parts, list):
            continue
        texts = [part['text'] for part in parts
                 if isinstance(part, dict) and part.get('type') == 'output_text'
                 and isinstance(part.get('text'), str)]
        size = sum(map(len, texts))
        if size > text_budget:
            continue
        text_budget -= size
        content = _visible(''.join(texts))
        if content:
            result.append({'role': 'assistant', 'channel': 'commentary',
                           'content': content, 'item_index': index})
    return result


def project_public_commentary(native_row_id, value):
    """Bind local presentation identities to an integer native DB row ID."""
    if type(native_row_id) is not int or native_row_id < 0:
        return []
    return [dict(item, id=f'native:{native_row_id}:commentary:{item["item_index"]}')
            for item in public_commentary_items(value)]
