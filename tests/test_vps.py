import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import storage
import vps


def disk(name, system=False, **extra):
    value={'name':'/dev/'+name,'type':'disk','size':8_000_000_000_000,'ro':False,'rm':False,
           'fstype':None,'mountpoints':[None],'serial':'serial-'+name}
    if system:
        value['children']=[{'name':'/dev/'+name+'1','type':'part','size':7_990_000_000_000,
                            'fstype':'ext4','mountpoints':['/']}]
    value.update(extra)
    return value


class StoragePlanTests(unittest.TestCase):
    def test_only_three_blank_data_drives_are_selected(self):
        result=storage.plan([disk('sda',True),disk('sdb'),disk('sdc'),disk('sdd')],7_990_000_000_000)
        self.assertEqual(result['system_disks'],['/dev/sda'])
        self.assertEqual([d['device'] for d in result['format']],['/dev/sdb','/dev/sdc','/dev/sdd'])
        self.assertEqual(result['raw_bytes'],32_000_000_000_000)

    def test_unknown_system_layout_never_selects_disks(self):
        with self.assertRaises(ValueError):
            storage.plan([disk('sda'),disk('sdb'),disk('sdc'),disk('sdd')],8_000_000_000_000)

    def test_existing_partitions_filesystems_read_only_or_mounted_drives_are_rejected(self):
        for extra in ({'fstype':'ext4'},{'children':[{'name':'/dev/sdb1'}]},
                      {'mountpoints':['/data']},{'ro':True}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                storage.plan([disk('sda',True),disk('sdb',**extra),disk('sdc'),disk('sdd')],8_000_000_000_000)

    def test_wrong_disk_count_or_unexpanded_os_filesystem_is_rejected(self):
        drives=[disk('sda',True),disk('sdb'),disk('sdc'),disk('sdd')]
        with self.assertRaises(ValueError):storage.plan(drives,120_000_000_000)
        with self.assertRaises(ValueError):storage.plan(drives[:3],8_000_000_000_000)

    def test_cancelled_plan_runs_no_format_or_mount_commands(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'LEDGER',Path(tmp)/'ledger'), \
             patch.object(storage,'POOL',str(Path(tmp)/'pool')), \
             patch.object(storage,'inventory',return_value=[disk('sda',True),disk('sdb'),disk('sdc'),disk('sdd')]), \
             patch('storage.shutil.disk_usage',return_value=Mock(total=8_000_000_000_000)), \
             patch('builtins.input',return_value='no'),patch.object(storage,'call') as call:
            with self.assertRaises(ValueError):storage.configure()
            call.assert_not_called()


class FreshSettingsTests(unittest.TestCase):
    def test_restore_keeps_profiles_indexers_credentials_but_erases_history_and_movie_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp)
            for name in ('radarr','prowlarr'):
                folder=snapshot/name;folder.mkdir()
                (folder/'config.xml').write_text('<Config><ApiKey>test-key</ApiKey><UrlBase>/old</UrlBase></Config>')
                with sqlite3.connect(folder/(name+'.db')) as db:
                    for table in ('VersionInfo','Config','QualityProfiles','CustomFormats','Indexers',
                                  'Movies','MovieMetadata','History','Blocklist','RootFolders','UnknownContent'):
                        db.execute('CREATE TABLE "'+table+'" (value TEXT)')
                        db.execute('INSERT INTO "'+table+'" VALUES (?)',
                                   ('historic-film-sensitive-title' if table in ('Movies','History') else 'setting',))
                (folder/'logs').mkdir();(folder/'logs/old.log').write_text('historic-film-sensitive-title')
                (folder/'old.db-wal').write_text('historic-film-sensitive-title')
            state=snapshot/'bot-state';state.mkdir()
            (state/'state.json').write_text(json.dumps({'offset':123,'jobs':{'99':{}},'watches':{'88':{}},
                                    'deletions':{'1':{}},'session':{'stage':'download'},'notices':{'old':'history'}}))
            (state/'catalog.sqlite3').write_bytes(b'public catalogue')
            (state/'other.json').write_text('old state')
            vps.fresh_snapshot(snapshot)
            for name in ('radarr','prowlarr'):
                folder=snapshot/name
                with sqlite3.connect(folder/(name+'.db')) as db:
                    for table in ('VersionInfo','Config','QualityProfiles','CustomFormats','Indexers'):
                        self.assertEqual(db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0],1)
                    for table in ('Movies','MovieMetadata','History','Blocklist','RootFolders','UnknownContent'):
                        self.assertEqual(db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0],0)
                self.assertNotIn(b'historic-film-sensitive-title',(folder/(name+'.db')).read_bytes())
                self.assertEqual({p.name for p in folder.iterdir()},{name+'.db','config.xml'})
                xml=vps.ET.parse(folder/'config.xml').getroot()
                self.assertEqual(xml.findtext('AuthenticationMethod'),'External')
                self.assertEqual(xml.findtext('ApiKey'),'test-key')
                self.assertEqual(xml.findtext('UrlBase'),'/prowlarr' if name=='prowlarr' else '')
            self.assertEqual(json.loads((state/'state.json').read_text()),{'offset':123,'jobs':{},'session':None})
            self.assertFalse((state/'other.json').exists())
            self.assertTrue((state/'catalog.sqlite3').exists())

    def test_qbittorrent_hash_matches_the_native_pbkdf2_format(self):
        salt=b'0123456789abcdef'
        encoded=vps.qbit_hash('test-pass',salt)
        a,b=encoded.split(':')
        self.assertEqual(base64.b64decode(a),salt)
        self.assertEqual(base64.b64decode(b),hashlib.pbkdf2_hmac('sha512',b'test-pass',salt,100000,64))

    def test_failure_messages_do_not_echo_cli_passwords(self):
        import subprocess
        failure=subprocess.CalledProcessError(1,['tool','private-test-password'])
        with patch('vps.subprocess.run',side_effect=failure), self.assertRaises(RuntimeError) as caught:
            vps.run(['tool','private-test-password'])
        self.assertNotIn('private-test-password',str(caught.exception))


