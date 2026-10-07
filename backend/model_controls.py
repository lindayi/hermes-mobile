"""Narrow read-only owner model inventory; no inferred reasoning capabilities."""
import re
from .hermes_client import IntegrationUnavailable

UNAVAILABLE = {'available': False, 'models': [], 'default': None}

from pathlib import Path
_OWNER_SOURCE = Path('/home/lindayi/projects/hermes-mobile')
_OWNER_RELEASES = Path('/home/lindayi/.local/share/hermes-mobile-deploy/releases')
_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '174377e30136ee87d8712c572e0d0de68112959c3368f7b5c9a539b06a544360',
    'backend/native_api_service.py': 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e79cfee2bf8d32c3f51dd3ee9247e23029376e10c5e55ea21aa373d6d72535c5',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact pre-photo source set, retained for drain/rollback.
_PRE_PHOTO_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '564f0ab912a1d138f2ac5b1271935ef5aa2c99cdd2ab48d0527363f1a031e65c',
    'backend/native_api_service.py': 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e79cfee2bf8d32c3f51dd3ee9247e23029376e10c5e55ea21aa373d6d72535c5',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact pre-clarification source set, retained for drain/rollback.
_PRE_CLARIFICATION_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '6e3a8796028925ea771bf90b2b97ba1fd7a47cbbd979a71e9be7fb525c129b16',
    'backend/native_api_service.py': 'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '230ab537cda34e2f8f497ce92a435b393a2cfc270638f1417213c6bc0a466610',
}

# Exact immediately prior version, retained for rollback.
_PRE_ROUTING_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '6e3a8796028925ea771bf90b2b97ba1fd7a47cbbd979a71e9be7fb525c129b16',
    'backend/native_api_service.py': (
        'a3a28cf5d83688e69e335c816febfe11'
        'acfdd72631fff14f4203d97b81e77c22'),
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '0159fbdd02705469853f51be7bb32479ea9e2fa0d6fdbc6789253d3b3c1c85fe',
}

# Exact immediate pre-timeout-fix source set, retained for drain/rollback.
_TIMEOUT_BASELINE_CONTROL_HASHES = {
    'backend/native_controls_service.py': 'f0b27766bb923976cc97dccacd54005989f74e026a6ecc2f167817a248ee24ab',
    'backend/native_run_controls.py': '5107e54ed631fe2579efe2fb50c6a8ba1e9e3616c4fcd1d0e8ead2f7f29445d9',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': 'e083b0941b2b849559cd685d946ed10fb14a87128cf8ea2d125f77cb38ce434b',
    'backend/native_session_deletion.py': '182246c696c5f409f9d6feafedbcd10278c938ad9b3bc858804ef3d49d15e0f6',
    'backend/native_notifications.py': '0159fbdd02705469853f51be7bb32479ea9e2fa0d6fdbc6789253d3b3c1c85fe',
}

_PREVIOUS_CONTROL_HASHES = {
    'backend/native_controls_service.py': '1d9a23a567c8896cd1f2c69f9e111e9c9be6297f5b7bfe773426891bd1354969',
    'backend/native_run_controls.py': '5107e54ed631fe2579efe2fb50c6a8ba1e9e3616c4fcd1d0e8ead2f7f29445d9',
    'backend/native_api_service.py':
        'a3a28cf5d83688e69e335c816febfe11acfdd72631fff14f4203d97b81e77c22',
    'backend/native_maintenance.py': '94feb8767f7bbbe5a641ad835712f0468b53e019a478d4887f18f0cfb0ed437a',
}


def _runtime_root_allowed(root):
    return (root.is_dir() and root.resolve() == root and
            (root == _OWNER_SOURCE or root.parent == _OWNER_RELEASES
             and re.fullmatch(r'[0-9a-f]{32}', root.name) is not None))


def _control_source_hashes(root):
    """Select an exact approved version, never a per-file mixture."""
    import hashlib
    try:
        versions = (_CONTROL_HASHES, _PRE_PHOTO_CONTROL_HASHES, _PRE_CLARIFICATION_CONTROL_HASHES,
                    _PRE_ROUTING_CONTROL_HASHES,
                    _TIMEOUT_BASELINE_CONTROL_HASHES, _PREVIOUS_CONTROL_HASHES)
        for hashes in versions:
            absent = set().union(*versions) - set(hashes)
            if any((root/name).exists() or (root/name).is_symlink() for name in absent):
                continue
            if all((root/name).is_file() and (root/name).resolve() == root/name
                   and hashlib.sha256((root/name).read_bytes()).hexdigest() == digest
                   for name, digest in hashes.items()):
                return dict(hashes)
    except OSError:
        pass
    return None


