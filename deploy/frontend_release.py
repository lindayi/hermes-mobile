"""Deterministic /hermes/ public frontend releases; no execution or network I/O."""
import hashlib
from pathlib import Path
import posixpath
import re

from deploy.assets import checked_path, public_tree


_TEXT = {'.html', '.css', '.js', '.mjs', '.webmanifest', '.svg'}


def build_frontend(source: Path, destination: Path) -> Path:
    """Build a fresh public tree and return its path. Never modify the source."""
    source, destination = checked_path(source), checked_path(destination)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Source and release directories must be separate')
    if destination.exists():
        raise FileExistsError(destination)
    files = {p.relative_to(source).as_posix(): p.read_bytes() for p in public_tree(source)}
    seen = {}
    for name in files:
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
            raise ValueError('Ambiguous public URL name')
        for path in (Path(name), *Path(name).parents):
            key = str(path).casefold()
            if key in seen and seen[key] != str(path):
                raise ValueError('Case-colliding public paths')
            seen[key] = str(path)
    digest = hashlib.sha256(b'hermes-frontend-release-v1\0')
    for name, data in sorted(files.items()):
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big') + encoded)
        digest.update(len(data).to_bytes(8, 'big') + data)
    version = digest.hexdigest()[:24]
    mapping = {name: name if name == 'index.html' else str(Path(name).with_name(
        f'{Path(name).stem}.{version}{Path(name).suffix}')) for name in files}
    output = {}
    for name, data in files.items():
        if Path(name).suffix in _TEXT:
            replacements = {}
            directory = posixpath.dirname(name) or '.'
            for old, new in mapping.items():
                replacements['/hermes/' + old] = '/hermes/' + new
                relative = posixpath.relpath(old, directory)
                renamed = posixpath.relpath(new, directory)
                replacements[relative] = renamed
                if not relative.startswith('../'):
                    replacements['./' + relative] = './' + renamed
            pattern = r'''(?<=[\s'"`(=])(''' + '|'.join(
                re.escape(key) for key in sorted(replacements, key=len, reverse=True)
            ) + r''')(?=[\s'"`)<>?#]|$)'''
            text = re.sub(pattern, lambda match: replacements[match[1]], data.decode('utf-8'))
            if name == 'sw.js':
                text = re.sub(r'''(\bconst\s+CACHE\s*=\s*)(['"])hermes-public-[^'"]*\2''',
                              lambda m: m[1] + m[2] + 'hermes-public-' + version + m[2], text)
            data = text.encode('utf-8')
        output[mapping[name]] = data
    destination.mkdir(parents=True)
    for name, data in output.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return destination
