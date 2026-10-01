import importlib.util
import json
import sqlite3
from pathlib import Path

import httpx
import pytest


def test_activation_tool_exists():
    assert importlib.util.find_spec('backend.member_runtime') is not None


def private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(content)
    path.chmod(0o600)
    return path


def native_db(path):
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT); CREATE TABLE messages(session_id TEXT,role TEXT,content TEXT);')
    path.chmod(0o600)


@pytest.fixture
def family(tmp_path, monkeypatch):
    from backend.auth_store import AuthStore
    from backend import profiles
    owner = tmp_path/'owner'
    home = owner/'profiles/member_abc123'
    home.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(profiles, 'PROFILE_ROOT', home.parent)
    private(home/'.env', 'OPENAI_API_KEY=member-fixture-only\n')
    private(home/'SOUL.md', 'Member only')
    private(home/'config.yaml', 'model:\n  provider: openai\n  default: fixture-model\nplatform_toolsets:\n  api_server: []\n')
    native_db(owner/'state.db')
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    store = AuthStore(state/'auth.sqlite')
    with store.transaction() as db:
        db.execute("INSERT INTO users VALUES ('owner','owner','default','ready','Owner',1)")
        db.execute("INSERT INTO users VALUES ('abc123','member','member_abc123','pending','Member',1)")
        db.execute("INSERT INTO settings VALUES ('provisioning:abc123','provisioned')")
    config = private(state/'config.json', json.dumps({'state_dir':str(state), 'profiles':{'default':str(owner)}, 'upstream_url':'http://127.0.0.1:18642', 'upstream_token':'owner-secret-not-for-members'}))
    runtime = private(state/'member.json', json.dumps({'hermes_home':str(home), 'port':18643, 'upstream_token':'member-token-'+'x'*40}))
    return dict(home=home, owner=owner, store=store, config=config, runtime=runtime, calls=[])


def transport(family, *, wrong_home=False, smoke_error=False, capability=True):
    def handle(request):
        family['calls'].append(request.url.path)
        assert request.headers['Authorization'] == 'Bearer member-token-'+'x'*40
        home = family['home']
        if request.url.path == '/v1/capabilities':
            return httpx.Response(200, json={'object':'hermes.api_server.capabilities', 'auth':{'required':True}, 'features':{key:capability for key in ('run_submission','run_events_sse','run_stop','run_approval_request_id','session_resources','session_chat')}, 'mobile_runtime':{'home':str(home), 'credential_policy':'member-only-v1', 'smoke_policy':'reserved-no-tools-v1'}})
        body = json.loads(request.content)
        target = family['owner'] if wrong_home else home
        if not (target/'state.db').exists():
            native_db(target/'state.db')
        if request.url.path == '/api/sessions':
            with sqlite3.connect(target/'state.db') as db:
                db.execute('INSERT INTO sessions VALUES (?,?)', (body['id'], body['title']))
            return httpx.Response(201, json={'id':body['id']})
        assert request.url.path.endswith('/chat')
        if smoke_error:
            return httpx.Response(503, json={'error':'provider unavailable'})
        sid = request.url.path.split('/')[3]
        answer = body['message'].split()[-1]
        with sqlite3.connect(target/'state.db') as db:
            db.execute('INSERT INTO messages VALUES (?,?,?)', (sid,'user',body['message']))
            db.execute('INSERT INTO messages VALUES (?,?,?)', (sid,'assistant',answer))
        return httpx.Response(200, json={'object':'hermes.session.chat.completion','session_id':sid,'message':{'role':'assistant','content':answer}})
    return httpx.MockTransport(handle)


