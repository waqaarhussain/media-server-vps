"""Reviewable, opt-in storage pooling. Never repartition the Ubuntu disk."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

POOL = '/srv/media'
BASE = '/srv/media-disks'
LEDGER = Path('/etc/media-server-storage.json')


def call(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inventory():
    return json.loads(subprocess.check_output(['lsblk','--bytes','--json','--paths','--output',
                     'NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL,RO,RM,ROTA'], text=True))['blockdevices']


def mounted(node):
    return [p for p in (node.get('mountpoints') or []) if p]


def walk(node):
    yield node
    for child in node.get('children') or []:
        yield from walk(child)


def plan(disks, root_bytes):
    disks = [d for d in disks if d.get('type')=='disk' and not d.get('rm')]
    roots = [d for d in disks if any('/' in mounted(n) for n in walk(d))]
    if not roots:
        raise ValueError('Cannot identify every Ubuntu system disk. Use an already mounted pool instead.')
    candidates = []
    for disk in disks:
        if disk in roots:
            continue
        if disk.get('ro') or disk.get('fstype') or disk.get('children') or mounted(disk):
            raise ValueError('A non-system disk already has partitions, a filesystem or a mount: '+disk['name']+
                             '. It will not be erased. Mount existing storage manually and choose existing mode.')
        if not re.fullmatch(r'/dev/(?:sd[a-z]+|vd[a-z]+|nvme\d+n\d+)',disk['name']):
            raise ValueError('Unsupported disk device name.')
        candidates.append(disk)
    # This auto-layout is deliberately limited to the requested four 8 TB drives.
    if len(disks)!=4 or any(not 7_000_000_000_000<=int(d['size'])<=9_000_000_000_000 for d in disks):
        raise ValueError('Auto-pool expects exactly four 8 TB disks. Use an existing mount for another layout.')
    if root_bytes < sum(int(d['size']) for d in roots)*0.80:
        raise ValueError('Ubuntu is using only a small part of its system disk(s). Expand its filesystem first, '
                         'or mount the unused capacity separately; this installer never resizes system partitions.')
    return {'format':[{'device':d['name'],'serial':d.get('serial') or '', 'size':int(d['size'])} for d in candidates],
            'system_disks':[d['name'] for d in roots], 'raw_bytes':sum(int(d['size']) for d in disks)}


def append_fstab(line):
    path=Path('/etc/fstab')
    text=path.read_text()
    if line not in text.splitlines():
        if not path.with_suffix('.media-server-vps.before').exists():
            shutil.copy2(path,path.with_suffix('.media-server-vps.before'))
        with path.open('a') as out:
            out.write('\n'+line+'\n')


def verify_pool(settings):
    pool=Path(settings['pool'])
    if settings.get('mode')=='mergerfs':
        kind=subprocess.check_output(['findmnt','-n','-o','FSTYPE','--target',pool],text=True).strip()
        if 'mergerfs' not in kind:
            raise ValueError('The combined media filesystem is not mounted. Apps must remain stopped.')
        for disk in settings.get('disks',[]):
            source=subprocess.check_output(['findmnt','-n','-o','UUID','--mountpoint',disk['mount']],text=True).strip()
            if source!=disk['uuid']:
                raise ValueError('A media disk is missing or mounted incorrectly: '+disk['mount'])
    if not pool.is_dir():
        raise ValueError('Media directory is unavailable.')


def configure(existing=None):
    if LEDGER.exists():
        settings=json.loads(LEDGER.read_text())
        verify_pool(settings)
        return settings
    if existing:
        path=Path(existing).resolve()
        if path==Path('/') or not path.is_dir() or any(c.isspace() for c in str(path)):
            raise ValueError('Choose an existing media directory with a simple absolute path, not /.')
        settings={'mode':'existing','pool':str(path),'disks':[]}
        LEDGER.write_text(json.dumps(settings,indent=2)+'\n');LEDGER.chmod(0o600)
        return settings
    disks=inventory()
    proposal=plan(disks,shutil.disk_usage('/').total)
    if Path(POOL).is_symlink():
        raise ValueError('/srv/media cannot be a symbolic link in automatic mode.')
    if Path(POOL).exists() and (os.path.ismount(POOL) or any(Path(POOL).iterdir())):
        raise ValueError('/srv/media must be empty and unmounted before creating a pool.')
    print('\nSTORAGE PLAN: four-drive media pool, no redundancy.')
    print('Ubuntu disk(s), left intact: '+', '.join(proposal['system_disks']))
    for d in proposal['format']:
        print('FORMAT ext4:',d['device'],'serial:',d['serial'] or '(not supplied)', 'bytes:',d['size'])
    print('Only the devices listed as FORMAT will be erased. Existing filesystems are never selected.')
    phrase='ERASE '+ ' '.join(d['device'] for d in proposal['format'])
    if proposal['format'] and input('Type exactly "'+phrase+'" to apply this plan: ').strip()!=phrase:
        raise ValueError('Storage plan cancelled. No disks formatted.')
    # Re-read immediately before any mkfs. Refuse a changed inventory or system-disk set.
    if plan(inventory(),shutil.disk_usage('/').total)!=proposal:
        raise ValueError('Disk inventory changed. No disks formatted.')
    journal=Path('/etc/media-server-storage.pending.json')
    if journal.exists():
        raise ValueError('A prior storage operation is incomplete. Review the pending journal before continuing.')
    pending={'proposal':proposal,'completed':[]}
    journal.write_text(json.dumps(pending,indent=2));journal.chmod(0o600)
    Path(BASE).mkdir(parents=True,exist_ok=True)
    Path(BASE).chmod(0o755)
    os_branch=Path(BASE)/'ubuntu-media';os_branch.mkdir(exist_ok=True)
    branches=[str(os_branch)];devices=[]
    for d in proposal['format']:
        device=d['device']
        # No force flag, no wipefs, no partition-table changes. mkfs independently refuses recognised data.
        call(['mkfs.ext4','-m','0','-L','media-pool',device], input=b'y\n')
        uuid=subprocess.check_output(['blkid','-s','UUID','-o','value',device],text=True).strip()
        mount=Path(BASE)/('disk-'+uuid);mount.mkdir(exist_ok=True)
        append_fstab('UUID='+uuid+' '+str(mount)+' ext4 defaults,noatime 0 2')
        call(['mount',str(mount)])
        branch=mount/'media';branch.mkdir(exist_ok=True)
        branches.append(str(branch));devices.append({'uuid':uuid,'mount':str(mount),'device':device,'branch':str(branch)})
        pending['completed']=devices;journal.write_text(json.dumps(pending,indent=2))
    Path(POOL).mkdir(parents=True,exist_ok=True)
    if any(Path(POOL).iterdir()) or os.path.ismount(POOL):
        raise ValueError('/srv/media must be an empty, unmounted directory before creating the pool.')
    options='defaults,allow_other,use_ino,category.create=mfs,minfreespace=50G,fsname=media-pool'
    for disk in devices:
        options+=',x-systemd.requires-mounts-for='+disk['mount']
    append_fstab(':'.join(branches)+' '+POOL+' fuse.mergerfs '+options+' 0 0')
    call(['systemctl','daemon-reload']);call(['mount',POOL])
    settings={'mode':'mergerfs','pool':POOL,'branches':branches,'disks':devices,'raw_bytes':proposal['raw_bytes']}
    verify_pool(settings)
    LEDGER.write_text(json.dumps(settings,indent=2)+'\n');LEDGER.chmod(0o600)
    journal.unlink()
    return settings
