"""One-time root permission grant; application bootstrap runs only after setuid.

Fixed CLI targets. Does not edit sudoers, WordPress, certificates, private app
state, native Hermes, or other profiles. NoNewPrivileges stays enabled.
"""
import argparse
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.assets import checked_path, public_tree, _write

PROJECT = Path('/home/lindayi/projects/hermes-mobile')
WEBROOT = Path('/var/www/html/hermes')
INCLUDE = Path('/etc/apache2/hermes-mobile.conf')
BEGIN = '# BEGIN HERMES SELF DEPLOY STATIC ONLY'
END = '# END HERMES SELF DEPLOY STATIC ONLY'


def harden(text, webroot):
    path = str(checked_path(webroot))
    if any(char in path for char in ('"', '\n', '\r')):
        raise ValueError('Unsafe Apache path')
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(END) < text.index(BEGIN):
            raise ValueError('Malformed existing self-deploy protection')
        start, stop = text.index(BEGIN), text.index(END)+len(END)
        text = text[:start]+text[stop:]
    # A specific default-handler inside FilesMatch beats inherited PHP handlers.
    # The negative match denies credentials, dotfiles, scripts, and temp files.
    return text.rstrip()+f'''\n\n{BEGIN}
<Directory "{path}">
    Options -Indexes -ExecCGI -FollowSymLinks -SymLinksIfOwnerMatch
    AllowOverride None
    <IfModule mod_rewrite.c>
        RewriteEngine Off
    </IfModule>
    <FilesMatch "^[A-Za-z0-9][A-Za-z0-9_.-]*\\.(?:html|css|js|mjs|webmanifest|svg|png|ico)$">
        SetHandler default-handler
    </FilesMatch>
    <FilesMatch "^(?![A-Za-z0-9][A-Za-z0-9_.-]*\\.(?:html|css|js|mjs|webmanifest|svg|png|ico)$)">
        Require expr "-d %{{REQUEST_FILENAME}}"
    </FilesMatch>
</Directory>
{END}
'''


def _open_components(base_fd, parts, *, directory):
    fd = os.dup(base_fd)
    try:
        for index, part in enumerate(parts):
            if part in ('', '.', '..') or '/' in part:
                raise ValueError('Unsafe directory component')
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if directory or index < len(parts)-1:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_directory(path):
    base = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _open_components(base, path.parts[1:], directory=True)
    finally:
        os.close(base)


def enable(webroot, include, backup, uid, gid, *, run, set_owner=os.fchown):
    webroot, include, backup = map(checked_path, (webroot, include, backup))
    files = public_tree(webroot)
    paths = [*files, *sorted((p for p in webroot.rglob('*') if p.is_dir()), key=lambda p:len(p.parts), reverse=True), webroot]
    info = include.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Apache include must be a regular, unaliased file')
    original = include.read_bytes()
    revised = harden(original.decode(), webroot).encode()
    backup.mkdir(mode=0o700)
    backup.chmod(0o700)
    _write(backup/'apache-include.conf', original, mode=0o600, directory_mode=0o700)
    handles=[];changed=[];apache_loaded=False
    root_fd=_open_directory(webroot)
    try:
        # Pin objects before handing over writable directories, so replacement
        # of a path cannot redirect a later privileged chown/chmod.
        for path in paths:
            fd=_open_components(root_fd,path.relative_to(webroot).parts,directory=path not in files)
            s=os.fstat(fd)
            if not (stat.S_ISDIR(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink==1)):
                os.close(fd);raise ValueError('Unexpected public filesystem object')
            handles.append((fd,s))
        _write(include, revised)
        run(['/usr/sbin/apache2ctl','configtest'])
        run(['/usr/bin/systemctl','reload','apache2'])
        apache_loaded=True
        for fd, s in handles:
            changed.append((fd,s))
            set_owner(fd,uid,gid)
            os.fchmod(fd,0o755 if stat.S_ISDIR(s.st_mode) else 0o644)
    except BaseException:
        for fd,s in reversed(changed):
            set_owner(fd,s.st_uid,s.st_gid)
            os.fchmod(fd,stat.S_IMODE(s.st_mode))
        _write(include, original, mode=stat.S_IMODE(info.st_mode))
        if apache_loaded:
            run(['/usr/sbin/apache2ctl','configtest'])
            run(['/usr/bin/systemctl','reload','apache2'])
        raise
    finally:
        for fd,_ in handles:os.close(fd)
        os.close(root_fd)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if os.geteuid()!=0:
        raise PermissionError('This one-time permission grant must be run with sudo from your terminal')
    user=pwd.getpwnam('lindayi')
    backup=Path('/var/backups')/('hermes-self-deploy-'+uuid.uuid4().hex)
    enable(WEBROOT,INCLUDE,backup,user.pw_uid,user.pw_gid,
           run=lambda args:subprocess.run(args,check=True))
    print('Granted lindayi write access only to Hermes public assets; Apache static-only protection enabled.',flush=True)
    print('Apache include backup:',backup,flush=True)
    # Application/tests/systemd-user operations must NEVER execute as root.
    os.initgroups(user.pw_name,user.pw_gid)
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.umask(0o077)
    os.environ.clear()
    os.environ.update(HOME=user.pw_dir,USER=user.pw_name,LOGNAME=user.pw_name,
                      PATH='/usr/local/bin:/usr/bin:/bin',LANG='C.UTF-8',
                      XDG_RUNTIME_DIR=f'/run/user/{user.pw_uid}',
                      DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{user.pw_uid}/bus')
    os.chdir(PROJECT)
    python=str(PROJECT/'.venv/bin/python')
    os.execv(python,[python,'-m','deploy.self_deploy','--bootstrap'])


if __name__=='__main__':main()
