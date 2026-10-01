"""Richer presentation remains an allowlist, never an argument/result dump."""
import json
from pathlib import Path
import subprocess

import pytest

from backend.tool_presentation import normalize_tool_event, tool_summary


ROOT = Path(__file__).resolve().parents[1]


def frontend_details(rows):
    """Exercise the real JS helper with backend-produced data, never fixtures."""
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         "import {toolDetail} from './frontend/tool-details.mjs';"
         "let input=''; for await(const chunk of process.stdin) input+=chunk;"
         "console.log(JSON.stringify(JSON.parse(input).map(toolDetail)));"],
        input=json.dumps(rows), text=True, capture_output=True, check=True,
        cwd=ROOT, timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize('name,args,detail', [
    ('read_file', {'path': 'src/app.py', 'offset': 40, 'limit': 25}, 'src/app.py · offset 40 · limit 25'),
    ('search_files', {'pattern': 'render', 'path': 'frontend', 'target': 'content', 'file_glob': '*.mjs', 'limit': 5}, 'render · frontend · target content · glob *.mjs · limit 5'),
    ('read_file', {'path': 'docs/guide.md', 'offset': 'password HIDDEN', 'limit': -1}, 'docs/guide.md'),
    ('search_files', {'pattern': 'password HIDDEN', 'path': 'src', 'target': 'HIDDEN', 'file_glob': '.env'}, 'src'),
])
def test_location_and_selection_context(name, args, detail):
    assert tool_summary(name, args) == f'{name}: {detail}'


@pytest.mark.parametrize('name,args,detail', [
    ('process', {'action': 'log', 'session_id': 'proc_ab12', 'offset': 10, 'limit': 20, 'data': 'HIDDEN'}, 'Log process · proc_ab12 · offset 10 · limit 20'),
    ('process', {'action': 'wait', 'session_id': 'proc_ab12', 'timeout': 30}, 'Wait process · proc_ab12 · timeout 30s'),
    ('browser_click', {'ref': '@e5'}, 'Click @e5'),
    ('browser_type', {'ref': '@e6', 'text': 'HIDDEN'}, 'Type into @e6'),
    ('browser_press', {'key': 'Enter'}, 'Press Enter'),
    ('browser_scroll', {'direction': 'down'}, 'Scroll down'),
    ('browser_snapshot', {'full': True}, 'Full snapshot'),
    ('browser_back', {}, 'Go back'),
    ('browser_console', {'expression': 'HIDDEN'}, 'Inspect browser console'),
    ('browser_exec', {'code': 'HIDDEN'}, 'Run browser script'),
    ('execute_code', {'code': 'HIDDEN'}, 'Run Python script'),
    ('skill_view', {'name': 'pdf', 'file_path': 'references/forms.md'}, 'pdf · references/forms.md'),
    ('skill_manage', {'action': 'patch', 'name': 'pdf', 'new_string': 'HIDDEN'}, 'patch · pdf'),
    ('terminal', {'command': 'git status --short', 'workdir': 'src', 'background': True}, 'git status --short · cwd src · background'),
    ('delegate_task', {'action': 'stop', 'subagent_id': 'agent_1', 'message': 'HIDDEN'}, 'Stop subagents · agent_1'),
])
def test_action_and_target_context(name, args, detail):
    assert tool_summary(name, args) == f'{name}: {detail}'


@pytest.mark.parametrize('value', [
    'https://example.com/docs?x=alice%20HIDDEN',
    'https://alice%20HIDDEN:pwd@example.com/docs',
    'https://example.com/password/../public',
    'https://example.com/%2570assword/../public',
    'https://example.com/%2525252570assword/HIDDEN',
    'https://example.com/docs?x=alice%2520HIDDEN',
    'safe words '*100+'password HIDDEN',
])
def test_entire_normalized_value_is_screened_before_clipping(value):
    assert tool_summary('web_search', {'query': value}) == 'web_search'
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'web_search', 'preview': value})['summary'] == 'web_search'


