#!/usr/bin/env python3
"""Fresh dedicated-server stack, importing settings from an encrypted legacy backup."""
import argparse
import base64
import copy
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import manage
import storage

REPO=Path(__file__).resolve().parents[1]
SETTINGS=Path('/etc/media-server-vps.json')
FILEBROWSER_VERSION='2.63.23'
APP_PORTS={'dashboard':80,'radarr':7878,'prowlarr':9696,'qbittorrent':8080,'files':8082,'ftp':21}
KEEP_COMMON={'VersionInfo','Config','Users','QualityProfiles','QualityDefinitions','CustomFormats','DelayProfiles',
             'Notifications','Metadata','Indexers','DownloadClients','Tags','Restrictions','ImportLists',
             'Applications','AppProfiles','Proxies','NamingConfig','AutoTagging','AutoTaggingRules','CustomFilters',
             'IndexerProxies','DownloadClientConfig','Profiles','Localization','ReleaseProfiles'}


def run(args, **kwargs):
    try:
        return subprocess.run([str(a) for a in args],check=True,**kwargs)
    except subprocess.CalledProcessError:
        # Some native CLIs accept a password argument. Never echo their argv.
        raise RuntimeError('A setup command failed. Review the service status; credentials suppressed.') from None


def save(state):
    manage.atomic(SETTINGS,json.dumps(state,indent=2)+'\n')


def find_backups(root='/'):
    """Read filenames and metadata only. Never decrypt, move or modify candidates."""
    excluded={'/proc','/sys','/dev','/run','/snap'}
    candidates=[];skipped=[]
    for directory,dirs,files in os.walk(root,followlinks=False,onerror=lambda error:skipped.append(error.filename)):
        dirs[:]=[name for name in dirs if str(Path(directory)/name) not in excluded]
        for name in files:
            if not name.lower().endswith(('.tar.gz.gpg','.tgz.gpg','.tar.gpg')):
                continue
            path=Path(directory)/name
            try:
                if path.is_symlink() or not path.is_file():continue
                info=path.stat()
                if info.st_size:
                    candidates.append({'path':str(path),'size':info.st_size,'mtime':info.st_mtime})
            except OSError:
                skipped.append(str(path))
    if skipped:
        print('Some locations could not be read. Upload to /root or /home and rescan if your backup is missing.')
    return sorted(candidates,key=lambda item:(-item['mtime'],item['path']))


def choose_backup():
    while True:
        print('Searching this VPS for encrypted backup files. This may take a moment…',flush=True)
        candidates=find_backups()
        if candidates:
            print('\nFound backups (newest first). These filenames have not yet been decrypted/validated:')
            for number,item in enumerate(candidates,1):
                stamp=time.strftime('%Y-%m-%d %H:%M',time.localtime(item['mtime']))
                print(f"{number}. {item['path']!r}  |  {item['size']/1024**2:.1f} MiB  |  {stamp} server time")
            prompt='Backup number [1], R to rescan, M to enter a path, or Q to cancel: '
        else:
            print('No encrypted backup found. Upload your .tar.gz.gpg file anywhere under /root or /home, then rescan.')
            prompt='R to rescan after uploading, M to enter a path, or Q to cancel: '
        while True:
            choice=input(prompt).strip()
            if not choice and candidates:choice='1'
            if choice.lower()=='q':raise ValueError('Backup selection cancelled. No restore started.')
            if choice.lower()=='r':break
            if choice.lower()=='m':
                path=Path(input('Backup file path: ').strip()).expanduser()
                if path.is_file():return str(path.resolve())
                print('That file was not found. Try again.');continue
            if choice.isdecimal() and 1<=int(choice)<=len(candidates):
                path=Path(candidates[int(choice)-1]['path'])
                if path.is_file():return str(path)
                print('That file is no longer available. Rescan with R.');continue
            print('Choose one of the displayed numbers or R, M, Q.')


