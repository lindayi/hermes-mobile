"""Offline, explicit historical async-notification handoff, before native restart.

Operator API (no CLI, discovery, SDK initialization, or source delivery changes)::

    manifest = migrate_records(state_dir, home, records, provenance=reviewed)

``records`` is an ordered list of exactly ``{event: dict, result: dict,
source: str}``: original event and FULL result, including held/dropped records.
Only async_delegation events with explicit delegation_id are accepted.
``reviewed`` is a separately approved manifest::

    {
        "version": 1, "profile": "default",
        "home": "/canonical/native/home", "state_dir": "/canonical/private/state",
        "owner_user_id": "<reviewed owner ID>", "expected_count": 80,
        "sources": {"<reviewed source label incl. native PID>": "<sha256>"},
        "records": [
            {"event_id": "async:<delegation_id>", "payload_sha256": "<sha256>",
             "result_sha256": "<sha256>", "source": "<reviewed source label>"}
        ]
    }

The ordered allowlist must match exactly, with no duplicate IDs. Payload hashes
use native canonical(event without the SDK's transient ``restored`` key); result
hashes use canonical(full result), both SHA256 of UTF-8. Source hashes identify
independently verified exact source bytes/snapshots, NOT arbitrary filenames.

REQUIRED CALLER PREFLIGHT: verify source bytes against approved hashes and verify
which native process each source came from; bind the 49 current + 31 archived
records to the separately reviewed allowlist. Do not manufacture approval by
hashing an unreviewed archive. This helper intentionally never opens archives or
reads async_delegations. It reads only ownership/lineage databases at explicit
home/state_dir paths and writes only the private native-notifications.sqlite.
Keep admission closed and serialize outbox writers through this operation and
restart. This is not a live-source snapshotter, restore executor, or lock manager.

All input/count/binding/ownership checks precede capture. Each component capture
commits independently. A later failure raises MigrationIncomplete with a payload-
free conservative partial ``manifest`` (a commit whose response was lost may not
be counted). Keep the old process/recovery evidence; do NOT restart on failure.
Exact retry is idempotent; conflicting existing data/provenance is never replaced.
The component retains payload/result conflict evidence and blocks such claims.

``complete`` means every reviewed row was handed off, NOT delivered. Historical
rows remain pending until the existing bridge receipt/ACK path. No helper path
claims SDK delivery, touches a registry/queue, starts an adapter/model, or ACKs.
Returned manifests contain IDs/digests/source hashes only, never event/results;
nothing is printed. Existing acknowledged rows are not reset on retry.
"""
import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path

from backend.native_notifications import NotificationCapture, NotificationOutbox, OwnerRoute, canonical, readonly


class MigrationIncomplete(RuntimeError):
    """Some rows may be durable; never restart on this incomplete manifest."""
    def __init__(self, manifest):
        self.manifest = manifest
        super().__init__('historical notification handoff incomplete; inspect manifest')


def _prepare(records, provenance):
    # The caller supplies a separately reviewed allowlist, not a discovered file.
    try:
        if not isinstance(records, list) or not isinstance(provenance, dict):
            raise ValueError
        records, provenance = json.loads(canonical([records, provenance]))
        count, approved, sources = provenance['expected_count'], provenance['records'], provenance['sources']
        if (type(count) is not int or count <= 0 or not isinstance(approved, list)
                or len(records) != count or len(approved) != count
                or not isinstance(sources, dict) or not sources):
            raise ValueError
        if any(not name or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)
               for name, sha in sources.items()):
            raise ValueError
        seen = set()
        for record, entry in zip(records, approved):
            if (set(record) != {'event', 'result', 'source'}
                    or set(entry) != {'event_id', 'payload_sha256', 'result_sha256', 'source'}
                    or not isinstance(record['event'], dict) or not isinstance(record['result'], dict)):
                raise ValueError
            event = record['event']
            ident = event.get('delegation_id')
            if event.get('type') != 'async_delegation' or not isinstance(ident, str) or not ident:
                raise ValueError
            key = 'async:' + ident
            payload = {k: v for k, v in event.items() if k != 'restored'}
            if (key in seen or entry['event_id'] != key
                    or entry['payload_sha256'] != hashlib.sha256(canonical(payload).encode()).hexdigest()
                    or entry['result_sha256'] != hashlib.sha256(canonical(record['result']).encode()).hexdigest()
                    or record['source'] != entry['source'] or record['source'] not in sources):
                raise ValueError
            seen.add(key)
        return records, provenance
    except (ValueError, TypeError, KeyError):
        raise ValueError('invalid reviewed notification batch') from None


