"""Durable, owner-bound records for native clarification waiters."""
from contextlib import closing
import asyncio
import json
import math
import time

from .runs import RunConflict
from .hermes_client import IntegrationUnavailable, NativeRunNotFound


class ClarificationJournal:
    def __init__(self, journal):
        self.journal = journal
        with closing(journal.connect()) as connection, connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS clarifications(
                question_id TEXT NOT NULL, user_id TEXT NOT NULL, profile TEXT NOT NULL,
                run_id TEXT NOT NULL, upstream_id TEXT NOT NULL, question TEXT NOT NULL,
                choices TEXT, multi_select INTEGER NOT NULL, status TEXT NOT NULL,
                answer TEXT, other INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,profile,run_id,question_id))''')

    @staticmethod
    def _normalize(item, upstream_id):
        if not isinstance(item, dict):
            return None
        question_id = item.get('question_id')
        question = item.get('question')
        choices = item.get('choices')
        multi_select = item.get('multi_select')
        status = item.get('status')
        created_at = item.get('created_at', item.get('timestamp'))
        updated_at = item.get('updated_at', created_at)
        if (not isinstance(question_id, str) or len(question_id) != 32
                or any(c not in '0123456789abcdef' for c in question_id)
                or not isinstance(question, str) or not question.strip() or len(question) > 4096
                or (choices is not None and (
                    not isinstance(choices, list) or len(choices) > 4
                    or any(not isinstance(choice, str) or not choice.strip() or len(choice) > 512
                           for choice in choices)))
                or type(multi_select) is not bool
                or status not in ('pending', 'answered', 'cancelled', 'expired', 'unknown')
                or type(created_at) not in (int, float) or not math.isfinite(created_at) or created_at <= 0
                or type(updated_at) not in (int, float) or not math.isfinite(updated_at) or updated_at < created_at):
            return None
        if choices == []:
            choices = None
        answer = item.get('answer')
        other = item.get('other')
        if status == 'answered':
            if (not isinstance(answer, (str, list)) or not answer
                    or type(other) is not bool):
                return None
            if isinstance(answer, str) and (not answer.strip() or len(answer) > 32768):
                return None
            if isinstance(answer, list) and (
                    len(answer) > 5 or any(not isinstance(value, str) or not value.strip()
                                            or len(value) > 32768 for value in answer)):
                return None
        elif answer is not None or other is not None:
            return None
        return {
            'question_id': question_id, 'question': question.strip(), 'choices': choices,
            'multi_select': multi_select, 'status': status, 'answer': answer, 'other': other,
            'created_at': float(created_at), 'updated_at': float(updated_at),
            'upstream_id': upstream_id,
        }

    @staticmethod
    def _view(row):
        return {
            'question_id': row['question_id'], 'run_id': row['run_id'],
            'session_id': row['session_id'], 'question': row['question'],
            'choices': json.loads(row['choices']) if row['choices'] else None,
            'multi_select': bool(row['multi_select']), 'status': row['status'],
            'answer': json.loads(row['answer']) if row['answer'] is not None else None,
            'other': bool(row['other']) if row['other'] is not None else None,
            'created_at': row['created_at'], 'updated_at': row['updated_at'],
        }

    @staticmethod
    def _encode_answer(answer):
        return json.dumps(answer, separators=(',', ':'), ensure_ascii=False)

    @staticmethod
    def _same_answer(encoded, answer):
        if encoded is None:
            return answer is None
        try:
            return json.loads(encoded) == answer
        except (TypeError, ValueError):
            return False

    def _save(self, user, run, item):
        with closing(self.journal.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self.journal._require_run(connection, user['id'], run['id'])
            if (current['profile'] != user['profile'] or current['upstream_id'] != item['upstream_id']
                    or run['upstream_id'] != item['upstream_id']):
                return None
            existing = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                AND c.question_id=?''',
                (user['id'], user['profile'], run['id'], item['question_id'])).fetchone()
            encoded_choices = json.dumps(item['choices']) if item['choices'] is not None else None
            encoded_answer = (self._encode_answer(item['answer'])
                              if item['answer'] is not None else None)
            if existing:
                if (existing['upstream_id'], existing['question'], existing['choices'],
                        existing['multi_select'], existing['created_at']) != (
                        item['upstream_id'], item['question'], encoded_choices,
                        int(item['multi_select']), item['created_at']):
                    return None
                if existing['status'] == 'answered':
                    if (item['status'] != 'answered'
                            or not self._same_answer(existing['answer'], item['answer'])
                            or existing['other'] != (int(item['other']) if item['other'] is not None else None)):
                        return self._view(existing)
                confirmed_attempt = (
                    existing['status'] in ('sending', 'unknown')
                    and item['status'] == 'answered'
                )
                if confirmed_attempt:
                    if (existing['answer'] is None
                            or not self._same_answer(existing['answer'], item['answer'])
                            or existing['other'] != int(item['other'])):
                        return self._view(existing)
                    item = {**item, 'updated_at': max(
                        item['updated_at'], math.nextafter(existing['updated_at'], math.inf))}
                if (existing['status'] in ('cancelled', 'expired')
                        and item['status'] != existing['status']):
                    return self._view(existing)
                restore_pending = (existing['status'] == 'unknown'
                                  and existing['answer'] is None
                                  and item['status'] == 'pending')
                if (existing['status'] == 'sending' and item['status'] == 'pending'
                        or existing['status'] == 'unknown' and item['status'] == 'pending'
                        and not restore_pending):
                    return self._view(existing)
                if restore_pending:
                    item = {**item, 'updated_at': max(
                        item['updated_at'], math.nextafter(existing['updated_at'], math.inf))}
                elif item['updated_at'] < existing['updated_at']:
                    return self._view(existing)
                if existing['status'] == 'answered' and item['status'] == 'pending':
                    return self._view(existing)
                if (existing['status'], existing['answer'], existing['other'], existing['updated_at']) == (
                        item['status'], encoded_answer,
                        int(item['other']) if item['other'] is not None else None,
                        item['updated_at']):
                    return self._view(existing)
                connection.execute('''UPDATE clarifications SET status=?,answer=?,other=?,updated_at=?
                    WHERE user_id=? AND profile=? AND run_id=? AND question_id=?''',
                    (item['status'], encoded_answer, int(item['other']) if item['other'] is not None else None,
                     item['updated_at'], user['id'], user['profile'], run['id'], item['question_id']))
            else:
                connection.execute('''INSERT INTO clarifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (item['question_id'], user['id'], user['profile'], run['id'], item['upstream_id'],
                     item['question'], encoded_choices, int(item['multi_select']), item['status'],
                     encoded_answer, int(item['other']) if item['other'] is not None else None,
                     item['created_at'], item['updated_at']))
            saved = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                AND c.question_id=?''', (user['id'], user['profile'], run['id'],
                                         item['question_id'])).fetchone()
            event = self._view(saved)
            event['observed_at'] = item['updated_at']
            connection.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                               (run['id'], 'clarification', json.dumps(event), item['updated_at']))
            return event

    def observe(self, user, run, result):
        if not isinstance(result, dict) or result.get('run_id') != run.get('upstream_id'):
            return set()
        records = result.get('clarifications', [])
        if not isinstance(records, list) or len(records) > 256:
            return set()
        pending = set()
        for raw in records:
            item = self._normalize(raw, run['upstream_id'])
            if item is not None:
                saved = self._save(user, run, item)
                if saved is not None and saved['status'] == 'pending':
                    pending.add(item['question_id'])
        return pending

    def event(self, user, run, event):
        if event.get('run_id') != run.get('upstream_id'):
            return None
        item = self._normalize(event, run['upstream_id'])
        return self._save(user, run, item) if item is not None else None

    def list(self, user, run_id):
        self.journal.get(user['id'], run_id)
        with closing(self.journal.connect()) as connection:
            rows = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                ORDER BY c.created_at,c.question_id LIMIT 256''',
                (user['id'], user['profile'], run_id)).fetchall()
        return [self._view(row) for row in rows]

    @staticmethod
    def _record_event(connection, run_id, item):
        event = {**item, 'observed_at': item['updated_at']}
        connection.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                           (run_id, 'clarification', json.dumps(event), item['updated_at']))

    def mark_pending_unknown(self, user, run_id, keep_pending=()):
        keep_pending = set(keep_pending)
        with closing(self.journal.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=?
                AND c.run_id=? AND c.status='pending' ''',
                (user['id'], user['profile'], run_id)).fetchall()
            for row in rows:
                if row['question_id'] in keep_pending:
                    continue
                updated_at = max(time.time(), math.nextafter(row['updated_at'], math.inf))
                cursor = connection.execute('''UPDATE clarifications SET status='unknown',updated_at=?
                    WHERE user_id=? AND profile=? AND run_id=? AND question_id=? AND status='pending' ''',
                    (updated_at, user['id'], user['profile'], run_id, row['question_id']))
                if cursor.rowcount:
                    item = self._view(row)
                    item.update(status='unknown', updated_at=updated_at)
                    self._record_event(connection, run_id, item)

    def mark_run_not_found_unknown(self, user, run_id):
        with closing(self.journal.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=?
                AND c.run_id=? AND c.status IN ('pending','sending')''',
                (user['id'], user['profile'], run_id)).fetchall()
            for row in rows:
                updated_at = max(time.time(), math.nextafter(row['updated_at'], math.inf))
                cursor = connection.execute('''UPDATE clarifications
                    SET status='unknown',updated_at=?
                    WHERE user_id=? AND profile=? AND run_id=? AND question_id=?
                    AND status IN ('pending','sending')''',
                    (updated_at, user['id'], user['profile'], run_id, row['question_id']))
                if cursor.rowcount:
                    item = self._view(row)
                    item.update(status='unknown', updated_at=updated_at)
                    self._record_event(connection, run_id, item)

    def recover(self):
        with closing(self.journal.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.status IN ('pending','sending')''').fetchall()
            for row in rows:
                updated_at = max(time.time(), math.nextafter(row['updated_at'], math.inf))
                cursor = connection.execute('''UPDATE clarifications SET status='unknown',updated_at=?
                    WHERE user_id=? AND profile=? AND run_id=? AND question_id=? AND status=? ''',
                    (updated_at, row['user_id'], row['profile'], row['run_id'],
                     row['question_id'], row['status']))
                if cursor.rowcount:
                    item = self._view(row)
                    item.update(status='unknown', updated_at=updated_at)
                    self._record_event(connection, row['run_id'], item)

    def claim(self, user, run, question_id, body):
        answer = body.get('answer')
        encoded = self._encode_answer(answer)
        now = time.time()
        with closing(self.journal.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('''SELECT * FROM clarifications
                WHERE user_id=? AND profile=? AND run_id=? AND question_id=?''',
                (user['id'], user['profile'], run['id'], question_id)).fetchone()
            if row is None:
                raise KeyError(question_id)
            if row['status'] in ('answered', 'sending'):
                if (not self._same_answer(row['answer'], answer)
                        or row['other'] != int(body['other'])):
                    raise RunConflict('Clarification answer already claimed')
                if row['status'] in ('answered', 'sending'):
                    return self._view(connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                        JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                        AND c.question_id=?''', (user['id'], user['profile'], run['id'],
                                                 question_id)).fetchone()), False
            if row['status'] != 'pending':
                raise RunConflict('Clarification is no longer pending')
            current = self.journal._require_run(connection, user['id'], run['id'])
            if (current['profile'] != user['profile'] or current['upstream_id'] != run['upstream_id']
                    or current['status'] != 'waiting_for_clarification'
                    or connection.execute('SELECT 1 FROM run_stop_intents WHERE run_id=?',
                                          (run['id'],)).fetchone()):
                raise RunConflict('Clarification is stale')
            connection.execute('''UPDATE clarifications SET status='sending',answer=?,other=?,updated_at=?
                WHERE user_id=? AND profile=? AND run_id=? AND question_id=?''',
                (encoded, int(body['other']), now, user['id'], user['profile'], run['id'], question_id))
            saved = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                AND c.question_id=?''', (user['id'], user['profile'], run['id'],
                                         question_id)).fetchone()
            view = self._view(saved)
            connection.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                               (run['id'], 'clarification',
                                json.dumps({**view, 'observed_at': now}), now))
            return view, True

    def finish(self, user, run_id, question_id, status):
        with closing(self.journal.connect()) as connection, connection:
            now = time.time()
            connection.execute('''UPDATE clarifications SET status=?,updated_at=?
                WHERE user_id=? AND profile=? AND run_id=? AND question_id=? AND status='sending' ''',
                (status, now, user['id'], user['profile'], run_id, question_id))
            row = connection.execute('''SELECT c.*,r.session_id FROM clarifications c
                JOIN runs r ON r.id=c.run_id WHERE c.user_id=? AND c.profile=? AND c.run_id=?
                AND c.question_id=?''', (user['id'], user['profile'], run_id, question_id)).fetchone()
            if row is None:
                return None
            view = self._view(row)
            view['observed_at'] = now
            connection.execute('INSERT INTO events(run_id,name,data,created_at) VALUES(?,?,?,?)',
                               (run_id, 'clarification', json.dumps(view), now))
            return view

    @staticmethod
    def validate_answer(record, body):
        if (not isinstance(body, dict) or set(body) != {'answer', 'other'}
                or type(body.get('other')) is not bool):
            return False
        answer, other = body['answer'], body['other']
        choices = record['choices']
        if record['multi_select'] and choices is not None:
            if (not isinstance(answer, list) or not answer or len(answer) > 5
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 32768
                           for value in answer)
                    or len(answer) != len(set(answer))):
                return False
            unknown = [value for value in answer if value not in choices]
            return (not unknown and not other) or (
                other and len(unknown) == 1 and answer[-1] == unknown[0])
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 32768:
            return False
        if choices is None:
            return not other
        return answer not in choices if other else answer in choices

    async def rehydrate(self, user, run_id, gateway, run):
        if not run.get('upstream_id') or run['status'] in ('completed', 'failed', 'cancelled'):
            return {'available': False, 'items': self.list(user, run_id), 'native_status': None}
        try:
            from urllib.parse import quote
            gateway.require_execution()
            if not hasattr(gateway, 'require_clarifications'):
                raise IntegrationUnavailable('Native clarification is unavailable')
            await gateway.require_clarifications()
            async with asyncio.timeout(10):
                result = await gateway.request(
                    'GET', '/v1/runs/' + quote(run['upstream_id'], safe=''))
            if (not isinstance(result, dict) or result.get('run_id') != run['upstream_id']
                    or result.get('status') not in (
                        'running', 'waiting_for_clarification', 'waiting_for_approval',
                        'stopping', 'completed', 'failed', 'cancelled')
                    or 'clarifications' in result and (
                        not isinstance(result['clarifications'], list)
                        or len(result['clarifications']) > 256)):
                raise IntegrationUnavailable('Native clarification status is invalid')
            if ('clarifications' not in result
                    and any(item['status'] in ('pending', 'sending')
                            for item in self.list(user, run_id))):
                raise IntegrationUnavailable('Native clarification status is unavailable')
            normalized = [self._normalize(item, run['upstream_id'])
                          for item in result.get('clarifications', [])]
            if any(item is None for item in normalized):
                raise IntegrationUnavailable('Native clarification identity is invalid')
            pending = [item for item in normalized if item['status'] == 'pending']
            if (result['status'] == 'waiting_for_clarification' and len(pending) != 1
                    or result['status'] == 'running' and pending):
                raise IntegrationUnavailable('Native clarification state is contradictory')
        except NativeRunNotFound:
            self.mark_run_not_found_unknown(user, run_id)
            return {'available': False, 'items': self.list(user, run_id), 'native_status': None}
        except Exception:
            return {'available': False, 'items': self.list(user, run_id), 'native_status': None}
        observed_pending = self.observe(user, run, result)
        keep_pending = (observed_pending
                        if result['status'] == 'waiting_for_clarification' else set())
        self.mark_pending_unknown(user, run_id, keep_pending)
        return {'available': True, 'items': self.list(user, run_id),
                'native_status': result['status']}