@pytest.mark.parametrize('command', [
    'python3 '+'tests/ '*80+'-c HIDDEN',
    'curl '+'https://example.com/ '*30+'-u alice:hunter2',
    'git status && printf HIDDEN', 'git status | printf HIDDEN',
    'git status\nprintf HIDDEN', "python3 '-c' HIDDEN",
    'node "--eval" HIDDEN', "curl '--user' alice:hunter2 https://example.com",
])
def test_command_validation_before_clipping_rejects_scripts(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'


@pytest.mark.parametrize('command', [
    'curl --cert identity.pem:hunter2 https://example.com',
    'curl -E identity.pem:hunter2 https://example.com',
    'curl -Eidentity.pem:hunter2 https://example.com',
    'curl -sSEidentity.pem:hunter2 https://example.com',
    'curl --cert=identity.pem:hunter2 https://example.com',
    'curl --pass hunter2 https://example.com',
    'curl --pass=hunter2 https://example.com',
    'curl --proxy-cert identity.pem:hunter2 https://example.com',
    'curl --cert identity.pem:hunt…',
    'curl --cer…', 'curl --pa hunter2',
    'curl --config /tmp/public-file', 'curl -K/tmp/public-file',
    'curl --unknown hunter2',
    'node --import data:text/javascript,throw/**/HIDDEN',
    'node --import=data:text/javascript,throw/**/HIDDEN',
    'node --loader data:text/javascript,throw/**/HIDDEN',
    'node --experimental-loader data:text/javascript,throw/**/HIDDEN',
    'node --require data:text/javascript,throw/**/HIDDEN',
    'node -rdata:text/javascript,throw/**/HIDDEN',
    'node --import data:text/javascript,HID…', 'node --imp…',
    'node --unknown HIDDEN',
    'curl '+'https://example.com/public/ '*30+'--cert identity.pem:hunter2',
    'node '+'tests/public.mjs '*40+'--import data:text/javascript,HIDDEN',
])
def test_credential_loaders_fail_closed_on_direct_native_and_nested_paths(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command})['summary'] == 'terminal'
    assert tool_summary('multi_tool_use.parallel', {'tool_uses': [
        {'name': 'terminal', 'args': {'command': command}},
    ]}) == 'multi_tool_use.parallel: terminal'


@pytest.mark.parametrize('command', [
    'curl -fsSL https://example.com/docs',
    'curl --head https://example.com/docs',
    'curl --max-time 30 https://example.com/docs',
    'node --test tests/browser/tool-detail.test.mjs',
    'node --check frontend/tool-details.mjs', 'node app.mjs',
    'git status --short', '/usr/bin/python3 -m pytest tests -q',
])
def test_simple_commands_remain_useful_with_loader_restrictions(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal: '+command
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command})['summary'] == 'terminal: '+command


@pytest.mark.parametrize('command', [
    'curl ftp://alice:hunter2@example.com/file',
    'curl ssh://alice:hunter2@example.com/file',
    'ftp://alice:hunter2@example.com/pytest tests',
    'ssh://alice:hunter2@example.com/git status',
    'git clone ssh://alice:hunter2@example.com/repo',
    'npm config set _auth dXNlcjpwYXNz',
    'npm config set auth dXNlcjpwYXNz',
    'npm install ftp://alice:hunter2@example.com/pkg',
    'pip install ssh://alice:hunter2@example.com/pkg',
    'python3 -m pip install ftp://alice:hunter2@example.com/pkg',
    'pytest tests --unknown hunter2', 'git --config foo hunter2',
    'git status --unknown hunter2', 'npm test --unknown hunter2',
    'uv run node --import data:text/javascript,HIDDEN',
    'python3 -m unknown HIDDEN', 'systemctl set-environment AUTH=hunter2',
    'date --unknown hunter2', 'df --unknown hunter2', 'du --unknown hunter2', 'free --unknown hunter2',
])
def test_unknown_command_shapes_and_non_http_credentials_fail_closed(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command})['summary'] == 'terminal'
    assert frontend_details([
        {'name': 'terminal', 'args': {'command': command}, 'summary': 'terminal: git status'},
        {'name': 'terminal', 'summary': 'terminal: '+command, 'content': command},
    ]) == ['Details omitted for privacy']*2


@pytest.mark.parametrize('command', [
    'git status', 'git diff --stat', 'git log -n 5',
    'pytest tests', 'pytest tests/test_app.py::test_public -q',
    'python3 -m pytest tests -q', 'python3 scripts/check.py',
    'node --test', 'npm test', 'npm run build', 'npm run lint',
    'pip list', 'pip3 show pytest', 'uv run pytest tests -q',
    'systemctl status hermes', 'date -u', 'df -h', 'du -sh src', 'free -h',
])
def test_known_simple_command_shapes_stay_useful(command):
    summary = tool_summary('terminal', {'command': command})
    assert summary == 'terminal: '+command
    assert frontend_details([
        {'name': 'terminal', 'args': {'command': command}}, {'name': 'terminal', 'summary': summary},
    ]) == [command]*2


def test_backend_frontend_truncated_node_command_stays_useful():
    command = 'node --test '+'tests/public.mjs '*40
    summary = tool_summary('terminal', {'command': command})
    assert summary.endswith('…')
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': summary.removeprefix('terminal: ')})['summary'] == summary
    assert frontend_details([{'name': 'terminal', 'summary': summary, 'content': 'unrelated raw log'}]) == [summary.removeprefix('terminal: ')]


def test_backend_frontend_loader_summaries_do_not_disclose_long_prefix_credentials():
    rows = []
    for command in [
        'curl '+'https://example.com/public/ '*5+'--cert identity.pem:hunter2',
        'curl '+'https://example.com/public/ '*30+'--pass hunter2',
        'node --test '+'tests/public.mjs '*40+'--import data:text/javascript,HIDDEN',
    ]:
        rows.append({'name': 'terminal', 'summary': tool_summary('terminal', {'command': command})})
        rows.append(normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command}))
    assert frontend_details(rows) == ['Arguments/output not recorded']*len(rows)