def test_verified_smoke_activates_only_member(family):
    from backend import member_runtime
    assert hasattr(member_runtime, 'activate'), 'Missing verified activation operation'
    before = json.loads(family['config'].read_text())
    result = member_runtime.activate(family['config'], 'abc123', family['runtime'], transport=transport(family))
    after = json.loads(family['config'].read_text())
    assert result['status'] == 'ready'
    assert after['profiles']['member_abc123'] == str(family['home'])
    assert after['gateway_profiles']['member_abc123'] == {'url':'http://127.0.0.1:18643','token':'member-token-'+'x'*40,'execution_ready':True}
    assert after['upstream_token'] == before['upstream_token']
    assert after['profiles']['default'] == before['profiles']['default']
    assert 'member_abc123' not in after.get('job_delivery_targets', {})
    assert family['config'].stat().st_mode & 0o077 == 0
    with family['store'].transaction() as db:
        assert db.execute("SELECT status FROM users WHERE id='abc123'").fetchone()[0] == 'ready'
    assert len(family['calls']) == 3


@pytest.mark.parametrize('change', ['no_provider','auto_provider','unsafe_url','owner_port','other_token','other_home'])
def test_rejects_unconfigured_or_ambiguous_runtime_before_network(family, change):
    from backend.member_runtime import activate, ActivationError
    runtime = json.loads(family['runtime'].read_text())
    config = json.loads(family['config'].read_text())
    if change == 'no_provider':
        private(family['home']/'config.yaml', 'model: {}\n')
    elif change == 'auto_provider':
        private(family['home']/'config.yaml', 'model: {provider: auto, default: some-model}\n')
    elif change == 'unsafe_url':
        runtime['url'] = 'https://remote.invalid'
    elif change == 'owner_port':
        config['upstream_url'] = 'http://localhost:18643'
    elif change == 'other_token':
        config['gateway_profiles'] = {'member_other':{'url':'http://127.0.0.1:19643','token':runtime['upstream_token']}}
    elif change == 'other_home':
        config['profiles']['member_other'] = str(family['home'])
    family['runtime'].write_text(json.dumps(runtime))
    family['config'].write_text(json.dumps(config))
    original = family['config'].read_bytes()
    with pytest.raises(ActivationError):
        activate(family['config'],'abc123',family['runtime'],transport=transport(family))
    assert family['calls'] == []
    assert family['config'].read_bytes() == original


@pytest.mark.parametrize('boundary', ['capabilities','wrong_database','smoke_error','network','no_member','disabled','ready','not_provisioned','public_token','symlink_token'])
def test_failures_never_enable_member_or_publish_config(family, boundary):
    from backend.member_runtime import activate
    original = family['config'].read_bytes()
    kwargs = {}
    if boundary == 'capabilities': kwargs['capability'] = False
    if boundary == 'wrong_database': kwargs['wrong_home'] = True
    if boundary == 'smoke_error': kwargs['smoke_error'] = True
    if boundary in ('no_member','disabled','ready','not_provisioned'):
        with family['store'].transaction() as db:
            if boundary == 'no_member': db.execute("DELETE FROM users WHERE id='abc123'")
            elif boundary == 'not_provisioned': db.execute("DELETE FROM settings WHERE key='provisioning:abc123'")
            else: db.execute("UPDATE users SET status=? WHERE id='abc123'", (boundary,))
    if boundary == 'public_token': family['runtime'].chmod(0o644)
    if boundary == 'symlink_token':
        real = family['runtime'].with_suffix('.actual')
        family['runtime'].rename(real)
        family['runtime'].symlink_to(real)
    network = transport(family, **kwargs)
    if boundary == 'network':
        def unavailable(request): raise httpx.ConnectError('secret must not escape')
        network = httpx.MockTransport(unavailable)
    with pytest.raises(Exception):
        activate(family['config'], 'abc123', family['runtime'], transport=network)
    assert family['config'].read_bytes() == original
    with family['store'].transaction() as db:
        row = db.execute("SELECT status FROM users WHERE id='abc123'").fetchone()
        if boundary not in ('ready','disabled','no_member'): assert row[0] == 'pending'
    if boundary == 'wrong_database':
        assert not any(path.endswith('/chat') for path in family['calls'])


