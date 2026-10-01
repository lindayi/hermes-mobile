"""Pure display-only recognition; never imported by model-history replay.

Exact header fingerprints freeze SUMMARY_PREFIX and the five shipped
_HISTORICAL_SUMMARY_PREFIXES from agent/context_compressor.py. Tests retain
all six literal headers. No SDK imports, fuzzy matching, or runtime config.
"""
from hashlib import sha256
from math import isfinite
import json

# Exact string producer: context_compressor.py merge-into-tail assembly. Only
# the EMPTY prior-content variant is wholly process text; never collapse a
# carrier that also contains preserved user/assistant words.
_EMPTY_MERGED_PREFIX = (
    '[PRIOR CONTEXT — for reference only; not a new message]\n\n\n'
    '[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n\n'
)


def _merged_carrier_ids(connection, session_id, columns):
    """Prove an empty merged assistant using its archived tool-call identity.

    Native compaction retains timestamp and call IDs, but may prune arguments.
    Require a unique empty archived source with the same ordered call identity;
    do not infer origin from role, keywords, session lineage or tool calls alone.
    SQL projects ONLY public call identity, never arguments or replay sidecars.
    """
    if not {'timestamp', 'tool_calls', 'active', 'compacted'} <= columns:
        return set()
    clean = ''.join(" AND COALESCE(" + name + ",'')=''" for name in
                    ('display_kind', 'display_metadata', 'platform_message_id') if name in columns)
    # Guard JSON before json_each/extract; malformed legacy payloads fail closed.
    identity = """(SELECT json_group_array(CASE WHEN type='object' THEN
        json_array(json_extract(value,'$.id'),json_extract(value,'$.type'),
                   json_extract(value,'$.function.name'),json_extract(value,'$.call_id'),
                   json_extract(value,'$.response_item_id')) END)
        FROM json_each(CASE WHEN json_valid(tool_calls) THEN
            CASE WHEN json_type(tool_calls)='array' THEN tool_calls ELSE '[]' END
            ELSE '[]' END)) AS call_identity"""
    rows = connection.execute(
        'SELECT id,active,compacted,timestamp,content,' + identity
        + " FROM messages WHERE session_id=? AND role='assistant'"
        ' AND timestamp IS NOT NULL AND (active=1 OR compacted=1)'
        ' AND length(CAST(content AS BLOB))<=8388608'
        ' AND length(CAST(tool_calls AS BLOB))<=8388608'
        + clean + " AND (content='' OR content LIKE '[PRIOR CONTEXT%') ORDER BY id",
        (session_id,))
    sources, candidates, live_counts = {}, [], {}
    for row in rows:
        calls = json.loads(row['call_identity'])
        if (not calls or any(not isinstance(call, list) or len(call) != 5
                             or not isinstance(call[0], str) or not call[0]
                             or call[1] != 'function'
                             or not isinstance(call[2], str) or not call[2]
                             or any(value is not None and not isinstance(value, str) for value in call[3:])
                             for call in calls)
                or len({call[0] for call in calls}) != len(calls)):
            continue
        key = (row['timestamp'], row['call_identity'])
        if row['content'] == '' and row['active'] == 0 and row['compacted'] == 1:
            sources.setdefault(key, []).append(row['id'])
        elif (row['content'].startswith(_EMPTY_MERGED_PREFIX)
              and standalone_envelope(row['content'][len(_EMPTY_MERGED_PREFIX):], require_end=True)):
            candidates.append((row['id'], key))
            live_counts[key] = live_counts.get(key, 0) + (row['active'] == 1)
    return {message_id for message_id, key in candidates
            if len(sources.get(key, ())) == 1 and sources[key][0] < message_id
            and live_counts.get(key, 0) <= 1}

_HEADER_HASHES = frozenset({
    '7e9c6ce1b413ec6c11da5f2f7b5a3313bfeaf120c23b262d83ae5531e23da9eb',
    'ff3014fed07c965aa893a69b555a91f0f63cacb013bd276d47efeefb35399391',
    '1c004c9c0ec1a25cbc96ae326724269d8ee67d363a8aeca17e722801e0f4b672',
    'f953b27ebb6288ec9aea994184be18cbc56783d2237241df2b1f24b55f3b0230',
    '2999da51e1781fab13ee35aa6b0f6d2815091130426d1c840a3b0fc9b7df7141',
    '5cd6c1b9dc95f8ff066df65c33abb66cad2718fe6f12bf736491ae9ea3b1c013',
})
_END = '--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---'


def standalone_envelope(content, *, require_end=False):
    """Whole known envelope only; merged carriers must keep their human content."""
    if not isinstance(content, str):
        return False
    header, separator, body = content.partition('\n')
    if not separator or not body.strip():
        return False
    if header != '[CONTEXT SUMMARY]:' and sha256(header.encode()).hexdigest() not in _HEADER_HASHES:
        return False
    if _END in body:
        # Exactly one final end marker: a following live ask is not a summary.
        return body.count(_END) == 1 and body.endswith('\n' + _END)
    return not require_end


