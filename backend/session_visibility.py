"""Read-only session-list policy; never apply it to direct history/inbox access.

Agent-owned verification sessions should have source='agent_test', or persisted
model_config={"_hermes_mobile": {"purpose": "agent_test"}} through an API that
actually supports it. The installed native creation API currently drops these
markers; owned probes require an existing verified testing session instead (see
docs/history-classification-repair.md). A human title is not provenance. This
module never writes metadata or retroactively marks native sessions.
"""


# Metadata-only audit of ~/.hermes/state.db on 2026-09-28, cross-checked
# against scripts/live_probe.py (deployment-verification title family) and
# scripts/live_auth_probe.py (PUBLIC_HERMES_APP_OK marker). Store exact IDs,
# not a title-prefix rule: identical human titles and future unknown probes stay
# visible. The old unsuffixed deployment-verification title is included too.
# Renaming a verified session keeps its origin.
VERIFIED_TEST_SESSION_IDS = (
    'api_1790570594_9efc871d',
    'api_1790571743_1d1c5208',
    'api_1790575010_8e1e6d88',
    'api_1790572001_2d1ae99c',
    'api_1790572140_79f45e95',
    # Read-only first-user-prompt verification + deployment-repair-release.md.
    # Exact provenance and regression evidence: docs/history-classification-repair.md.
    'api_1790638979_bfc85b3e',
)


# Separately verified at the parent's request via a read-only SQL equality
# check of the FIRST public user message (only boolean results were returned):
# 'This is a migration health check. Reply with exactly OK. Do not use any tools.'
# All five IDs matched exactly. The other five CLI sessions did not. No message
# text, tool output, reasoning, or system prompt was extracted or stored.
VERIFIED_HEALTHCHECK_SESSION_IDS = (
    '20260905_153550_864153',
    '20260905_153626_d6a777',
    '20260905_153637_05468c',
    '20260905_153713_191770',
    '20260905_153759_639642',
)


def classification_sql(columns):
    """Return a SQL CASE expression and bindings: hidden / cron / tests / chats.

    ``columns`` is the set from PRAGMA table_info(sessions). Missing or malformed
    model_config is treated as unknown, not as evidence of test/delegate origin.
    """
    config = ("CASE WHEN json_valid(model_config) THEN model_config ELSE '{}' END"
              if 'model_config' in columns else "'{}'")
    expression = (
        "CASE WHEN COALESCE(source,'') IN ('subagent','delegate') "
        f"OR json_extract({config}, '$._delegate_from') IS NOT NULL THEN 'hidden' "
        "WHEN source='cron' THEN 'cron' "
        "WHEN source='agent_test' "
        f"OR (source='api_server' AND id IN ({','.join('?' for _ in VERIFIED_TEST_SESSION_IDS)})) "
        f"OR (source='cli' AND id IN ({','.join('?' for _ in VERIFIED_HEALTHCHECK_SESSION_IDS)})) "
        f"OR json_extract({config}, '$._hermes_mobile.purpose')='agent_test' THEN 'tests' "
        "ELSE 'chats' END"
    )
    return expression, (*VERIFIED_TEST_SESSION_IDS, *VERIFIED_HEALTHCHECK_SESSION_IDS)


def session_filter(columns, kind='chats', q=''):
    """Return ``(WHERE SQL, tuple)`` for both count and paginated selection.

    Validate the route's kind as Literal['chats', 'all', 'cron', 'tests']; direct
    Python callers receive ValueError for unknown modes. Search matches literal
    title/ID text, with the catalogue's existing 200-character cap.
    """
    if kind not in ('chats', 'all', 'cron', 'tests'):
        raise ValueError('Invalid session kind; expected chats, all, cron, or tests')
    expression, params = classification_sql(columns)
    if kind == 'all':
        where = f"WHERE ({expression}) != 'hidden'"
    else:
        where = f'WHERE ({expression}) = ?'
        params = (*params, kind)
    term = '%' + q[:200].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    where += " AND (COALESCE(title,'') LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')"
    return where, (*params, term, term)
