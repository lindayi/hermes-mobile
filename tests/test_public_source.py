"""Public-source privacy regression: VAPID contact is a public site, not personal email."""
import inspect
from backend.notifications import NotificationService


def test_default_push_contact_uses_public_site():
    assert inspect.signature(NotificationService).parameters['vapid_subject'].default == 'https://lindayi.me'
