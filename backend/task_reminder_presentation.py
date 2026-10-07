"""Prove display-only task suffixes from an owned admission and native turn.

No native SDK imports or model-history mutation. Literal grammar mirrors
agent/conversation_compression.py and tools/todo_tool.py. Unknown/multiline
variants deliberately remain human text. Marker presence alone proves nothing.
"""
import json
import re
from bisect import bisect_right
from collections import Counter
from math import isfinite

HEADER = '[Your active task list was preserved across context compression]'
_SKILL_HEAD = ('[Skills pruned during compression — reload before acting on these tasks]\n'
    'The task list above crossed the compression boundary verbatim, but the skill instructions that governed it were pruned. Before '
    'executing any preserved task that depends on these skills, reload them first: ')
_SKILL_END = ('. After reloading, re-check that each pending task is still justified — findings recorded before the boundary may have invalidated it.')
MAX_ROWS = 8192
MAX_BYTES = 1024 * 1024
MAX_WORK = 4 * MAX_ROWS


def suffix(content, human):
    if not isinstance(content, str) or not isinstance(human, str) or not human.strip():
        return None
    prefix = human + '\n\n'
    if not content.startswith(prefix) or len(content.encode('utf-8')) > MAX_BYTES:
        return None
    body = content[len(prefix):]
    task, sep, skills = body.partition('\n\n')
    lines = task.split('\n')
    if lines[0] != HEADER or not 2 <= len(lines) <= 257:
        return None
    ids = set()
    for line in lines[1:]:
        match = re.fullmatch(r'- \[([ >])\] ([A-Za-z0-9_-]{1,128})\. ([^\r\n]+) \((pending|in_progress)\)', line)
        if (not match or (match[1] == '>') != (match[4] == 'in_progress') or match[2] in ids
                or len(match[3]) > 4000 or match[3] != match[3].strip()):
            return None
        ids.add(match[2])
    if sep:
        if not skills.startswith(_SKILL_HEAD) or not skills.endswith(_SKILL_END):
            return None
        calls = skills[len(_SKILL_HEAD):-len(_SKILL_END)].split('; ')
        if not 1 <= len(calls) <= 20 or len(calls) != len(set(calls)) or any(
                not re.fullmatch(r"skill_view\(name='[A-Za-z0-9_:/.-]{1,128}'\)", call) for call in calls):
            return None
    return body


def _time(value):
    return type(value) in (float, int) and isfinite(value) and value > 0


