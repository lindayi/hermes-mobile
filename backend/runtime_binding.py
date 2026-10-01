"""Trusted activation proof for a BFF's loaded native home and gateway mapping.

Only the console verifier writes proofs. No raw listener credential is persisted
in the proof or returned to callers. Old session-id-only markers fail closed.
"""
import hashlib
import hmac
import json
from pathlib import Path


def activation_record(member_id, profile, home, url, token, smoke_session):
    return json.dumps({'version': 1, 'binding': fingerprint(member_id, profile, home, url, token),
                       'smoke_session': smoke_session}, sort_keys=True)


def fingerprint(member_id, profile, home, url, token):
    payload = [member_id, profile, str(Path(home).resolve()), url.rstrip('/'), token]
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


class RuntimeBinding:
    def __init__(self, store, homes, gateways):
        self.store = store
        # Snapshot exactly what create_app loaded, never later on-disk settings.
        self.loaded = {profile: (home, gateways[profile]['url'], gateways[profile]['token'])
                       for profile, home in homes.items() if profile in gateways}

    def matches(self, user):
        if user['role'] == 'owner' and user['profile'] == 'default':
            return True
        profile = user['profile']
        if (user['role'] != 'member' or profile != 'member_'+user['id']
                or user['status'] != 'ready' or profile not in self.loaded):
            return False
        with self.store.transaction() as db:
            row = db.execute('''SELECT s.value FROM settings s JOIN users u
                ON s.key='runtime_activation:' || u.id
                WHERE u.id=? AND u.profile=? AND u.role='member' AND u.status='ready' ''',
                (user['id'], profile)).fetchone()
        if not row:
            return False
        try:
            proof = json.loads(row['value'])
        except (ValueError, TypeError):
            return False
        expected = fingerprint(user['id'], profile, *self.loaded[profile])
        return (isinstance(proof, dict) and proof.get('version') == 1
                and isinstance(proof.get('binding'), str) and proof['binding'].isascii()
                and hmac.compare_digest(proof['binding'], expected))
