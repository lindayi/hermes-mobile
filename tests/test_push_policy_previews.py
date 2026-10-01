"""Server push payloads contain bounded plain public previews, not private data."""
import json
from types import SimpleNamespace
import pytest
from test_push_policy import policy


def test_public_preview_is_informative_plain_bounded_and_inbox_linked(policy):
    s = policy.service
    item = s.ingest('alice','public','Meeting notes ready','Three decisions are ready to review.',category='completion')
    assert s.flush()['sent'] == 2
    payload = json.loads(policy.sent[0]['data'])
    assert payload['title'] == 'Meeting notes ready'
    assert payload['body'] == 'Three decisions are ready to review.'
    assert payload['inbox_id'] == item['id']
    assert payload['url'] == '/hermes/?inbox='+item['id']
    assert payload['tag'] == item['id']
    s.ingest('alice','long','Title '*50,'Public response. '*100,category='scheduled')
    assert s.flush()['sent'] == 2
    payload = json.loads(policy.sent[-1]['data'])
    assert 0 < len(payload['title']) <= 100
    assert 0 < len(payload['body']) <= 180


@pytest.mark.parametrize('unsafe', [
    'API_KEY=sk-secret-value', 'password:\n  dragon', 'access_token = abcdef',
    'Authorization: Bearer\nabcdefgh', 'Bearer\t abcdef', 'Bearer\u200b abcdef',
    '-----BEGIN PRIVATE KEY-----\nMIIsecret\n-----END PRIVATE KEY-----',
    'Result '+('clean '*60)+'-----BEGIN RSA PRIVATE KEY-----\nprivate',
    'https://example.test/result?token=private', 'www.example.test/?password=private',
    'Your authentication code is 194582', 'Recovery codes:\n11112222\n33334444',
    'verification\ncode: 908765', 'OTP: 123456', 'Secret:\nvalue',
    '<script>alert(1)</script>', '[Open](https://evil.test)', '`tool output`',
    'Result\u202eoverride', 'Traceback (most recent call last): /home/private/file.py',
    'Tool output: credential dump', 'A' * 100001,
], ids=lambda value: str(value)[:25])
@pytest.mark.parametrize('field',['title','body'])
def test_suspicious_content_fails_closed_in_both_fields_before_clipping(policy, unsafe, field):
    s = policy.service
    text = {'title':'Public result ready','body':'Review your result.'}
    text[field] = unsafe
    s.ingest('alice','unsafe',**text,category='scheduled')
    assert s.flush()['sent'] == 2
    payload = json.loads(policy.sent[0]['data'])
    assert payload['title'] == 'Hermes'
    assert payload['body'] == 'You have a new notification.'
    assert s.list_inbox('alice')[0][field] == unsafe, 'Inbox content must not be rewritten'


def test_hide_details_replaces_both_fields_using_latest_retry_preferences(policy):
    s = policy.service
    s.send_push = lambda **kw: (policy.sent.append(kw) or SimpleNamespace(status_code=503))
    s.ingest('alice','retry','Meeting notes ready','Three decisions are ready.',category='completion')
    assert s.flush()['failed'] == 2
    assert json.loads(policy.sent[0]['data'])['title'] == 'Meeting notes ready'
    prefs = s.get_preferences('alice','d1')
    s.set_preferences('alice','d1',{**prefs,'hide_details':True})
    policy.now[0] += 61
    s.send_push = lambda **kw: (policy.sent.append(kw) or SimpleNamespace(status_code=201))
    assert s.flush()['sent'] == 2
    payloads = {x['subscription_info']['endpoint']:json.loads(x['data']) for x in policy.sent[2:]}
    hidden = next(value for endpoint,value in payloads.items() if endpoint.endswith('/d1'))
    assert hidden['title'] == 'Hermes'
    assert hidden['body'] == 'You have a new notification.'
    shown = next(value for endpoint,value in payloads.items() if endpoint.endswith('/d2'))
    assert shown['title'] == 'Meeting notes ready'
    assert s.flush()['sent'] == 0
