"""Assembled /sessions read_list path, never native deletion requests."""
import sqlite3

import pytest

from test_session_deletion import deletion_app
from test_auth import BASE


@pytest.mark.parametrize('tombstone', [False, True])
def test_sessions_api_body_search_keeps_count_pagination_and_tombstones(deletion_app, tmp_path, tombstone):
    app, client, user, calls, _ = deletion_app
    db = tmp_path / 'native' / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute("UPDATE messages SET content='bodyOnly otter' WHERE id=1")
        c.execute("INSERT INTO messages(session_id,role,content) VALUES('cli-1','user','bodyOnly otter')")
    if tombstone:
        app.state.journal.claim_deletion(user['id'], user['profile'], 'cli-1')
    before = db.read_bytes()
    response = client.get(BASE + '/sessions', params={'q': 'bodyOnly', 'limit': 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page['total'] == (1 if tombstone else 2)
    assert [row['id'] for row in page['items']] == (['wa-1'] if tombstone else ['cli-1'])
    assert 'run_status' in page['items'][0]
    next_page = client.get(BASE + '/sessions', params={'q': 'bodyOnly', 'limit': 1, 'offset': 1}).json()
    assert [row['id'] for row in next_page['items']] == ([] if tombstone else ['wa-1'])
    assert next_page['total'] == page['total']
    assert client.get(BASE + '/sessions', params={'q': 'q' * 201}).status_code == 422
    assert db.read_bytes() == before
    assert not any(method == 'DELETE' for method, _ in calls)
