#!/usr/bin/env bash
# Sync the git checkout (/root/cpanel-clone, source of truth) to the real
# deployment directory (/opt/forgehost) that forgehostd and forgehost-api
# actually run from. Needed because /root is mode 700 -- the unprivileged
# forgehost-api user can never traverse into /root/cpanel-clone, even via a
# symlink (found the hard way while building Phase h: a symlink from
# /opt/forgehost into /root/cpanel-clone, this repo's original layout,
# silently broke every forgehost-api file access with a generic permission
# error).
#
# Also re-installs requirements.txt into /opt/forgehost's own venv every
# run (fast/no-op when nothing changed -- pip just checks versions) --
# added after a real crash loop in Phase 2: a new dependency (croniter) was
# pip-installed into the *dev* venv only, deploy.sh synced the code that
# imports it, and forgehostd crash-looped with ModuleNotFoundError on the
# deployed side until someone noticed and installed it there too. Now that
# step can't be forgotten.
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

if [ -x "$DST/.venv/bin/pip" ]; then
  "$DST/.venv/bin/pip" install -q -r "$DST/requirements.txt"
fi

echo "Deployed $SRC -> $DST"
