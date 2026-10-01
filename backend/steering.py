"""Durable, owner-bound steering control records (not run outcomes)."""
from contextlib import closing
import json
import sqlite3
import time
import uuid


class SteeringJournal:
    def __init__(self, journal):
        self.journal = journal
        with closing(journal.connect()) as c, c:
            c.execute('''CREATE TABLE IF NOT EXISTS steering_attempts(
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, profile TEXT NOT NULL,
                run_id TEXT NOT NULL, upstream_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, input TEXT NOT NULL,
                status TEXT NOT NULL, steer_id TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, UNIQUE(user_id,idempotency_key))''')
            c.execute('CREATE TABLE IF NOT EXISTS steering_evidence(run_id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @staticmethod
    def blocks_deletion(connection, run):
        """Classify history only; caller retains active-run/worker transaction guards.

        Native terminal closure changes accepted receipts to ``unknown`` without
        revoking acceptance. Only an exact, bound positive receipt excuses that
        history; it never proves delivery and nothing here updates the journal.
        """
        try:
            rows = connection.execute('''SELECT user_id,profile,run_id,upstream_id,
                idempotency_key,status,steer_id FROM steering_attempts WHERE run_id=?''',
                (run['id'],)).fetchall()
        except sqlite3.DatabaseError:
            return True
        for row in rows:
            if row['status'] in ('accepted_unconfirmed', 'not_delivered'):
                continue
            if row['status'] != 'unknown' or run['status'] not in ('completed', 'failed', 'cancelled'):
                return True
            if (any(not isinstance(row[field], str) or not row[field] or row[field] != run[field]
                    for field in ('user_id', 'profile', 'upstream_id'))
                    or not isinstance(run['id'], str) or not run['id']
                    or not isinstance(row['steer_id'], str) or not row['steer_id']
                    or row['steer_id'] != row['idempotency_key']
                    or sum(other['idempotency_key'] == row['idempotency_key']
                           or other['steer_id'] == row['steer_id'] for other in rows) != 1):
                return True
            try:
                evidence = connection.execute('SELECT data FROM steering_evidence WHERE run_id=?',
                                              (run['id'],)).fetchall()
            except sqlite3.DatabaseError:
                return True
            if len(evidence) != 1:
                return True
            def unique_object(pairs):
                obj = dict(pairs)
                if len(obj) != len(pairs):
                    raise ValueError('Duplicate evidence field')
                return obj
            try:
                data = json.loads(evidence[0]['data'], object_pairs_hook=unique_object)
            except (ValueError, TypeError, RecursionError):
                return True
            if (not isinstance(data, dict) or set(data) - {'steer_receipts', 'pending_steer'}
                    or not isinstance(data.get('steer_receipts'), list)
                    or ('pending_steer' in data and not isinstance(data['pending_steer'], str))):
                return True
            receipts = {}
            for receipt in data['steer_receipts']:
                if (not isinstance(receipt, dict)
                        or set(receipt) != {'run_id', 'steer_id', 'status', 'accepted'}
                        or receipt['run_id'] != run['upstream_id']
                        or not isinstance(receipt['steer_id'], str) or not receipt['steer_id']
                        or receipt['steer_id'] in receipts
                        or type(receipt['accepted']) is not bool
                        or receipt['status'] not in ('unknown', 'accepted_unconfirmed', 'not_delivered')
                        or (receipt['status'] == 'accepted_unconfirmed' and not receipt['accepted'])
                        or (receipt['status'] == 'not_delivered' and receipt['accepted'])):
                    return True
                receipts[receipt['steer_id']] = receipt
            receipt = receipts.get(row['steer_id'])
            if not receipt or receipt['status'] != 'unknown' or receipt['accepted'] is not True:
                return True
        return False

    @staticmethod
    def view(row):
        return {k: row[k] for k in ('id', 'run_id', 'idempotency_key', 'input',
                                    'status', 'steer_id', 'created_at', 'updated_at')}

    def _event(self, c, row):
        c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                  (row['run_id'], 'steering', json.dumps(self.view(row)), time.time()))

    def _existing(self, c, user, run, body):
        from .runs import RunConflict
        row = c.execute('SELECT * FROM steering_attempts WHERE user_id=? AND idempotency_key=?',
                        (user['id'], body['idempotency_key'])).fetchone()
        if row is None:
            return None
        if (row['profile'], row['run_id'], row['upstream_id'], row['input']) != (
                user['profile'], run['id'], run['upstream_id'], body['input']):
            raise RunConflict('Steering idempotency key already used for another request')
        return self.view(row)

    def existing(self, user, run, body):
        with closing(self.journal.connect()) as c:
            return self._existing(c, user, run, body)

    def claim(self, user, run, body):
        now = time.time()
        with closing(self.journal.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            existing = self._existing(c, user, run, body)
            if existing is not None:
                return existing, False
            from .runs import RunConflict
            current = c.execute('SELECT * FROM runs WHERE id=? AND user_id=? AND profile=?',
                                (run['id'], user['id'], user['profile'])).fetchone()
            if (not current or current['status'] != 'running' or not current['upstream_id']
                    or current['upstream_id'] != run['upstream_id']):
                raise RunConflict('Run is not accepting steering')
            aid = uuid.uuid4().hex
            c.execute('INSERT INTO steering_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (aid, user['id'], user['profile'], run['id'], run['upstream_id'],
                       body['idempotency_key'], body['input'], 'sending', None, now, now))
            row = c.execute('SELECT * FROM steering_attempts WHERE id=?', (aid,)).fetchone()
            self._event(c, row)
            return self.view(row), True

    def finish(self, aid, status, steer_id=None):
        with closing(self.journal.connect()) as c, c:
            changed = c.execute("UPDATE steering_attempts SET status=?,steer_id=?,updated_at=? WHERE id=? AND status='sending'",
                                (status, steer_id, time.time(), aid)).rowcount
            row = c.execute('SELECT * FROM steering_attempts WHERE id=?', (aid,)).fetchone()
            if changed:
                self._event(c, row)
            return self.view(row)

    def recover(self):
        # Only called at process startup, never while another live dispatcher owns claims.
        with closing(self.journal.connect()) as c, c:
            rows = c.execute("UPDATE steering_attempts SET status='unknown',updated_at=? WHERE status='sending' RETURNING *",
                             (time.time(),)).fetchall()
            for row in rows:
                self._event(c, row)

    def attempts(self, user, rid):
        with closing(self.journal.connect()) as c:
            rows = c.execute('SELECT * FROM steering_attempts WHERE user_id=? AND profile=? AND run_id=? ORDER BY rowid',
                             (user['id'], user['profile'], rid)).fetchall()
            return [self.view(row) for row in rows]

    def evidence(self, user, rid):
        self.journal.get(user['id'], rid)
        with closing(self.journal.connect()) as c:
            row = c.execute('SELECT data FROM steering_evidence WHERE run_id=?', (rid,)).fetchone()
            return json.loads(row['data']) if row else {}

    def observe(self, user, run, result):
        """Correlated native control evidence, never inference of delivery."""
        if not isinstance(result, dict) or result.get('run_id') != run['upstream_id']:
            return
        with closing(self.journal.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            # A deletion may commit after observation's last ownership check.
            # Fence all evidence, receipt updates and events under this writer lock.
            self.journal._require_run(c, user['id'], run['id'])
            prior = c.execute('SELECT data FROM steering_evidence WHERE run_id=?', (run['id'],)).fetchone()
            data = json.loads(prior['data']) if prior else {}
            old = json.dumps(data, sort_keys=True)
            pending = result.get('pending_steer')
            if isinstance(pending, str) and len(pending) <= 1048576:
                data['pending_steer'] = pending
            receipts = result.get('steer_receipts')
            if isinstance(receipts, list) and len(receipts) <= 256:
                saved = {r['steer_id']: r for r in data.get('steer_receipts', [])}
                for receipt in receipts:
                    if (not isinstance(receipt, dict) or receipt.get('run_id') != run['upstream_id']
                            or not isinstance(receipt.get('steer_id'), str)
                            or receipt.get('idempotency_key', receipt['steer_id']) != receipt['steer_id']
                            or receipt.get('status') not in ('accepted_unconfirmed', 'not_delivered', 'unknown')
                            or type(receipt.get('accepted')) is not bool
                            or (receipt['status'] == 'accepted_unconfirmed' and not receipt['accepted'])
                            or (receipt['status'] == 'not_delivered' and receipt['accepted'])):
                        continue
                    row = c.execute("""SELECT * FROM steering_attempts WHERE user_id=? AND profile=?
                        AND run_id=? AND upstream_id=? AND idempotency_key=?""",
                        (user['id'], user['profile'], run['id'], run['upstream_id'], receipt['steer_id'])).fetchone()
                    if row is None:
                        continue
                    # A confirmed negative receipt is final. Missing/older evidence cannot undo it.
                    if row['status'] == 'not_delivered' and receipt['status'] != 'not_delivered':
                        continue
                    previous = saved.get(receipt['steer_id'], {})
                    if previous.get('status') == 'unknown' and receipt['status'] == 'accepted_unconfirmed':
                        continue
                    clean = {k: receipt[k] for k in ('run_id', 'steer_id', 'status', 'accepted')}
                    saved[receipt['steer_id']] = clean
                    if row['status'] != receipt['status'] or row['steer_id'] != receipt['steer_id']:
                        c.execute('UPDATE steering_attempts SET status=?,steer_id=?,updated_at=? WHERE id=?',
                                  (receipt['status'], receipt['steer_id'], time.time(), row['id']))
                        updated = c.execute('SELECT * FROM steering_attempts WHERE id=?', (row['id'],)).fetchone()
                        self._event(c, updated)
                data['steer_receipts'] = list(saved.values())
            encoded = json.dumps(data, sort_keys=True)
            if encoded != old:
                c.execute('INSERT OR REPLACE INTO steering_evidence VALUES(?,?)', (run['id'], encoded))
                c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                          (run['id'], 'steering', encoded, time.time()))
