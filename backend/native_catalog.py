"""Read-only native history catalogue; never writes Hermes SQLite state.

A narrow fallback for inventory while an authenticated gateway API is unavailable.
Native resume/execution is deliberately handled separately via the gateway contract.
"""
from pathlib import Path
import json
import re
import sqlite3
from backend.tool_presentation import tool_summary
from backend.context_compression_presentation import compression_ids
from backend.runtime_notice_presentation import runtime_notice_ids
from backend.task_reminder_presentation import reminder_projections, matches_user


# Expansion, not target-page limits: keep ordinary multi-hundred tool turns whole.
TURN_PAGE_MAX_ROWS = 10_000
TURN_PAGE_MAX_EXTRA_BYTES = 8 * 1024 * 1024
_TURN_PUBLIC_BYTES = ('COALESCE(length(CAST(content AS BLOB)),0)'
                      '+COALESCE(length(CAST(tool_calls AS BLOB)),0)')
_TURN_METADATA = ("id,role,CASE WHEN role='user' AND (" + _TURN_PUBLIC_BYTES + ')<='
                  + str(TURN_PAGE_MAX_EXTRA_BYTES) + ' THEN content END AS content,'
                  + _TURN_PUBLIC_BYTES + ' AS public_bytes')


def _turn_page_start(candidates, requested_offset, count, processes=()):
    """Consume newest-first boundary metadata, never a whole transcript."""
    offset, extra_bytes = requested_offset, 0
    for distance, row in enumerate(candidates):
        if count + distance > TURN_PAGE_MAX_ROWS:
            return offset, 'row_cap'
        if distance:
            extra_bytes += row['public_bytes']
            if extra_bytes > TURN_PAGE_MAX_EXTRA_BYTES:
                return offset, 'byte_cap'
        offset = requested_offset - distance
        if offset == 0:
            break
        if row['role'] == 'user' and row['id'] not in processes:
            if row['public_bytes'] > TURN_PAGE_MAX_EXTRA_BYTES:
                # Do not load an oversized user envelope just to classify it.
                return offset, 'byte_cap'
            if not _non_turn_user(row['content']):
                break
    return offset, None


def _composed_page(connection, session_id, visibility, synthetic, offset, limit, total, turn_boundary, processes=()):
    """Merge ordered IDs with journal rows, retaining only a bounded suffix."""
    from collections import deque
    from heapq import merge
    from itertools import islice

    if offset >= total:
        return [], offset, None
    end = min(total, offset + limit)
    native = ((row['id'], -1, row['id']) for row in connection.execute(
        'SELECT id FROM messages WHERE session_id=?' + visibility + ' ORDER BY id', (session_id,)))
    ordered = merge(native, sorted(synthetic, key=lambda row: (row[0], row[1])),
                    key=lambda row: (row[0], row[1]))
    retained = list(deque(islice(ordered, end),
                         maxlen=TURN_PAGE_MAX_ROWS + 1 if turn_boundary else limit))
    start = end - len(retained)

    def candidates():
        for index in range(offset - start, -1, -1):
            item = retained[index][2]
            if isinstance(item, int):
                yield connection.execute('SELECT ' + _TURN_METADATA
                                         + ' FROM messages WHERE session_id=? AND id=?',
                                         (session_id, item)).fetchone()
            else:
                public_bytes = len((item.get('content') or '').encode('utf-8'))
                if item.get('tool_calls'):
                    public_bytes += len(json.dumps(item['tool_calls']).encode('utf-8'))
                yield dict(item, public_bytes=public_bytes)

    reason = None
    if turn_boundary:
        offset, reason = _turn_page_start(candidates(), offset, end - offset, processes)
    return [row[2] for row in retained[offset - start:]], offset, reason


def _tool_calls(value):
    try:
        calls = json.loads(value) if value else []
    except (ValueError, TypeError):
        return []
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _remember_tool_names(names, calls, summaries):
    for call in calls:
        function = call.get('function')
        if isinstance(function, dict) and isinstance(call.get('id'), str) and isinstance(function.get('name'), str):
            names[call['id']] = function['name']
            call['summary'] = tool_summary(function['name'], function.get('arguments'))
            summaries[call['id']] = call['summary']


