"""Synthetic shape copied from codex_responses_adapter.py:1438–1449; no native import."""
import importlib
import importlib.util
import json

import pytest


@pytest.mark.parametrize('text', [
    '“password”: “SYNTHETIC_PRIVATE_VALUE”',
    'password=“first SYNTHETIC_PRIVATE_VALUE”',
    'password=\nSYNTHETIC_PRIVATE_VALUE',
    '‘password’: ‘first SYNTHETIC_PRIVATE_VALUE’',
    'password="first\nSYNTHETIC_PRIVATE_VALUE"',
])
def test_review_assignment_values_do_not_leak(text):
    result = api().public_commentary_items([message('Before\n' + text + '\nAfter')])
    assert 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(result)
    assert result[0]['content'].startswith('Before\n')
    assert result[0]['content'].endswith('\nAfter')


def test_review_cjk_adjacent_key_is_redacted():
    text = '密钥sk-' + 'A' * 24 + '。\n检查继续。'
    assert api().public_commentary_items([message(text)])[0]['content'] == '密钥[redacted]。\n检查继续。'


@pytest.mark.parametrize('text', [
    'Basic checks preserve Unicode — 检查。',
    'The bearer of this message will check the token count.',
    'Basic characterization preserves\nUnicode — 检查。',
])
def test_review_auth_words_preserve_prose(text):
    assert api().public_commentary_items([message(text)])[0]['content'] == text


@pytest.mark.parametrize('text, secret', [
    ('Authorization: Basic abc', 'abc'),
    ('Authorization: Bearer short', 'short'),
    ('Basic dXNlcjpwYXNzd29yZA==', 'dXNlcjpwYXNzd29yZA=='),
    ('Bearer PRIVATE_CREDENTIAL', 'PRIVATE_CREDENTIAL'),
])
def test_review_actual_auth_credentials_stay_redacted(text, secret):
    result = api().public_commentary_items([message(text)])
    assert secret not in json.dumps(result)


@pytest.mark.parametrize('tag', ['think', 'thinking', 'reasoning', 'reasoning_scratchpad', 'thought'])
def test_review_partial_private_opener_removes_only_private_remainder(tag):
    result = api().public_commentary_items([message(f'Public <{tag} SYNTHETIC_PRIVATE_VALUE')])
    assert result[0]['content'] == 'Public '
    assert 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(result)


@pytest.mark.parametrize('text', ['Public <span unfinished', 'Public <thinker unfinished',
                                 'Public <b>检查</b>\nI’ll continue.'])
def test_review_nonprivate_markup_is_preserved(text):
    assert api().public_commentary_items([message(text)])[0]['content'] == text


def api():
    assert importlib.util.find_spec('backend.public_commentary'), 'public commentary helper missing'
    return importlib.import_module('backend.public_commentary')


def message(text, phase='commentary'):
    return {'type': 'message', 'role': 'assistant', 'status': 'completed',
            'id': 'provider-item', 'phase': phase,
            'content': [{'type': 'output_text', 'text': text}]}


def test_public_unicode_markdown_and_whitespace_are_preserved_exactly():
    text = '  I’ll check the token count — then ...\n\n检查密码和凭据。\n- **password** and `credential` are ordinary words.\n'
    assert api().public_commentary_items([message(text)])[0]['content'] == text


def test_native_adapter_sidecar_selects_only_public_commentary():
    sidecar = [message('PRIVATE_ANALYSIS', 'analysis'), message('Checking files.'),
               message('PRIVATE_FINAL', 'final_answer'),
               {'type': 'reasoning', 'phase': 'commentary', 'content': 'PRIVATE_REASONING'},
               message('Next step.')]
    result = api().public_commentary_items(json.dumps(sidecar))
    assert result == [
        {'role': 'assistant', 'channel': 'commentary', 'content': 'Checking files.', 'item_index': 1},
        {'role': 'assistant', 'channel': 'commentary', 'content': 'Next step.', 'item_index': 4},
    ]
    assert 'PRIVATE' not in json.dumps(result)


def test_malformed_and_private_parts_are_fail_closed():
    helper = api().public_commentary_items
    for value in (None, {}, 42, '[broken', '{"reasoning_content":"PRIVATE"}', '[[' * 1000):
        assert helper(value) == []
    items = [None, {}, message('PRIVATE', ' Commentary '),
             dict(message('PRIVATE'), content='PRIVATE'),
             dict(message('PRIVATE'), content=[{'type': 'refusal', 'text': 'PRIVATE'},
                                               {'type': 'output_text', 'text': {'private': 'PRIVATE'}},
                                               {'type': 'text', 'text': 'PRIVATE'}])]
    assert helper(items) == []


def test_think_blocks_and_credentials_are_not_public():
    helper = api().public_commentary_items
    for tag in ('think', 'thinking', 'reasoning', 'REASONING_SCRATCHPAD', 'thought'):
        text = f'Checking. <{tag}>PRIVATE_THOUGHT</{tag}> Done.'
        assert helper([message(text)])[0]['content'] == 'Checking.  Done.'
        assert helper([message(f'Checking. <{tag}>PRIVATE_THOUGHT')])[0]['content'] == 'Checking. '
    split = message('unused')
    split['content'] = [{'type': 'output_text', 'text': 'Checking. <thi'},
                        {'type': 'output_text', 'text': 'nk>PRIVATE</think> Done.'}]
    assert helper([split])[0]['content'] == 'Checking.  Done.'
    assert helper([message('<think>PRIVATE_ONLY</think>')]) == []
    assert helper([message('«redacted:sk-…»')])[0]['content'] == '«redacted:sk-…»'
    for secret in ('api_key=PRIVATE_CREDENTIAL', 'Bearer PRIVATE_CREDENTIAL',
                   '"password": "PRIVATE CREDENTIAL"', 'sk-PRIVATECREDENTIAL123',
                   'https://user:PRIVATE@example.com/path?token=PRIVATE#PRIVATE'):
        result = helper([message('Before\n' + secret + '\nAfter')])
        assert len(result) == 1
        assert result[0]['content'].startswith('Before\n')
        assert result[0]['content'].endswith('\nAfter')
        assert 'PRIVATE' not in result[0]['content']
    assert 'PRIVATE' not in json.dumps(helper([message('Visit https://user:PRIVATE@example.com/path?token=PRIVATE')]))


def test_bounded_inputs_and_stable_native_row_item_identity():
    module = api()
    assert module.public_commentary_items(' ' * (module.MAX_SIDECAR_BYTES + 1)) == []
    assert module.public_commentary_items([message('x')] * (module.MAX_ITEMS + 1)) == []
    assert module.public_commentary_items([message('x' * (module.MAX_TEXT_CHARS + 1))]) == []
    items = [message('PRIVATE', 'analysis'), message('Checking.')]
    first = module.project_public_commentary(42, items)
    assert first == module.project_public_commentary(42, json.dumps(items))
    assert first[0]['id'] == 'native:42:commentary:1'
    assert first[0]['role'] == 'assistant' and first[0]['channel'] == 'commentary'
    assert first[0]['id'] != module.project_public_commentary(43, items)[0]['id']
