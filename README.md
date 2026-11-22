# Media Server VPS

A native Ubuntu media stack with a dashboard, Telegram movie bot, Radarr, Prowlarr, qBittorrent, FTP and File Browser. Each web app has its own port. This repository is separate from the original AppBox project.

## Fresh library migration

This installer imports **settings only** from an encrypted `media-server backup` archive. It restores app binaries, indexers, quality profiles, API credentials and Telegram configuration. It empties movie records, movie files, download/import history, blocklists, queued commands and old bot requests/watchlists in a temporary decrypted copy **before Radarr starts**. Old logs, artwork and backup caches are discarded. No media payload or old torrent session is imported. The original encrypted archive is never modified.

The shared public IMDb catalogue can be retained. This contains public movie metadata, not your download history. The Telegram update offset is retained to avoid replaying old commands. Finish any active downloads on the old server first; they will not resume here.

## One combined backup on AppBox and the dedicated server

On the **current AppBox Ubuntu VM** where the bot and Radarr run:

```bash
update-live && media-server backup
```

`update-live` uses the original `media-server` repository on AppBox. The updated backup authenticates to the qBittorrent app slot using Radarr's saved connection, reads its preferences and category definitions, and puts `snapshot/qbit-settings.json` inside the **same encrypted `.tar.gz.gpg` archive** as the usual Radarr/Prowlarr/bot snapshot. One archive and one backup encryption passphrase are sufficient. No separate qBittorrent export is needed.

If several Radarr categories use the same qBittorrent connection, it is read once with no extra connection prompt. Distinct qBittorrent instances prompt you to choose one. If no connection can be discovered, the helper asks for the current WebUI URL and hidden login. If authentication or capture fails, the backup stops before stopping services and does not report success. Capture does not change the old client's settings or downloads.

Copy the single completed archive to the new server. The installer finds it by number and uses the embedded qBittorrent settings automatically, with no second settings file or second encryption passphrase. It still clears movie/library/download history during settings-only migration. Old source databases in the encrypted backup remain full snapshots for recovery; the original archive is not modified.

On the dedicated server, future backups use the same command:

```bash
sudo media-server backup
```

Portable preferences supported by the new qBittorrent version are restored, including speed limits, queue limits, connection limits, disk-cache preferences where available, and seeding limits. Category names are retained, including **Movies, Kids Movies, TV Series, Kids TV Series and radarr**. Their old save paths are replaced with new download staging paths. Radarr imports completed movies to `/srv/media/Movies` or `/srv/media/Kids/Movies`, so the bot's **Save to Kids** option still controls the final library destination.

Custom completion scripts, watched folders, proxy/interface bindings, old WebUI credentials and unsupported preferences are captured for review, but are not automatically activated. RSS feeds/rules, search plugins, cookies, torrent entries, resume data, per-torrent settings, payloads and download history are not exported from qBittorrent. Delete-files seeding actions are changed to remove the torrent entry while retaining its files. The new login and ports take precedence. A root-only `/etc/media-qbit-migration.json` report lists applied preferences, review items and new category paths.

### Older backups

Archives made before this update remain supported. If they lack embedded qBittorrent settings, the installer offers the earlier separate `qbit-settings-....json.gpg` export, or **S** for fresh defaults. The standalone export helper remains available for this compatibility case. `--qbit-settings /path/qbit-settings-....json.gpg` explicitly overrides the embedded settings; `--fresh-qbit` deliberately skips them.

## Before running

* Use a fresh Ubuntu 24.04 or 26.04 server with root/sudo access and the same CPU architecture as the backup. Restored native binaries must be compatible with the target libraries; the installer rejects an older libc.
* Upload the encrypted `.tar.gz.gpg` backup to the new server and keep its encryption passphrase ready.
* Automatic pooling expects exactly **4 × 8 TB physical disks**. Existing RAID, partitions on data disks, or a small Ubuntu root filesystem require a reviewed storage layout first. Do not assume provider-installed RAID can be erased safely.
* Keep the old Telegram bot running until the new stack is ready. The new bot remains stopped until you explicitly activate it.

## Install on the NEW server

Download to a file, then run it so interactive prompts work:

```bash
curl -fsSL https://raw.githubusercontent.com/waqaarhussain/media-server-vps/main/bootstrap.sh -o /tmp/media-vps-bootstrap.sh
sudo bash /tmp/media-vps-bootstrap.sh
```

After cloning or safely updating this repository, the installer searches the VPS for encrypted `.tar.gz.gpg`, `.tgz.gpg` and `.tar.gpg` files and shows a numbered list, newest first, with sizes and modification times. Choose the backup by number, or press Enter for number 1. It only reads filenames and metadata during discovery; your backup is still validated when decrypted. You can rescan after uploading, enter a path manually, or cancel. Virtual system directories (`/proc`, `/sys`, `/dev`, `/run`, `/snap`) and symbolic links are skipped.

You will then be asked for storage mode, public server IP/hostname, shared app password and backup encryption passphrase. The login name is **waqaar**. Password entry is hidden and the password is not committed to this public repository. Use the same password when retrying a partially completed installation.

Optional explicit paths:

```bash
sudo bash /tmp/media-vps-bootstrap.sh --restore /root/media-backup.tar.gz.gpg --host YOUR_SERVER_IP
```

For an already mounted storage directory, add `--storage /your/media/path`. This mode does not format disks. It assumes you have configured persistent mounts correctly.

### Four-drive pooling

Automatic mode uses **mergerfs** to present the drives at `/srv/media`. It keeps the Ubuntu system disk intact and uses a directory on its existing filesystem as one branch. Only the remaining completely blank, unmounted disks are candidates for ext4 formatting. It prints their exact device names, serials and sizes, then requires you to type the displayed `ERASE ...` phrase. It rechecks the layout before formatting.