def _tool_status(content):
    """Absence of a reported error is not evidence of success."""
    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        return 'unknown'
    if not isinstance(result, dict):
        return 'unknown'
    status = result.get('status')
    exit_code = result.get('exit_code')
    has_exit_code = type(exit_code) is int
    if (result.get('error') or result.get('success') is False or result.get('isError') is True
            or status in ('failed', 'error', 'timeout', 'cancelled') or (has_exit_code and exit_code != 0)):
        return 'failed'
    if result.get('success') is True or status in ('success', 'ok') or (has_exit_code and exit_code == 0):
        return 'success'
    if status == 'completed':
        return 'completed'
    return 'unknown'


def _delegation_status(content):
    """Recognize only the native batch-completion envelope, never mentions."""
    if not isinstance(content, str) or not re.match(r'\A\[ASYNC DELEGATION BATCH COMPLETE — [^\]\r\n]+\](?:\r?\n|\Z)', content):
        return None
    headers = re.findall(r'^--- [✓✗⚠] TASK \d+/\d+(?:(?!^--- ).)*?  \(status=([^,\s)]+)([^\n]*)\) ---$', content, re.MULTILINE | re.DOTALL)
    outcomes = []
    for status, detail in headers:
        if 'TRUNCATED:' in detail:
            outcomes.append('mixed')
        elif status in ('completed', 'success'):
            outcomes.append('completed')
        elif status in ('failed', 'error', 'timeout', 'cancelled'):
            outcomes.append('failed')
        else:
            outcomes.append('mixed')
    if outcomes:
        return outcomes[0] if len(set(outcomes)) == 1 else 'mixed'
    if re.search(r'^--- ERROR ---\nThe batch did not complete successfully:', content, re.MULTILINE):
        return 'failed'
    # The envelope proves completion, not a successful outcome.
    return 'completed'


def _native_guidance(content):
    """Only a whole native OOB envelope is a non-turn user record."""
    if not isinstance(content, str):
        return None
    opening = ('[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered '
               'once at this position; not tool output and not a new delivery when replayed '
               'from conversation history]')
    match = re.fullmatch(re.escape(opening) + r'\r?\n(.*?)\r?\n\[/OUT-OF-BAND USER MESSAGE\]',
                         content, re.DOTALL)
    return match.group(1) if match else None


def _non_turn_user(content):
    return _delegation_status(content) is not None or _native_guidance(content) is not None


def _ordinary_user(row, processes=()):
    return (row['role'] == 'user' and row['id'] not in processes
            and not _non_turn_user(row['content']))


def _first_turn_row(rows, processes=()):
    """Only explicit non-turn rows may precede an admitted user."""
    return next((row for row in rows if row['role'] != 'system'
                 and row['id'] not in processes
                 and not (row['role'] == 'user' and _non_turn_user(row['content']))), None)


def _completed_turn(rows, run, tool_events=(), processes=(), reminders=()):
    """Match one anchored turn and prove recorded tools, never equal text later.

    Anonymous callbacks cannot be attributed by globally matching tool names.
    Keep the coherent journal overlay unless every recorded call has a native
    result ID in this same admitted turn. Final text alone proves no tools.
    """
    expected = set()
    for event in tool_events:
        data = event['data']
        call_id = data.get('tool_call_id') or data.get('toolCallId') or data.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            return False
        expected.add(call_id)
    first = _first_turn_row(rows, processes)
    if not matches_user(first, run, reminders):
        return False
    last, results = first, set()
    for row in rows:
        if row['id'] in processes:
            continue
        if _ordinary_user(row, processes):
            break
        if row['role'] == 'tool' and 'tool_call_id' in row.keys():
            results.add(row['tool_call_id'])
        last = row
    return (last['role'] == 'assistant' and not _tool_calls(last['tool_calls'])
            and last['content'] == run['output'] and expected <= results)