def is_context_compression(row):
    """Require persisted native scaffolding provenance, never a text mention."""
    return (row.get('display_kind') == 'hidden'
            and row.get('role') in ('user', 'assistant')
            and not row.get('platform_message_id') and not row.get('display_metadata')
            and not row.get('tool_calls')
            and standalone_envelope(row.get('content')))


def _retained_head_summary_ids(connection, session_id, columns):
    """Recognize archive → cloned head → NEW summary → cloned older tail.

    The installed compressor preserves head/tail timestamps; _insert_message_rows
    assigns the standalone summary a new timestamp. All head rows and the first
    tail row must have unique, ordered sources in the latest archive generation,
    with a removed middle. Never scan past an arbitrary prefix to find a match.
    Tool-result bodies may be pruned, but only a paired persisted call identity
    can substitute for exact public content. No tool arguments leave SQLite.

    Proof budgets: 32 head rows, 64 generations, 8192 archived public rows, one
    8 MiB envelope. Exhaustion/missing fields/ambiguous identity means no proof.
    This is historical structural evidence, not cryptographic authorship.
    """
    if not {'timestamp', 'tool_calls', 'active', 'compacted'} <= columns:
        return set()
    prefix = list(connection.execute(
        'SELECT id,role,timestamp,compacted,'
        " (content LIKE '[CONTEXT COMPACTION%' OR content LIKE '[CONTEXT SUMMARY]:%') AS possible"
        " FROM messages WHERE session_id=? AND active=1 AND role!='system' ORDER BY id LIMIT 34",
        (session_id,)))
    candidates = [i for i, row in enumerate(prefix[:-1]) if row['possible']
                  and isinstance(row['timestamp'], (int, float))
                  and isinstance(prefix[i + 1]['timestamp'], (int, float))
                  and row['timestamp'] > prefix[i + 1]['timestamp']]
    if len(candidates) != 1:
        return set()
    index = candidates[0]
    if not 0 < index <= 32 or index + 1 >= len(prefix):
        return set()
    head, candidate, tail = prefix[:index], prefix[index], prefix[index + 1]
    stamp = candidate['timestamp']
    if (not isinstance(stamp, (int, float)) or not isfinite(stamp) or candidate['compacted'] != 0
            or any(row['compacted'] != 0 or not isinstance(row['timestamp'], (int, float))
                   or not isfinite(row['timestamp']) or row['timestamp'] >= stamp for row in [*head, tail])):
        return set()
    conflicts = [name for name in ('display_kind', 'display_metadata', 'platform_message_id',
                                   'tool_calls', 'tool_call_id', 'tool_name') if name in columns]
    clean = ''.join(" AND COALESCE(" + name + ",'')=''" for name in conflicts)
    body = connection.execute(
        "SELECT content FROM messages WHERE id=? AND role IN ('user','assistant')"
        ' AND length(CAST(content AS BLOB))<=8388608' + clean,
        (candidate['id'],)).fetchone()
    if body is None or not standalone_envelope(body['content'], require_end=True):
        return set()
    # An earlier human paste, even with a different role/timestamp, is NOT new.
    if connection.execute('SELECT 1 FROM messages WHERE session_id=? AND id<? AND content=? LIMIT 1',
                          (session_id, candidate['id'], body['content'])).fetchone():
        return set()
    archive = list(connection.execute(
        "SELECT id,active,compacted,timestamp FROM messages WHERE session_id=? AND id<? AND role!='system'"
        ' ORDER BY id LIMIT 8193', (session_id, head[0]['id'])))
    if not archive or len(archive) > 8192:
        return set()
    # All matched sources belong to one archive generation, not a cross-epoch
    # assortment. Repeated exact opening identities delimit retained generations.
    identity = ['role', 'timestamp', 'tool_calls'] + [name for name in
        ('tool_call_id', 'tool_name', 'display_kind', 'display_metadata', 'platform_message_id')
        if name in columns]
    equal = ' AND '.join('a.' + name + ' IS b.' + name for name in identity)
    sources = []
    for row in [*head, tail]:
        content_equal = 'a.content IS b.content'
        if row['role'] == 'tool' and {'tool_call_id', 'tool_name'} <= columns:
            # A pruned result is allowed only behind its retained head call.
            # JSON is guarded; malformed calls never supply pairing evidence.
            paired = connection.execute(
                "SELECT count(*) AS matches, sum(json_extract(c.value,'$.type')='function'"
                " AND json_extract(c.value,'$.function.name')=t.tool_name) AS valid"
                ' FROM messages t JOIN messages a ON a.session_id=t.session_id'
                ' JOIN json_each(CASE WHEN length(CAST(a.tool_calls AS BLOB))<=8388608'
                ' AND json_valid(a.tool_calls) THEN CASE WHEN json_type(a.tool_calls)=\'array\''
                " THEN a.tool_calls ELSE '[]' END ELSE '[]' END) c"
                " WHERE t.id=? AND t.tool_call_id!='' AND a.role='assistant' AND a.active=1"
                " AND a.id>=? AND a.id<? AND c.type='object'"
                " AND json_extract(c.value,'$.id')=t.tool_call_id",
                (row['id'], head[0]['id'], row['id'])).fetchone()
            if paired['matches'] == paired['valid'] == 1:
                content_equal = '1'
        matches = list(connection.execute(
            'SELECT b.id,b.active,b.compacted FROM messages a JOIN messages b'
            ' ON b.session_id=a.session_id AND ' + equal + ' AND ' + content_equal
            + ' WHERE a.id=? AND b.timestamp IS NOT NULL'
            ' AND (b.active=1 OR b.compacted=1) ORDER BY b.id LIMIT 66', (row['id'],)))
        live = [match['id'] for match in matches if match['active'] == 1]
        old = [match['id'] for match in matches if match['active'] == 0
               and match['compacted'] == 1 and match['id'] < head[0]['id']]
        if len(matches) > 65 or live != [row['id']] or not old:
            return set()
        sources.append(old)
    if sources[0][0] != archive[0]['id']:
        return set()
    generation_start = sources[0][-1]
    generation = [row for row in archive if row['id'] >= generation_start]
    if any(row['active'] != 0 or row['compacted'] != 1
           or not isinstance(row['timestamp'], (int, float))
           or not isfinite(row['timestamp']) or row['timestamp'] >= stamp
           for row in generation):
        return set()
    selected = [[message_id for message_id in group if message_id >= generation_start]
                for group in sources]
    if any(len(group) != 1 for group in selected):
        return set()
    source_ids = [group[0] for group in selected]
    archived_ids = [row['id'] for row in generation]
    if (archived_ids[:len(head)] != source_ids[:-1]
            or source_ids[-1] not in archived_ids[len(head) + 1:]):
        return set()
    return {candidate['id']}


