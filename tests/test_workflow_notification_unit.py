"""Static source-unit contract; never invokes systemd or the notification worker."""
import configparser
from pathlib import Path


def test_notification_user_unit_replaces_private_devices_without_other_changes():
    unit = configparser.ConfigParser(interpolation=None)
    unit.optionxform = str
    source = Path(__file__).resolve().parents[1]
    unit.read(source / 'deploy/hermes-workflow-notifications.service')

    # Fixed paths are compared as strings, never read or executed. Exact equality
    # catches removed safeguards, broader permissions and extra launch commands.
    assert unit.sections() == ['Unit', 'Service']
    assert dict(unit['Unit']) == {
        'Description': 'Record verified workflow outcomes in the Hermes owner Inbox',
    }
    assert dict(unit['Service']) == {
        'Type': 'oneshot',
        'WorkingDirectory': '/home/lindayi/projects/hermes-mobile-git',
        'Environment': 'PYTHONDONTWRITEBYTECODE=1',
        'UMask': '0077',
        'NoNewPrivileges': 'yes',
        'PrivateTmp': 'yes',
        'RestrictAddressFamilies': 'AF_UNIX',
        'SystemCallArchitectures': 'native',
        'SystemCallFilter': '~@raw-io',
        'PrivateNetwork': 'yes',
        'ProtectSystem': 'strict',
        'ProtectHome': 'read-only',
        'ReadWritePaths': '/home/lindayi/.local/share/hermes-mobile-live',
        'ExecStart': (
            '/home/lindayi/projects/hermes-mobile-git/.venv/bin/python '
            '/home/lindayi/projects/hermes-mobile-git/scripts/workflow_notifications.py --apply'
        ),
    }