def reminder_projections(connection, session_id, columns, snapshot, compressions, processes, rewrites):
    """Return ID-bound projections, only for uniquely anchored complete turns.

    Budgets are per request, not per candidate. SQL loads no private sidecars,
    tool result bodies, or arguments. A known compression record within the
    run's recorded lifetime is required; a final answer must match in the same
    first admitted native turn. Shared admissions, provenance conflicts,
    missing timing/fields, and incomplete/active turns fail closed.
    """
    from .native_catalog import _relocated_anchor, _first_turn_row, _ordinary_user, _completed_turn
    if not snapshot or not {'id', 'role', 'content', 'tool_calls', 'timestamp'} <= columns:
        return {}
    entries = [*snapshot.get('prior', ()), snapshot]
    used_work = 2 * len(entries) + len(compressions) + len(rewrites)
    if (max(len(entries), len(compressions), len(rewrites)) > MAX_ROWS
            or used_work > MAX_WORK):
        return {}
    anchors = [_relocated_anchor(e.get('anchor'), rewrites) for e in entries]
    counts = Counter(a['message_id'] for a in anchors if a and a['session_id'] == session_id)
    boundaries = sorted(counts)
    ends = dict(zip(boundaries, boundaries[1:]))
    origins = {}
    for old, new in rewrites.items():
        origins[new] = min(old, origins.get(new, old))
    projections, used_rows, used_bytes = {}, 0, 0
    # Load each compression timestamp once, including misses in the work
    # budget. Bounded IN batches avoid both SQLite variable limits and the
    # former journal-entry x compression SELECT loop. Interval lookup below
    # is logarithmic, not another entry x timestamp scan in Python.
    stamps = []
    compression_list = sorted(compressions)
    for offset in range(0, len(compression_list), 256):
        batch = compression_list[offset:offset + 256]
        used_work += 1
        if used_work > MAX_WORK:
            return {}
        for row in connection.execute('SELECT timestamp FROM messages WHERE session_id=? AND id IN ('
                + ','.join('?' for _ in batch) + ') LIMIT ?', (session_id, *batch, len(batch) + 1)):
            used_rows += 1
            if used_rows > MAX_ROWS:
                return {}
            if _time(row[0]):
                stamps.append(row[0])
    stamps.sort()
    for index, entry in enumerate(entries):
        # Reserve both candidate and original-generation SQL, even if either
        # returns zero rows. Per-row work below reserves any body/ID SELECT.
        used_work += 2
        if used_work > MAX_WORK:
            return {}
        run, anchor = entry.get('run'), anchors[index]
        if (not run or not anchor or anchor['session_id'] != session_id
                or anchor['canonical_session_id'] != session_id or run.get('status') != 'completed'
                or not isinstance(run.get('output'), str)
                or not _time(run.get('created_at')) or not _time(run.get('updated_at'))
                or run['updated_at'] < run['created_at']):
            continue
        start = anchor['message_id']
        if counts[start] != 1:
            continue
        end = ends.get(start)
        stamp_index = bisect_right(stamps, run['updated_at']) - 1
        if stamp_index < 0 or stamps[stamp_index] < run['created_at']:
            continue
        compression_time = stamps[stamp_index]
        conflict = ' OR '.join("COALESCE(" + key + ",'')!=''" for key in
            ('tool_calls', 'tool_call_id', 'tool_name', 'display_kind', 'display_metadata', 'platform_message_id') if key in columns) or '0'
        rows, first, reminder = [], None, None
        identity_bytes = 'COALESCE(length(CAST(tool_call_id AS BLOB)),0)' if 'tool_call_id' in columns else '0'
        for meta in connection.execute('SELECT id,role,timestamp,('
                + conflict + ') AS conflict,CASE WHEN role IN (\'user\',\'assistant\') THEN '
                'COALESCE(length(CAST(content AS BLOB)),0) ELSE 0 END + '
                + identity_bytes + ' AS size FROM messages '
                'WHERE session_id=? AND id>? AND (? IS NULL OR id<=?) '
                + ('AND (active=1 OR compacted=1) ' if {'active','compacted'} <= columns else ('AND active=1 ' if 'active' in columns else ''))
                + 'ORDER BY id LIMIT ?', (session_id, start, end, end, MAX_ROWS + 1)):
            used_rows += 1
            used_bytes += meta['size'] or 0
            used_work += 2
            if used_rows > MAX_ROWS or used_bytes > MAX_BYTES or used_work > MAX_WORK:
                return {}  # no partial proof on exhaustion
            if meta['id'] in processes or meta['id'] in rewrites:
                continue
            row = dict(meta, content=None, tool_calls='[{}]' if meta['role'] == 'assistant' and meta['conflict'] else None)
            if meta['role'] in ('user', 'assistant'):
                row['content'] = connection.execute('SELECT content FROM messages WHERE id=?', (meta['id'],)).fetchone()[0]
            if first is not None and _ordinary_user(row, processes):
                break
            if first is None:
                first = _first_turn_row(iter([row]), processes)
                if first is None:
                    continue
                reminder = suffix(first['content'], run['input'])
                if (first['role'] != 'user' or first['conflict'] or reminder is None
                        or not _time(first['timestamp'])
                        or not run['created_at'] <= first['timestamp'] <= compression_time):
                    break
                row['content'] = run['input']
            if meta['role'] == 'tool' and 'tool_call_id' in columns:
                row['tool_call_id'] = connection.execute('SELECT tool_call_id FROM messages WHERE id=?', (meta['id'],)).fetchone()[0]
            rows.append(row)
        if not reminder or not rows or not _completed_turn(iter(rows), run, entry.get('tool_events', ()), processes):
            continue
        if not _time(rows[-1]['timestamp']) or not compression_time <= rows[-1]['timestamp'] <= run['updated_at']:
            continue
        # Relocation cannot cross a new human in the original generation.
        # Compaction can also insert retained assistant/tool history whose
        # originals no longer exist (or whose tool bodies were pruned). Those
        # carriers need both a preceding in-run compression and pre-run time,
        # and must precede the relocated boundary. Human rows still require
        # exact archive-copy identity; a stale timestamp alone is not proof.
        original = entry['anchor']['message_id']
        admitted, retained_history = None, False
        for meta in connection.execute(
                'SELECT id,role,timestamp,CASE WHEN role=\'user\' THEN length(CAST(content AS BLOB)) ELSE 0 END AS size '
                'FROM messages WHERE session_id=? AND id>? AND id<=? ORDER BY id LIMIT ?',
                (session_id, original, first['id'], MAX_ROWS + 1)):
            used_rows += 1
            used_bytes += meta['size'] or 0
            used_work += 2
            if used_rows > MAX_ROWS or used_bytes > MAX_BYTES or used_work > MAX_WORK:
                return {}
            if (meta['id'] in compressions and meta['id'] < start
                    and _time(meta['timestamp'])
                    and run['created_at'] <= meta['timestamp'] <= run['updated_at']):
                retained_history = True
            if meta['id'] in processes or meta['role'] == 'system' or origins.get(meta['id'], meta['id']) <= original:
                continue
            if (retained_history and meta['id'] < start and meta['role'] in ('assistant', 'tool')
                    and _time(meta['timestamp']) and meta['timestamp'] < run['created_at']):
                continue
            candidate = dict(meta, content=None)
            if candidate['role'] == 'user':
                candidate['content'] = connection.execute('SELECT content FROM messages WHERE id=?', (candidate['id'],)).fetchone()[0]
            if _first_turn_row(iter([candidate]), processes) is None:
                continue
            admitted = rewrites.get(candidate['id'], candidate['id'])
            break
        if admitted != first['id']:
            continue
        child = dict(id='runtime-reminder:' + str(first['id']), role='tool', kind='context_compression',
                     name='Runtime reminders', status='completed', content=reminder,
                     timestamp=compression_time, turn_boundary=False)
        projections[first['id']] = dict(content=run['input'], run_id=run['id'], runtime_reminders=[child])
    return projections


