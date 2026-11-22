import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

import qbit_migrate as q
import vps


def document(**prefs):
    return {'schema':1,'kind':'qbit-settings-only','version':'v4.6.3','preferences':prefs,
            'categories':{'Movies':{'savePath':'/APPBOX/Movies'},'Kids Movies':{'savePath':'/APPBOX/Kids'},
                          'Kids TV Series':{},'TV Series':{},'radarr':{},'../Unknown':{}}}


class PortableSettingsTests(unittest.TestCase):
    def test_exports_preferences_and_categories_without_requesting_history_or_torrents(self):
        client=Mock()
        client.call.side_effect=['v4.6.3',{'up_limit':123,'web_ui_password':'private-value'},document()['categories']]
        result=q.capture(client)
        self.assertEqual(result['preferences']['up_limit'],123)
        self.assertEqual([call.args[0] for call in client.call.call_args_list],
                         ['app/version','app/preferences','torrents/categories'])
        self.assertNotIn('torrents',result)

    def test_portable_settings_keep_limits_queueing_and_avoid_old_commands_credentials_and_paths(self):
        source=document(up_limit=123,max_active_downloads=4,max_ratio=1.0,max_ratio_act=1,
                        autorun_enabled=True,autorun_program='/APPBOX/delete.sh',save_path='/APPBOX',
                        web_ui_password='private',proxy_ip='old-proxy',scan_dirs={'/old':0},
                        listen_port=1234,dl_limit='bad-type',disk_cache=100)
        target={key:value for key,value in source['preferences'].items()}
        target['dl_limit']=0;target.pop('disk_cache')
        values,review=q.portable_preferences(source,target)
        self.assertEqual(values,{'up_limit':123,'max_active_downloads':4,'max_ratio':1.0,'max_ratio_act':1})
        self.assertIn('autorun_program',review)
        self.assertIn('disk_cache',review)
        self.assertIn('dl_limit',review)
        self.assertNotIn('private',json.dumps(values))

    def test_payload_deleting_ratio_actions_are_not_replayed(self):
        values,review=q.portable_preferences(document(max_ratio_act=3),{'max_ratio_act':0})
        self.assertEqual(values['max_ratio_act'],1)
        self.assertTrue(review)

    def test_category_names_are_kept_but_every_path_is_new_and_outside_library_roots(self):
        source=document();before=copy.deepcopy(source)
        paths=q.category_paths(source,'/srv/media')
        self.assertEqual(set(paths),set(source['categories']))
        self.assertEqual(paths['Kids Movies'],'/srv/media/Downloads/Complete/Kids Movies')
        self.assertEqual(paths['radarr'],'/srv/media/Downloads/Complete/Movies')
        self.assertEqual(paths['../Unknown'],'/srv/media/Downloads/Complete/Other')
        self.assertTrue(all(p.startswith('/srv/media/Downloads/Complete/') for p in paths.values()))
        self.assertEqual(source,before)
        self.assertNotIn('APPBOX',json.dumps(paths))

    def test_invalid_export_is_rejected(self):
        for value in ({},[],{'kind':'other','schema':1},dict(document(),categories={'bad\nname':{}})):
            with self.subTest(value=value),self.assertRaises(ValueError):q.validate(value)

    def test_optional_settings_picker_only_selects_qbit_exports(self):
        found=[{'path':'/root/other.json.gpg'}, {'path':'/root/qbit-settings-one.json.gpg'}]
        with patch.object(vps,'find_backups',return_value=found),patch('builtins.input',return_value='1'):
            self.assertEqual(vps.choose_qbit_backup(),'/root/qbit-settings-one.json.gpg')
        with patch.object(vps,'find_backups',return_value=[]),patch('builtins.input',return_value='s'):
            self.assertIsNone(vps.choose_qbit_backup())

    def test_import_reconnects_categories_and_verifies_portable_settings_with_no_torrent_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool=Path(tmp)/'media';(pool/'Downloads/Complete').mkdir(parents=True)
            client=vps.Qbit('not-a-real-password')
            preferences={'up_limit':0};categories={};calls=[]
            def get(path):return copy.deepcopy(preferences if path=='app/preferences' else categories)
            def post(path,values):
                calls.append(path)
                if path=='auth/login':return b'Ok.'
                if path=='app/setPreferences':preferences.update(json.loads(values['json']))
                if path.startswith('torrents/'):
                    categories[values['category']]={'savePath':values['savePath']}
                return b''
            with patch.object(client,'get',side_effect=get),patch.object(client,'post',side_effect=post),\
                 patch.object(vps.os,'chown'),patch.object(vps.manage,'atomic') as audit:
                client.configure(str(pool),{'uid':1,'gid':1},document(up_limit=123))
            self.assertEqual(preferences['up_limit'],123)
            self.assertEqual(preferences['save_path'],str(pool)+'/Downloads/Complete')
            self.assertEqual(categories['Kids Movies']['savePath'],str(pool)+'/Downloads/Complete/Kids Movies')
            self.assertEqual(json.loads(audit.call_args.args[1])['preferences_not_verified'],[])
            self.assertFalse(any(p in ('torrents/info','torrents/add','torrents/delete') for p in calls))

    def test_backup_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'keep.gpg';path.write_bytes(b'original')
            with self.assertRaises(FileExistsError):q.encrypt(document(),path,b'test\n')
            self.assertEqual(path.read_bytes(),b'original')

    def test_real_encrypted_roundtrip_contains_settings_only_and_wrong_password_is_rejected(self):
        if not shutil.which('gpg'):self.skipTest('GnuPG unavailable')
        import socket
        try:
            probe=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);probe.close()
        except PermissionError:
            self.skipTest('Runtime blocks Unix sockets needed by gpg-agent')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'gnupg';home.mkdir(mode=0o700)
            with patch.dict('os.environ',{'GNUPGHOME':str(home)}):
                path=Path(tmp)/'qbit-settings.json.gpg';source=document(up_limit=54321,proxy_password='sensitive-test-value')
                q.encrypt(source,path,b'test-encryption-secret\n')
                self.assertNotIn(b'sensitive-test-value',path.read_bytes())
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                with patch.object(q,'secret',return_value=b'test-encryption-secret\n'):
                    self.assertEqual(q.load_backup(path),source)
                with patch.object(q,'secret',return_value=b'wrong\n'),self.assertRaises(ValueError):q.load_backup(path)
            subprocess.run(['gpgconf','--homedir',str(home),'--kill','gpg-agent'],capture_output=True)