def _control_sources_match(root):
    return _control_source_hashes(root) is not None


def standalone_owner_verified():
    """Read-only deployment attestation; OS account/service manager are trusted.

    No credentials read. Reject a changed launcher, native implementation,
    process command, cwd, or listener socket. Tests inject this probe.
    """
    import hashlib
    import os
    import subprocess
    from pathlib import Path
    try:
        native=Path('/usr/local/lib/hermes-agent/gateway/platforms/api_server.py')
        if hashlib.sha256(native.read_bytes()).hexdigest()!='187c92509b3769c04756f0dc800d3597ea891ea21262e8a32ceaf3972ac95300':
            return False
        pid=subprocess.check_output(
            ['/usr/bin/systemctl','--user','show','hermes-mobile-api.service',
             '--property=MainPID','--value'],text=True,timeout=2).strip()
        if not pid.isdigit() or int(pid)<=0:
            return False
        proc=Path('/proc')/pid
        root=(proc/'cwd').resolve()
        if proc.stat().st_uid!=os.getuid() or not _runtime_root_allowed(root):
            return False
        if root == _OWNER_SOURCE:
            launcher=root/'backend/native_api_service.py'
            if hashlib.sha256(launcher.read_bytes()).hexdigest()!=_CONTROL_HASHES['backend/native_api_service.py']:
                return False
        else:
            launcher=root/'backend/native_controls_service.py'
            if not _control_sources_match(root):
                return False
        if (proc/'cmdline').read_bytes().split(b'\0')[:-1]!=[b'/usr/local/lib/hermes-agent/venv/bin/python',str(launcher).encode()]:
            return False
        sockets={os.readlink(p) for p in (proc/'fd').iterdir()}
        for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            fields=line.split()
            if fields[1]=='0100007F:48D2' and fields[3]=='0A' and 'socket:['+fields[9]+']' in sockets:
                return True
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return False


async def options(gateway):
    gateway.require_execution()
    caps = await gateway.request('GET', '/v1/capabilities')
    if not isinstance(caps, dict) or not isinstance(caps.get('features'), dict):
        return dict(UNAVAILABLE)
    features = caps['features']
    if features.get('run_submission') is not True or features.get('model_options') is not True:
        return dict(UNAVAILABLE)
    aliases = await gateway.request('GET', '/v1/models')
    if (not isinstance(aliases, dict) or not isinstance(aliases.get('data'), list)
            or len(aliases['data']) != 1 or not isinstance(aliases['data'][0], dict)
            or aliases['data'][0].get('id') != 'hermes-agent'):
        return dict(UNAVAILABLE)
    catalog = await gateway.request('GET', '/api/model/options')
    if not isinstance(catalog, dict) or catalog.get('provider') != 'copilot':
        return dict(UNAVAILABLE)
    providers = catalog.get('providers')
    if not isinstance(providers, list) or any(
        not isinstance(row, dict) or not isinstance(row.get('slug'), str)
        or not isinstance(row.get('authenticated'), bool)
        or not isinstance(row.get('models'), list)
        or any(not isinstance(model, str) for model in row['models'])
        for row in providers
    ):
        return dict(UNAVAILABLE)
    rows = [row for row in providers if row['slug'] == 'copilot' and row['authenticated'] is True]
    if len(rows) != 1:
        return dict(UNAVAILABLE)
    model_ids = list(dict.fromkeys(
        m for m in rows[0]['models']
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', m)
        and m not in ('hermes-agent', 'default')
    ))
    if not model_ids:
        return dict(UNAVAILABLE)
    models = [{'id':m,'provider':'copilot','label':m,'reasoning_efforts':[]}
              for m in model_ids]
    default = catalog.get('model')
    safe_default = ({'model':default,'provider':'copilot'}
                    if isinstance(default, str) and default in model_ids else None)
    return {'available':True,'models':models,'default':safe_default}