def compression_ids(connection, session_id, columns):
    """Recognize persisted scaffolding or a proven archive→active boundary.

    active/compacted on a row alone says nothing about its authorship. Unmarked
    rows require either the FIRST active public record immediately after an
    archive, or the bounded retained-head/summary/older-tail proof above. Both
    require an exact complete envelope, no platform/display conflict, and no
    earlier equal body (which could be a retained human lookalike).
    Archived generations require compacted=1 and the exact full reserved
    envelope. A deliberate byte-identical human paste archived by old SDKs
    with NULL provenance is indistinguishable; keywords/quotes are not enough.
    Session lineage alone never confers summary identity.
    Read only public content and provenance, never reasoning/API sidecars.
    """
    if 'tool_calls' not in columns:
        return set()
    optional = [key for key in ('display_kind', 'display_metadata', 'platform_message_id')
                if key in columns]
    # Classification must not defeat the catalogue's bounded turn expansion.
    fields = ','.join(['id', 'role',
                      'CASE WHEN length(CAST(content AS BLOB))<=8388608 THEN content END AS content',
                      'tool_calls IS NOT NULL AND tool_calls != \'\' AS tool_calls']
                     + [key if key == 'display_kind' else
                        key + " IS NOT NULL AND " + key + " != '' AS " + key for key in optional])
    result = _merged_carrier_ids(connection, session_id, columns)
    result.update(_retained_head_summary_ids(connection, session_id, columns))
    visibility = (' AND (active=1 OR compacted=1)' if {'active', 'compacted'} <= columns
                  else ' AND active=1' if 'active' in columns else '')
    if 'display_kind' in columns:
        for row in connection.execute('SELECT ' + fields + ' FROM messages WHERE session_id=?'
                                      + visibility + " AND display_kind='hidden'", (session_id,)):
            if is_context_compression(dict(row)):
                result.add(row['id'])
    if not {'active', 'compacted'} <= columns:
        return result
    for candidate in connection.execute('SELECT ' + fields + ' FROM messages WHERE session_id=?'
                                        " AND active=0 AND compacted=1 AND role IN ('user','assistant')"
                                        " AND (content LIKE '[CONTEXT COMPACTION%' OR content LIKE '[CONTEXT SUMMARY]:%')",
                                        (session_id,)):
        row = dict(candidate)
        if (not row['tool_calls'] and not any(row.get(key) for key in optional)
                and standalone_envelope(row['content'], require_end=True)):
            result.add(row['id'])
    first = connection.execute('SELECT ' + fields + ' FROM messages WHERE session_id=?'
                               " AND active=1 AND role!='system' ORDER BY id LIMIT 1",
                               (session_id,)).fetchone()
    if first is None:
        return result
    row = dict(first)
    if (row['role'] not in ('user', 'assistant') or row['tool_calls']
            or any(row.get(key) for key in optional)
            or not standalone_envelope(row['content'], require_end=True)):
        return result
    previous = connection.execute('SELECT active,compacted FROM messages WHERE session_id=?'
                                  " AND id<? AND role!='system' ORDER BY id DESC LIMIT 1",
                                  (session_id, row['id'])).fetchone()
    if previous is None or previous['active'] != 0 or previous['compacted'] != 1:
        return result
    duplicate = connection.execute('SELECT 1 FROM messages WHERE session_id=? AND id<?'
                                   ' AND content=? LIMIT 1',
                                   (session_id, row['id'], row['content'])).fetchone()
    if duplicate is None:
        result.add(row['id'])
    return result
