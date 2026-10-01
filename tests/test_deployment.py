import importlib.util
from pathlib import Path
import pytest


def test_vhost_include_targets_only_existing_lindayi_https():
    assert importlib.util.find_spec('deploy.install') is not None, 'Deployment installer missing'
    from deploy.install import add_include
    other='<VirtualHost *:443>\n ServerName other.test\n</VirtualHost>\n'
    owner='<VirtualHost *:443>\n ServerName lindayi.me\n SSLEngine on\n</VirtualHost>\n'
    plain='<VirtualHost *:80>\n ServerName lindayi.me\n</VirtualHost>\n'
    result=add_include(other+owner+plain)
    assert result.count('Include /etc/apache2/hermes-mobile.conf')==1
    assert other in result and plain in result
    assert add_include(result)==result
    with pytest.raises(ValueError):add_include(other+plain)


def test_public_copy_rejects_secrets_and_symlinks(tmp_path):
    from deploy.install import public_files
    (tmp_path/'index.html').write_text('public')
    assert [p.name for p in public_files(tmp_path)]==['index.html']
    (tmp_path/'secrets.json').write_text('test-only')
    with pytest.raises(ValueError):public_files(tmp_path)
    (tmp_path/'secrets.json').unlink()
    (tmp_path/'linked.html').symlink_to(tmp_path/'index.html')
    with pytest.raises(ValueError):public_files(tmp_path)


def test_web_install_checks_config_and_rolls_back_on_failure(tmp_path):
    from deploy.install import install_web
    frontend=tmp_path/'frontend';frontend.mkdir();(frontend/'index.html').write_text('public')
    vhost=tmp_path/'vhost.conf';original='<VirtualHost *:443>\n ServerName lindayi.me\n</VirtualHost>\n';vhost.write_text(original)
    include=tmp_path/'include.conf';include.write_text('old include')
    source=tmp_path/'source.conf';source.write_text('new include')
    def reject(*args,**kwargs):raise RuntimeError('invalid config')
    with pytest.raises(RuntimeError):
        install_web(frontend,tmp_path/'web',vhost,include,source,reject)
    assert vhost.read_text()==original
    assert include.read_text()=='old include'
    calls=[]
    install_web(frontend,tmp_path/'web',vhost,include,source,lambda args:calls.append(args))
    assert (tmp_path/'web/index.html').read_text()=='public'
    assert '/usr/sbin/apache2ctl' in calls[0]


def test_installer_requires_explicit_root_execution(monkeypatch):
    import os
    from deploy.install import main
    monkeypatch.setattr(os,'geteuid',lambda:1002)
    with pytest.raises(PermissionError):main()