if __name__=='__main__':unittest.main()


class ConnectionTests(unittest.TestCase):
    def test_new_local_clients_and_radarr_application_are_created_without_movie_requests(self):
        import copy
        calls=[]
        roots=[]
        def api(app,endpoint,payload=None,method=None):
            calls.append((app,endpoint,copy.deepcopy(payload),method))
            if endpoint=='rootfolder':
                if method=='POST':roots.append(payload)
                return roots
            if endpoint=='downloadclient':return []
            if endpoint=='downloadclient/schema':
                return [{'implementation':'QBittorrent','fields':[{'name':n} for n in
                    ('host','port','username','password','category','useSsl','urlBase')]}]
            if endpoint=='config/downloadclient':return {'id':1}
            if endpoint=='applications':return []
            if endpoint=='applications/schema':
                return [{'implementation':'Radarr','fields':[{'name':n} for n in ('baseUrl','prowlarrUrl','apiKey')]}]
            return {}
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);folder=home/'.config/media-request-bot';folder.mkdir(parents=True)
            (folder/'radarr.env').write_text('RADARR_API_KEY=test-key\nRADARR_URL=http://old\n')
            tree=vps.ET.ElementTree(vps.ET.fromstring('<Config><UrlBase/></Config>'))
            with patch.object(vps.ET,'parse',return_value=tree),patch.object(vps.manage,'api',side_effect=api),\
                 patch.object(vps.manage,'own'):
                vps.arr_connections({'home':tmp},'/srv/media','dummy-pass')
            self.assertIn('RADARR_URL=http://127.0.0.1:17878',(folder/'radarr.env').read_text())
        client=next(c[2] for c in calls if c[:2]==('radarr','downloadclient') and c[3]=='POST')
        fields={f['name']:f['value'] for f in client['fields']}
        self.assertEqual(fields['port'],18080)
        self.assertEqual(fields['category'],'Movies')
        app=next(c[2] for c in calls if c[:2]==('prowlarr','applications') and c[3]=='POST')
        self.assertEqual(app['syncLevel'],'fullSync')
        self.assertEqual({f['name']:f['value'] for f in app['fields']}['prowlarrUrl'],
                         'http://127.0.0.1:19696/prowlarr')
        self.assertFalse(any(c[1].startswith(('movie','release')) for c in calls))
