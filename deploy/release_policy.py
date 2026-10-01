"""Pure fail-closed routine/sensitive classification of an entire release diff.

Mirrored exactly by the `Classify release` job in .github/workflows/production.yml.
Anything not positively on the small routine allowlist is sensitive; there is no
broad fallback. No I/O: callers supply the complete change inventory.
"""
from dataclasses import dataclass
import re

ROUTINE = 'routine'
SENSITIVE = 'sensitive'
MAX_FILES = 250
MAX_PATH = 200

# Canonical relative POSIX path: ASCII, no empty/hidden/dot segments.
CANONICAL = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*')
# Exact audited presentation-only frontend files (SEC-1). Not a basename pattern:
# ui.mjs (passkey login/recovery/account management), app.js (bootstrap and
# service-worker registration), index.html (auth markup), api/webauthn/sw,
# manifest, link rendering (markdown), secret redaction (tool-details), API calls
# (model-controls), cross-session placement, SVG and every new file stay sensitive.
FRONTEND_ROUTINE = frozenset({
    'frontend/styles.css', 'frontend/viewport.mjs', 'frontend/session-swipe.mjs',
    'frontend/disclosure-reachability.mjs', 'frontend/icons/apple-touch-icon.png',
    'frontend/icons/icon-192.png', 'frontend/icons/icon-512.png'})
TESTS = (re.compile(r'tests/test_[a-z0-9_]+\.py'),
         re.compile(r'tests/browser/[a-z0-9][a-z0-9-]*\.(?:spec|test)\.mjs'),
         re.compile(r'tests/browser/[a-z0-9_]+_fixture\.py'),
         re.compile(r'tests/fixtures/[a-z0-9][a-z0-9-]*\.json'))
DOC = re.compile(r'docs/([a-z0-9][a-z0-9-]*)\.md')
# Substring match on the doc name: deliberately over-broad (fail closed).
DOC_SENSITIVE = ('account', 'admin', 'agent', 'artifact', 'attestation', 'auth', 'backup', 'bootstrap',
                 'ci', 'credential', 'cron', 'delivery', 'deploy', 'development', 'family', 'git',
                 'hosted', 'hygiene', 'implementation', 'install', 'instruction', 'invite', 'job',
                 'member', 'migration', 'native', 'notification', 'operational', 'operations', 'operator',
                 'passkey', 'permission',
                 'policy', 'privacy', 'production', 'push', 'recovery', 'release', 'restore', 'rollback',
                 'routine', 'runtime', 'scheduler', 'secret', 'security', 'signing', 'token',
                 'webauthn', 'workflow')
MODES = frozenset({'000000', '100644'})
STATUSES = frozenset({'A', 'D', 'M', 'R'})
RAW = re.compile(r':([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) '
                 r'([ACDMRTUX])([0-9]{0,3})')


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    previous_path: str | None = None
    old_mode: str | None = None
    new_mode: str | None = None


@dataclass(frozen=True)
class Classification:
    risk: str
    reasons: tuple = ()


def path_is_routine(path):
    if not isinstance(path, str) or not path.isascii() or len(path) > MAX_PATH \
            or not CANONICAL.fullmatch(path):
        return False
    if path in FRONTEND_ROUTINE or any(pattern.fullmatch(path) for pattern in TESTS):
        return True
    match = DOC.fullmatch(path)
    return bool(match) and not any(word in match.group(1) for word in DOC_SENSITIVE)


def _change_reason(item):
    if not isinstance(item, Change):
        return 'malformed change record'
    if item.status not in STATUSES:
        return f'unsupported change status {item.status!r}: {item.path!r}'
    if item.status == 'R' and item.previous_path is None:
        return f'rename without from path: {item.path!r}'
    if item.status != 'R' and item.previous_path is not None:
        return f'unexpected from path: {item.path!r}'
    for mode in (item.old_mode, item.new_mode):
        # Cloud compare data carries no modes; the host always supplies both.
        if (item.old_mode, item.new_mode) != (None, None) and mode not in MODES:
            return f'unsupported file mode {mode!r}: {item.path!r}'
    for path in (item.path, *((item.previous_path,) if item.status == 'R' else ())):
        if not path_is_routine(path):
            return f'sensitive path: {path!r}'
    return None


def classify(changes, *, truncated=False):
    """Return ROUTINE only for a complete, bounded, non-empty, all-routine diff."""
    if truncated:
        return Classification(SENSITIVE, ('truncated diff inventory',))
    if not isinstance(changes, (list, tuple)):
        return Classification(SENSITIVE, ('malformed diff inventory',))
    if not changes:
        return Classification(SENSITIVE, ('empty diff from deployed base',))
    if len(changes) > MAX_FILES:
        return Classification(SENSITIVE, (f'overlarge diff: more than {MAX_FILES} files',))
    reasons = tuple(reason for reason in map(_change_reason, changes) if reason)
    return Classification(SENSITIVE, reasons) if reasons else Classification(ROUTINE)


def parse_git_raw(output):
    """Strictly parse `git diff --raw -z --no-abbrev` output; reject anything else."""
    if not isinstance(output, str):
        raise ValueError('Git raw diff must be text')
    if output == '':
        return []
    if not output.endswith('\0'):
        raise ValueError('Git raw diff is not NUL terminated')
    fields = output[:-1].split('\0')
    changes = []
    index = 0
    while index < len(fields):
        match = RAW.fullmatch(fields[index])
        if not match:
            raise ValueError('Malformed Git raw diff record')
        old_mode, new_mode, _, _, status, score = match.groups()
        if score and status not in {'R', 'C'}:
            raise ValueError('Unexpected Git similarity score')
        count = 2 if status in {'R', 'C'} else 1
        paths = fields[index + 1:index + 1 + count]
        if len(paths) != count or any(not path or path.startswith(':') for path in paths):
            raise ValueError('Malformed Git raw diff paths')
        previous, path = (paths[0], paths[1]) if count == 2 else (None, paths[0])
        changes.append(Change(status, path, previous, old_mode, new_mode))
        index += 1 + count
    return changes