def fresh_snapshot(snapshot):
    """Sanitize our decrypted temporary copy, never the original encrypted file."""
    audit={}
    for app in ('radarr','prowlarr'):
        folder=snapshot/app
        dbpath=folder/(app+'.db')
        with sqlite3.connect(dbpath) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise ValueError('Backup database failed validation.')
            db.execute('PRAGMA foreign_keys=OFF')
            tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if not row[0].startswith('sqlite_')]
            cleared=[]
            for table in tables:
                if table in KEEP_COMMON:
                    continue
                # Quote identifiers from sqlite's schema; unknown content tables are also emptied.
                db.execute('DELETE FROM "'+table.replace('"','""')+'"')
                cleared.append(table)
            # Retain list definitions but prevent an automatic library refill on first start.
            if 'ImportLists' in tables:
                columns={r[1] for r in db.execute('PRAGMA table_info(ImportLists)')}
                if 'EnableAutomaticAdd' in columns:
                    db.execute('UPDATE ImportLists SET EnableAutomaticAdd=0')
                if 'Enabled' in columns:
                    db.execute('UPDATE ImportLists SET Enabled=0')
            db.commit()
            db.execute('VACUUM')
            audit[app]={'kept':sorted(set(tables)&KEEP_COMMON),'emptied':sorted(cleared)}
        # Logs, art caches, old backups and history sidecars must not be restored.
        for item in folder.iterdir():
            if item.name in (app+'.db','config.xml'):
                continue
            if item.is_dir(): shutil.rmtree(item)
            else: item.unlink()
        xml=ET.parse(folder/'config.xml');root=xml.getroot()
        for name,value in (('AuthenticationMethod','External'),('AuthenticationRequired','Enabled'),
                           ('EnableSsl','False'),('LaunchBrowser','False'),
                           ('UrlBase','/prowlarr' if app=='prowlarr' else '')):
            node=root.find(name)
            if node is None: node=ET.SubElement(root,name)
            node.text=value
        xml.write(folder/'config.xml',encoding='unicode')
    old=snapshot/'bot-state/state.json'
    state=json.loads(old.read_text()) if old.exists() else {}
    # Carry the update offset to avoid replaying old Telegram commands. No old jobs/watchlists/UI/history.
    manage.atomic(old,json.dumps({'offset':int(state.get('offset',0)),'jobs':{},'session':None})+'\n')
    for item in (snapshot/'bot-state').iterdir():
        if item.name not in ('state.json','catalog.sqlite3'):
            if item.is_dir(): shutil.rmtree(item)
            else: item.unlink()
    manage.atomic(snapshot/'fresh-settings-audit.json',json.dumps(audit,indent=2)+'\n')


def service(name, body):
    manage.atomic('/etc/systemd/system/'+name+'.service',body,0o644)


def folders(pool, cfg):
    for relative in ('Movies','Kids/Movies','TV Shows','Downloads/Incomplete','Downloads/Complete','Torrent Archive'):
        path=Path(pool)/relative;path.mkdir(parents=True,exist_ok=True)
        os.chown(path,cfg['uid'],cfg['gid']);path.chmod(0o2775)
    # Only branch roots are changed. We do not recursively chown an entire media library.
    plan=json.loads(storage.LEDGER.read_text())
    for branch in plan.get('branches',[]):
        os.chown(branch,cfg['uid'],cfg['gid']);Path(branch).chmod(0o2775)


def qbit_hash(password, salt=None):
    salt=salt or os.urandom(16)
    digest=hashlib.pbkdf2_hmac('sha512',password.encode(),salt,100000,64)
    return base64.b64encode(salt).decode()+':'+base64.b64encode(digest).decode()


class Qbit:
    def __init__(self,password):
        import http.cookiejar
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.password=password
    def post(self,path,values):
        req=urllib.request.Request('http://127.0.0.1:18080/api/v2/'+path,
             urllib.parse.urlencode(values).encode(),headers={'Referer':'http://127.0.0.1:18080'})
        try:
            with self.opener.open(req,timeout=15) as response:return response.read()
        except Exception:
            raise RuntimeError('Local qBittorrent API request failed; credentials suppressed.') from None
    def configure(self,pool):
        for _ in range(30):
            try:
                if self.post('auth/login',{'username':'waqaar','password':self.password}).strip()==b'Ok.':break
            except RuntimeError:pass
            time.sleep(1)
        else:raise RuntimeError('qBittorrent login could not be verified.')
        self.post('app/setPreferences',{'json':json.dumps({'save_path':pool+'/Downloads/Complete',
            'temp_path_enabled':True,'temp_path':pool+'/Downloads/Incomplete',
            'export_dir_fin':pool+'/Torrent Archive','export_dir':'','listen_port':6881,'random_port':False})})
        for category in ('Movies','Kids','TV'):
            # A fresh qBittorrent profile has no categories. Reconcile an interrupted install safely.
            try:self.post('torrents/createCategory',{'category':category,'savePath':pool+'/Downloads/Complete'})
            except RuntimeError:self.post('torrents/editCategory',{'category':category,'savePath':pool+'/Downloads/Complete'})


