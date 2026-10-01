"""Read-only public conversation search, including sanitized public commentary.

Native FTS is deliberately not used: its corpus includes private/tool fields.
Missing message schemas fall back to title/ID; errors/budget exhaustion do NOT
fall back to misleading partial totals. One scan returns bounded distinct IDs,
not transcript arrays. All bindings, classification and tombstones are shared
by count/page in the caller's read-only snapshot. Known standalone scaffolding
is excluded; NULL-provenance exact human pastes remain inherently ambiguous.
"""
import json
import sqlite3
import time

MAX_SEARCH_STEPS = 5_000_000
MAX_SEARCH_SECONDS = 10
MAX_MATCHED_SESSIONS = 100_000
MAX_SEARCH_FIELD_BYTES = 8 * 1024 * 1024


def _budget_exceeded():
    raise sqlite3.OperationalError('Catalogue search budget exceeded')


def _public_body(role, content, provenance, sidecar):
    from .native_catalog import _delegation_status, _native_guidance
    from .public_commentary import _visible, public_commentary_items

    if not isinstance(content, str):
        content = ''
    if content.lstrip().startswith('['):
        try:
            blocks = json.loads(content)
        except (ValueError, RecursionError):
            blocks = None
        if isinstance(blocks, list):
            content = '\n'.join(block['text'] for block in blocks
                                if isinstance(block, dict) and block.get('type') in ('text', 'input_text', 'output_text')
                                and isinstance(block.get('text'), str))
    if role == 'user':
        if _delegation_status(content) is not None:
            return ''
        guidance = _native_guidance(content)
        return guidance if guidance is not None else content
    return _visible(content) + '\n' + '\n'.join(item['content'] for item in public_commentary_items(sidecar))


def search_filter(connection, columns, *, kind, q, excluded_ids=()):
    from .session_visibility import session_filter
    where, params = session_filter(columns, kind=kind)
    if excluded_ids:
        where += ' AND id NOT IN (SELECT value FROM json_each(?))'
        params = (*params, json.dumps(list(excluded_ids)))
    if not q:
        return where, params, lambda: None
    deadline = time.monotonic() + MAX_SEARCH_SECONDS
    steps = 0

    def check_deadline():
        if time.monotonic() > deadline:
            _budget_exceeded()

    def progress():
        nonlocal steps
        steps += 1000
        return steps > MAX_SEARCH_STEPS or time.monotonic() > deadline

    # The connection is per-request and closes after count/page; both share this
    # budget. Exhaustion raises OperationalError (API 503), never partial totals.
    connection.set_progress_handler(progress, 1000)
    check_deadline()
    term = '%' + q[:200].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    messages = {row[1] for row in connection.execute('PRAGMA table_info(messages)')}
    matches = []
    if {'session_id', 'role', 'content'} <= messages:
        def project(*args):
            if time.monotonic() > deadline:
                _budget_exceeded()
            result = _public_body(*args)
            if time.monotonic() > deadline:
                _budget_exceeded()
            return result

        connection.create_function('catalog_public_body', 4, project)
        connection.create_function('catalog_search_overflow', 0, _budget_exceeded)
        visible = ''
        if {'active', 'compacted'} <= messages:
            visible += ' AND (active=1 OR compacted=1)'
        elif 'active' in messages:
            visible += ' AND active=1'
        if 'display_kind' in messages:
            visible += " AND COALESCE(display_kind,'')!='hidden'"
        # Recognize raw process rows using the history projector, BEFORE public
        # block normalization. Candidate lookup returns only session IDs and a
        # structural flag; tool arguments never enter Python or public search.
        # No candidate => no tool-column access (including ordinary searches).
        processes = set()
        if 'id' in messages:
            from .runtime_notice_presentation import MAX_ITERATIONS_SUMMARY_REQUEST, runtime_notice_ids
            from .context_compression_presentation import compression_ids
            candidates = connection.execute(
                'SELECT DISTINCT session_id,CASE WHEN length(CAST(content AS BLOB))>'
                + str(MAX_SEARCH_FIELD_BYTES) + ' THEN catalog_search_overflow()'
                ' ELSE content=? COLLATE BINARY END AS notice FROM messages'
                " WHERE role IN ('user','assistant')" + visible
                + ' AND session_id IN (SELECT id FROM sessions ' + where + ')'
                + " AND (content=? COLLATE BINARY OR content LIKE '[CONTEXT COMPACTION%'"
                " OR content LIKE '[CONTEXT SUMMARY]:%' OR content LIKE '[PRIOR CONTEXT%') LIMIT ?",
                (MAX_ITERATIONS_SUMMARY_REQUEST, *params, MAX_ITERATIONS_SUMMARY_REQUEST,
                 MAX_MATCHED_SESSIONS + 1))
            for index, candidate in enumerate(candidates):
                check_deadline()
                if index >= MAX_MATCHED_SESSIONS:
                    _budget_exceeded()
                if candidate['notice']:
                    processes.update(runtime_notice_ids(connection, candidate['session_id'], messages))
                elif 'tool_calls' in messages:
                    compressions = compression_ids(connection, candidate['session_id'], messages)
                    if compressions and {'timestamp', 'active', 'compacted'} <= messages:
                        from .native_catalog import _native_rewrites
                        # Match history's proven retained-copy identity, never
                        # equal text alone. SQL compares identity; only IDs cross
                        # into Python, under this request's existing SQL budget.
                        rewrites = _native_rewrites(connection, candidate['session_id'], messages)
                        compressions.update(new for old, new in rewrites.items() if old in compressions)
                    processes.update(compressions)
                if len(processes) > MAX_MATCHED_SESSIONS:
                    _budget_exceeded()
            visible += ' AND id NOT IN (SELECT value FROM json_each(?))'
            body_params = (json.dumps(sorted(processes)), *params)
        else:
            body_params = params
        provenance = ' OR '.join(f"COALESCE({name},'')!=''" for name in
                                 ('display_kind', 'display_metadata', 'platform_message_id') if name in messages) or '0'
        sidecar = 'codex_message_items' if 'codex_message_items' in messages else 'NULL'
        size = 'max(COALESCE(length(CAST(content AS BLOB)),0),COALESCE(length(CAST(' + sidecar + ' AS BLOB)),0))'
        # One body scan for the complete eligible catalogue, not one per row or
        # one per count/page. Only deduplicated IDs cross the SQLite boundary.
        query = ("SELECT DISTINCT session_id FROM messages WHERE role IN ('user','assistant')"
                 + visible + ' AND session_id IN (SELECT id FROM sessions ' + where + ')'
                 + ' AND (CASE WHEN ' + size + '>' + str(MAX_SEARCH_FIELD_BYTES)
                 + ' THEN catalog_search_overflow() ELSE catalog_public_body(role,content,('
                 + provenance + '),' + sidecar + ") END) LIKE ? ESCAPE '\\' LIMIT ?")
        matches = [row[0] for row in connection.execute(query, (*body_params, term, MAX_MATCHED_SESSIONS + 1))]
        if len(matches) > MAX_MATCHED_SESSIONS or time.monotonic() > deadline:
            raise sqlite3.OperationalError('Catalogue search budget exceeded')
    where += (" AND (COALESCE(title,'') LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\'"
              ' OR id IN (SELECT value FROM json_each(?)))')
    check_deadline()
    return where, (*params, term, term, json.dumps(matches)), check_deadline