@pytest.mark.parametrize('command', ['git status --short', 'curl -fsSL https://example.com/docs', 'node --test tests/browser/tool-detail.test.mjs'])
@pytest.mark.parametrize('workdir', ["/tmp/project's working tree", '/tmp/project (copy)', 'src'])
def test_backend_frontend_terminal_metadata_round_trip_prioritizes_summary(command, workdir):
    args = {'command': command, 'workdir': workdir, 'background': True}
    summary = tool_summary('terminal', args)
    expected = command+' · cwd '+workdir+' · background'
    assert summary == 'terminal: '+expected
    rows = [
        {'name': 'terminal', 'args': args},
        {'name': 'terminal', 'summary': summary},
        {'name': 'terminal', 'summary': summary, 'content': 'unrelated raw log'},
        {'name': 'terminal', 'summary': summary, 'content': {'output': 'unrelated raw log'}},
    ]
    assert frontend_details(rows) == [expected]*len(rows)


@pytest.mark.parametrize('workdir', ['/tmp/password-hunter2', 'src && printf HIDDEN', '--import data:text/javascript,HIDDEN', 'src · background'])
def test_backend_frontend_terminal_sensitive_cwd_does_not_discard_command(workdir):
    args = {'command': 'git status --short', 'workdir': workdir, 'background': True}
    summary = tool_summary('terminal', args)
    assert summary == 'terminal: git status --short · background'
    assert frontend_details([
        {'name': 'terminal', 'args': args}, {'name': 'terminal', 'summary': summary, 'content': 'unrelated raw log'},
    ]) == ['git status --short · background']*2


@pytest.mark.parametrize('command', [
    'git status · cwd /tmp/public',
    'git status · cwd /tmp/public · background',
])
def test_raw_terminal_commands_cannot_impersonate_summary_metadata(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command})['summary'] == 'terminal'


@pytest.mark.parametrize('name,preview,expected', [
    ('process', 'wait proc_ab12 30s', 'process: Wait process · proc_ab12 · timeout 30s'),
    ('process', 'submit proc_ab12 "HIDDEN"', 'process: Submit process'),
    ('browser_click', '@e5', 'browser_click: Click @e5'),
    ('browser_press', 'Enter', 'browser_press: Press Enter'),
    ('browser_scroll', 'down', 'browser_scroll: Scroll down'),
    ('browser_type', 'HIDDEN', 'browser_type'),
    ('browser_console', 'HIDDEN', 'browser_console: Inspect browser console'),
    ('read_file', 'guide.md (lines 40-65)', 'read_file: guide.md (lines 40-65)'),
])
def test_native_preview_recovers_only_known_shapes(name, preview, expected):
    event = normalize_tool_event({'event': 'tool.started', 'tool': name, 'preview': preview, 'arguments': {'text': 'HIDDEN'}})
    assert event == {'event': 'tool.started', 'tool': name, 'summary': expected}


def test_names_and_oversized_argument_json_fail_closed():
    assert tool_summary('password_HIDDEN') == 'tool'
    assert tool_summary('read_file', '{"path":"docs/guide.md","content":"'+'x'*10000+'"}') == 'read_file'


def test_live_budget_preserves_more_than_120_characters_without_payload_fields():
    preview = 'public search words ' * 30
    result = normalize_tool_event({'event': 'tool.started', 'tool': 'web_search', 'preview': preview, 'args': {'code': 'HIDDEN'}, 'result': 'HIDDEN'})
    assert 120 < len(result['summary']) <= 360
    assert result.keys() == {'event', 'tool', 'summary'}
    assert result['summary'].endswith('…')
    assert tool_summary('web_search', {'query': preview+'password HIDDEN'}) == 'web_search'


def test_parallel_descriptions_are_fairly_bounded_and_keep_unknown_names():
    result = tool_summary('multi_tool_use.parallel', {'tool_uses': [
        {'name': 'web_search', 'args': {'query': 'public search words ' * 80}},
        {'name': 'read_file', 'args': {'path': 'docs/guide.md', 'offset': 10, 'limit': 5}},
        {'name': 'unknown_tool', 'args': {'code': 'HIDDEN'}},
    ]})
    assert 'read_file: docs/guide.md · offset 10 · limit 5' in result
    assert 'unknown_tool' in result
    assert len(result) <= 360
    assert tool_summary('delegate_task', {'goal': 'Inspect UI', 'context': 'HIDDEN'}) == 'delegate_task: Inspect UI'
    assert tool_summary('delegate_task', {'tasks': [{'goal': 'safe words '*60+'password HIDDEN'}, {'goal': 'Run tests'}]}) == 'delegate_task: 2 tasks: Run tests'