class EmbeddedRestoreTests(unittest.TestCase):
    def test_combined_archive_settings_are_selected_before_database_sanitisation(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp);source=document(up_limit=42)
            (snapshot/'qbit-settings.json').write_text(json.dumps(source))
            with patch.object(vps,'fresh_snapshot') as fresh,patch.object(vps,'choose_qbit_backup') as chooser:
                self.assertEqual(vps.prepare_restore(snapshot),source)
                fresh.assert_called_once_with(snapshot);chooser.assert_not_called()

    def test_explicit_override_or_fresh_defaults_do_not_parse_old_embedded_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp);(snapshot/'qbit-settings.json').write_text('bad old export')
            override=document(up_limit=99)
            with patch.object(vps,'fresh_snapshot'):
                self.assertEqual(vps.prepare_restore(snapshot,override,use_embedded=False),override)
                self.assertIsNone(vps.prepare_restore(snapshot,use_embedded=False))

    def test_invalid_embedded_settings_stop_before_sanitisation(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp);(snapshot/'qbit-settings.json').write_text('{}')
            with patch.object(vps,'fresh_snapshot') as fresh,self.assertRaises(ValueError):
                vps.prepare_restore(snapshot)
            fresh.assert_not_called()


if __name__=='__main__':unittest.main()
