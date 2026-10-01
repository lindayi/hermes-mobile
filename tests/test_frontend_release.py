from pathlib import Path
import re
import subprocess

import pytest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    files = {
        'index.html': '<link href="/hermes/styles.css?old=1"><script src="/hermes/app.js"></script>',
        'styles.css': '@import "./theme.css";body{background:url(icons/brand.svg#logo)}',
        'theme.css': 'body{color:red}',
        'app.js': "import {render} from './ui.mjs';navigator.serviceWorker.register('/hermes/sw.js',{scope:'/hermes/'});",
        'ui.mjs': "import {name} from './dep.mjs';export const render=()=>name+'/hermes/icons/brand.svg';",
        'dep.mjs': "export const name='Hermes';",
        'sw.js': "const CACHE='hermes-public-v3';const ASSETS=['/hermes/index.html','/hermes/styles.css','/hermes/ui.mjs'];const prefix='hermes-public-';",
        'manifest.webmanifest': '{"start_url":"/hermes/","icons":[{"src":"/hermes/icons/brand.svg"}]}',
        'icons/brand.svg': '<svg/>',
        'icons/icon.png': b'\x89PNG\x00',
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_build_versions_entire_graph_without_mutating_source(source, tmp_path):
    from deploy.frontend_release import build_frontend
    from deploy.assets import public_tree
    before = tree(source)
    output = tmp_path / 'release'
    assert build_frontend(source, output) == output
    names = tree(output)
    assert len(public_tree(output)) == len(before)
    version = re.fullmatch(r'app\.([0-9a-f]{16,})\.js', next(n for n in names if n.startswith('app.')))[1]
    mapping = {n: n if n == 'index.html' else str(Path(n).with_name(f'{Path(n).stem}.{version}{Path(n).suffix}')) for n in before}
    assert set(names) == set(mapping.values())
    for old, new in mapping.items():
        if old != 'index.html':
            assert new in names
    assert f'/hermes/styles.{version}.css?old=1' in names['index.html'].decode()
    assert f"'./ui.{version}.mjs'" in names[mapping['app.js']].decode()
    assert f"'/hermes/sw.{version}.js'" in names[mapping['app.js']].decode()
    assert f"'./dep.{version}.mjs'" in names[mapping['ui.mjs']].decode()
    assert f'/hermes/icons/brand.{version}.svg' in names[mapping['ui.mjs']].decode()
    assert f'./theme.{version}.css' in names[mapping['styles.css']].decode()
    assert f'icons/brand.{version}.svg#logo' in names[mapping['styles.css']].decode()
    assert f'hermes-public-{version}' in names[mapping['sw.js']].decode()
    assert "const prefix='hermes-public-'" in names[mapping['sw.js']].decode()
    assert f'/hermes/icons/brand.{version}.svg' in names[mapping['manifest.webmanifest']].decode()
    assert names[mapping['icons/icon.png']] == before['icons/icon.png']
    assert tree(source) == before
    build_frontend(source, tmp_path / 'again')
    assert tree(tmp_path / 'again') == names
    completed = subprocess.run(['node', '--input-type=module', '-e', f"import {{render}} from '{(output/mapping['ui.mjs']).as_uri()}';console.log(render())"], capture_output=True, text=True, check=True)
    assert completed.stdout.strip() == f'Hermes/hermes/icons/brand.{version}.svg'
    (source / 'theme.css').write_text('body{color:blue}')
    build_frontend(source, tmp_path / 'changed')
    assert set(tree(tmp_path / 'changed')) & set(names) == {'index.html'}


@pytest.mark.parametrize('hazard', ['case', 'directory-case', 'nested-output', 'parent-output', 'symlink', 'private', 'hardlink', 'ambiguous-name', 'existing'])
def test_rejects_unsafe_inputs_before_writing(source, tmp_path, hazard):
    from deploy.frontend_release import build_frontend
    import os
    output = tmp_path / 'release'
    if hazard == 'case':
        (source / 'UI.mjs').write_text('export const x=1;')
    elif hazard == 'directory-case':
        (source / 'Icons').mkdir()
        (source / 'Icons/new.svg').write_text('<svg/>')
    elif hazard == 'nested-output':
        output = source / 'release'
    elif hazard == 'parent-output':
        output = tmp_path
    elif hazard == 'symlink':
        (source / 'alias.js').symlink_to(source / 'app.js')
    elif hazard == 'private':
        (source / 'secret.json').write_text('{}')
    elif hazard == 'hardlink':
        os.link(source / 'app.js', source / 'alias.js')
    elif hazard == 'ambiguous-name':
        (source / 'bad?.js').write_text('')
    elif hazard == 'existing':
        output.mkdir()
        (output / 'index.html').write_text('do not overwrite')
    before = tree(source)
    with pytest.raises((ValueError, FileExistsError)):
        build_frontend(source, output)
    assert tree(source) == before
    if hazard not in ('existing', 'parent-output'):
        assert not output.exists()


def test_preserves_external_and_runtime_urls(source, tmp_path):
    from deploy.frontend_release import build_frontend
    extra = '\nconst urls=["https://cdn.invalid/hermes/app.js","//cdn.invalid/hermes/ui.mjs","/app-api/ui.mjs","/hermes/","data:text/plain,ui.mjs"];'
    with (source / 'app.js').open('a') as stream:
        stream.write(extra)
    output = build_frontend(source, tmp_path / 'release')
    assert extra in next(output.glob('app.*.js')).read_text()
