import importlib.util
from pathlib import Path
import stat
import json
import os
import pytest


@pytest.fixture
def trees(tmp_path):
    source, web, backup = (tmp_path/x for x in ('source','web','private/backup'))
    source.mkdir(); web.mkdir()
    (source/'index.html').write_text('new page')
    (web/'index.html').write_text('old page')
    return source, web, backup


@pytest.mark.parametrize('hazard', ['source-link','web-link','backup-link','parent-link','source-hardlink','web-hardlink','source-fifo','source-backup','web-backup','traversal'])
def test_publisher_rejects_unsafe_paths_before_writing(trees, tmp_path, hazard):
    from deploy.assets import publish_assets
    source, web, backup = trees
    original_web = web
    if hazard in ('source-link','web-link'):
        alias=tmp_path/'alias';alias.symlink_to(source if hazard=='source-link' else web, target_is_directory=True)
        if hazard=='source-link': source=alias
        else: web=alias
    elif hazard=='backup-link':
        backup.parent.mkdir();backup.symlink_to(tmp_path/'destination', target_is_directory=True)
    elif hazard=='parent-link':
        alias=tmp_path/'alias';alias.symlink_to(tmp_path, target_is_directory=True);backup=alias/'new-backup'
    elif hazard.endswith('hardlink'):
        os.link(tmp_path/'web/index.html',(source if hazard=='source-hardlink' else web)/'linked.html')
    elif hazard=='source-fifo': os.mkfifo(source/'bad.html')
    elif hazard=='source-backup': backup=source/'backup'
    elif hazard=='web-backup': backup=web/'backup'
    elif hazard=='traversal': backup=tmp_path/'unused/../backup'
    with pytest.raises((ValueError,OSError)):publish_assets(source, web, backup)
    assert (original_web/'index.html').read_text()=='old page'


def test_restore_rejects_tampered_path_before_any_writes(trees, tmp_path):
    from deploy.assets import publish_assets, restore_assets
    source, web, backup=trees
    publish_assets(source,web,backup)
    target=tmp_path/'escape.html';target.write_text('unchanged')
    data=json.loads((backup/'manifest.json').read_text())
    data['new']['../escape.html']='0'*64
    (backup/'manifest.json').write_text(json.dumps(data))
    with pytest.raises((ValueError,OSError)):restore_assets(web,backup)
    assert target.read_text()=='unchanged'
    assert (web/'index.html').read_text()=='new page'


def test_public_subdirectories_are_readable_under_private_umask(trees):
    from deploy.assets import publish_assets
    source,web,backup=trees
    (source/'icons').mkdir();(source/'icons/i.svg').write_text('<svg/>')
    old=os.umask(0o077)
    try: publish_assets(source,web,backup)
    finally: os.umask(old)
    assert stat.S_IMODE((web/'icons').stat().st_mode)==0o755


@pytest.mark.parametrize('direction',['file-to-directory','directory-to-file'])
def test_layout_conflict_fails_before_backup_or_partial_publication(trees, direction):
    from deploy.assets import publish_assets
    source,web,backup=trees
    file_root,dir_root=(web,source) if direction=='file-to-directory' else (source,web)
    (file_root/'bundle.js').write_text('bundle')
    (dir_root/'bundle.js').mkdir();(dir_root/'bundle.js/part.js').write_text('part')
    with pytest.raises(ValueError):publish_assets(source,web,backup)
    assert (web/'index.html').read_text()=='old page'
    assert not backup.exists()


def test_write_failure_restores_preimages(trees, monkeypatch):
    import deploy.assets as assets
    source,web,backup=trees
    (source/'app.js').write_text('new code')
    real=assets._write
    def fail_once(path,data,**kw):
        if path==web/'index.html' and data==b'new page': raise OSError('injected write failure')
        return real(path,data,**kw)
    monkeypatch.setattr(assets,'_write',fail_once)
    with pytest.raises(OSError):assets.publish_assets(source,web,backup)
    assert (web/'index.html').read_text()=='old page'
    assert not (web/'app.js').exists()


def test_actual_http_sees_published_files_and_verified_rollback(trees):
    from deploy.assets import publish_assets, restore_assets
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    from functools import partial
    from threading import Thread
    from urllib.request import urlopen
    source,web,backup=trees
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(web)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/index.html'
        with urlopen(url) as response:assert response.read()==b'old page'
        publish_assets(source,web,backup)
        with urlopen(url) as response:assert response.read()==b'new page'
        restore_assets(web,backup)
        with urlopen(url) as response:assert response.read()==b'old page'
        (backup/'files/index.html').write_text('tampered')
        with pytest.raises(ValueError):restore_assets(web,backup)
    finally:server.shutdown();server.server_close();thread.join()


def test_publication_and_rollback_retain_exact_previous_assets(tmp_path):
    assert importlib.util.find_spec('deploy.assets'), 'Unprivileged static publisher missing'
    from deploy.assets import publish_assets, restore_assets
    source, web, backup = (tmp_path/x for x in ('source','web','private/backup'))
    source.mkdir(); web.mkdir()
    (source/'index.html').write_text('new page')
    (source/'app.js').write_text('new code')
    (source/'icons').mkdir(); (source/'icons/icon.svg').write_text('<svg/>')
    (web/'index.html').write_text('old page')
    (web/'obsolete.js').write_text('old code')
    publish_assets(source, web, backup)
    assert (web/'index.html').read_text() == 'new page'
    assert (web/'app.js').read_text() == 'new code'
    assert not (web/'obsolete.js').exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert stat.S_IMODE((backup/'manifest.json').stat().st_mode) == 0o600
    assert stat.S_IMODE((web/'app.js').stat().st_mode) == 0o644
    restore_assets(web, backup)
    assert (web/'index.html').read_text() == 'old page'
    assert (web/'obsolete.js').read_text() == 'old code'
    assert not (web/'app.js').exists()
    assert not (web/'icons/icon.svg').exists()