def _journal_turn(run, events):
    """Materialize one proved, completed admission before a later native turn."""
    common = {'tool_calls': None, 'timestamp': run['created_at'], 'run_id': run['id'],
              'run_status': run['status'], 'source': 'journal', 'run_error': run['error']}
    prefix = 'journal:' + run['id'] + ':'
    items = [dict(common, id=prefix + 'user', role='user', content=run['input'])]
    pending, text_parts = [], []

    def public_text(text, event_id, observed_at, timed_chunks=None):
        if text.strip():
            item = dict(common, id=prefix + 'text:' + str(event_id), role='assistant',
                        channel='commentary', content=text, timestamp=observed_at, observed_at=observed_at)
            if timed_chunks is not None:
                item['timed_chunks'] = timed_chunks
            items.append(item)

    def flush_text():
        if text_parts:
            # One public disclosure, but no loss of recorded receipt boundaries:
            # late background results may need to split it after materialization.
            public_text(''.join(text for _, text, _ in text_parts), text_parts[0][0], text_parts[0][2],
                        [dict(text=text, observed_at=stamp) for _, text, stamp in text_parts]
                        if len(text_parts) <= 8192 else [])
            text_parts.clear()

    for event in events:
        if event['name'] == 'delta':
            text_parts.append((event['id'], event['data']['text'], event.get('observed_at')))
            continue
        if event['name'] == 'commentary':
            flush_text()
            public_text(event['data']['text'], event['id'], event.get('observed_at'))
            continue
        if event['name'] == 'steering':
            flush_text()
            data = event['data']
            items.append(dict(id=prefix + 'steering:' + data['id'], role='user', kind='guidance',
                              content=data['input'], tool_calls=None, timestamp=data.get('created_at'),
                              run_id=run['id'], source='journal', steering_id=data['id'],
                              idempotency_key=data['idempotency_key'], steer_id=data.get('steer_id'),
                              steering_status=data['status'], turn_boundary=False))
            continue
        if event['name'] != 'tool':
            continue
        data = event['data']
        name = data.get('name') or data.get('tool') or data.get('tool_name') or 'Tool'
        call_id = data.get('tool_call_id') or data.get('toolCallId') or data.get('call_id')
        status = data.get('status') or data.get('event', '').removeprefix('tool.')
        running = status in ('started', 'running', 'working', 'pending')
        previous = next((item for item in pending if (item.get('tool_call_id') == call_id
                                                     if call_id else item['name'] == name)), None)
        item = dict(common, id=prefix + str(event['id']), role='tool', content='', name=name,
                    tool_call_id=call_id, summary=data.get('summary') or (previous or {}).get('summary') or name,
                    status=status, error=data.get('error'), is_error=data.get('is_error'),
                    timestamp=event.get('observed_at'), observed_at=event.get('observed_at'), duration=data.get('duration'))
        if previous is not None and not running:
            pending.remove(previous)
            # Completion updates outcome, not where the call originally appeared.
            previous.update({key: value for key, value in item.items() if key not in ('id', 'timestamp', 'observed_at')})
        else:
            flush_text()
            items.append(item)
            if running:
                pending.append(item)
    # Authoritative output replaces the uncommitted tail, equal or different.
    # Earlier boundary-committed progress stays; without output keep the draft.
    if run['output'] is None:
        flush_text()
    for item in pending:
        item['status'] = 'unknown'
    final = dict(common, id=prefix + 'assistant', role='assistant', content=run['output'],
                 timestamp=run.get('updated_at'), observed_at=run.get('updated_at'))
    # These are already allowlisted public replay deltas, not visible progress.
    # Only a separately loaded, owned background receipt can commit a prefix.
    # Omit oversized metadata wholesale rather than impute missing boundaries.
    if text_parts and len(text_parts) <= 8192 and sum(len(text) for _, text, _ in text_parts) <= 262144:
        final['public_tail'] = [dict(text=text, observed_at=stamp) for _, text, stamp in text_parts]
    items.append(final)
    # Presentation-only messages travel with their following raw row. Paging and
    # turn expansion still operate on the original composed sequence.
    raw, guidance = [], []
    for item in items:
        if item.get('kind') == 'guidance':
            guidance.append(item)
        else:
            if guidance:
                item['_guidance_before'] = guidance
                guidance = []
            raw.append(item)
    return raw


