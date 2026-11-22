#!/usr/bin/env bash
set -euo pipefail
# Download to a file before running: installer prompts read the real terminal.
if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi
apt-get update -qq
apt-get install -y git ca-certificates curl python3 python3-pil python3-psutil gnupg sqlite3 libsqlite3-0 libicu-dev mediainfo nginx apache2-utils qbittorrent-nox vsftpd openssh-server openssl mergerfs e2fsprogs util-linux fuse3
if [[ ! -d /opt/media-server-vps/.git ]]; then
    if [[ -e /opt/media-server-vps ]]; then
        echo '/opt/media-server-vps already exists and is not a checkout. Nothing overwritten.' >&2
        exit 1
    fi
    git clone https://github.com/waqaarhussain/media-server-vps.git /opt/media-server-vps
else
    if [[ $(git -C /opt/media-server-vps remote get-url origin) != https://github.com/waqaarhussain/media-server-vps.git ]]; then
        echo 'Existing checkout has a different origin. Setup stopped.' >&2
        exit 1
    fi
    if [[ -n $(git -C /opt/media-server-vps status --porcelain --untracked-files=no) ]]; then
        echo 'Existing checkout has local edits. Setup stopped without overwriting them.' >&2
        exit 1
    fi
    git -C /opt/media-server-vps pull --ff-only origin main
fi
exec bash /opt/media-server-vps/install.sh "$@"