@pytest.mark.parametrize('boundary', ['revocation','config_edit','replace_failure','after_replace_crash'])
def test_atomic_publication_and_concurrent_changes_fail_closed(family, monkeypatch, boundary):
    from backend import member_runtime as module
    original = family['config'].read_bytes()
    real_transport = transport(family)
    def handle(request):
        response = real_transport.handle_request(request)
        if request.url.path.endswith('/chat'):
            if boundary == 'revocation':
                with family['store'].transaction() as db:
                    db.execute("UPDATE users SET status='disabled' WHERE id='abc123'")
            elif boundary == 'config_edit':
                data = json.loads(original)
                data['execution_ready'] = False
                family['config'].write_text(json.dumps(data))
        return response
    if boundary == 'replace_failure':
        monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    elif boundary == 'after_replace_crash':
        real_write = module.atomic_config
        def fail_after_write(*args):
            real_write(*args)
            raise OSError('simulated crash before auth commit')
        monkeypatch.setattr(module, 'atomic_config', fail_after_write)
    with pytest.raises(Exception):
        module.activate(family['config'], 'abc123', family['runtime'], transport=httpx.MockTransport(handle))
    with family['store'].transaction() as db:
        assert db.execute("SELECT status FROM users WHERE id='abc123'").fetchone()[0] == ('disabled' if boundary == 'revocation' else 'pending')
    if boundary in ('revocation','replace_failure'):
        assert family['config'].read_bytes() == original
    if boundary == 'config_edit':
        assert json.loads(family['config'].read_text())['execution_ready'] is False
        assert 'gateway_profiles' not in json.loads(family['config'].read_text())
    if boundary == 'after_replace_crash':
        monkeypatch.setattr(module, 'atomic_config', real_write)
        assert module.activate(family['config'], 'abc123', family['runtime'], transport=transport(family))['status'] == 'ready'


def test_activation_closes_native_and_auth_connections(family, monkeypatch):
    from backend import member_runtime as module
    connections = []
    connect = module.sqlite3.connect
    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs)
        if kwargs.get('uri'): connections.append(db)
        return db
    monkeypatch.setattr(module.sqlite3, 'connect', tracked)
    module.activate(family['config'],'abc123',family['runtime'],transport=transport(family))
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')


def test_cli_help_and_errors_never_leak_config_or_network_secrets(family, monkeypatch, capsys):
    from backend import member_runtime as module
    with pytest.raises(SystemExit) as exc:
        module.main(['--help'])
    assert exc.value.code == 0
    assert '--runtime-config' in capsys.readouterr().out
    monkeypatch.setattr(module, 'activate', lambda *args: (_ for _ in ()).throw(RuntimeError('SECRET-DO-NOT-PRINT')))
    with pytest.raises(SystemExit) as exc:
        module.main(['--config',str(family['config']),'--member-id','abc123','--runtime-config',str(family['runtime'])])
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert 'SECRET-DO-NOT-PRINT' not in output.out + output.err


@pytest.mark.parametrize('change', ['stale_gateway','stale_profile','delivery_target'])
def test_refuses_stale_mapping_or_unverified_scheduler(family, change):
    from backend.member_runtime import activate, ActivationError
    config = json.loads(family['config'].read_text())
    if change == 'stale_gateway':
        config['gateway_profiles'] = {'member_abc123':{'url':config['upstream_url'], 'token':config['upstream_token'],'execution_ready':True}}
    elif change == 'stale_profile':
        config['profiles']['member_abc123'] = str(family['owner'])
    else:
        config['job_delivery_targets'] = {'member_abc123':'whatsapp:owner'}
    family['config'].write_text(json.dumps(config))
    with pytest.raises(ActivationError):
        activate(family['config'],'abc123',family['runtime'],transport=transport(family))
    assert family['calls'] == []