def _native_rewrites(connection, session_id, columns):
    """Resolve compaction copies, not equal text from distinct native turns.

    archive_and_compact preserves this public identity while changing row IDs.
    Require an archived source and a unique live successor (or newest archived
    generation). Multiple live identities are ambiguous and remain untouched.
    Query IDs only: do not materialize the full tool transcript or sidecars.
    """
    if not {'active', 'compacted'} <= columns:
        return {}
    identity = ['role', 'content', 'timestamp', 'tool_calls']
    identity += [name for name in ('tool_call_id', 'tool_name', 'display_kind',
                                  'display_metadata', 'platform_message_id') if name in columns]
    partition = ','.join(identity)
    # One identity sort/window, rather than archived × later-row comparisons.
    # SQLite retains exact NULL-safe identity semantics; Python receives IDs only.
    pairs = connection.execute(
        'WITH identities AS (SELECT id,active,compacted,'
        ' SUM(active=1) OVER identity_group AS live_count,'
        ' MAX(CASE WHEN active=1 THEN id END) OVER identity_group AS live_id,'
        ' MAX(id) OVER identity_group AS newest_id'
        ' FROM messages WHERE session_id=? AND timestamp IS NOT NULL'
        ' AND (active=1 OR compacted=1)'
        ' WINDOW identity_group AS (PARTITION BY ' + partition + '))'
        ' SELECT id AS old_id,CASE WHEN live_id>id THEN live_id ELSE newest_id END AS new_id'
        ' FROM identities WHERE active=0 AND compacted=1 AND live_count<=1'
        ' AND newest_id>id', (session_id,))
    return {row['old_id']: row['new_id'] for row in pairs}


def _rewritten_turn_ids(connection, session_id, anchor, run, rewrites, processes=(), reminders=()):
    """Prove admission in the oldest usable anchor generation, then trace IDs.

    Each generation ends at its first ordinary user or reinserted older row.
    Never widen a relocated start into an interval across summary/new-turn rows.
    Only IDs accumulate; public bodies are streamed one row at a time.
    """
    if not anchor or anchor['canonical_session_id'] != session_id or not rewrites:
        return None
    original = anchor['message_id']
    target = rewrites.get(original, original)
    anchors = sorted({original, target} | {old for old, new in rewrites.items() if new == target})
    origins = {}
    for old, new in rewrites.items():
        origins[new] = min(old, origins.get(new, old))
    query = ('SELECT id,role,content FROM messages WHERE session_id=? AND id>?'
             ' AND (active=1 OR compacted=1) ORDER BY id')
    admitted = None
    for boundary in anchors:
        first = _first_turn_row(iter(connection.execute(query, (session_id, boundary))), processes)
        if matches_user(first, run, reminders):
            admitted = first['id']
            break
    if admitted is None:
        return None
    current = rewrites.get(admitted, admitted)
    copies = sorted({admitted, current} | {old for old, new in rewrites.items() if new == current})
    owned = {current}
    later_user = False
    for start in copies:
        for row in connection.execute(query, (session_id, start)):
            if row['id'] in processes:
                continue
            if _ordinary_user(row, processes):
                later_user = later_user or row['id'] not in rewrites
                break
            # A cloned earlier context row starts another generation, even if
            # that compaction did not emit a user-role summary.
            if origins.get(row['id'], row['id']) <= origins.get(start, start):
                break
            resolved = rewrites.get(row['id'], row['id'])
            if origins.get(resolved, row['id']) < start and resolved not in owned:
                # This retained row existed before this user copy but was not
                # inside an earlier proved turn. Its old end boundary may have
                # aged out; relocation must not assign it to the overlay.
                break
            owned.add(resolved)
    return owned, later_user


def _relocated_anchor(anchor, rewrites):
    return dict(anchor, message_id=rewrites.get(anchor['message_id'], anchor['message_id'])) if anchor else None


