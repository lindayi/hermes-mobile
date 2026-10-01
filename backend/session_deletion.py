"""Owner-only stored-history deletion; durable claims are not native erasure.

No native SQLite writers, implicit retry, or all-copy cleanup. See the v1 contract.
"""
import asyncio
from contextlib import closing
from urllib.parse import quote


class SessionDeletion:
    def __init__(self, journal):
        self.journal = journal

    def sessions(self, catalog, user, limit, offset, q, kind):
        """Filter BEFORE count/pagination; native catalogue connections stay read-only."""
        with closing(self.journal.connect()) as c:
            blocked = [row[0] for row in c.execute(
                'SELECT session_id FROM session_deletions WHERE user_id=? AND profile=?',
                (user['id'], user['profile']))]
        return catalog.sessions(user['profile'], limit, offset, q, kind, excluded_ids=blocked)

    def _list_snapshot(self, catalog, user, limit, offset, q, kind):
        """Keep one read-only observer: data_version is connection-local."""
        import sqlite3
        observer = sqlite3.connect(self.journal.path.resolve().as_uri() + '?mode=ro',
                                   uri=True, timeout=10, check_same_thread=False)
        try:
            version = observer.execute('PRAGMA data_version').fetchone()[0]
            page = self.list_page(catalog, user, limit, offset, q, kind)
            # The post-await check must never wait for a SQLite writer.
            observer.execute('PRAGMA busy_timeout=0')
            return observer, version, page
        except BaseException:
            observer.close()
            raise

    async def read_list(self, catalog, user, limit, offset, q, kind):
        """Publish only a journal-stable page, with at most one full reread.

        All scans/waits run off-loop. After each await a constant-size, zero-wait
        data_version check detects commits during the read or thread handoff.
        No await follows successful validation. Unrelated journal writes may
        conservatively invalidate a page; no schema/epoch or writer changes.
        """
        from .hermes_client import IntegrationUnavailable
        for _ in range(2):
            task = asyncio.create_task(asyncio.to_thread(
                self._list_snapshot, catalog, user, limit, offset, q, kind))
            try:
                observer, version, page = await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled await cannot stop SQLite's worker. Close only
                # after it finishes, never concurrently with its connection use.
                def close_snapshot(finished):
                    if not finished.cancelled() and finished.exception() is None:
                        finished.result()[0].close()
                task.add_done_callback(close_snapshot)
                raise
            with closing(observer):
                if observer.execute('PRAGMA data_version').fetchone()[0] == version:
                    return page
        raise IntegrationUnavailable('Session list changed during read; retry the list request')

    def list_page(self, catalog, user, limit, offset, q, kind):
        """Collect all blocking list/status/pending reads in one worker call."""
        page = self.sessions(catalog, user, limit, offset, q, kind)
        statuses = self.journal.session_statuses(
            user['id'], user['profile'], [item['id'] for item in page['items']])
        for item in page['items']:
            item.update(run=statuses[item['id']], run_status=statuses[item['id']]['status'])
        pending = self.pending(user)
        if pending:
            page['pending_deletions'] = pending
        return page

    def pending(self, user):
        """Content-free root operation summaries, scoped to the current owner/profile."""
        if user['role'] != 'owner':
            return []
        with closing(self.journal.connect()) as c:
            rows = c.execute('''SELECT DISTINCT session_id FROM session_deletion_operations
                WHERE user_id=? AND profile=? AND state IN ('prepared','native_unknown')
                ORDER BY session_id LIMIT 100''',
                (user['id'], user['profile'])).fetchall()
        return [{'id': row['session_id'], 'status': 'unconfirmed'} for row in rows]

    async def available(self, gateway):
        from .hermes_client import IntegrationUnavailable
        try:
            gateway.require_execution()
            async with asyncio.timeout(5):
                caps = await gateway.request('GET', '/v1/capabilities')
            features = caps.get('features') if isinstance(caps, dict) else None
            version = features.get('mobile_session_delete_version') if isinstance(features, dict) else None
            return type(version) is int and version == 1
        except (IntegrationUnavailable, TimeoutError):
            return False

    def _outcome(self, user, operation_id, state, targets=()):
        """Content-free, monotonic outcome; a later unknown cannot undo proof."""
        with closing(self.journal.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            params = (user['id'], user['profile'], operation_id)
            row = c.execute('SELECT state FROM session_deletion_operations WHERE user_id=? AND profile=? AND operation_id=?', params).fetchone()
            if not row:
                raise KeyError(operation_id)
            if row['state'] in ('deleted', 'refused'):
                return row['state']
            c.execute('UPDATE session_deletion_operations SET state=? WHERE user_id=? AND profile=? AND operation_id=?', (state, *params))
            if state == 'deleted':
                import time
                aliases = set(targets)
                for target in targets:
                    related, _ = self.journal._related_runs(c, user['profile'], target)
                    aliases.update(related)
                c.executemany('INSERT OR IGNORE INTO session_deletions VALUES(?,?,?,?,?,?)',
                    [(user['id'], user['profile'], sid, operation_id, 'deleted', time.time()) for sid in aliases])
            if state == 'refused':
                c.execute('DELETE FROM session_deletions WHERE user_id=? AND profile=? AND operation_id=?', params)
            else:
                c.execute('UPDATE session_deletions SET state=? WHERE user_id=? AND profile=? AND operation_id=?', (state, *params))
            return state

    @staticmethod
    def _receipt_state(reply, session_id, operation_id):
        if (not isinstance(reply, dict) or reply.get('id') != session_id
                or reply.get('operation_id') != operation_id):
            return 'native_unknown'
        if reply.get('deleted') is True and reply.get('status', 'deleted') == 'deleted':
            import re
            targets = reply.get('deleted_ids', [session_id])
            if (not isinstance(targets, list) or not 1 <= len(targets) <= 100 or session_id not in targets
                    or any(not isinstance(sid, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', sid) is None for sid in targets)):
                return 'native_unknown'
            return 'deleted'
        if reply.get('deleted') is False and reply.get('status') == 'refused':
            return 'refused'
        return 'native_unknown'

    @staticmethod
    def _response(session_id, state):
        from .hermes_client import IntegrationUnavailable
        from .runs import RunConflict
        if state == 'deleted':
            return {'id': session_id, 'deleted': True}
        if state == 'refused':
            raise RunConflict('Native deletion refused safely; session was not deleted')
        raise IntegrationUnavailable('Deletion outcome is unresolved; automatic retry is disabled')

    async def _reconcile_operation(self, user, session_id, operation_id, gateway):
        reply = None
        try:
            async with asyncio.timeout(10):
                reply = await gateway.request('GET', '/api/mobile/session-deletions/' + operation_id)
        except Exception:
            pass  # A missing/unavailable receipt is not evidence of refusal.
        state = self._receipt_state(reply, session_id, operation_id)
        targets = reply.get('deleted_ids', [session_id]) if state == 'deleted' else ()
        return self._response(session_id, self._outcome(user, operation_id, state, targets))

    async def reconcile(self, user, session_id, gateway):
        with closing(self.journal.connect()) as c:
            row = c.execute('''SELECT operation_id,state FROM session_deletion_operations
                WHERE user_id=? AND profile=? AND session_id=? ORDER BY rowid DESC LIMIT 1''',
                (user['id'], user['profile'], session_id)).fetchone()
        if not row:
            raise KeyError(session_id)
        if row['state'] in ('deleted', 'refused'):
            return self._response(session_id, row['state'])
        return await self._reconcile_operation(user, session_id, row['operation_id'], gateway)

    async def delete(self, user, session_id, runtime):
        claim = runtime.claim_deletion(user, session_id)
        operation_id = claim['operation_id']
        reply = None
        try:
            async with asyncio.timeout(30):
                reply = await runtime.gateway.request('DELETE', '/api/mobile/sessions/' + quote(session_id, safe=''),
                    json={'confirm': True, 'operation_id': operation_id})
        except asyncio.CancelledError:
            self._outcome(user, operation_id, 'native_unknown')
            raise
        except Exception:
            pass
        # Only committed success is trusted directly. Refusal must be durable
        # and read back; bare status codes/false/missing rows cannot unlock admission.
        if self._receipt_state(reply, session_id, operation_id) == 'deleted':
            return self._response(session_id, self._outcome(user, operation_id, 'deleted', reply.get('deleted_ids', [session_id])))
        self._outcome(user, operation_id, 'native_unknown')
        return await self._reconcile_operation(user, session_id, operation_id, runtime.gateway)
