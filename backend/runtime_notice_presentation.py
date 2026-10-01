"""Display-only exact native runtime notices, without SDK imports.

Frozen from installed agent/context_compressor.py:185-193 and
chat_completion_helpers.handle_max_iterations:2873-2879. The runtime appends
only role=user and this exact content; SessionDB strips internal flags. The
reserved whole content is therefore the SDK's authoritative persisted marker.
Legacy byte-identical human pastes with NULL provenance cannot be distinguished.
Explicit human/unknown provenance always wins. Never rewrite model history.
"""

MAX_ITERATIONS_SUMMARY_REQUEST = (
    "You've reached the maximum number of tool-calling iterations allowed. "
    "Please provide a final response summarizing what you've found and accomplished so far, "
    "without calling any more tools."
)


def runtime_notice_ids(connection, session_id, columns):
    """Return IDs only, using bound exact equality and optional schema fields."""
    where = "session_id=? AND role='user' AND content=? COLLATE BINARY"
    for name in ('tool_calls', 'platform_message_id', 'display_metadata'):
        if name in columns:
            where += f" AND ({name} IS NULL OR {name}='')"
    if 'display_kind' in columns:
        where += " AND (display_kind IS NULL OR display_kind IN ('','hidden'))"
    if {'active', 'compacted'} <= columns:
        where += ' AND (active=1 OR compacted=1)'
    elif 'active' in columns:
        where += ' AND active=1'
    return {row['id'] for row in connection.execute(
        'SELECT id FROM messages WHERE ' + where, (session_id, MAX_ITERATIONS_SUMMARY_REQUEST))}