def install_qbit(cfg,pool,host,password):
    home=Path('/var/lib/media-stack/qbittorrent');conf=home/'.config/qBittorrent/qBittorrent.conf'
    if not conf.exists():
        text='''[LegalNotice]
Accepted=true

[Preferences]
WebUI\\Address=127.0.0.1
WebUI\\Port=18080
WebUI\\Username=waqaar
WebUI\\LocalHostAuth=true
WebUI\\AuthSubnetWhitelistEnabled=false
WebUI\\CSRFProtection=true
WebUI\\HostHeaderValidation=true
WebUI\\ServerDomains=127.0.0.1;localhost;HOST
WebUI\\Password_PBKDF2="@ByteArray(HASH)"
'''.replace('HOST',host).replace('HASH',qbit_hash(password))
        manage.atomic(conf,text)
        manage.own(home,cfg)
    service('media-qbittorrent',f'''[Unit]
Description=Media qBittorrent
Wants=network-online.target
After=network-online.target
RequiresMountsFor={pool}
[Service]
User=waqaar
Group={cfg['group']}
Environment=HOME={home}
ExecStart=/usr/bin/qbittorrent-nox --webui-port=18080
Restart=on-failure
UMask=0002
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','media-qbittorrent'])
    Qbit(password).configure(pool)


def install_filebrowser(cfg,pool,password):
    binary=Path('/usr/local/bin/filebrowser')
    if not binary.exists():
        arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
        if not arch:raise ValueError('Unsupported File Browser CPU architecture.')
        asset='linux-'+arch+'-filebrowser.tar.gz'
        base=f'https://github.com/filebrowser/filebrowser/releases/download/v{FILEBROWSER_VERSION}/'
        with urllib.request.urlopen(base+f'filebrowser_{FILEBROWSER_VERSION}_checksums.txt',timeout=60) as r:
            sums=r.read().decode()
        expected=next((line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*')==asset),None)
        if not expected:raise RuntimeError('File Browser release checksum is unavailable.')
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/asset
            with urllib.request.urlopen(base+asset,timeout=120) as r,archive.open('wb') as out:shutil.copyfileobj(r,out)
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
                raise RuntimeError('File Browser checksum mismatch. Nothing installed.')
            with tarfile.open(archive) as tar:
                member=next((m for m in tar.getmembers() if m.name.lstrip('./')=='filebrowser' and m.isfile()),None)
                if member is None:raise RuntimeError('File Browser binary is missing from the official archive.')
                with tar.extractfile(member) as source,binary.open('wb') as out:shutil.copyfileobj(source,out)
            binary.chmod(0o755)
    folder=Path('/var/lib/media-stack/filebrowser');folder.mkdir(parents=True,exist_ok=True)
    database=folder/'filebrowser.db'
    if not database.exists():
        run([binary,'--database',database,'config','init','--address','127.0.0.1','--port','18082','--root',pool,
             '--minimumPasswordLength','8','--branding.name','Media House Files','--branding.theme','dark',
             '--fileMode','0o664','--dirMode','0o775','--disableExec'],capture_output=True)
        run([binary,'--database',database,'users','add','waqaar',password,'--perm.admin'],capture_output=True)
    manage.own(folder,cfg)
    service('media-filebrowser',f'''[Unit]
Description=Media File Browser
After=network-online.target
RequiresMountsFor={pool}
[Service]
User=waqaar
Group={cfg['group']}
ExecStart={binary} --database {database}
Restart=on-failure
UMask=0002
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths={pool} {folder}
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','media-filebrowser'])


def configure_ftp(pool,host,password):
    run(['chpasswd'],input=('waqaar:'+password+'\n').encode())
    # FTP uses a local account; keep that app account out of remote SSH logins.
    manage.atomic('/etc/ssh/sshd_config.d/90-media-service.conf','DenyUsers waqaar\n',0o644)
    run(['sshd','-t']);run(['systemctl','reload','ssh'])
    key=Path('/etc/ssl/private/media-server-vps-ftp.key');cert=Path('/etc/ssl/certs/media-server-vps-ftp.crt')
    if not key.exists():
        run(['openssl','req','-x509','-nodes','-newkey','rsa:2048','-days','825','-subj','/CN='+host,
             '-keyout',key,'-out',cert],capture_output=True)
        key.chmod(0o600)
    manage.atomic('/etc/vsftpd.user_list','waqaar\n',0o600)
    manage.atomic('/etc/vsftpd.conf',f'''listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=002
chroot_local_user=YES
allow_writeable_chroot=YES
local_root={pool}
userlist_enable=YES
userlist_deny=NO
userlist_file=/etc/vsftpd.user_list
pam_service_name=vsftpd
pasv_enable=YES
pasv_min_port=50000
pasv_max_port=50100
pasv_address={host}
pasv_addr_resolve=YES
ssl_enable=YES
force_local_logins_ssl=NO
force_local_data_ssl=NO
rsa_cert_file={cert}
rsa_private_key_file={key}
utf8_filesystem=YES
''',0o600)
    run(['systemctl','enable','--now','vsftpd']);run(['systemctl','restart','vsftpd'])


def arr_connections(cfg,pool,password):
    envfile=Path(cfg['home'])/'.config/media-request-bot/radarr.env'
    values=manage.env_read(envfile)
    xml=ET.parse('/var/lib/radarr/config.xml').getroot()
    values.update(RADARR_URL='http://127.0.0.1:17878'+(xml.findtext('UrlBase') or ''),
                  RADARR_MOVIES_ROOT=pool+'/Movies',RADARR_KIDS_ROOT=pool+'/Kids/Movies')
    manage.env_write(envfile,values,cfg)
    for root in (pool+'/Movies',pool+'/Kids/Movies'):
        if not any(r['path']==root for r in manage.api('radarr','rootfolder')):
            manage.api('radarr','rootfolder',{'path':root},'POST')
    for client in manage.api('radarr','downloadclient'):
        client['enable']=False
        manage.api('radarr','downloadclient/'+str(client['id']),client,'PUT')
    schemas=manage.api('radarr','downloadclient/schema')
    resource=copy.deepcopy(next(r for r in schemas if r['implementation']=='QBittorrent'))
    resource.pop('id',None)
    resource.update(name='Dedicated server qBittorrent',enable=True,removeCompletedDownloads=True)
    fields={f['name']:f for f in resource['fields']}
    for name,value in {'host':'127.0.0.1','port':18080,'useSsl':False,'urlBase':'',
                       'username':'waqaar','password':password,'category':'Movies'}.items():
        if name in fields:fields[name]['value']=value
    current=next((c for c in manage.api('radarr','downloadclient') if c['name']==resource['name']),None)
    if current:
        resource['id']=current['id'];manage.api('radarr','downloadclient/'+str(current['id']),resource,'PUT')
    else:manage.api('radarr','downloadclient',resource,'POST')
    host=manage.api('radarr','config/downloadclient')
    host.update(enableCompletedDownloadHandling=True,removeCompletedDownloads=True)
    manage.api('radarr','config/downloadclient',host,'PUT')
    apps=manage.api('prowlarr','applications')
    if not any(a.get('implementation')=='Radarr' for a in apps):
        app=copy.deepcopy(next(a for a in manage.api('prowlarr','applications/schema') if a['implementation']=='Radarr'))
        app.pop('id',None)
        app.update(name='Dedicated server Radarr',syncLevel='fullSync',tags=[])
        apps.append(app)
    for app in apps:
        if app.get('implementation')!='Radarr':continue
        for field in app.get('fields',[]):
            if field['name']=='baseUrl':field['value']=values['RADARR_URL']
            if field['name']=='prowlarrUrl':field['value']='http://127.0.0.1:19696/prowlarr'
            if field['name']=='apiKey':field['value']=values['RADARR_API_KEY']
        if app.get('id'):
            manage.api('prowlarr','applications/'+str(app['id']),app,'PUT')
        else:
            manage.api('prowlarr','applications',app,'POST')
    manage.api('prowlarr','command',{'name':'ApplicationIndexerSync'},'POST')
    adapter=envfile.parent/'year-adapter.json'
    if adapter.exists():
        data=json.loads(adapter.read_text())
        data.update(radarrUrl=values['RADARR_URL'],prowlarrUrl='http://127.0.0.1:19696/prowlarr')
        manage.atomic(adapter,json.dumps(data)+'\n');manage.own(adapter,cfg)


def configure_web(cfg,pool,password):
    run(['htpasswd','-i','-B','-c','/etc/nginx/media-vps.htpasswd','waqaar'],input=(password+'\n').encode(),capture_output=True)
    os.chown('/etc/nginx/media-vps.htpasswd',0,__import__('grp').getgrnam('www-data').gr_gid)
    Path('/etc/nginx/media-vps.htpasswd').chmod(0o640)
    servers=[]
    for port,backend,auth in ((80,18000,True),(7878,17878,True),(9696,19696,True),(8080,18080,False),(8082,18082,False)):
        extra='auth_basic "Media server"; auth_basic_user_file /etc/nginx/media-vps.htpasswd;' if auth else ''
        servers.append(f'''server {{
 listen {port}; server_name _; {extra}
 client_max_body_size 0;
 location / {{
  proxy_pass http://127.0.0.1:{backend};
  proxy_http_version 1.1;
  proxy_set_header Host $http_host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  proxy_request_buffering off;
  proxy_read_timeout 3600;
 }}
}}''')
    manage.atomic('/etc/nginx/sites-available/media-server-vps','\n'.join(servers)+'\n',0o644)
    default=Path('/etc/nginx/sites-enabled/default')
    if default.is_symlink():default.unlink()
    link=Path('/etc/nginx/sites-enabled/media-server-vps')
    if not link.exists():link.symlink_to('/etc/nginx/sites-available/media-server-vps')
    service('media-dashboard',f'''[Unit]
Description=Media server home dashboard
After=network-online.target
RequiresMountsFor={pool}
[Service]
User=waqaar
Group={cfg['group']}
ExecStart=/usr/bin/python3 {REPO}/dashboard/server.py {pool}
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
''')
    run(['nginx','-t']);run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','media-dashboard','nginx']);run(['systemctl','reload','nginx'])
    ufw=shutil.which('ufw')
    if ufw and 'Status: active' in subprocess.check_output([ufw,'status'],text=True):
        for rule in ('80/tcp','7878/tcp','9696/tcp','8080/tcp','8082/tcp','21/tcp','50000:50100/tcp','6881/tcp','6881/udp'):
            run([ufw,'allow',rule])


def check_ready():
    state=json.loads(SETTINGS.read_text())
    if not state.get('ready'):
        raise ValueError('Installation is incomplete. Finish setup before activation.')
    storage.verify_pool(state['storage'])
    for name in ('radarr','prowlarr','media-qbittorrent','media-filebrowser','media-dashboard','nginx','vsftpd'):
        if not manage.active(name):raise ValueError('Service is not running: '+name)
    if manage.api('radarr','movie'):
        print('Radarr already contains movies added since migration.')
    return state


def install(args):
    if os.geteuid()!=0:raise ValueError('Run the installer as root or through sudo.')
    release=Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release or not any('VERSION_ID="'+v+'"' in release for v in ('24.04','26.04')):
        raise ValueError('Use Ubuntu 24.04 or 26.04 for this installer.')
    cfg=manage.account('waqaar')
    state=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if not state.get('restored') and any(Path('/var/lib/'+app+'/config.xml').exists() for app in ('radarr','prowlarr')):
        raise ValueError('Existing app data found. This installer requires a fresh dedicated server.')
    backup=Path(args.restore).expanduser().resolve()
    if not backup.is_file():raise ValueError('Encrypted backup file not found. Upload it first.')
    if not state.get('storage'):
        if args.storage:
            state['storage']=storage.configure(args.storage)
        else:
            print('1. Detect and combine four 8 TB drives (blank data disks only)')
            print('2. Use an existing mounted media directory')
            choice=input('Storage mode [1]: ').strip() or '1'
            if choice not in ('1','2'):raise ValueError('Choose storage mode 1 or 2.')
            state['storage']=storage.configure(input('Mounted media directory: ').strip() if choice=='2' else None)
        save(state)
    storage.verify_pool(state['storage']);pool=state['storage']['pool']
    host=args.host or state.get('host') or input('Server public IPv4 address or hostname (for FTP): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host):raise ValueError('Invalid host. Enter an IP/hostname without scheme or port.')
    password=getpass.getpass('Password for waqaar on all web apps and FTP: ')
    if len(password)<8 or '\n' in password or ':' in password:raise ValueError('Use at least 8 characters, without colons/newlines.')
    if password!=getpass.getpass('Confirm password: '):raise ValueError('Passwords differ.')
    if not state.get('restored'):
        manage.restore(cfg,backup,prepare=fresh_snapshot)
        state['restored']=True;save(state)
    base=Path('/var/lib/media-stack');base.mkdir(parents=True,exist_ok=True)
    os.chown(base,cfg['uid'],cfg['gid']);base.chmod(0o750)
    folders(pool,cfg)
    print('Installing and verifying local qBittorrent…')
    install_qbit(cfg,pool,host,password)
    print('Installing File Browser…')
    install_filebrowser(cfg,pool,password)
    print('Configuring FTP and optional FTP TLS…')
    configure_ftp(pool,host,password)
    print('Reconnecting restored settings to the new local apps…')
    arr_connections(cfg,pool,password)
    manage.deploy_bot(cfg,start=False)
    run(['systemctl','stop','media-request-bot.service'])
    # Every storage-writing service depends on the media mount.
    for name in ('radarr','prowlarr','media-request-bot'):
        manage.atomic('/etc/systemd/system/'+name+'.service.d/storage.conf',
                      '[Unit]\nRequiresMountsFor='+pool+'\n',0o644)
    configure_web(cfg,pool,password)
    state.update(host=host,pool=pool,ready=True,mode='fresh-library-settings-only');save(state)
    manage.install_helpers()
    check_ready()
    print('\nInstalled. Open http://'+host+'/ for the dashboard.')
    print('App ports: Radarr 7878, Prowlarr 9696, qBittorrent 8080, Files 8082. FTP 21.')
    print('Fresh library: no media files, movie records or history imported.')
    print('On the OLD server run: media-server stop')
    print('Then on THIS server run: media-vps activate')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('install','status','activate'),nargs='?',default='install')
    parser.add_argument('--restore');parser.add_argument('--storage');parser.add_argument('--host')
    args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('Use sudo media-vps.')
    if args.command=='install':
        if not args.restore:args.restore=choose_backup()
        install(args)
    elif args.command=='status':
        state=check_ready();print('Services ready. Media directory: '+state['pool'])
    else:
        check_ready()
        if input('After stopping the old bot, type OLD BOT STOPPED: ').strip()!='OLD BOT STOPPED':
            raise ValueError('Activation cancelled. Only one server may poll this Telegram token.')
        run(['systemctl','enable','--now','media-request-bot.service'])
        if not manage.active('media-request-bot'):raise RuntimeError('Bot did not start. Check its service log.')
        state=json.loads(SETTINGS.read_text());state['bot_activated']=True;save(state)
        print('New bot started. Send /start in Telegram.')


if __name__=='__main__':
    try:
        with open('/run/lock/media-server-vps.lock','w') as lock:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            main()
    except (ValueError,RuntimeError) as error:
        print('Setup stopped: '+str(error),file=sys.stderr)
        raise SystemExit(1)
    except (OSError,sqlite3.Error,StopIteration):
        print('Setup stopped. Check disk mounts, app API readiness and the setup step above. '
              'Credentials suppressed.',file=sys.stderr)
        raise SystemExit(1)
