import importlib.util
from pathlib import Path
import os
import pytest


def test_one_time_setup_limits_grant_to_hermes_static_tree(tmp_path):
    assert importlib.util.find_spec('deploy.enable_self_deploy'), 'Scoped administrator setup missing'
    from deploy.enable_self_deploy import enable
    web=tmp_path/'hermes';web.mkdir();(web/'index.html').write_text('old');(web/'icons').mkdir();(web/'icons/a.svg').write_text('<svg/>')
    wordpress=tmp_path/'wp-config.php';wordpress.write_text('untouched')
    include=tmp_path/'hermes.conf';include.write_text('# original\n')
    before=wordpress.stat();calls=[];granted=[]
    def owner(fd,uid,gid):granted.append(Path(os.readlink(f'/proc/self/fd/{fd}')))
    enable(web,include,tmp_path/'backup',os.getuid(),os.getgid(),run=lambda cmd:calls.append(cmd),set_owner=owner)
    assert set(granted)=={web,web/'index.html',web/'icons',web/'icons/a.svg'}
    assert wordpress.stat().st_mtime_ns==before.st_mtime_ns
    assert (tmp_path/'backup/apache-include.conf').read_text()=='# original\n'
    text=include.read_text()
    assert 'SetHandler default-handler' in text and 'Require expr "-d %{REQUEST_FILENAME}"' in text and 'AllowOverride None' in text
    assert calls==[['/usr/sbin/apache2ctl','configtest'],['/usr/bin/systemctl','reload','apache2']]


def test_bad_apache_configuration_never_grants_permissions(tmp_path):
    from deploy.enable_self_deploy import enable
    web=tmp_path/'hermes';web.mkdir();(web/'index.html').write_text('old')
    include=tmp_path/'hermes.conf';include.write_text('# original\n')
    granted=[]
    def fail(cmd):raise RuntimeError('configuration rejected')
    with pytest.raises(RuntimeError):enable(web,include,tmp_path/'backup',1,1,run=fail,set_owner=lambda *args:granted.append(args))
    assert include.read_text()=='# original\n'
    assert not granted


