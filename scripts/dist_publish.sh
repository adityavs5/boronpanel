#!/usr/bin/env bash
# Build, sign and upload the public Boron source archive and the install.sh
# launcher to the distribution server (fresh installs).
#
# Signing happens locally: the Ed25519 private key never leaves this machine.
# The server only receives the archive, its manifest and the manifest signature.
# A published version is immutable; re-publishing it is refused unless the
# bytes are identical. Channel pointers (channels/<name>.manifest) are copies
# of a release manifest and its signature. scripts/dist_release.sh runs the
# same steps together with the license-gated panel update release.
#
# Usage: scripts/dist_publish.sh [--channel stable|beta] [--no-channel] [--allow-downgrade]
#   --allow-downgrade  let the channel pointer move to an older version
# Environment:
#   BORON_SIGNING_KEY  Ed25519 private key PEM (default: $BORON_RELEASE_SIGNING_KEY_FILE,
#                      then secrets/release-signing.key)
#   BORON_DIST_HOST    SSH destination (default: root@104.234.179.66)
#   BORON_DIST_SSH_KEY SSH identity file (default: ./master)
#   BORON_DIST_ROOT    Web root on the server (default: /srv/download)
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
# shellcheck source=scripts/dist_common.sh
. scripts/dist_common.sh
channel='stable'
update_channel=true
allow_downgrade=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel) [[ $# -ge 2 ]] || die '--channel requires a value'; channel="$2"; shift 2 ;;
        --no-channel) update_channel=false; shift ;;
        --allow-downgrade) allow_downgrade=true; shift ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ "$channel" =~ ^(stable|beta)$ ]] || die "unknown channel: $channel"

signing_key="${BORON_SIGNING_KEY:-${BORON_RELEASE_SIGNING_KEY_FILE:-secrets/release-signing.key}}"
host="${BORON_DIST_HOST:-root@104.234.179.66}"
ssh_key="${BORON_DIST_SSH_KEY:-master}"
dist_root="${BORON_DIST_ROOT:-/srv/download}"
[[ "$dist_root" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "invalid BORON_DIST_ROOT: $dist_root"

[[ -r "$signing_key" ]] || die "signing key not readable: $signing_key"
# The archive is built from HEAD, so uncommitted edits are never packaged.
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || printf 'warning: uncommitted changes are not included; packaging HEAD %s\n' "$(git rev-parse --short HEAD)" >&2

version="$(git show HEAD:version.py | sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p')"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "cannot read version from version.py"

out="$(mktemp -d)"
trap 'rm -rf -- "$out"' EXIT
dist_build_public "$out/public" "$version" "$channel" "$signing_key"
dist_write_apply "$out/apply.sh"
dist_upload "$out" "$host" "$ssh_key" "$dist_root" "$version" "$channel" "$update_channel" "$allow_downgrade"
