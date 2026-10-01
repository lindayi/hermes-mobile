"""Read-only, allowlisted session telemetry; lifetime usage is not context size.

Native SessionDB.update_token_counts accumulates input/output tokens. Native
api_server's GET session does not expose proven per-request context occupancy
or its window. Do not infer either from model names or global configuration.
"""
from contextlib import closing


def unknown_run():
    return dict(status='unknown', last_status=None, id=None, source=None, observed_at=None)


def session_telemetry(catalog, journal, user, sid):
    # Profile lookup and exact native session validation precede journal access.
    # Select no message bodies, prompts, reasoning or raw model configuration.
    with closing(catalog._connect(user['profile'])) as connection:
        columns = {r[1] for r in connection.execute('PRAGMA table_info(sessions)')}
        fields = [name for name in ('id', 'model', 'input_tokens', 'output_tokens') if name in columns]
        if 'model_config' in columns:
            fields.append("CASE WHEN json_valid(model_config) THEN CASE WHEN json_type(model_config, '$.provider')='text' THEN json_extract(model_config, '$.provider') END END AS provider")
        row = connection.execute('SELECT '+','.join(fields)+' FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        values = dict(row)
    for key in ('model', 'provider'):
        value = values.get(key)
        values[key] = value if isinstance(value, str) and value.strip() and len(value) <= 200 and not any(ord(c) < 32 for c in value) else None
    for key in ('input_tokens', 'output_tokens'):
        value = values.get(key)
        values[key] = value if type(value) is int and 0 <= value <= 9223372036854775807 else None
    return dict(model=values.get('model'), provider=values.get('provider'),
                metadata=dict(source='native_session', observed_at=None, freshness='persisted'),
                context=dict(used_tokens=None, limit_tokens=None, source=None, observed_at=None, estimated=False),
                usage=dict(scope='session_lifetime', input_tokens=values.get('input_tokens'), output_tokens=values.get('output_tokens'), source='native_session_totals' if {'input_tokens','output_tokens'} & columns else None, observed_at=None),
                run=journal.session_statuses(user['id'], user['profile'], [sid])[sid])