@pytest.mark.parametrize('wordpress_parent', [False, True])
def test_hardened_directory_really_serves_static_only_with_apache(tmp_path, wordpress_parent):
    from deploy.enable_self_deploy import harden
    from urllib.request import urlopen
    from urllib.error import HTTPError, URLError
    import pwd, grp, socket, subprocess, time
    if not Path('/usr/sbin/apache2').exists() or os.geteuid()==0:
        pytest.skip('requires installed Apache and an unprivileged test user')
    site=tmp_path/'site';site.mkdir()
    web=site/'hermes';web.mkdir()
    parent_rules=''
    if wordpress_parent:
        (site/'index.php').write_text('WordPress route fixture')
        (site/'.htaccess').write_text('RewriteEngine On\nRewriteRule ^index\\.php$ - [L]\nRewriteCond %{REQUEST_FILENAME} !-f\nRewriteCond %{REQUEST_FILENAME} !-d\nRewriteRule . /index.php [L]\n')
        parent_rules=f'<Directory "{site}">\n Options +FollowSymLinks\n AllowOverride FileInfo\n Require all granted\n</Directory>\n'

    for name in ('index.html','app.js','example.php.html','bad.php','credentials.json','.env','upload.py'):
        (web/name).write_text('fixture content')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config=tmp_path/'apache.conf'
    modules='\n'.join(f'LoadModule {name}_module /usr/lib/apache2/modules/mod_{name}.so' for name in ('mpm_event','authz_core','dir','mime','rewrite'))
    config.write_text(f'''ServerRoot /etc/apache2
ServerName localhost
Listen 127.0.0.1:{port}
PidFile {tmp_path}/apache.pid
ErrorLog {tmp_path}/error.log
User {pwd.getpwuid(os.getuid()).pw_name}
Group {grp.getgrgid(os.getgid()).gr_name}
{modules}
TypesConfig /etc/mime.types
DocumentRoot {site}
{parent_rules}
DirectoryIndex index.html
<FilesMatch "\\.html$">
 SetHandler bogus-handler
</FilesMatch>
<Directory "{web}">
 Require all granted
</Directory>
'''+harden('',web))
    subprocess.run(['/usr/sbin/apache2','-t','-f',str(config)],check=True,capture_output=True)
    process=subprocess.Popen(['/usr/sbin/apache2','-X','-f',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        url=f'http://127.0.0.1:{port}/hermes'
        deadline=time.monotonic()+5
        while True:
            try:
                with urlopen(url+'/index.html',timeout=1) as response:assert response.read()==b'fixture content'
                break
            except URLError:
                if process.poll() is not None or time.monotonic()>=deadline:raise
                time.sleep(.05)
        with urlopen(url+'/',timeout=2) as response:assert response.read()==b'fixture content'
        if wordpress_parent:
            with urlopen(f'http://127.0.0.1:{port}/wp-route',timeout=2) as response:assert response.read()==b'WordPress route fixture'
        for name in ('app.js','example.php.html'):
            with urlopen(url+'/'+name,timeout=2) as response:assert response.status==200
        outside=tmp_path/'outside-secret.php';outside.write_text('outside fixture')
        (web/'linked.html').symlink_to(outside)
        for name in ('bad.php','credentials.json','.env','upload.py','linked.html'):
            with pytest.raises(HTTPError) as error:urlopen(url+'/'+name,timeout=2)
            assert error.value.code==403
    finally:
        process.terminate();process.communicate(timeout=5)


def test_root_grant_cannot_follow_an_ancestor_swapped_during_open(tmp_path, monkeypatch):
    import deploy.enable_self_deploy as setup
    web=tmp_path/'hermes';web.mkdir();(web/'index.html').write_text('old')
    icons=web/'icons';icons.mkdir();(icons/'a.svg').write_text('icon')
    outside=tmp_path/'outside';outside.mkdir();victim=outside/'a.svg';victim.write_text('not public');victim.chmod(0o600)
    include=tmp_path/'apache.conf';include.write_text('# original\n')
    real_open=os.open;swapped=False;granted=[]
    def racing_open(path,flags,*args,**kwargs):
        nonlocal swapped
        if str(path).endswith('a.svg') and not swapped:
            swapped=True;icons.rename(web/'saved-icons');icons.symlink_to(outside,target_is_directory=True)
            try:return real_open(path,flags,*args,**kwargs)
            finally:icons.unlink();(web/'saved-icons').rename(icons)
        return real_open(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',racing_open)
    setup.enable(web,include,tmp_path/'backup',os.getuid(),os.getgid(),run=lambda cmd:None,
                 set_owner=lambda fd,*unused:granted.append(Path(os.readlink(f'/proc/self/fd/{fd}'))))
    assert swapped
    assert victim not in granted
    assert victim.stat().st_mode & 0o777==0o600


def test_bootstrap_exec_happens_after_permanent_privilege_drop(monkeypatch):
    import deploy.enable_self_deploy as setup
    from types import SimpleNamespace
    calls=[];execution=[]
    monkeypatch.setattr(setup.sys,'argv',['enable_self_deploy.py'])
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(setup.pwd,'getpwnam',lambda name:SimpleNamespace(pw_name='lindayi',pw_uid=1002,pw_gid=1002,pw_dir='/home/lindayi'))
    monkeypatch.setattr(setup,'enable',lambda *a,**kw:calls.append('grant'))
    monkeypatch.setattr(os,'initgroups',lambda *a:calls.append('groups'))
    monkeypatch.setattr(os,'setgid',lambda *a:calls.append('gid'))
    monkeypatch.setattr(os,'setuid',lambda *a:calls.append('uid'))
    monkeypatch.setattr(os,'umask',lambda *a:None)
    monkeypatch.setattr(os,'chdir',lambda *a:None)
    monkeypatch.setattr(os,'environ',{'SUDO_USER':'root','SECRET':'fixture'})
    def execute(path,args):calls.append('exec');execution.append((path,args,dict(os.environ)))
    monkeypatch.setattr(os,'execv',execute)
    setup.main()
    assert calls==['grant','groups','gid','uid','exec']
    assert execution[0][1][-3:]==['-m','deploy.self_deploy','--bootstrap']
    assert 'SECRET' not in execution[0][2] and 'SUDO_USER' not in execution[0][2]
    assert execution[0][2]['HOME']=='/home/lindayi'


def test_partial_grant_failure_restores_original_modes_and_include(tmp_path):
    from deploy.enable_self_deploy import enable
    web=tmp_path/'hermes';web.mkdir();(web/'index.html').write_text('old');(web/'index.html').chmod(0o600)
    include=tmp_path/'apache.conf';include.write_text('# original\n')
    calls=[];grants=0
    def owner(fd,uid,gid):
        nonlocal grants
        if uid==999:
            grants+=1
            if grants==2:raise OSError('injected grant failure')
    with pytest.raises(OSError):enable(web,include,tmp_path/'backup',999,999,run=lambda c:calls.append(c),set_owner=owner)
    assert include.read_text()=='# original\n'
    assert (web/'index.html').stat().st_mode & 0o777==0o600
    assert len(calls)==4


def test_setup_refuses_nonpublic_files_before_changing_apache(tmp_path):
    from deploy.enable_self_deploy import enable
    web=tmp_path/'hermes';web.mkdir();(web/'index.html').write_text('old');(web/'secret.pem').write_text('fake')
    include=tmp_path/'hermes.conf';include.write_text('# original\n')
    with pytest.raises(ValueError):enable(web,include,tmp_path/'backup',1,1,run=lambda cmd:pytest.fail('no command expected'))
    assert include.read_text()=='# original\n'
