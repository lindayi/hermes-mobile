"""Compose native history and owned durable runs without executing anything."""


def conversation_snapshot(catalog, journal, user, session_id, limit=100, offset=0, latest=False, *, turn_boundary=False):
    # Every page uses the same composed sequence, including journal-only turns.
    # Bracket the native transaction with ALL owned admissions, not just latest.
    for _ in range(3):
        state = journal.snapshot_state(user['id'], user['profile'], session_id)
        page = catalog.messages(user['profile'], session_id, limit, offset, latest,
                                snapshot=state, turn_boundary=turn_boundary)
        if state == journal.snapshot_state(user['id'], user['profile'], session_id):
            # Seed ordered public activity before applying terminal state. The
            # cursor includes excluded lifecycle events, so active SSE resumes
            # after this coherent seed without reordering text or tool rows.
            page['tool_replay'] = ({'run_id': page['run']['id'], 'events': state['replay_events'],
                                    'cursor': state['event_cursor']}
                                   if page['run'] else None)
            return page
    from .hermes_client import IntegrationUnavailable
    raise IntegrationUnavailable('Conversation changed while reading; refresh the snapshot')
