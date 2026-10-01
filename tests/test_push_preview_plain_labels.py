"""Colon prose remains useful without admitting opaque or malformed URI prefixes."""
import pytest
from backend.notification_policy import GENERIC_PREVIEW, safe_preview

LABELS = [
    ('Minimalist: CAD105, in stock.', 'Minimalist: CAD105, in stock.'),
    ('**Summary:** Freed200MB.', 'Summary: Freed200MB.'),
    ('__Summary:__ Freed200MB.', 'Summary: Freed200MB.'),
    ('## Summary: Freed200MB.', 'Summary: Freed200MB.'),
    ('Reminder: meeting at 09:00.', 'Reminder: meeting at 09:00.'),
]
URIS = [
    'ftp://example.test/private', 'mailto:private@example.test', 'tel:+15555550100',
    'data:text/plain,private', 'javascript:alert(1)', 'file:///private/report',
    'urn:example:private', 'custom+scheme.v1-x:private', 'custom:*', 'custom:**',
    'custom:_', 'custom:#', 'custom:~', '**custom:private**',
    'ＦＴＰ：//example.test/private',
    *[scheme + spacing + ': private' for scheme in
      ('http', 'https', 'ftp', 'ftps', 'mailto', 'tel', 'data', 'javascript', 'file', 'urn')
      for spacing in ('', ' ')],
]

@pytest.mark.parametrize('raw,expected', LABELS)
@pytest.mark.parametrize('field', ['title', 'body'])
def test_ordinary_labels_are_informative(raw, expected, field):
    text = {'title': 'Public update', 'body': 'Ready.'}
    text[field] = raw
    assert safe_preview(**text) == {**text, field: expected}

@pytest.mark.parametrize('uri', URIS)
@pytest.mark.parametrize('field', ['title', 'body'])
def test_uri_rejections_survive_label_exception(uri, field):
    for prefix in ('', 'Public result. ' * 30):
        text = {'title': 'Public update', 'body': 'Ready.'}
        text[field] = prefix + uri
        assert safe_preview(**text) == GENERIC_PREVIEW
