import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend.app import Settings, create_app
from test_auth import BASE, BOOTSTRAP, ORIGIN, enroll
from test_native_catalog import create_native_db


def setup(tmp_path):
    home = tmp_path/'native'; home.mkdir(); create_native_db(home/'state.db')
    app = create_app(Settings(state_dir=tmp_path/'app', profiles={'default': home}, bootstrap_secret=BOOTSTRAP))
    return app, home


def test_unknown_telemetry_authenticated_validated_readonly(tmp_path):
    app, home = setup(tmp_path)
    before = (home/'state.db').read_bytes()
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(BASE+'/sessions/cli-1/telemetry').status_code == 401
        client.headers['Origin'] = ORIGIN; enroll(client)
        assert client.get(BASE+'/sessions/absent/telemetry').status_code == 404
        response = client.get(BASE+'/sessions/cli-1/telemetry')
        assert response.status_code == 200
        data = response.json()
        assert data['model'] is None and data['provider'] is None
        assert data['context'] == dict(used_tokens=None, limit_tokens=None, source=None, observed_at=None, estimated=False)
        assert data['usage']['scope'] == 'session_lifetime'
        assert data['run']['status'] == 'unknown'
        assert 'PRIVATE' not in response.text
    assert (home/'state.db').read_bytes() == before


def test_persisted_model_and_lifetime_totals_never_become_context(tmp_path):
    from backend.session_telemetry import session_telemetry
    app, home = setup(tmp_path)
    with sqlite3.connect(home/'state.db') as db:
        for column, kind in [('model','TEXT'), ('model_config','TEXT'), ('input_tokens','INTEGER'), ('output_tokens','INTEGER')]:
            db.execute(f'ALTER TABLE sessions ADD COLUMN {column} {kind}')
        db.execute('UPDATE sessions SET model=?,model_config=?,input_tokens=?,output_tokens=? WHERE id=?',
                   ('real-model', '{"provider":"actual-provider","api_key":"SECRET","context_length":12345}', 999999, 456, 'cli-1'))
    data = session_telemetry(app.state.catalog, app.state.journal, dict(id='owner', profile='default'), 'cli-1')
    assert data['model'] == 'real-model' and data['provider'] == 'actual-provider'
    assert data['usage']['input_tokens'] == 999999
    assert data['usage']['output_tokens'] == 456
    assert data['usage']['source'] == 'native_session_totals'
    assert data['context']['used_tokens'] is None and data['context']['limit_tokens'] is None
    assert data['metadata'] == dict(source='native_session', observed_at=None, freshness='persisted')
    assert 'SECRET' not in str(data)


def test_batched_status_is_owned_profile_bound_latest_and_allowlisted(tmp_path, monkeypatch):
    app, _ = setup(tmp_path)
    journal = app.state.journal
    old, _ = journal.submit('owner', 'default', 'cli-1', 'SECRET input', 'old')
    journal.finish('owner', old['id'], 'failed', error='SECRET error')
    new, _ = journal.submit('owner', 'default', 'cli-1', 'SECRET newer', 'new')
    journal.finish('owner', new['id'], 'completed', output='SECRET output')
    foreign, _ = journal.submit('other', 'default', 'cli-1', 'SECRET foreign', 'foreign')
    journal.finish('other', foreign['id'], 'failed')
    journal.submit('owner', 'other-profile', 'cli-1', 'SECRET profile', 'profile')
    statements = []
    original = journal.connect
    def traced():
        connection = original(); connection.set_trace_callback(statements.append); return connection
    monkeypatch.setattr(journal, 'connect', traced)
    assert hasattr(journal, 'session_statuses'), 'Missing batched status lookup'
    result = journal.session_statuses('owner', 'default', ['cli-1', 'wa-1'])
    assert result['cli-1']['status'] == 'idle'
    assert result['cli-1']['last_status'] == 'completed'
    assert result['cli-1']['id'] == new['id']
    assert result['wa-1']['status'] == 'unknown'
    assert 'SECRET' not in str(result)
    assert len([s for s in statements if s.lstrip().upper().startswith(('SELECT','WITH'))]) == 1