def matches_user(row, run, reminders=()):
    if row is None or row['role'] != 'user':
        return False
    attachment_ids = (run['attachment_ids']
                      if 'attachment_ids' in run.keys() else [])
    if isinstance(attachment_ids, list) and attachment_ids:
        content = row['content']
        if isinstance(content, str) and content.startswith('\x00json:'):
            try:
                parts = json.loads(content[len('\x00json:'):])
            except (ValueError, TypeError):
                return False
            if not isinstance(parts, list):
                return False
            text, images = [], 0
            for part in parts:
                if not isinstance(part, dict):
                    return False
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    if part['text'] == '[screenshot] [photo attachment omitted after processing]':
                        images += 1
                    else:
                        text.append(part['text'])
                elif part.get('type') in {'image_url', 'input_image', 'image'}:
                    images += 1
                else:
                    return False
            return text == [run['input']] and images == len(attachment_ids)
        if not isinstance(content, str):
            return False
        markers = '\n[screenshot]' * len(attachment_ids)
        omitted = '\n[screenshot] [photo attachment omitted after processing]' * len(attachment_ids)
        return content in (run['input'] + markers, run['input'] + omitted)
    if row['content'] == run['input']:
        return True
    if (row['id'] in reminders and reminders[row['id']]['run_id'] == run['id']
            and reminders[row['id']]['content'] == run['input']):
        return True
    return False