def migrate_records(state_dir, home, records, *, provenance):
    """Preseed reviewed records before native restart; delivery still requires ACK."""
    records, provenance = _prepare(records, provenance)
    state_dir, home = Path(state_dir).resolve(), Path(home).resolve()
    if (type(provenance.get('version')) is not int or provenance['version'] != 1
            or provenance.get('profile') != 'default' or provenance.get('home') != str(home)
            or provenance.get('state_dir') != str(state_dir) or not provenance.get('owner_user_id')):
        raise ValueError('reviewed owner binding mismatch')
    # Parents are bound canonically above; child databases must not alias other
    # paths or each other. Check before ownership reads or constructor writes.
    # The caller holds exclusive admission and serializes writers (no TOCTOU lock).
    outbox_path = state_dir / 'native-notifications.sqlite'
    identities = set()
    for path in (home / 'state.db', state_dir / 'auth.sqlite', state_dir / 'runs.sqlite', outbox_path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path == outbox_path:
                continue  # Only a genuinely absent outbox may be created.
            raise ValueError('reviewed database binding unavailable') from None
        except OSError:
            raise ValueError('reviewed database binding unavailable') from None
        identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or identity in identities:
            raise ValueError('reviewed database binding must be regular and unaliased')
        identities.add(identity)
    try:
        with readonly(state_dir / 'auth.sqlite') as db:
            owners = db.execute("SELECT id FROM users WHERE role='owner' AND profile='default' AND status='ready'").fetchall()
            if len(owners) != 1 or owners[0]['id'] != provenance['owner_user_id']:
                raise ValueError('reviewed owner binding mismatch')
    except sqlite3.Error:
        raise ValueError('reviewed owner binding unavailable') from None
    route = OwnerRoute(home, state_dir)
    if any(route(record['event']) != 'owned' for record in records):
        raise ValueError('positive owner proof required for every reviewed record')
    def require_owned(event):
        if route(event) != 'owned':
            raise ValueError('positive owner proof lost')
        return 'owned'

    box = NotificationOutbox(outbox_path)
    # Constructor only persists the component's home binding; never install hooks.
    NotificationCapture(box, home, route)
    entries = []
    manifest = dict(version=1, complete=False, expected_count=provenance['expected_count'],
                    captured_count=0, records=entries, sources=provenance['sources'])
    for record, approved in zip(records, provenance['records']):
        entry = {**approved, 'source_sha256': provenance['sources'][record['source']]}
        evidence = canonical({**{k: provenance[k] for k in ('version', 'profile', 'home', 'state_dir', 'owner_user_id')}, **entry})
        try:
            with box.transaction() as db:
                if db.execute('SELECT 1 FROM notification_conflicts WHERE event_id=?', (entry['event_id'],)).fetchone():
                    raise ValueError('unresolved notification conflict')
            prior = box.record(entry['event_id'])
            if prior and (prior['historical'] != 1 or prior['provenance'] != evidence
                          or prior['result'] is None or prior['state'] not in ('pending', 'delivered')):
                raise ValueError('existing notification metadata conflict')
            box.import_records([dict(event=record['event'], result=record['result'], provenance=evidence)], classify=require_owned)
        except Exception:
            manifest['failed_event_id'] = entry['event_id']
            raise MigrationIncomplete(manifest) from None
        entries.append(entry)
        manifest['captured_count'] = len(entries)
    manifest['complete'] = True
    return manifest
