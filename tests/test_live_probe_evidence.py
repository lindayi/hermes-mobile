"""Probe evidence regressions: synthetic HTTP only, never a live model or service."""
import asyncio
import json
import re

import httpx
import pytest

from scripts import live_probe


LEGACY_MARKER = 'HERMES_MOBILE_LIVE_TOOL_OK'
SESSION_ID = 'offline-verified-test'


class ProbeHTTP:
    """Model the native latest/oldest page contract with chronological rows."""

    def __init__(self, monkeypatch, *, old_count=1, persist_tool=True, trailing_count=0):
        self.rows = [{'id': i + 1, 'role': 'tool', 'content': LEGACY_MARKER}
                     for i in range(old_count)]
        self.persist_tool = persist_tool
        self.trailing_count = trailing_count
        self.prompts = []
        self.history_requests = []
        self.marker = LEGACY_MARKER
        monkeypatch.setattr(live_probe.Path, 'read_text', lambda *_: json.dumps({
            'upstream_url': 'http://offline.test', 'upstream_token': 'fixture'}))
        client_type = httpx.AsyncClient
        monkeypatch.setattr(live_probe.httpx, 'AsyncClient', lambda **kwargs: client_type(
            **kwargs, transport=httpx.MockTransport(self.handle)))

    def handle(self, request):
        path = request.url.path
        if request.method == 'POST':
            assert path == '/v1/runs', 'No session creation or other writes permitted'
            body = json.loads(request.content)
            assert body['session_id'] == SESSION_ID
            self.prompts.append(body['input'])
            if len(self.prompts) % 2:
                self.marker = re.search(r'printf ([A-Za-z0-9_]+)', body['input'])[1]
                if self.persist_tool:
                    self.rows.append({'id': len(self.rows) + 1, 'role': 'tool',
                                      'content': self.marker, 'tool_name': 'terminal'})
                for _ in range(self.trailing_count):
                    self.rows.append({'id': len(self.rows) + 1, 'role': 'assistant',
                                      'content': 'Unrelated newer message'})
            return httpx.Response(200, json={'run_id': f'run-{len(self.prompts)}'})
        assert request.method == 'GET'
        if path == '/v1/capabilities':
            return httpx.Response(200, json={'features': {'run_approval_request_id': True}})
        if path == '/api/sessions/' + SESSION_ID:
            return httpx.Response(200, json={'session': {'id': SESSION_ID, 'source': 'agent_test'}})
        if path.startswith('/v1/runs/'):
            return httpx.Response(200, json={'status': 'completed', 'output': self.marker})
        if path == '/api/sessions/' + SESSION_ID + '/messages':
            limit = int(request.url.params.get('limit', '500'))
            offset = int(request.url.params.get('offset', '0'))
            order = request.url.params.get('order', 'latest')
            self.history_requests.append((len(self.prompts), limit, offset, order))
            rows = self.rows if order == 'oldest' else list(reversed(self.rows))
            page = rows[offset:offset + min(limit, 500)]
            if order == 'latest':
                page = list(reversed(page))
            return httpx.Response(200, json={'session_id': SESSION_ID,
                'requested_session_id': SESSION_ID, 'data': page,
                'pagination': {'limit': min(limit, 500), 'offset': offset,
                               'order': order, 'returned': len(page)}})
        pytest.fail('Unexpected offline request: ' + str(request.url))

    def run(self):
        asyncio.run(live_probe.main(SESSION_ID))


@pytest.mark.parametrize('same_marker', [False, True])
@pytest.mark.parametrize('trailing_count', [0, 1001])
def test_historical_only_tool_evidence_cannot_attest_current_execution(
        monkeypatch, capsys, same_marker, trailing_count):
    probe = ProbeHTTP(monkeypatch, old_count=1500, persist_tool=False,
                      trailing_count=trailing_count)
    # Deliberate nonce collision proves the pre-run boundary independently of randomness.
    if same_marker:
        monkeypatch.setattr(live_probe.secrets, 'token_hex', lambda _: 'offline_nonce')
        for row in probe.rows:
            row['content'] = LEGACY_MARKER + '_offline_nonce'
    with pytest.raises(AssertionError, match='persisted tool result'):
        probe.run()
    assert len(probe.prompts) == 2
    assert 'PASS:' not in capsys.readouterr().out


