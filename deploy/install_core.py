"""Install reviewed Hermes compatibility patches. Requires explicit sudo execution."""
from pathlib import Path

ALLOWED={'run_agent.py','gateway/platforms/api_server.py','cron/scheduler.py','tools/send_message_tool.py'}


def patch_targets(text):
    paths=set()
    for line in text.splitlines():
        if line.startswith(('--- ','+++ ')):
            name=line[4:].split('\t')[0]
            if not name.startswith(('a/','b/')) or name[2:] not in ALLOWED:
                raise ValueError('Unexpected compatibility patch target')
            paths.add(name[2:])
    if not paths:raise ValueError('Empty compatibility patch')
    return paths


def validate_baselines(root,records,patched_only=False):
    import hashlib
    for name,record in records.items():
        if name not in ALLOWED:raise ValueError('Unexpected fingerprint target')
        path=Path(root)/name
        if path.is_symlink():raise ValueError('Refusing symlinked source')
        current=hashlib.sha256(path.read_bytes()).hexdigest()
        expected={record['staged_sha256']} if patched_only else {record['installed_sha256'],record['staged_sha256']}
        if current not in expected:raise ValueError('Source fingerprint mismatch: '+name)


def main():
    import os,shutil,subprocess,json
    from datetime import datetime
    if os.geteuid()!=0:raise PermissionError('Run this reviewed installer with sudo')
    project=Path('/home/lindayi/projects/hermes-mobile')
    root=Path('/usr/local/lib/hermes-agent')
    patches=[project/'patches/native-compat.patch',project/'patches/cron-delivery.patch']
    records={}
    for name in ('native-compat-baseline.json','cron-delivery-baseline.json'):
        records.update(json.loads((project/'patches'/name).read_text()))
    validate_baselines(root,records)
    all_paths=set()
    active=[]
    for patch in patches:
        targets=patch_targets(patch.read_text())
        for name in targets:
            if (root/name).is_symlink():raise ValueError('Refusing a symlinked Hermes target')
        command=['/usr/bin/patch','--batch','--fuzz=0','-p1','-i',str(patch)]
        result=subprocess.run(command+['--forward','--dry-run'],cwd=root,capture_output=True,text=True)
        if result.returncode:
            reverse=subprocess.run(command+['--reverse','--dry-run'],cwd=root,capture_output=True,text=True)
            if reverse.returncode:raise RuntimeError('Patch does not match installed Hermes; review required: '+patch.name+'\n'+result.stdout+result.stderr)
            print('Already applied:',patch.name)
        else:
            active.append(command)
            all_paths.update(targets)
    backup=Path('/var/backups')/('hermes-mobile-core-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    for name in all_paths:
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/name,saved)
    try:
        for command in active:subprocess.run(command+['--forward'],cwd=root,check=True)
        validate_baselines(root,records,patched_only=True)
        if all_paths:subprocess.run([str(root/'venv/bin/python'),'-m','py_compile',*[str(root/name) for name in sorted(all_paths)]],check=True)
    except Exception:
        for name in all_paths:shutil.copy2(backup/name,root/name)
        raise
    print('Compatibility patches installed. Backup:',backup)
    print('Restarting only the lindayi Hermes gateway to load the patches and loopback API.')
    subprocess.run(['/usr/sbin/runuser','-u','lindayi','--','/usr/bin/env',
        'XDG_RUNTIME_DIR=/run/user/1002','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus',
        '/usr/bin/systemctl','--user','restart','hermes-gateway.service'],check=True)
    print('No schedules, job destinations, sessions, or credentials were changed by this installer.')


if __name__=='__main__':main()
