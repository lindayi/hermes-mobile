"""Durable local notifications for bridge-managed, authoritative request outcomes.

Never opens native history or contacts a runtime. Public final output is the only
request text admitted; preview redaction remains NotificationService's job.
"""
from contextlib import closing
import time


class RequestNotificationService:
    def __init__(self, notifications, journal, *, resolve_user, session_validator, clock=time.time, session_resolver=None):
        self.notifications = notifications
        self.journal = journal
        self.resolve_user = resolve_user
        self.session_validator = session_validator
        self.session_resolver = session_resolver or (lambda user, sid: sid)
        self.clock = clock
        with notifications._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS request_notification_activation(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), activated_at REAL NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO request_notification_activation VALUES(1,?)', (clock(),))
            db.execute('CREATE TABLE IF NOT EXISTS request_notification_cursor(singleton INTEGER PRIMARY KEY, position INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO request_notification_cursor VALUES(1,0)')
            db.execute('CREATE TABLE IF NOT EXISTS request_notification_unknown(run_id TEXT PRIMARY KEY, since REAL NOT NULL)')
            self.activated_at = db.execute('SELECT activated_at FROM request_notification_activation').fetchone()[0]

    def tick(self):
        with self.notifications._db() as state:
            cursor = state.execute('SELECT position FROM request_notification_cursor WHERE singleton=1').fetchone()[0]
        with closing(self.journal.connect()) as db, db:
            # Serialize against stop/deletion admission through notification commit.
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('''SELECT rowid AS position,id,user_id,profile,session_id,output,status,updated_at FROM runs r
                WHERE rowid>? AND updated_at>=?
                AND NOT EXISTS(SELECT 1 FROM run_stop_intents WHERE run_id=r.id)
                AND NOT EXISTS(SELECT 1 FROM session_deletions d WHERE d.user_id=r.user_id
                    AND d.profile=r.profile AND d.session_id=r.session_id)
                ORDER BY rowid LIMIT 100''', (cursor, self.activated_at)).fetchall()
            for row in rows:
                user = self.resolve_user(row['user_id'])
                if (not user or user['id'] != row['user_id'] or user['status'] != 'ready'
                        or user['profile'] != row['profile']):
                    continue
                session_id = self.session_resolver(user, row['session_id'])
                if not session_id or not self.session_validator(user, session_id):
                    continue
                if row['status'] == 'unknown':
                    with self.notifications._db() as state:
                        state.execute('INSERT OR IGNORE INTO request_notification_unknown VALUES(?,?)',
                                      (row['id'], min(self.clock(), row['updated_at'])))
                        since = state.execute('SELECT since FROM request_notification_unknown WHERE run_id=?', (row['id'],)).fetchone()[0]
                    if self.clock() - since >= 120:
                        self.notifications.ingest(row['user_id'], 'request:'+row['id']+':unknown',
                            'Request outcome unresolved',
                            'The request outcome is unresolved. Open Hermes to check before trying again.',
                            session_id=session_id, category='attention', profile=row['profile'])
                    continue
                with self.notifications._db() as state:
                    state.execute('DELETE FROM request_notification_unknown WHERE run_id=?', (row['id'],))
                if row['status'] not in ('completed','failed'):
                    continue
                failed = row['status'] == 'failed'
                self.notifications.ingest(row['user_id'], 'request:'+row['id']+':terminal',
                    'Request needs attention' if failed else 'Request finished',
                    ('Your request could not finish. Open Hermes to review its status.' if failed else
                     row['output'] or 'Your request finished. Open Hermes to review it.'),
                    session_id=session_id, category='attention' if failed else 'completion',
                    profile=row['profile'])
            # Only advance after all durable ingests. A crash can repeat, never skip.
            with self.notifications._db() as state:
                state.execute('UPDATE request_notification_cursor SET position=? WHERE singleton=1',
                              (rows[-1]['position'] if len(rows) == 100 else 0,))
