#!/usr/bin/env bash
# Install the FileBrowser Quantum binary (the file manager) to
# /usr/local/bin/filebrowser-quantum. One-time host step, re-run-safe.
#
# The version + sha256 are pinned: the binary is fetched from the project's
# GitHub release and its checksum verified before install, so we never run an
# unverified download (same posture as the project's other pinned third-party
# binaries -- wp-cli.phar, composer). After the binary is present,
# borond's fb.bootstrap op writes the config + systemd unit and starts the
# 127.0.0.1-only service; you do not run the binary directly.
set -euo pipefail

VERSION="v1.4.0-stable"
ARCH="linux-amd64"
URL="https://github.com/gtsteffaniak/filebrowser/releases/download/${VERSION}/${ARCH}-filebrowser"
SHA256="f104afa1398a9daf629113d5250951159e389837624b6924abc6e8ca845c62ac"
DEST="/usr/local/bin/filebrowser-quantum"

if [ -x "$DEST" ] && echo "${SHA256}  ${DEST}" | sha256sum -c - >/dev/null 2>&1; then
  echo "FileBrowser Quantum ${VERSION} already installed at ${DEST} (checksum OK)"
  exit 0
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
echo "Downloading FileBrowser Quantum ${VERSION} (${ARCH}) ..."
curl -sSL -o "$TMP" "$URL"
echo "${SHA256}  ${TMP}" | sha256sum -c -   # aborts on mismatch (set -e)
install -m 0755 -o root -g root "$TMP" "$DEST"
echo "Installed $("$DEST" version 2>/dev/null | grep -i version | head -1 || echo "$DEST")"
echo "Next: borond's fb.bootstrap runs at daemon start (or trigger it) to write config + start the service."