class NativeCatalog:
    def __init__(self, profiles):
        self.profiles = {name: Path(path).resolve() for name, path in profiles.items()}

    def _connect(self, profile):
        path = self.profiles[profile] / 'state.db'
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    def sessions(self, profile, limit=50, offset=0, q='', kind='chats', *, excluded_ids=()):
        from contextlib import closing
        from backend.catalog_search import search_filter
        limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
        with closing(self._connect(profile)) as c:
            c.execute('BEGIN')
            columns = {row[1] for row in c.execute('PRAGMA table_info(sessions)')}
            where, params, check_deadline = search_filter(c, columns, kind=kind, q=q, excluded_ids=excluded_ids)
            total = c.execute('SELECT count(*) FROM sessions ' + where, params).fetchone()[0]
            check_deadline()
            rows = c.execute('SELECT id,title,source,started_at,last_activity_at,parent_session_id FROM sessions ' + where + ' ORDER BY COALESCE(last_activity_at,started_at) DESC,id LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
            items = [dict(row, updated_at=row['last_activity_at'] or row['started_at']) for row in rows]
        check_deadline()
        return {'items': items, 'total': total}

    def jobs(self, profile):
        import json
        path = self.profiles[profile] / 'cron' / 'jobs.json'
        if not path.exists():
            return {'items': []}
        payload = json.loads(path.read_text())
        jobs = payload.get('jobs', []) if isinstance(payload, dict) else payload
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        allowed = ('id','name','prompt','schedule','enabled','state','next_run_at','last_run_at','last_status','no_agent')
        return {'items': [{key: job.get(key) for key in allowed} for job in jobs]}

    def conversation_roots(self, profile, session_ids):
        """Current compression identity, not an admission/history watermark.

        Mirror SessionDB's turn-lease parent walk: compression-ended parents
        only, with branch/delegate markers bound to that exact parent (copied
        markers on a later compression child do not make it a new fork).
        One read-only snapshot is closed before admission can dispatch upstream.
        """
        from contextlib import closing
        with closing(self._connect(profile)) as c:
            c.execute('BEGIN')
            available = {row[1] for row in c.execute('PRAGMA table_info(sessions)')}
            fields = ','.join(name for name in ('id', 'parent_session_id', 'end_reason', 'source', 'model_config')
                              if name in available)
            rows, roots = {}, {}

            def row_for(sid):
                if sid not in rows:
                    row = c.execute('SELECT ' + fields + ' FROM sessions WHERE id=?', (sid,)).fetchone()
                    rows[sid] = dict(row) if row else {}
                return rows[sid]

            for sid in session_ids:
                current, seen = sid, set()
                while True:
                    if current in seen or len(seen) >= 100:
                        raise ValueError('Ambiguous native compression lineage')
                    seen.add(current)
                    row = row_for(current)
                    parent = row.get('parent_session_id')
                    if not parent or row.get('source') == 'tool':
                        break
                    config = json.loads(row.get('model_config') or '{}')
                    if not isinstance(config, dict):
                        raise ValueError('Invalid native compression provenance')
                    if any(config.get(marker) == parent for marker in
                           ('_branched_from', '_delegate_from', '_reset_from')):
                        break
                    if row_for(parent).get('end_reason') != 'compression':
                        break
                    current = parent
                roots[sid] = current
            return roots

    def history_anchor(self, profile, session_id, canonical_session_id=None):
        """Capture an ID boundary, including hidden rows; never a text heuristic."""
        from contextlib import closing
        with closing(self._connect(profile)) as c:
            if not c.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise KeyError(session_id)
            message_id = c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE session_id=?', (session_id,)).fetchone()[0]
        return {'session_id': session_id, 'canonical_session_id': canonical_session_id or session_id,
                'message_id': message_id}

    def messages(self, profile, session_id, limit=100, offset=0, latest=False, *, snapshot=None, turn_boundary=False):
        from contextlib import closing
        limit, offset = max(1, min(int(limit), 500)), max(0, int(offset))
        run, anchor = (snapshot['run'], snapshot['anchor']) if snapshot else (None, None)
        overlay = run
        has_guidance = bool(snapshot and any(event['name'] == 'steering'
                                            for event in snapshot.get('replay_events', ())))
        has_public_text = bool(snapshot and any(event['name'] in ('delta', 'commentary')
                                               for event in snapshot.get('replay_events', ())))
        synthetic = []
        conservative = False
        with closing(self._connect(profile)) as c:
            # Count, turn-completion proof, and page must share one native view.
            c.execute('BEGIN')
            if not c.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise KeyError(session_id)
            columns={row[1] for row in c.execute('PRAGMA table_info(messages)')}
            compressions = compression_ids(c, session_id, columns)
            notices = runtime_notice_ids(c, session_id, columns)
            rewrites = _native_rewrites(c, session_id, columns)
            # Copies share the complete public identity, including provenance.
            compressions.update(new for old, new in rewrites.items() if old in compressions)
            notices.update(new for old, new in rewrites.items() if old in notices)
            processes = compressions | notices
            reminders = reminder_projections(c, session_id, columns, snapshot, compressions, processes, rewrites)
            process_keep = (' OR id IN (' + ','.join(str(value) for value in sorted(processes)) + ')'
                                if processes else '')
            visibility=' AND (active=1 OR compacted=1)' if {'active','compacted'}<=columns else (' AND active=1' if 'active' in columns else '')
            if rewrites:
                visibility += ' AND id NOT IN (' + ','.join(str(int(value)) for value in rewrites) + ')'
            rewritten_turn = _rewritten_turn_ids(c, session_id, anchor, run, rewrites, processes, reminders)
            anchor = _relocated_anchor(anchor, rewrites)
            # Recover prior accepted input/output only within its admission ID
            # interval. Equal text in other turns is never evidence of persistence.
            prior = snapshot.get('prior', []) if snapshot else []
            for index, entry in enumerate(prior):
                old, start = entry['run'], _relocated_anchor(entry['anchor'], rewrites)
                if start is None and c.execute('SELECT 1 FROM messages WHERE session_id=?'+visibility+' LIMIT 1', (session_id,)).fetchone():
                    # Legacy attribution is unknown: existing native history is
                    # authoritative, just as for the latest unanchored run.
                    continue
                end = _relocated_anchor((prior[index + 1] if index + 1 < len(prior) else snapshot)['anchor'], rewrites)
                boundary = start['message_id'] if start else 0
                matched = complete = False
                if (start and end and start['canonical_session_id'] == session_id
                        and start['session_id'] == session_id and end['session_id'] == session_id):
                    query = ('SELECT id,role,content,tool_calls FROM messages WHERE session_id=? AND id>? AND id<=?'
                             + visibility + ' ORDER BY id')
                    args = (session_id, boundary, end['message_id'])
                    first = _first_turn_row(iter(c.execute(query, args)), processes)
                    matched = matches_user(first, old, reminders)
                    complete = matched and isinstance(old['output'], str) and _completed_turn(iter(c.execute(query, args)), old, processes=processes, reminders=reminders)
                if entry.get('replay_events'):
                    # Prove ownership in the original/archived generation before
                    # following copies. A relocated interval can cross an external
                    # turn whose user boundary was not reinserted by compaction.
                    rewritten_prior = _rewritten_turn_ids(c, session_id, entry['anchor'], old, rewrites, processes, reminders)
                    if rewritten_prior is not None:
                        owned, _ = rewritten_prior
                        visibility += ' AND id NOT IN (' + ','.join(str(value) for value in sorted(owned)) + ')'
                    elif matched and not rewrites:
                        following = next((row['id'] for row in c.execute(query, args)
                                          if row['id'] > first['id'] and _ordinary_user(row, processes)), None)
                        visibility += ' AND (id<' + str(int(first['id']))
                        visibility += ' OR id>=' + str(int(following)) if following is not None else ' OR id>' + str(int(end['message_id']))
                        visibility += process_keep + ')'
                    synthetic.extend((boundary, index, item)
                                     for item in _journal_turn(old, entry['replay_events']))
                    continue
                for role, content, present in [('user', old['input'], matched), ('assistant', old['output'], complete)]:
                    if present or content is None:
                        continue
                    position = end['message_id'] if role == 'assistant' and matched and end else boundary
                    item = {'id': 'journal:' + old['id'] + ':' + role, 'role': role,
                            'content': content, 'tool_calls': None,
                            'timestamp': old['created_at'] if role == 'user' else old.get('updated_at'),
                            'run_id': old['id'], 'run_status': old['status'], 'source': 'journal',
                            'reconciliation': 'unverified', 'run_error': old['error']}
                    synthetic.append((position, index, item))
            if anchor:
                history_position = None
                if (run['status'] == 'completed' and isinstance(run['output'], str)
                        and anchor['canonical_session_id'] == session_id):
                    call_field = ',tool_call_id' if 'tool_call_id' in columns else ''
                    tail = c.execute('SELECT id,role,content,tool_calls' + call_field + ' FROM messages WHERE session_id=? AND id>?'
                                     + visibility + ' ORDER BY id', (session_id, anchor['message_id']))
                    if not has_guidance and not has_public_text and _completed_turn(iter(tail), run, snapshot.get('tool_events', ()), processes, reminders):
                        overlay = None
                if overlay and rewritten_turn is not None:
                    owned, conservative = rewritten_turn
                    later = c.execute("SELECT id,role,content FROM messages WHERE session_id=? AND role='user' AND id>?"
                                      + visibility + ' ORDER BY id', (session_id, max(owned)))
                    if _first_turn_row(iter(later), processes) is not None:
                        history_position = min(owned)
                    visibility += ' AND id NOT IN (' + ','.join(str(value) for value in sorted(owned)) + ')'
                elif overlay:
                    # Only the first anchored turn belongs to the overlay. A
                    # later external user starts a distinct native turn.
                    tail = c.execute('SELECT id,role,content FROM messages WHERE session_id=? AND id>?'
                                     + visibility + ' ORDER BY id', (session_id, anchor['message_id']))
                    first = _first_turn_row(iter(tail), processes)
                    matched = (matches_user(first, run, reminders)
                               and anchor['canonical_session_id'] == session_id)
                    if matched:
                        next_user = next((row['id'] for row in tail if _ordinary_user(row, processes)), None)
                        visibility += ' AND (id<' + str(int(first['id']))
                        visibility += (' OR id>=' + str(int(next_user))) if next_user is not None else ''
                        visibility += process_keep + ')'
                        conservative = next_user is not None
                        if next_user is not None:
                            history_position = first['id']
                    elif first is not None:
                        conservative = True
                if (overlay and history_position is not None and run['status'] == 'completed'
                        and isinstance(run['output'], str) and (snapshot.get('tool_events') or has_guidance or has_public_text)):
                    # A completed old web turn must remain before later CLI/WA
                    # turns. Reuse exactly the proved native ownership boundary;
                    # do not search for matching text or attach tools at the end.
                    synthetic.extend((history_position, len(prior), item)
                                     for item in _journal_turn(run, snapshot['replay_events']))
                    overlay = None
            total = c.execute('SELECT count(*) FROM messages WHERE session_id=?'+visibility, (session_id,)).fetchone()[0]
            if not anchor and total:
                overlay = None
            total += len(synthetic)
            if latest:
                offset = max(0, total - limit)
            requested_offset, boundary_reason = offset, None
            if turn_boundary and not synthetic and offset < total:
                # Walk only public boundary data, not complete tool transcripts.
                candidates = c.execute('SELECT ' + _TURN_METADATA
                                       + ' FROM messages WHERE session_id=?'
                                       + visibility + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                                       (session_id, min(offset + 1, TURN_PAGE_MAX_ROWS + 1), total - offset - 1))
                offset, boundary_reason = _turn_page_start(candidates, offset, min(limit, total - offset), processes)
                limit += requested_offset - offset
            fields = ['id', 'role', 'content', 'tool_calls', 'timestamp']
            fields += [name for name in ('tool_name', 'tool_call_id', 'effect_disposition', 'codex_message_items') if name in columns]
            selected = None
            if synthetic:
                selected, offset, boundary_reason = _composed_page(
                    c, session_id, visibility, synthetic, offset, limit, total, turn_boundary, processes)
                ids = [value for value in selected if isinstance(value, int)]
                # A contiguous composed range also contains a contiguous native
                # range under the same visibility predicate; no huge IN clause.
                rows = (c.execute('SELECT '+','.join(fields)+' FROM messages WHERE session_id=?'
                                  + visibility + ' AND id>=? AND id<=? ORDER BY id',
                                  (session_id, ids[0], ids[-1])).fetchall() if ids else [])
            else:
                rows = c.execute('SELECT '+','.join(fields)+' FROM messages WHERE session_id=?'+visibility+' ORDER BY id LIMIT ? OFFSET ?', (session_id, limit, offset)).fetchall()
            names = {}
            summaries = {}
            if rows and any(row['role'] == 'tool' for row in rows):
                # A latest page may start after its assistant's parallel calls.
                previous = c.execute("SELECT tool_calls FROM messages WHERE session_id=? AND role='assistant' AND id<? AND tool_calls IS NOT NULL"+visibility+' ORDER BY id', (session_id, rows[0]['id']))
                for row in previous:
                    _remember_tool_names(names, _tool_calls(row['tool_calls']), summaries)
            items = []
            for row in rows:
                item = dict(row)
                if item['id'] in reminders:
                    item.update({key: value for key, value in reminders[item['id']].items() if key != 'run_id'})
                compression = item['id'] in compressions
                sidecar = item.pop('codex_message_items', None)
                if item['role'] == 'assistant' and sidecar:
                    from .public_commentary import project_public_commentary
                    commentary = project_public_commentary(item['id'], sidecar)
                    if commentary:
                        item['public_commentary'] = commentary
                if item['tool_calls']:
                    item['tool_calls'] = _tool_calls(item['tool_calls'])
                if item['role'] == 'assistant':
                    _remember_tool_names(names, item['tool_calls'] or [], summaries)
                if item['role'] == 'tool':
                    item['name'] = item.get('tool_name') or names.get(item.get('tool_call_id')) or 'tool'
                    item['summary'] = summaries.get(item.get('tool_call_id')) or tool_summary(item['name'])
                    item['status'] = 'unknown' if item.get('effect_disposition') == 'unknown' else _tool_status(item['content'])
                elif item['role'] == 'user':
                    status = _delegation_status(item['content'])
                    if status is not None:
                        item.update(role='tool', name='delegate_task', kind='delegation', status=status)
                if compression:
                    item.update(role='tool', kind='context_compression',
                                name='Context compression', status='completed')
                if item['id'] in notices:
                    item.update(role='tool', kind='runtime_notice',
                                name='Tool limit reached', status='completed')
                items.append(item)
            if selected is not None:
                by_id = {item['id']: item for item in items}
                items = [by_id[value] if isinstance(value, int) else value for value in selected]
        raw_count = len(items)
        expanded = []
        for item in items:
            if item.get('source') == 'journal' and item.get('role') == 'user' and item.get('kind') != 'guidance':
                children = [child for projection in reminders.values() if projection['run_id'] == item.get('run_id')
                            for child in projection['runtime_reminders']]
                if children:
                    item['runtime_reminders'] = children
            expanded.extend(item.pop('_guidance_before', []))
            expanded.append(item)
        page = {'items': expanded, 'total': total, 'offset': offset}
        if len(expanded) != raw_count:
            page['count'] = raw_count
        if turn_boundary:
            page.update(count=raw_count, turn_boundary={
                'requested_offset': requested_offset, 'expanded': offset < requested_offset,
                'complete': boundary_reason is None, 'truncated': boundary_reason is not None,
                'reason': boundary_reason, 'max_rows': TURN_PAGE_MAX_ROWS,
                'max_extra_bytes': TURN_PAGE_MAX_EXTRA_BYTES,
            })
        if overlay:
            children = [child for projection in reminders.values() if projection['run_id'] == overlay['id']
                        for child in projection['runtime_reminders']]
            if children:
                overlay = dict(overlay, runtime_reminders=children)
        if snapshot is not None:
            mode = 'overlay' if overlay else ('legacy-unanchored' if run and not anchor else 'history')
            page.update(run=overlay, last_run=overlay or run, snapshot={'mode': mode, 'anchored': anchor is not None})
            if conservative or synthetic:
                page['snapshot']['reconciliation'] = 'conservative-union'
        return page
