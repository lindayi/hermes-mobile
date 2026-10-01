"""Unprivileged static-only publishing; backups stay outside the web root.

Each file replacement is atomic. A whole multi-file release is not a filesystem
transaction; the controller verifies and restores on failure. No services here.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import stat
import re


def _digest(data):
    return hashlib.sha256(data).hexdigest()


ALLOWED = {'.html', '.css', '.js', '.mjs', '.webmanifest', '.svg', '.png', '.ico'}


def checked_path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Path traversal is not allowed')
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symlinked deployment paths are not allowed')
    return path


def _asset_name(name):
    path = Path(name)
    if (not isinstance(name, str) or path.is_absolute() or not path.parts
            or '\\' in name or any(p.startswith('.') for p in path.parts)
            or path.suffix not in ALLOWED):
        raise ValueError('Non-public asset name')
    return path


def public_tree(root, *, require_index=True):
    root = checked_path(root)
    if not root.is_dir():
        raise ValueError('Public asset directory missing')
    files = []
    for path in sorted(root.rglob('*')):
        mode = path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(mode.st_mode) or stat.S_ISDIR(mode.st_mode)):
            raise ValueError('Special files are not public assets')
        if any(p.startswith('.') for p in path.relative_to(root).parts):
            raise ValueError('Hidden public paths are not allowed')
        if path.is_file():
            _asset_name(str(path.relative_to(root)))
            if mode.st_nlink != 1:
                raise ValueError('Hardlinked public assets are not allowed')
            files.append(path)
    if require_index and root/'index.html' not in files:
        raise ValueError('Missing index.html')
    return files


def _assets(root):
    root = checked_path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in public_tree(root)}


def _mkdirs(path, mode):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        directory.chmod(mode)


def _write(path, data, *, mode=0o644, directory_mode=0o755):
    path = Path(path)
    checked_path(path)
    _mkdirs(path.parent, directory_mode)
    fd, temporary = tempfile.mkstemp(prefix='.hermes-deploy-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish_assets(source, webroot, backup_dir):
    source, webroot, backup_dir = map(checked_path, (source, webroot, backup_dir))
    if (source.is_relative_to(webroot) or webroot.is_relative_to(source)
            or backup_dir.is_relative_to(source) or backup_dir.is_relative_to(webroot)
            or source.is_relative_to(backup_dir) or webroot.is_relative_to(backup_dir)
            or backup_dir.is_relative_to('/var/www')):
        raise ValueError('Deployment and private backup paths must be separate')
    if backup_dir.exists():
        raise FileExistsError(backup_dir)
    new, old = _assets(source), _assets(webroot)
    for name in new:
        target = webroot/name
        if target.is_dir() or any(parent.exists() and not parent.is_dir() for parent in target.parents if parent != webroot and parent.is_relative_to(webroot)):
            raise ValueError('Static file/directory layout conflict; nothing published')
    _mkdirs(backup_dir, 0o700)
    for name, data in old.items():
        _write(backup_dir/'files'/name, data, mode=0o600, directory_mode=0o700)
    manifest = {'version': 1, 'webroot': str(webroot.absolute()),
                'old': {name: _digest(data) for name, data in old.items()},
                'new': {name: _digest(data) for name, data in new.items()}}
    _write(backup_dir/'manifest.json', json.dumps(manifest).encode(), mode=0o600, directory_mode=0o700)
    try:
        for name, data in new.items():
            _write(webroot/name, data)
        for name in old.keys()-new.keys():
            (webroot/name).unlink()
    except BaseException:
        restore_assets(webroot, backup_dir)
        raise


def restore_assets(webroot, backup_dir):
    webroot, backup_dir = map(checked_path, (webroot, backup_dir))
    if backup_dir.is_relative_to(webroot) or backup_dir.is_relative_to('/var/www'):
        raise ValueError('Static backup must be private')
    public_tree(webroot, require_index=False)
    manifest_path = checked_path(backup_dir/'manifest.json')
    manifest = json.loads(manifest_path.read_text())
    if (not isinstance(manifest, dict) or set(manifest) != {'version','webroot','old','new'}
            or manifest['version'] != 1 or manifest['webroot'] != str(webroot)):
        raise ValueError('Invalid static backup manifest')
    for version in ('old', 'new'):
        records = manifest[version]
        if not isinstance(records, dict) or 'index.html' not in records:
            raise ValueError('Invalid static backup records')
        for name, digest in records.items():
            _asset_name(name)
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Invalid static checksum')
    files = {}
    for name in manifest['old']:
        path = checked_path(backup_dir/'files'/name)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe static backup file')
        files[name] = path.read_bytes()
    if any(_digest(data) != manifest['old'][name] for name, data in files.items()):
        raise ValueError('Static backup checksum mismatch')
    for name, data in files.items():
        _write(webroot/name, data)
    for name in manifest['new'].keys()-manifest['old'].keys():
        (webroot/name).unlink(missing_ok=True)
