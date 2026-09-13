#!/usr/bin/env bash
# Sync this git checkout (source of truth) to the real
# deployment directory (/opt/boron) that borond and boron-api
# actually run from. Needed because /root is mode 700 -- the unprivileged
# boron-api user can never traverse into /root/cpanel-clone, even via a
# symlink (found the hard way while building Phase h: a symlink from
# /opt/boron into /root/cpanel-clone, this repo's original layout,
# silently broke every boron-api file access with a generic permission
# error).
#
# Also re-installs requirements.txt into /opt/boron's own venv every
# run (fast/no-op when nothing changed -- pip just checks versions) --
# added after a real crash loop in Phase 2: a new dependency (croniter) was
# pip-installed into the *dev* venv only, deploy.sh synced the code that
# imports it, and borond crash-looped with ModuleNotFoundError on the
# deployed side until someone noticed and installed it there too. Now that
# step can't be forgotten.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST=/opt/boron

rsync -a --delete \
  --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='*.pyc' \
  --exclude='frontend/node_modules' --exclude='frontend/.vite' \
  --exclude='frontend/test-results' --exclude='frontend/playwright-report' \
  "$SRC"/ "$DST"/

# Excludes .venv: it's not touched by rsync above either, and a previous
# version of this script swept its whole tree (including .venv/bin's
# executables) back to 644, breaking `systemctl start boron-api` with a
# confusing 203/EXEC -- caught immediately by starting the service right
# after running this script.
mapfile -d '' DEPLOY_ENTRIES < <(find "$DST" -maxdepth 1 -mindepth 1 ! -name '.venv' -print0)
if ((${#DEPLOY_ENTRIES[@]})); then
  chown -R root:root "${DEPLOY_ENTRIES[@]}"
fi
find "$DST" -path "$DST/.venv" -prune -o -type d -exec chmod 755 {} \;
find "$DST" -path "$DST/.venv" -prune -o -type f -exec chmod 644 {} \;
find "$DST/scripts" -name '*.py' -exec chmod 755 {} \;
find "$DST/scripts" -name '*.sh' -exec chmod 755 {} \;

if [ -x "$DST/.venv/bin/python" ]; then
  # Invoke pip as a module so a venv moved during the one-time
  # Boron deployment does not depend on pip's old absolute
  # shebang path.
  "$DST/.venv/bin/python" -m pip install -q -r "$DST/requirements.txt"
fi

echo "Deployed $SRC -> $DST"