def test_reused_session_gets_a_fresh_marker_each_invocation(monkeypatch, capsys):
    probe = ProbeHTTP(monkeypatch)
    probe.run()
    first_marker = probe.marker
    probe.run()
    assert probe.marker != first_marker
    assert first_marker != LEGACY_MARKER
    assert capsys.readouterr().out.count('PASS:') == 2
    # Continuation must recall, not echo a marker supplied in the second prompt.
    assert first_marker not in probe.prompts[1]
    assert probe.marker not in probe.prompts[3]


@pytest.mark.parametrize('old_count', [0, 501, 1500])
def test_fresh_tool_result_is_found_in_long_reused_history(monkeypatch, capsys, old_count):
    probe = ProbeHTTP(monkeypatch, old_count=old_count)
    probe.run()
    assert 'PASS:' in capsys.readouterr().out
    assert probe.history_requests[0][0] == 0, 'Boundary must precede model submission'


def test_new_tool_evidence_beyond_one_latest_page_is_not_truncated(monkeypatch, capsys):
    probe = ProbeHTTP(monkeypatch, old_count=1500, trailing_count=1001)
    probe.run()
    assert 'PASS:' in capsys.readouterr().out
    assert [offset for turns, _, offset, _ in probe.history_requests if turns] == [0, 500, 1000]


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_evidence_from_an_unbound_or_changed_session_is_rejected(monkeypatch, capsys, phase):
    probe = ProbeHTTP(monkeypatch)
    original = probe.handle

    def handle(request):
        response = original(request)
        if request.url.path.endswith('/messages') and bool(probe.prompts) == (phase == 'after'):
            payload = response.json()
            payload['session_id'] = 'unrelated-session'
            if phase == 'before':
                payload.pop('requested_session_id')
            return httpx.Response(200, json=payload)
        return response

    monkeypatch.setattr(probe, 'handle', handle)
    with pytest.raises(RuntimeError, match='history.*session'):
        probe.run()
    if phase == 'before':
        assert probe.prompts == []
    assert 'PASS:' not in capsys.readouterr().out


def test_nonadvancing_history_pagination_fails_closed(monkeypatch, capsys):
    probe = ProbeHTTP(monkeypatch, old_count=1500, persist_tool=False, trailing_count=1001)
    original = probe.handle
    first_page = None

    def handle(request):
        nonlocal first_page
        if request.url.path.endswith('/messages') and probe.prompts:
            offset = int(request.url.params.get('offset', 0))
            assert offset <= 500, 'Probe kept requesting history after pagination stopped advancing'
            if first_page is not None:
                return httpx.Response(200, json=first_page)
            response = original(request)
            first_page = response.json()
            return response
        return original(request)

    monkeypatch.setattr(probe, 'handle', handle)
    with pytest.raises(RuntimeError, match='history.*pagination'):
        probe.run()
    assert 'PASS:' not in capsys.readouterr().out


@pytest.mark.parametrize('turn', [1, 2])
def test_current_run_cannot_return_a_previous_marker(monkeypatch, capsys, turn):
    probe = ProbeHTTP(monkeypatch)
    original = probe.handle

    def handle(request):
        response = original(request)
        if request.method == 'GET' and request.url.path == f'/v1/runs/run-{turn}':
            return httpx.Response(200, json={'status': 'completed', 'output': LEGACY_MARKER})
        return response

    monkeypatch.setattr(probe, 'handle', handle)
    with pytest.raises(AssertionError, match='execution/continuation'):
        probe.run()
    assert 'PASS:' not in capsys.readouterr().out


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_new_non_tool_marker_does_not_count_as_tool_evidence(monkeypatch, capsys, role):
    probe = ProbeHTTP(monkeypatch)
    original = probe.handle

    def handle(request):
        response = original(request)
        if request.method == 'POST':
            probe.rows[-1]['role'] = role
        return response

    monkeypatch.setattr(probe, 'handle', handle)
    with pytest.raises(AssertionError, match='persisted tool result'):
        probe.run()
    assert 'PASS:' not in capsys.readouterr().out


def test_stable_native_resolved_session_keeps_its_message_boundary(monkeypatch, capsys):
    probe = ProbeHTTP(monkeypatch)
    original = probe.handle

    def handle(request):
        response = original(request)
        if request.url.path.endswith('/messages'):
            payload = response.json()
            payload['session_id'] = 'stable-compaction-tip'
            return httpx.Response(200, json=payload)
        return response

    monkeypatch.setattr(probe, 'handle', handle)
    probe.run()
    assert 'PASS:' in capsys.readouterr().out
