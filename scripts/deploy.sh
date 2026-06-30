#!/usr/bin/env bash
# Sync the git checkout (/root/cpanel-clone, source of truth) to the real
# deployment directory (/opt/forgehost) that forgehostd and forgehost-api
# actually run from. Needed because /root is mode 700 -- the unprivileged
# forgehost-api user can never traverse into /root/cpanel-clone, even via a
# symlink (found the hard way while building Phase h: a symlink from
# /opt/forgehost into /root/cpanel-clone, this repo's original layout,
# silently broke every forgehost-api file access with a generic permission
# error). /opt/forgehost's own venv is NOT touched here -- run
# `.venv/bin/pip install -r requirements.txt` there separately if
# dependencies changed.
set -euo pipefail

SRC=/root/cpanel-clone
DST=/opt/forgehost

rsync -a --delete \
  --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='*.pyc' \
  "$SRC"/ "$DST"/

# Excludes .venv: it's not touched by rsync above either, and a previous
# version of this script swept its whole tree (including .venv/bin's
# executables) back to 644, breaking `systemctl start forgehost-api` with a
# confusing 203/EXEC -- caught immediately by starting the service right
# after running this script.
chown -R root:root $(find "$DST" -maxdepth 1 -mindepth 1 ! -name '.venv')
find "$DST" -path "$DST/.venv" -prune -o -type d -exec chmod 755 {} \;
find "$DST" -path "$DST/.venv" -prune -o -type f -exec chmod 644 {} \;
find "$DST/scripts" -name '*.py' -exec chmod 755 {} \;
find "$DST/scripts" -name '*.sh' -exec chmod 755 {} \;

echo "Deployed $SRC -> $DST"