def test_status_routes_match_and_preserve_pages(tmp_path):
    app, _ = setup(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN; enroll(client)
        with app.state.auth.store.transaction() as db:
            uid = db.execute('SELECT id FROM users').fetchone()['id']
        run, _ = app.state.journal.submit(uid, 'default', 'cli-1', 'private', 'r')
        app.state.journal.finish(uid, run['id'], 'waiting_for_approval')
        page = client.get(BASE+'/sessions?limit=1&q=CLI').json()
        assert page['total'] == 1 and len(page['items']) == 1
        row = page['items'][0]
        assert row.get('run_status') == 'waiting_for_approval'
        assert row['run'] == client.get(BASE+'/sessions/cli-1/telemetry').json()['run']
        assert client.get(BASE+'/sessions?q=missing').json() == dict(items=[], total=0)
        with app.state.auth.store.transaction() as db:
            db.execute("UPDATE users SET status='pending' WHERE id=?", (uid,))
        assert client.get(BASE+'/sessions/cli-1/telemetry').status_code == 409


@pytest.mark.parametrize('status,expected', [('queued','queued'), ('running','running'), ('waiting_for_approval','waiting_for_approval'), ('stopping','stopping'), ('failed','failed'), ('unknown','unknown'), ('completed','idle'), ('cancelled','idle')])
def test_every_run_status_and_empty_batch(tmp_path, status, expected):
    app, _ = setup(tmp_path)
    journal = app.state.journal
    run, _ = journal.submit('owner', 'default', 'cli-1', 'private', 'key')
    if status == 'running':
        journal.set_upstream('owner', run['id'], 'upstream')
    elif status != 'queued':
        journal.finish('owner', run['id'], status)
    assert journal.session_statuses('owner','default',['cli-1'])['cli-1']['status'] == expected
    assert journal.session_statuses('foreign','default',['cli-1'])['cli-1']['status'] == 'unknown'
    assert journal.session_statuses('owner','foreign',['cli-1'])['cli-1']['status'] == 'unknown'
    assert journal.session_statuses('owner','default',[]) == {}


def test_telemetry_cannot_read_other_profile_or_unknown_session(tmp_path):
    from backend.session_telemetry import session_telemetry
    from backend.native_catalog import NativeCatalog
    app, home = setup(tmp_path)
    other = tmp_path/'other'; other.mkdir(); create_native_db(other/'state.db')
    with sqlite3.connect(other/'state.db') as db:
        db.execute("UPDATE sessions SET id='other-only' WHERE id='cli-1'")
    catalog = NativeCatalog({'default':home, 'other':other})
    for profile,sid in [('default','other-only'), ('other','cli-1'), ('missing','cli-1'), ('../default','cli-1')]:
        with pytest.raises(KeyError):
            session_telemetry(catalog,app.state.journal,dict(id='owner',profile=profile),sid)


def test_malformed_metadata_is_unknown_not_raw_config(tmp_path):
    from backend.session_telemetry import session_telemetry
    app, home = setup(tmp_path)
    with sqlite3.connect(home/'state.db') as db:
        db.executescript('ALTER TABLE sessions ADD COLUMN model TEXT; ALTER TABLE sessions ADD COLUMN model_config TEXT; ALTER TABLE sessions ADD COLUMN input_tokens INTEGER;')
    for config in ('{broken', '[]', 'null', '{"provider":{"api_key":"SECRET"}}', '{"provider":42}'):
        with sqlite3.connect(home/'state.db') as db:
            db.execute('UPDATE sessions SET model=?,model_config=?,input_tokens=?', ('', config, -1))
        data = session_telemetry(app.state.catalog, app.state.journal, dict(id='owner', profile='default'), 'cli-1')
        assert data['model'] is None and data['provider'] is None
        assert data['usage']['input_tokens'] is None
        assert 'SECRET' not in str(data)