It never repartitions/resizes Ubuntu, reshapes RAID or wipes a disk containing a recognised filesystem/partition. If Ubuntu uses only a small partition, or the provider installed RAID, setup stops rather than claiming all capacity is available. Have that layout reviewed before proceeding. Incomplete storage operations leave a root-only journal for manual review; they are not blindly repeated.

Raw capacity is **4 × 8 TB = 32 TB**, before Ubuntu, filesystem overhead and free-space headroom. The pool reserves 50 GB headroom per branch for new files. **There is no redundancy.** A failed drive loses the files stored on that drive. A pooled directory is not a backup.

### Clean folders

```text
/srv/media/Movies
/srv/media/Kids/Movies
/srv/media/Kids/TV Shows
/srv/media/TV Shows
/srv/media/Downloads/Incomplete
/srv/media/Downloads/Complete
/srv/media/Torrent Archive
```

File Browser can manage these folders. Radarr and qBittorrent are reconnected to these local paths. Restored AppBox download clients are disabled. Existing indexer and quality settings are preserved. TV folders are available for future use; this does not install Sonarr or implement TV downloads in the bot.

## Access

Open `http://YOUR_SERVER_IP/` for the Media House dashboard. Use **waqaar** and the password entered during setup.

| Service | Address / port |
| --- | --- |
| Dashboard | `http://YOUR_SERVER_IP/` |
| Radarr | `http://YOUR_SERVER_IP:7878/` |
| Prowlarr | `http://YOUR_SERVER_IP:9696/prowlarr/` |
| qBittorrent | `http://YOUR_SERVER_IP:8080/` |
| File Browser | `http://YOUR_SERVER_IP:8082/` |
| FTP, including Infuse | server IP, port `21` |

The dashboard, Radarr and Prowlarr use an authenticated Nginx front end. Their backends listen on loopback. qBittorrent and File Browser use their own authentication through Nginx. FTP is a file-transfer endpoint, not a browser page. FTP also supports optional explicit TLS with a self-signed certificate. Plain FTP and plain HTTP do not encrypt credentials or traffic; configure trusted TLS before using them over an untrusted network.

If the provider has a firewall, allow the ports above, TCP `50000–50100` for passive FTP, and TCP/UDP `6881` for torrents. Preserve your SSH/admin access. The installer adds these rules to an already active UFW firewall; it does not enable/reset a firewall or alter the provider's firewall. It denies SSH login for the app service account only; keep using your existing server admin account.

## Telegram cutover

After installation succeeds, on the **old** server:

```bash
media-server stop
```

Then on the **new** server:

```bash
sudo media-vps activate
```

Type `OLD BOT STOPPED` when prompted. Two servers must not poll the same Telegram token simultaneously. Send `/start` to the existing bot. Its library starts empty.

## Maintenance

```bash
sudo media-vps status
sudo update-live
sudo media-server backup
sudo media-server recommendations
```

`update-live` updates this repository and deploys bot code. It keeps the bot stopped until the initial activation has happened. It does not rerun disk formatting. Backups include app/bot settings and VPS configuration references but exclude media payloads. A migration restore deliberately does not replay old torrent sessions, disk UUIDs, queues or histories.

The legacy `media-server restore` command restores a complete legacy application snapshot. **Use the bootstrap installer above for settings-only migration**, not that standalone restore command. Advanced legacy setup commands can start the bot; leave them alone until cutover.

## Validation and limits

Run tests with:

```bash
PYTHONPATH=scripts:bot python3 -m unittest discover -s tests
```

Tests cover bot behaviour, conservative disk selection, cancelling before formatting, settings-only database sanitisation, credential-safe errors, combined encrypted backup structure, embedded qBittorrent settings import, category path mapping and password hashing. The native GPG roundtrip test runs where Unix sockets for gpg-agent are available; it is skipped in restricted environments. File Browser is pinned and its downloaded release checksum is verified. Its actual binary was smoke-tested for initialisation and login. The dashboard HTTP endpoints were checked locally.

The complete installer has **not** been run against a disposable four-disk Ubuntu machine. Native service installation, provider networking and the actual disk layout must still be validated on the new server. Do not run this installer on the old AppBox host.

### qBittorrent backup connection troubleshooting

If Radarr's saved connection cannot be used for the backup, the helper reports the failing API endpoint and a safe HTTP/network error, then offers a manual WebUI URL/login retry. It never prints response bodies, passwords or session cookies. A normal client User-Agent is supplied, and API authentication uses matching Origin/Referer headers with an in-memory cookie jar. A redirect to another origin is refused instead of forwarding login data there.

You can bypass the saved connection explicitly and still create one combined backup:

```bash
media-server backup --qbit-url https://YOUR_QBIT_WEBUI_HOST
```

Enter the existing qBittorrent WebUI username/password at the prompts, not in the command. This does not change Radarr's saved connection or qBittorrent's settings. Authentication and settings reads must succeed before the backup begins. HTTP 403 alone does not distinguish a temporary login/IP ban from a Host/Origin or provider proxy restriction. Avoid repeated password guesses; use the credentials shown by your provider.

The login client supports both the legacy HTTP 200 / `Ok.` response and qBittorrent 5.2+ HTTP 204 with an empty body. It retains whichever session cookie the server issues, including `QBT_SID_<port>`, instead of assuming the cookie is named `SID`. Protected settings reads must still succeed before the backup proceeds. An unexpected empty HTTP 200 is not accepted as authenticated.
