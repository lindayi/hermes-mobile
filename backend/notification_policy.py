"""Fail-closed push classification and preview policy; no content retrieval."""
import re
CATEGORIES = ('completion', 'approval', 'attention', 'scheduled', 'operational')
ALL_CATEGORIES = (*CATEGORIES, 'background', 'test', 'internal')


def default_preferences():
    return {'enabled': True, 'categories': dict.fromkeys(CATEGORIES, True), 'hide_details': False, 'revision': 0}


def validate_preferences(payload):
    if (not isinstance(payload, dict) or set(payload) != {'enabled', 'categories', 'hide_details', 'revision'}
            or type(payload['revision']) is not int or not 0 <= payload['revision'] < 2**53
            or type(payload['enabled']) is not bool or type(payload['hide_details']) is not bool
            or not isinstance(payload['categories'], dict) or set(payload['categories']) != set(CATEGORIES)
            or any(type(value) is not bool for value in payload['categories'].values())):
        raise ValueError('Invalid push preferences')
    return {'enabled': payload['enabled'], 'categories': dict(payload['categories']),
            'hide_details': payload['hide_details'], 'revision': payload['revision']}


def validate_presence(payload):
    if (not isinstance(payload, dict) or set(payload) != {'client_id','session_id','visible','sequence'}
            or not isinstance(payload['client_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', payload['client_id'])
            or type(payload['visible']) is not bool or type(payload['sequence']) is not int
            or not 0 <= payload['sequence'] < 2**53
            or (payload['session_id'] is not None and (not isinstance(payload['session_id'], str)
                or not 1 <= len(payload['session_id']) <= 256
                or any(ord(c) < 33 for c in payload['session_id'])))):
        raise ValueError('Invalid presence')
    return dict(payload)


GENERIC_PREVIEW = {'title': 'Hermes', 'body': 'You have a new notification.'}
_SENSITIVE = re.compile(
    r'(?i)\b(?:api[\s_-]*key|(?:access|refresh)[\s_-]*token|token|secret|password|passwd|'
    r'authorization|credentials?|bearer|private[\s_-]*key|'
    r'(?:auth(?:entication)?|verification|recovery|backup|one[\s-]*time|security|login|sign[\s-]*in)[\s_-]*codes?|otp|pin)\b'
    r'|-----BEGIN[\s\S]{0,60}PRIVATE KEY|\b(?:ftps?|mailto|tel|data|javascript|file|urn)\s*:'
    r'|traceback|\b\w*(?:Exception|Error)\b|tool[\s_-]+(?:output|result)|/home/|/tmp/'
    r'|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}'
)


# Only balanced Markdown labels with whitespace/end after their closing marker
# are prose. Do not exempt opaque URI punctuation (e.g. custom:**).
_PLAIN_LABEL = re.compile(r'(?i)(\*\*|__)([a-z][a-z0-9+.-]*:)\1(?=\s|$)')
_URI = re.compile(r'(?i)\b[a-z][a-z0-9+.-]*:\S')
_REFERENCE_LINK = re.compile(r'(?i)\[[^\]\n]*\]\(\s*(?:https?://|www\.)[^\s)]*\s*\)')
_REFERENCE_URL = re.compile(r'(?i)\b(?:https?://|www\.)[^\s<>()\[\]{}]+')
_RESIDUAL_REFERENCE = re.compile(r'(?i)\bhttps?\s*:|www\.|\]\s*\(')


def safe_preview(title, body):
    """Conservative generic fallback, not a claim to detect all private content.

    Inspect BOTH complete bounded inputs before truncation: a secret label beyond
    the displayed boundary must still suppress the entire preview. Never log text.
    """
    import unicodedata
    if any(not isinstance(text, str) or len(text) > 100000 for text in (title, body)):
        return dict(GENERIC_PREVIEW)
    clean = []
    for text in (title, body):
        text = unicodedata.normalize('NFKC', text)
        if (len(text) > 100000 or _SENSITIVE.search(text) or any(c in '<>`' for c in text)
                or any(unicodedata.category(c).startswith('C') and c not in '\n\r\t' for c in text)):
            return dict(GENERIC_PREVIEW)
        # Inspect secrets in the COMPLETE input first. Public references are then
        # removed, never followed or included in the lock-screen payload.
        text = _REFERENCE_URL.sub('', _REFERENCE_LINK.sub('', text))
        if (_RESIDUAL_REFERENCE.search(text)
                or _URI.search(_PLAIN_LABEL.sub(r'\2 ', text))):
            return dict(GENERIC_PREVIEW)
        # Plain text only; remove non-link Markdown decoration and collapse lines.
        clean.append(' '.join(re.sub(r'[*_#~]+', '', text).split()))
        if (not clean[-1] or _SENSITIVE.search(clean[-1])
                or _RESIDUAL_REFERENCE.search(clean[-1]) or _URI.search(clean[-1])):
            return dict(GENERIC_PREVIEW)
    return {'title': clean[0][:100] or 'Hermes',
            'body': clean[1][:180] or GENERIC_PREVIEW['body']}
