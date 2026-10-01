"""Local-only observations, not an external host/bridge outage watchdog.

Optional native capability absence is not an outage. Incident/recovery records
are durable Inbox records, never a claim that a phone received a push.
"""
import time
import uuid
import json
import os
import re
import stat
from pathlib import Path


class OperationalNotificationService:
    def __init__(self, notifications, *, owner, disk_free, clock=time.time,
                 push_worker_error=lambda: None, background_worker_error=lambda: False,
                 deployment_status_path=None):
        self.notifications = notifications
        self.owner = owner
        self.disk_free = disk_free
        self.clock = clock
        self.push_worker_error = push_worker_error
        self.background_worker_error = background_worker_error
        self.deployment_status_path = Path(deployment_status_path) if deployment_status_path is not None else None
        with notifications._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS operational_notification_state(
                kind TEXT PRIMARY KEY, bad_since REAL, good_since REAL, incident_id TEXT,
                incident_user TEXT, recorded INTEGER NOT NULL DEFAULT 0)''')
            db.execute("INSERT OR IGNORE INTO operational_notification_state(kind) VALUES('disk')")
            db.execute('''CREATE TABLE IF NOT EXISTS operational_deployment_observation(
                source TEXT PRIMARY KEY, baseline TEXT, last_release TEXT)''')
            # One bounded durable counter per recipient, not an event log. Observe
            # the existing transport's committed sent transition, never a queue's
            # disappearance or a global/non-raising flush result. The trigger only
            # writes monitor state, and survives sender/monitor process restarts.
            db.execute('''CREATE TABLE IF NOT EXISTS operational_push_observation(
                user_id TEXT PRIMARY KEY, successes INTEGER NOT NULL DEFAULT 0,
                failure_baseline INTEGER NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS operational_push_sent
                AFTER UPDATE OF status ON outbox
                WHEN NEW.status='sent' AND OLD.status!='sent'
                BEGIN
                    INSERT INTO operational_push_observation(user_id,successes)
                    SELECT i.user_id,1 FROM inbox i
                    JOIN notification_policy p ON p.inbox_id=i.id
                    WHERE i.id=NEW.inbox_id AND p.category IN
                        ('completion','attention','approval','scheduled','test')
                    ON CONFLICT(user_id) DO UPDATE SET successes=successes+1;
                END''')
        if self.deployment_status_path is not None:
            initial = self._read_deployment()
            with notifications._db() as db:
                # Only terminal failures observed at activation are history. A
                # running attempt may fail before our very first polling tick.
                baseline = initial[1] if initial and initial[0] in ('failed','rolled_back','rollback_failed') else None
                db.execute('INSERT OR IGNORE INTO operational_deployment_observation VALUES(?,?,?)',
                           (str(self.deployment_status_path), baseline, None))

    def tick(self):
        user = self.owner()
        if not user or user['status'] != 'ready' or user['role'] != 'owner':
            return
        free = self.disk_free()
        self._observe(user, 'disk', free < 1024**3, free > 2*1024**3, 120, 120,
                      'Server storage critically low', 'Server storage recovered',
                      'Less than 1 GiB of server storage remains. Free storage to avoid interrupted work.',
                      'Available server storage has recovered above 2 GiB.')
        with self.notifications._db() as db:
            failed = bool(db.execute('''SELECT 1 FROM outbox o
                JOIN notification_policy p ON p.inbox_id=o.inbox_id
                JOIN inbox i ON i.id=o.inbox_id
                WHERE i.user_id=? AND p.category IN ('completion','attention','approval','scheduled','test')
                AND o.status IN ('pending','retry','policy_pending','policy_retry')
                AND o.last_error IN ('transport_error','provider_error') AND o.expires_at>?
                LIMIT 1''', (user['id'], self.clock())).fetchone())
            active_categories = {r[0] for r in db.execute('''SELECT DISTINCT p.category FROM outbox o
                JOIN notification_policy p ON p.inbox_id=o.inbox_id JOIN inbox i ON i.id=o.inbox_id
                WHERE i.user_id=? AND o.expires_at>? AND o.status IN
                ('pending','retry','sending','policy_pending','policy_retry','policy_sending')''',
                (user['id'], self.clock()))}
        # An exception servicing only our own alerts is not a new delivery incident.
        worker_error = self.push_worker_error()
        worker_failed = worker_error is True and active_categories != {'operational'}
        failed = failed or worker_failed
        # Excluding our own work is UNKNOWN, not evidence of healthy delivery.
        # A False worker flag means only "no exception". Require this recipient's
        # positive non-operational sent evidence after the last bad observation.
        with self.notifications._db() as db:
            db.execute('INSERT OR IGNORE INTO operational_push_observation(user_id) VALUES(?)', (user['id'],))
            if failed:
                db.execute('UPDATE operational_push_observation SET failure_baseline=successes WHERE user_id=?',
                           (user['id'],))
            evidence = db.execute('SELECT successes,failure_baseline FROM operational_push_observation WHERE user_id=?',
                                  (user['id'],)).fetchone()
        good = not failed and evidence['successes'] > evidence['failure_baseline']
        self._observe(user, 'push', failed, good, 300, 0,
                      'Push delivery interrupted', 'Push delivery recovered',
                      'Push delivery has been failing. Check Hermes Inbox and the push delivery service.',
                      'The observed push delivery failure has cleared. Check Inbox for any missed updates.')
        background_error = self.background_worker_error()
        self._observe(user, 'background', background_error is True, background_error is False, 300, 0,
                      'Background result delivery interrupted', 'Background result delivery recovered',
                      'Background result delivery has been interrupted. Check the bridge delivery service.',
                      'The observed background result delivery error has cleared.')
        self._deployment(user)

    def _read_deployment(self):
        if self.deployment_status_path is None:
            return None
        try:
            path = self.deployment_status_path
            if any(parent.is_symlink() for parent in path.parents):
                return None
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or info.st_uid != os.getuid() or info.st_mode & 0o002:
                    return None
                content = stream.read(65537)
                if len(content) > 65536:
                    return None
            payload = json.loads(content)
            status = payload.get('status')
            release = payload.get('release', 'unidentified')
            if status not in ('running','failed','rolled_back','rollback_failed','succeeded') or not isinstance(release,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',release):
                return None
            return status, release
        except (OSError,ValueError,AttributeError):
            return None

    def _deployment(self, user):
        observation = self._read_deployment()
        if observation is None:
            return
        status, release = observation
        with self.notifications._db() as db:
            previous = db.execute('SELECT * FROM operational_deployment_observation WHERE source=?',
                                  (str(self.deployment_status_path),)).fetchone()
            if previous['baseline'] == release and status not in ('running','succeeded'):
                return
            db.execute('UPDATE operational_deployment_observation SET baseline=NULL,last_release=? WHERE source=?',
                       (release,str(self.deployment_status_path)))
            if previous['last_release'] != release:
                db.execute("UPDATE operational_notification_state SET bad_since=NULL WHERE kind='deployment'")
        self._observe(user, 'deployment', status in ('failed','rolled_back','rollback_failed'),
                      status == 'succeeded', 300 if status == 'failed' else 0, 0,
                      'Deployment needs attention', 'Deployment recovered',
                      'A deployment failed or was rolled back. Review the deployment status before retrying.',
                      'A subsequent deployment succeeded. The recorded deployment incident has cleared.')

    def _observe(self, user, kind, bad, good, threshold, recovery_threshold, title, recovery_title, body, recovery_body):
        now = self.clock()
        with self.notifications._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO operational_notification_state(kind) VALUES(?)', (kind,))
            row = dict(db.execute('SELECT * FROM operational_notification_state WHERE kind=?', (kind,)).fetchone())
            if kind != 'push' or bad or good:
                row['bad_since'] = (row['bad_since'] if row['bad_since'] is not None else now) if bad else None
            row['good_since'] = (row['good_since'] if row['good_since'] is not None else now) if good else None
            if not row['incident_id'] and bad and now-row['bad_since'] >= threshold:
                row.update(incident_id=uuid.uuid4().hex, incident_user=user['id'], recorded=0)
            db.execute('''UPDATE operational_notification_state SET bad_since=?,good_since=?,
                incident_id=?,incident_user=?,recorded=? WHERE kind=?''',
                (row['bad_since'], row['good_since'], row['incident_id'], row['incident_user'], row['recorded'], kind))
        if not row['incident_id'] or row['incident_user'] != user['id']:
            return
        event_id = 'operational:'+kind+':'+row['incident_id']
        if not row['recorded']:
            self.notifications.ingest(user['id'], event_id+':incident', title, body,
                                      category='operational', profile=user['profile'])
            with self.notifications._db() as db:
                db.execute('UPDATE operational_notification_state SET recorded=1 WHERE kind=? AND incident_id=?',
                           (kind, row['incident_id']))
        elif good and now-row['good_since'] >= recovery_threshold:
            with self.notifications._db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('''UPDATE outbox SET status='suppressed',last_error='operational_recovered'
                    WHERE status IN ('pending','retry','policy_pending','policy_retry') AND inbox_id IN
                    (SELECT id FROM inbox WHERE user_id=? AND delivery_id=?)''',
                    (user['id'], event_id+':incident'))
                delivered = bool(db.execute('''SELECT 1 FROM outbox o JOIN inbox i ON i.id=o.inbox_id
                    WHERE i.user_id=? AND i.delivery_id=? AND o.status='sent' LIMIT 1''',
                    (user['id'], event_id+':incident')).fetchone())
            self.notifications.ingest(user['id'], event_id+':recovery', recovery_title, recovery_body,
                                      category='operational', profile=user['profile'], silent=not delivered)
            with self.notifications._db() as db:
                db.execute('''UPDATE operational_notification_state SET incident_id=NULL,incident_user=NULL,
                    recorded=0,bad_since=NULL,good_since=NULL WHERE kind=? AND incident_id=?''',
                    (kind, row['incident_id']))
