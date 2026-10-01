"""Reviewed root-only installer for the existing lindayi.me HTTPS vhost.

Run only when instructed after local tests. It does not create credentials, alter
cron jobs, issue certificates, or modify WordPress files. Backups are root-private.
"""
from pathlib import Path
import re

INCLUDE='    Include /etc/apache2/hermes-mobile.conf\n'


def add_include(text):
    count=0
    def replace(match):
        nonlocal count
        block=match.group(0)
        if not re.search(r'^\s*ServerName\s+lindayi\.me\s*$',block,re.M):
            return block
        count+=1
        if 'Include /etc/apache2/hermes-mobile.conf' in block:
            return block
        return re.sub(r'(^\s*ServerName\s+lindayi\.me[^\n]*\n)',lambda m:m.group(1)+INCLUDE,block,count=1,flags=re.M)
    result=re.sub(r'<VirtualHost\s+[^>]*:443\s*>.*?</VirtualHost>',replace,text,flags=re.S|re.I)
    if count!=1:
        raise ValueError('Expected exactly one existing HTTPS vhost for lindayi.me')
    return result


def public_files(root):
    root=Path(root)
    result=[]
    allowed={'.html','.css','.js','.mjs','.webmanifest','.svg','.png','.ico'}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Public assets must not be symlinks')
        if path.is_file():
            if path.suffix not in allowed or any(part.startswith('.') for part in path.relative_to(root).parts):
                raise ValueError('Non-public file in frontend artifact: '+str(path.relative_to(root)))
            result.append(path)
    if not (root/'index.html').is_file():
        raise ValueError('Missing public entry point')
    return result


def install_web(frontend,webroot,vhost,include,source_include,run):
    import shutil
    frontend,webroot,vhost,include=map(Path,(frontend,webroot,vhost,include))
    assets=public_files(frontend)
    if webroot.is_symlink() or include.is_symlink() or vhost.is_symlink():
        raise ValueError('Deployment targets must not be symlinks')
    old_vhost=vhost.read_text()
    revised=add_include(old_vhost)
    old_include=include.read_bytes() if include.exists() else None
    webroot.mkdir(parents=True,exist_ok=True,mode=0o755)
    for asset in assets:
        target=webroot/asset.relative_to(frontend)
        if target.is_symlink() or not target.resolve().is_relative_to(webroot.resolve()):
            raise ValueError('Unsafe existing public asset path')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
        shutil.copyfile(asset,target)
        target.chmod(0o644)
    try:
        include.write_bytes(Path(source_include).read_bytes())
        include.chmod(0o644)
        vhost.write_text(revised)
        run(['/usr/sbin/apache2ctl','configtest'])
        run(['/usr/bin/systemctl','reload','apache2'])
    except Exception:
        vhost.write_text(old_vhost)
        if old_include is None:
            include.unlink(missing_ok=True)
        else:
            include.write_bytes(old_include)
        raise


def main():
    import os
    import shutil
    import subprocess
    from datetime import datetime
    if os.geteuid()!=0:
        raise PermissionError('Run this reviewed installer with sudo from your terminal')
    project=Path('/home/lindayi/projects/hermes-mobile')
    vhost=Path('/etc/apache2/sites-available/000-default.conf')
    include=Path('/etc/apache2/hermes-mobile.conf')
    # Validate before touching the site's configuration.
    public_files(project/'frontend')
    add_include(vhost.read_text())
    backup=Path('/var/backups')/('hermes-mobile-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    shutil.copy2(vhost,backup/'000-default.conf')
    if include.exists():
        shutil.copy2(include,backup/'hermes-mobile.conf')
    run=lambda args:subprocess.run(args,check=True)
    run(['/usr/sbin/a2enmod','proxy','proxy_http','headers'])
    install_web(project/'frontend',Path('/var/www/html/hermes'),vhost,include,project/'deploy/apache-hermes-mobile.conf',run)
    print('HTTPS frontend and private API proxy installed at https://lindayi.me/hermes/')
    print('Existing TLS certificate reused. WordPress files and cron jobs untouched.')
    print('Apache configuration backup:',backup)


if __name__=='__main__':
    main()
