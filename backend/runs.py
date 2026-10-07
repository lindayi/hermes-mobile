"""Durable app run journal. Local admission is NOT a cross-channel gateway lock."""
from contextlib import closing
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path


class RunConflict(ValueError):
    pass


NATIVE_RUN_LOST_ERROR = 'Native run no longer exists; automatic retry is disabled'


class RunJournal:
    @staticmethod
    def _public_replay(row):
        """Same public activity as streaming, never raw arguments or reasoning."""
        data = json.loads(row['data'])
        if not isinstance(data, dict):
            return None
        if row['name'] == 'steering':
            if (data.get('status') not in ('accepted_unconfirmed', 'not_delivered', 'unknown')
                    or not all(isinstance(data.get(key), str) and data[key]
                               for key in ('id', 'run_id', 'idempotency_key', 'input'))
                    or data['run_id'] != row['run_id']
                    or (data.get('steer_id') is not None and not isinstance(data['steer_id'], str))
                    or any(type(data.get(key)) not in (int, float)
                           for key in ('created_at', 'updated_at'))):
                return None
            allowed = ('id', 'run_id', 'idempotency_key', 'input', 'status',
                       'steer_id', 'created_at', 'updated_at')
            data = {key: data[key] for key in allowed if key in data}
        elif row['name'] == 'tool':
            allowed = ('event', 'tool', 'name', 'tool_name', 'run_id', 'id', 'call_id',
                       'tool_call_id', 'toolCallId', 'status', 'is_error', 'error', 'duration', 'summary')
            data = {key: data[key] for key in allowed if key in data}
        elif row['name'] == 'clarification':
            if (data.get('run_id') != row['run_id']
                    or not isinstance(data.get('question_id'), str)
                    or len(data['question_id']) != 32
                    or any(c not in '0123456789abcdef' for c in data['question_id'])
                    or not isinstance(data.get('session_id'), str)
                    or not isinstance(data.get('question'), str) or not data['question'].strip()
                    or (data.get('choices') is not None and (
                        not isinstance(data['choices'], list) or len(data['choices']) > 4
                        or any(not isinstance(value, str) or not value.strip()
                               for value in data['choices'])))
                    or type(data.get('multi_select')) is not bool
                    or data.get('status') not in (
                        'pending', 'sending', 'answered', 'cancelled', 'expired', 'unknown')
                    or type(data.get('created_at')) not in (int, float)
                    or type(data.get('updated_at')) not in (int, float)):
                return None
            allowed = ('question_id', 'run_id', 'session_id', 'question', 'choices',
                       'multi_select', 'status', 'answer', 'other', 'created_at', 'updated_at')
            data = {key: data[key] for key in allowed if key in data}
        else:
            if (data.get('channel') in ('analysis', 'reasoning')
                    or data.get('phase') in ('analysis', 'reasoning')
                    or any(key in data for key in ('analysis', 'reasoning', 'reasoning_content'))):
                return None
            text = data.get('text', data.get('delta'))
            if not isinstance(text, str):
                return None
            data = dict(text=text, **({'id': data['id']} if isinstance(data.get('id'), str) else {}))
        result = dict(id=row['id'], name=row['name'], data=data)
        if row['name'] in ('tool', 'commentary', 'delta', 'clarification'):
            result['observed_at'] = row['created_at']
        return result

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as c, c:
            c.executescript('''CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, profile TEXT NOT NULL,
              session_id TEXT NOT NULL, input TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              status TEXT NOT NULL, output TEXT, error TEXT, upstream_id TEXT,
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              UNIQUE(user_id,idempotency_key));
              CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,name TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS run_history_anchors(
              run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
              canonical_session_id TEXT NOT NULL, message_id INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS run_selections(run_id TEXT PRIMARY KEY, selection TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS session_deletion_operations(
              operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, profile TEXT NOT NULL,
              session_id TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS session_deletions(
              user_id TEXT NOT NULL, profile TEXT NOT NULL, session_id TEXT NOT NULL,
              operation_id TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
              PRIMARY KEY(user_id,profile,session_id));
              CREATE TABLE IF NOT EXISTS run_stop_intents(run_id TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS deployment_gate(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT NOT NULL);''')
        self.path.chmod(0o600)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def set_deployment_gate(self, owner):
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM deployment_gate').fetchone():
                raise RunConflict('A deployment is already in progress')
            c.execute('INSERT INTO deployment_gate VALUES(1,?)', (owner,))

    def clear_deployment_gate(self, owner):
        with closing(self.connect()) as c, c:
            c.execute('DELETE FROM deployment_gate WHERE owner=?', (owner,))

    @staticmethod
    def _require_session(c, user_id, profile, session_id):
        if c.execute('SELECT 1 FROM session_deletions WHERE user_id=? AND profile=? AND session_id=?',
                     (user_id, profile, session_id)).fetchone():
            raise RunConflict('Session deletion is pending or completed; automatic retry is disabled')

    @staticmethod
    def _require_deletion_admission(c, user_id, profile):
        if c.execute("SELECT 1 FROM session_deletion_operations WHERE user_id=? AND profile=? AND state IN ('prepared','native_unknown')",
                     (user_id, profile)).fetchone():
            raise RunConflict('Profile has an unresolved session deletion; reconcile before starting work')

    def require_session(self, user_id, profile, session_id):
        with closing(self.connect()) as c:
            self._require_session(c, user_id, profile, session_id)

    @staticmethod
    def _related_runs(c, profile, session_id):
        rows = c.execute('''SELECT r.*, a.session_id AS anchor_session_id,
            a.canonical_session_id FROM runs r LEFT JOIN run_history_anchors a ON a.run_id=r.id
            WHERE r.profile=?''', (profile,)).fetchall()
        aliases, related = {session_id}, {}
        while True:
            before = len(aliases)
            for row in rows:
                ids = {row['session_id'], row['anchor_session_id'], row['canonical_session_id']} - {None}
                if aliases & ids:
                    aliases.update(ids)
                    related[row['id']] = row
            if len(aliases) == before:
                return aliases, list(related.values())

    @staticmethod
    def _expire_pending_approvals(c, user_id, profile, *, run_id=None, approval_id=None, request_id=None):
        """Caller holds the writer lock; retain grants as non-executable audit rows."""
        now = time.time()
        if type(now) not in (int, float) or not math.isfinite(now) or now <= 0:
            return []
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='orchestration_approvals'").fetchone():
            return []
        rows = c.execute('''SELECT a.id,a.expires_at FROM orchestration_approvals a
            JOIN runs r ON r.id=a.run_id WHERE r.user_id=? AND r.profile=?
            AND r.status IN ('completed','failed','cancelled') AND a.status='pending'
            AND (? IS NULL OR r.id=?) AND (? IS NULL OR a.id=?)
            AND (? IS NULL OR a.request_id=?) ORDER BY a.id''',
            (user_id, profile, run_id, run_id, approval_id, approval_id, request_id, request_id)).fetchall()
        expired = []
        for row in rows:
            expiry = row['expires_at']
            # SQLite's REAL affinity still permits text/blob corruption. Never
            # coerce it or infer a sending/unknown decision's outcome from time.
            if type(expiry) not in (int, float) or not math.isfinite(expiry) or not 0 < expiry <= now:
                continue
            if c.execute("UPDATE orchestration_approvals SET status='expired' WHERE id=? AND status='pending'",
                         (row['id'],)).rowcount:
                expired.append(row['id'])
        return expired

    def reconcile_expired_approvals(self, user_id, profile, *, run_id=None, approval_id=None, request_id=None):
        """Expire owned terminal-run pending grants; return changed approval IDs.

        Optional identities are exact, conjunctive filters for operator repair.
        No native decisions, run transitions, notifications, or deletion claims.
        """
        identities = (user_id, profile, *(v for v in (run_id, approval_id, request_id) if v is not None))
        if any(not isinstance(v, str) or not v.strip() for v in identities):
            raise ValueError('Approval reconciliation identities must be non-empty strings')
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            return self._expire_pending_approvals(c, user_id, profile, run_id=run_id,
                                                 approval_id=approval_id, request_id=request_id)

    def claim_deletion(self, user_id, profile, session_id, *, busy_run_ids=()):
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM deployment_gate').fetchone():
                raise RunConflict('Bridge deployment in progress; retry later')
            self._require_deletion_admission(c, user_id, profile)
            aliases, _ = self._related_runs(c, profile, session_id)
            # Native proves delegate membership only inside its guarded mutation.
            # Until then no local queued/unknown work may race that target set.
            profile_runs = c.execute('SELECT * FROM runs WHERE profile=?', (profile,)).fetchall()
            for alias in aliases:
                self._require_session(c, user_id, profile, alias)
            self._expire_pending_approvals(c, user_id, profile)
            if any(row['status'] not in ('completed', 'failed', 'cancelled') for row in profile_runs):
                raise RunConflict('Session has active or unresolved related work')
            tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for run in profile_runs:
                if run['id'] in busy_run_ids:
                    raise RunConflict('Session has active or unresolved local workers')
                if 'steering_attempts' in tables:
                    from .steering import SteeringJournal
                    if SteeringJournal.blocks_deletion(c, run):
                        raise RunConflict('Session has unresolved steering')
                if 'orchestration_approvals' in tables and c.execute(
                        "SELECT 1 FROM orchestration_approvals WHERE run_id=? AND status IN ('pending','sending','unknown')",
                        (run['id'],)).fetchone():
                    raise RunConflict('Session has unresolved approval work')
            operation_id = uuid.uuid4().hex
            c.execute('INSERT INTO session_deletion_operations VALUES(?,?,?,?,?,?)',
                      (operation_id, user_id, profile, session_id, 'prepared', time.time()))
            c.executemany('INSERT INTO session_deletions VALUES(?,?,?,?,?,?)',
                          [(user_id, profile, alias, operation_id, 'prepared', time.time()) for alias in aliases])
            return dict(operation_id=operation_id, state='prepared')

    @staticmethod
    def _attachment_ids(c, run_id):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attachments'").fetchone():
            return []
        return [row[0] for row in c.execute(
            "SELECT id FROM attachments WHERE run_id=? AND state IN ('bound','expired','releasing') ORDER BY position,id",
            (run_id,))]

    def submit(self, user_id, profile, session_id, text, key, *, history_anchor=None, selection=None,
               conversation_roots=None, attachment_ids=(), attachment_store=None):
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM deployment_gate').fetchone():
                raise RunConflict('Bridge deployment in progress; retry later')
            self._require_deletion_admission(c, user_id, profile)
            self._require_session(c, user_id, profile, session_id)
            row = c.execute('SELECT * FROM runs WHERE user_id=? AND idempotency_key=?', (user_id,key)).fetchone()
            if row:
                self._require_session(c, user_id, row['profile'], row['session_id'])
                if (row['profile'],row['session_id'],row['input']) != (profile,session_id,text):
                    raise RunConflict('Idempotency key already used for another request')
                saved=c.execute('SELECT selection FROM run_selections WHERE run_id=?',(row['id'],)).fetchone()
                if (json.loads(saved[0]) if saved else None)!=selection:
                    raise RunConflict('Idempotency key already used for another selection')
                saved_attachments = self._attachment_ids(c, row['id'])
                if saved_attachments != list(attachment_ids):
                    raise RunConflict('Idempotency key already used for other photo attachments')
                extra = {'selection':selection} if selection is not None else {}
                if saved_attachments:
                    extra['attachment_ids'] = saved_attachments
                return dict(row, **extra), False
            if c.execute("SELECT 1 FROM runs WHERE profile=? AND session_id=? AND status IN ('queued','running','stopping','waiting_for_approval','waiting_for_clarification','unknown')", (profile,session_id)).fetchone():
                raise RunConflict('Session has an active or unresolved run')
            # Match the dedicated native listener's bounded capacity. Unknown
            # dispatch still consumes a slot: absence of a receipt is not proof
            # that execution never started. Idempotent replay above consumes none.
            if c.execute("SELECT COUNT(*) FROM runs WHERE profile=? AND status NOT IN ('completed','failed','cancelled')", (profile,)).fetchone()[0] >= 4:
                raise RunConflict('Profile run capacity reached (4 active or unresolved runs)')
            # Resolve canonical/legacy aliases while holding the same writer
            # lock as admission. A per-request string check alone is not a lock
            # on the native conversation (nor may changing user bypass it).
            anchor = history_anchor() if history_anchor is not None else None
            identities = {session_id}
            if anchor is not None:
                identities.update((anchor['session_id'], anchor['canonical_session_id']))
            for identity in identities:
                aliases, related = self._related_runs(c, profile, identity)
                for alias in aliases:
                    self._require_session(c, user_id, profile, alias)
                if any(row['status'] not in ('completed', 'failed', 'cancelled') for row in related):
                    raise RunConflict('Session has an active or unresolved run')
            if conversation_roots is not None:
                # Old anchors are immutable history boundaries, not current
                # conversation identity. Compression can rotate after admission
                # without creating any journal edge to its new native tip.
                active = c.execute('''SELECT r.session_id,a.session_id AS anchor_session_id,
                    a.canonical_session_id FROM runs r
                    LEFT JOIN run_history_anchors a ON a.run_id=r.id
                    WHERE r.profile=? AND r.status NOT IN ('completed','failed','cancelled')''',
                    (profile,)).fetchall()
                active_ids = {sid for row in active for sid in row if sid is not None}
                roots = conversation_roots(identities | active_ids)
                if {roots[sid] for sid in identities} & {roots[sid] for sid in active_ids}:
                    raise RunConflict('Session has an active or unresolved run')
            rid = uuid.uuid4().hex
            now = time.time()
            c.execute('INSERT INTO runs(id,user_id,profile,session_id,input,idempotency_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (rid,user_id,profile,session_id,text,key,'queued',now,now))
            if selection is not None:
                c.execute('INSERT INTO run_selections VALUES(?,?)',(rid,json.dumps(selection,sort_keys=True)))
            if attachment_ids:
                if attachment_store is None:
                    raise ValueError('Photo storage is unavailable')
                attachment_store.bind(c, user_id, profile, session_id, list(attachment_ids), rid)
            # Persist exactly the identity checked above, never reload it after
            # admission. Anchor failure leaves no run or selection behind.
            if anchor is not None:
                c.execute('INSERT INTO run_history_anchors VALUES(?,?,?,?)',
                          (rid, anchor['session_id'], anchor['canonical_session_id'], anchor['message_id']))
            extra = {'selection':selection} if selection is not None else {}
            if attachment_ids:
                extra['attachment_ids'] = list(attachment_ids)
            return dict(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone(), **extra), True

    def _require_run(self, c, user_id, run_id):
        row = c.execute('SELECT * FROM runs WHERE id=? AND user_id=?', (run_id, user_id)).fetchone()
        if not row:
            raise KeyError(run_id)
        self._require_session(c, user_id, row['profile'], row['session_id'])
        return row

    def get(self, user_id, run_id):
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            row = self._require_run(c, user_id, run_id)
            saved=c.execute('SELECT selection FROM run_selections WHERE run_id=?',(run_id,)).fetchone()
            extra = {'selection':json.loads(saved[0])} if saved else {}
            attachments = self._attachment_ids(c, run_id)
            if attachments:
                extra['attachment_ids'] = attachments
            return dict(row, **extra)

    def session_statuses(self, user_id, profile, session_ids):
        """One allowlisted read for a page, scoped before newest-run selection."""
        from .session_telemetry import unknown_run
        ids = list(dict.fromkeys(session_ids))
        result = {sid: unknown_run() for sid in ids}
        if not ids:
            return result
        with closing(self.connect()) as c:
            rows = c.execute('''SELECT id,session_id,status,updated_at FROM runs
                WHERE rowid IN (SELECT MAX(rowid) FROM runs
                WHERE user_id=? AND profile=? AND session_id IN ('''
                + ','.join('?' for _ in ids) + ') GROUP BY session_id)',
                (user_id, profile, *ids)).fetchall()
        known = {'queued','running','waiting_for_approval','waiting_for_clarification',
                 'stopping','failed','unknown','completed','cancelled'}
        for row in rows:
            last = row['status'] if row['status'] in known else 'unknown'
            result[row['session_id']] = dict(status='idle' if last in ('completed','cancelled') else last,
                last_status=last, id=row['id'], source='web_run_journal', observed_at=row['updated_at'])
        return result

    def latest(self, user_id, profile, session_id):
        """Discover only this principal's turn; rowid breaks timestamp ties."""
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            self._require_session(c, user_id, profile, session_id)
            row = c.execute('''SELECT r.*, a.message_id AS anchor_message_id,
                a.session_id AS anchor_session_id, a.canonical_session_id AS anchor_canonical_session_id
                FROM runs r LEFT JOIN run_history_anchors a ON a.run_id=r.id
                WHERE r.user_id=? AND r.profile=? AND r.session_id=?
                ORDER BY r.rowid DESC LIMIT 1''', (user_id, profile, session_id)).fetchone()
            if row is None:
                return {'run': None, 'anchor': None}
            run = dict(row)
            attachments = self._attachment_ids(c, run['id'])
            if attachments:
                run['attachment_ids'] = attachments
            anchor = {key: run.pop('anchor_' + key) for key in ('message_id', 'session_id', 'canonical_session_id')}
            return {'run': run, 'anchor': anchor if anchor['message_id'] is not None else None}

    def _replay_events(self, connection, run_id):
        """First acceptance fixes position; subsequent receipts update in place."""
        replay, accepted = [], {}
        for row in connection.execute(
                "SELECT id,run_id,name,data,created_at FROM events WHERE run_id=? "
                "AND name IN ('tool','commentary','delta','steering','clarification') ORDER BY id", (run_id,)):
            event = self._public_replay(row)
            if event is None:
                continue
            if event['name'] == 'steering':
                aid = event['data']['id']
                if aid in accepted:
                    previous = accepted[aid]['data']
                    if (previous['status'] != 'not_delivered'
                            and all(previous[key] == event['data'][key]
                                    for key in ('run_id', 'idempotency_key', 'input', 'created_at'))):
                        accepted[aid]['data'] = event['data']
                    continue
                if event['data']['status'] != 'accepted_unconfirmed':
                    continue
                accepted[aid] = event
            elif event['name'] == 'clarification':
                question_id = event['data']['question_id']
                previous = next((item for item in replay if item['name'] == 'clarification'
                                 and item['data']['question_id'] == question_id), None)
                if previous is not None:
                    previous['data'] = event['data']
                    continue
            replay.append(event)
        return replay

    def snapshot_state(self, user_id, profile, session_id):
        """Owned admissions and latest tool replay share one read transaction."""
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            self._require_session(c, user_id, profile, session_id)
            candidates = c.execute('''SELECT r.*, a.message_id AS anchor_message_id,
                a.session_id AS anchor_session_id, a.canonical_session_id AS anchor_canonical_session_id
                FROM runs r LEFT JOIN run_history_anchors a ON a.run_id=r.id
                WHERE r.user_id=? AND r.profile=? ORDER BY r.rowid''',
                (user_id, profile)).fetchall()
            aliases, selected = {session_id}, {}
            while True:
                before = len(aliases)
                for row in candidates:
                    identities = {row['session_id'], row['anchor_session_id'],
                                  row['anchor_canonical_session_id']} - {None}
                    if aliases & identities:
                        aliases.update(identities)
                        selected[row['id']] = row
                if before == len(aliases):
                    break
            rows = [row for row in candidates if row['id'] in selected]
            replay_events, event_cursor = [], 0
            if rows:
                event_cursor = c.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE run_id=?',
                                         (rows[-1]['id'],)).fetchone()[0]
                replay_events = self._replay_events(c, rows[-1]['id'])
            prior_replays = {}
            attachments_by_run = {}
            if rows and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attachments'").fetchone():
                run_ids = [row['id'] for row in rows]
                for offset in range(0, len(run_ids), 250):
                    batch = run_ids[offset:offset + 250]
                    for attachment in c.execute('''SELECT a.run_id,a.id FROM attachments a
                        WHERE a.user_id=? AND a.profile=? AND a.run_id IN ('''
                        + ','.join('?' for _ in batch) + ''')
                        AND a.state IN ('bound','expired','releasing')
                        ORDER BY a.run_id,a.position,a.id''', (user_id, profile, *batch)):
                        attachments_by_run.setdefault(attachment['run_id'], []).append(attachment['id'])
            for row in rows[:-1]:
                if c.execute("SELECT 1 FROM events WHERE run_id=? AND name IN ('steering','clarification') LIMIT 1",
                             (row['id'],)).fetchone():
                    events = self._replay_events(c, row['id'])
                    if any(event['name'] in ('steering','clarification') for event in events):
                        prior_replays[row['id']] = events
        entries = []
        for row in rows:
            run = dict(row)
            attachments = attachments_by_run.get(run['id'], [])
            if attachments:
                run['attachment_ids'] = attachments
            anchor = {key: run.pop('anchor_' + key) for key in ('message_id', 'session_id', 'canonical_session_id')}
            entries.append({'run': run, 'anchor': anchor if anchor['message_id'] is not None else None})
            if run['id'] in prior_replays:
                entries[-1]['replay_events'] = prior_replays[run['id']]
        return dict(entries[-1] if entries else {'run': None, 'anchor': None},
                    prior=entries[:-1], replay_events=replay_events, event_cursor=event_cursor,
                    tool_events=[event for event in replay_events if event['name'] == 'tool'])

    def set_upstream(self, user_id, run_id, upstream_id):
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            self._require_run(c, user_id, run_id)
            c.execute("UPDATE runs SET upstream_id=?,status='running',updated_at=? WHERE id=? AND user_id=?", (upstream_id,time.time(),run_id,user_id))

    def finish(self,user_id,run_id,status,output=None,error=None,*,expected=None):
        if status not in ('completed','failed','cancelled','unknown','stopping',
                          'waiting_for_approval','waiting_for_clarification'):
            raise ValueError('Invalid run state')
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            current = self._require_run(c, user_id, run_id)
            if expected and any(current[k] != v for k, v in expected.items()):
                return
            changed = c.execute("UPDATE runs SET status=?,output=COALESCE(?,output),error=?,updated_at=? WHERE id=? AND user_id=? AND status NOT IN ('completed','failed','cancelled')", (status,output,error,time.time(),run_id,user_id)).rowcount
            if not changed:
                return
            if status in ('completed','failed','cancelled'):
                self._expire_pending_approvals(c, user_id, current['profile'], run_id=run_id)
            if status == 'stopping':
                c.execute('INSERT OR IGNORE INTO run_stop_intents VALUES(?)', (run_id,))
            # Terminal state is the SSE drain barrier: publish its event in the
            # same transaction so readers never observe completion without it.
            name='done' if status in ('completed','failed','cancelled','unknown') else 'status'
            data={'status':status,'output':output,'error':error}
            c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',(run_id,name,json.dumps(data),time.time()))

    def set_active_status(self, user_id, run_id, status, *, upstream_id=None):
        if status not in ('running', 'waiting_for_clarification'):
            raise ValueError('Invalid active run state')
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            current = self._require_run(c, user_id, run_id)
            if (current['status'] in ('completed', 'failed', 'cancelled', 'stopping')
                    or upstream_id is not None and current['upstream_id'] != upstream_id
                    or c.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?', (run_id,)).fetchone()):
                return False
            changed = c.execute('''UPDATE runs SET status=?,error=NULL,updated_at=?
                WHERE id=? AND user_id=? AND profile=? AND upstream_id=?
                AND status IN ('running','waiting_for_clarification','unknown')''',
                (status, time.time(), run_id, user_id, current['profile'], current['upstream_id'])).rowcount
            if changed:
                c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                           (run_id, 'status', json.dumps({'status': status}), time.time()))
            return bool(changed)

    def event(self,user_id,run_id,name,data):
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            self._require_run(c, user_id, run_id)
            cursor=c.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',(run_id,name,json.dumps(data),time.time()))
            return cursor.lastrowid

    def events(self,user_id,run_id,after=0):
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            self._require_run(c, user_id, run_id)
            rows=c.execute('SELECT id,name,data,created_at FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 1000',(run_id,int(after))).fetchall()
            return [dict(id=r['id'],name=r['name'],data=json.loads(r['data']),observed_at=r['created_at']) for r in rows]

    def recover(self):
        # Dispatch may have reached Hermes before a crash. Never retry it blindly.
        with closing(self.connect()) as c,c:
            c.execute("INSERT OR IGNORE INTO run_stop_intents SELECT id FROM runs WHERE status='stopping' OR error='Stop outcome is unresolved'")
            c.execute("UPDATE runs SET status='unknown',error='Bridge restarted; upstream outcome must be reconciled',updated_at=? WHERE status IN ('queued','running','stopping','waiting_for_approval','waiting_for_clarification')",(time.time(),))
