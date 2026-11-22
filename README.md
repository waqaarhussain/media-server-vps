# Media Server VPS

A native Ubuntu media stack with a dashboard, Telegram movie bot, Radarr, Prowlarr, qBittorrent, FTP and File Browser. Each web app has its own port. This repository is separate from the original AppBox project.

## Fresh library migration

This installer imports **settings only** from an encrypted `media-server backup` archive. It restores app binaries, indexers, quality profiles, API credentials and Telegram configuration. It empties movie records, movie files, download/import history, blocklists, queued commands and old bot requests/watchlists in a temporary decrypted copy **before Radarr starts**. Old logs, artwork and backup caches are discarded. No media payload or old torrent session is imported. The original encrypted archive is never modified.

The shared public IMDb catalogue can be retained. This contains public movie metadata, not your download history. The Telegram update offset is retained to avoid replaying old commands. Finish any active downloads on the old server first; they will not resume here.

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

The installer searches the VPS for encrypted `.tar.gz.gpg`, `.tgz.gpg` and `.tar.gpg` files and shows a numbered list, newest first, with sizes and modification times. Choose the backup by number, or press Enter for number 1. It only reads filenames and metadata during discovery; your backup is still validated when decrypted. You can rescan after uploading, enter a path manually, or cancel. Virtual system directories (`/proc`, `/sys`, `/dev`, `/run`, `/snap`) and symbolic links are skipped.

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

Tests cover bot behaviour, conservative disk selection, cancelling before formatting, settings-only database sanitisation, credential-safe errors and qBittorrent password hashing. File Browser is pinned and its downloaded release checksum is verified. Its actual binary was smoke-tested for initialisation and login. The dashboard HTTP endpoints were checked locally.

The complete installer has **not** been run against a disposable four-disk Ubuntu machine. Native service installation, provider networking and the actual disk layout must still be validated on the new server. Do not run this installer on the old AppBox host.
