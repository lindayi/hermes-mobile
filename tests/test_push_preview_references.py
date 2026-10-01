"""Useful previews retain public outcomes while removing source URLs, not secrets."""
import pytest
from backend.notification_policy import GENERIC_PREVIEW, safe_preview


@pytest.mark.parametrize('reference', [
    'https://shop.example/products/pfd',
    'http://shop.example/products/pfd',
    'www.shop.example/products/pfd',
    '[Store](https://shop.example/products/pfd)',
])
def test_public_deal_stays_informative_without_its_reference(reference):
    result = safe_preview('Deal found', 'Minimalist: CAD105, in stock. ' + reference)
    assert result == {'title': 'Deal found', 'body': 'Minimalist: CAD105, in stock.'}


@pytest.mark.parametrize('unsafe', [
    'https://shop.example/?token=not-for-preview',
    'https://shop.example/?password=not-for-preview',
    'ftp://shop.example/private',
    'mailto:private@example.test',
    'Public text. ' * 50 + 'api_key=not-for-preview',
    '[Open](https://evil.test)',
    'https://shop.example/products/pfd',
])
def test_secret_inputs_and_reference_only_messages_remain_generic(unsafe):
    assert safe_preview('Deal found', unsafe) == GENERIC_PREVIEW


def test_reference_in_title_is_removed_without_exposing_destination():
    assert safe_preview('Invoice ready https://example.test/invoice', 'Review before sending.') == {
        'title': 'Invoice ready', 'body': 'Review before sending.'}


@pytest.mark.parametrize('raw', [
    'pass[Source](https://example.test/public)word: SYNTHETIC_ONLY_4729',
    'pass**word: SYNTHETIC_ONLY_4729',
])
@pytest.mark.parametrize('field', ['title', 'body'])
def test_transformed_sensitive_labels_are_checked_before_clipping(raw, field):
    content = {'title': 'Public result', 'body': 'Ready.'}
    content[field] = raw
    assert safe_preview(**content) == GENERIC_PREVIEW


def test_late_transformed_sensitive_label_suppresses_whole_preview():
    body = ('SYNTHETIC_ONLY_4729 ' + 'Public detail. ' * 40 +
            'pass[Source](https://example.test/public)word: SYNTHETIC_ONLY_4729')
    assert safe_preview('Public result', body) == GENERIC_PREVIEW
