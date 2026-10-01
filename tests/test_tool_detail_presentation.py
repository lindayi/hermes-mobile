import pytest

from backend.tool_presentation import tool_summary, normalize_tool_event


@pytest.mark.parametrize('name,args,expected', [
    ('read_file', {'path': '/home/lindayi/projects/hermes-mobile/frontend/ui.mjs'},
     'read_file: /home/lindayi/projects/hermes-mobile/frontend/ui.mjs'),
    ('terminal', {'command': 'pytest /home/lindayi/projects/hermes-mobile/tests -q'},
     'terminal: pytest /home/lindayi/projects/hermes-mobile/tests -q'),
    ('read_file', {'path': '/home/lindayi/.ssh/id_rsa'}, 'read_file'),
    ('web_search', {'query': 'a' * 48}, 'web_search'),
])
def test_long_public_paths_are_not_mistaken_for_opaque_credentials(name, args, expected):
    assert tool_summary(name, args) == expected


def test_nested_wrapper_summary_survives_on_result_rows():
    args = {'tool_uses': [
        {'recipient_name': 'functions.read_file', 'parameters': {'path': 'docs/guide.md'}},
        {'recipient_name': 'multi_tool_use.parallel', 'parameters': {'tool_uses': [
            {'function': {'name': 'terminal', 'arguments': '{"command":"git status --short"}'}}
        ]}},
    ]}
    result = tool_summary('multi_tool_use.parallel', args)
    assert 'read_file: docs/guide.md' in result
    assert 'terminal: git status --short' in result
    assert len(result) <= 360
    deep = {'tool_uses': [{'recipient_name': 'read_file', 'parameters': {'path': 'HIDDEN'}}]}
    for _ in range(15):
        deep = {'tool_uses': [{'recipient_name': 'multi_tool_use.parallel', 'parameters': deep}]}
    assert 'HIDDEN' not in tool_summary('multi_tool_use.parallel', deep)


@pytest.mark.parametrize('command', ['python3 -c "print(HIDDEN)"', 'python3 -cprint(HIDDEN)', 'python3 -Ic "print(HIDDEN)"', 'node -e "console.log(HIDDEN)"', 'node --eval "console.log(HIDDEN)"', 'node -p "HIDDEN"'])
def test_inline_code_commands_are_not_argument_previews(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'
    assert normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': command})['summary'] == 'terminal'


def test_encoded_urls_are_decoded_before_removing_userinfo_and_query():
    for url in ['https%3A%2F%2Falice%3Ahunter2%40example.com%2Fdocs', 'https%3A%2F%2Falice%3Ahunter2%40example.com%2Fdocs%3Fx%3DHIDDEN']:
        assert tool_summary('web_search', {'query': url}) == 'web_search: https://example.com/docs'


@pytest.mark.parametrize('command', ['curl -u alice:hunter2 https://example.com', 'curl --user alice:hunter2 https://example.com', 'curl -Ualice:hunter2 https://example.com', 'curl --proxy-user alice:hunter2 https://example.com', 'curl -H "Cookie: abc" https://example.com', 'curl --header "Cookie: abc" https://example.com', 'curl -d HIDDEN https://example.com', 'curl --data-raw HIDDEN https://example.com'])
def test_credential_or_body_bearing_curl_flags_are_not_exposed(command):
    assert tool_summary('terminal', {'command': command}) == 'terminal'
    assert tool_summary('terminal', {'command': 'curl https://example.com/docs'}) == 'terminal: curl https://example.com/docs'


def test_truncated_native_preview_remains_useful_and_completion_does_not_invent_output():
    start = normalize_tool_event({'event': 'tool.started', 'tool': 'terminal', 'preview': 'pytest tests/long-public-path…'})
    assert start['summary'] == 'terminal: pytest tests/long-public-path…'
    assert normalize_tool_event({'event': 'tool.completed', 'tool': 'terminal', 'duration': 0.1, 'error': False}) == {'event': 'tool.completed', 'tool': 'terminal', 'duration': 0.1, 'error': False}
