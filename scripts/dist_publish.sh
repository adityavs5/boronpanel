#!/usr/bin/env bash
# Build, sign and upload a Boron release to the distribution server.
#
# Signing happens locally: the Ed25519 private key never leaves this machine.
# The server only receives the archive, its manifest and the manifest signature.
# A published version is immutable; re-publishing it is refused unless the
# bytes are identical. Channel pointers (channels/<name>.manifest) are copies
# of a release manifest and its signature.
#
# Usage: scripts/dist_publish.sh [--channel stable|beta] [--no-channel]
# Environment:
#   BORON_SIGNING_KEY  Ed25519 private key PEM (default: secrets/release-signing.key)
#   BORON_DIST_HOST    SSH destination (default: root@104.234.179.66)
#   BORON_DIST_SSH_KEY SSH identity file (default: ./master)
#   BORON_DIST_ROOT    Web root on the server (default: /srv/download)
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
channel='stable'
update_channel=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel) [[ $# -ge 2 ]] || die '--channel requires a value'; channel="$2"; shift 2 ;;
        --no-channel) update_channel=false; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ "$channel" =~ ^(stable|beta)$ ]] || die "unknown channel: $channel"

signing_key="${BORON_SIGNING_KEY:-secrets/release-signing.key}"
host="${BORON_DIST_HOST:-root@104.234.179.66}"
ssh_key="${BORON_DIST_SSH_KEY:-master}"
dist_root="${BORON_DIST_ROOT:-/srv/download}"
ssh_opts=(-i "$ssh_key" -o IdentitiesOnly=yes -o BatchMode=yes)

[[ -r "$signing_key" ]] || die "signing key not readable: $signing_key"
# The archive is built from HEAD, so uncommitted edits are never packaged.
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || printf 'warning: uncommitted changes are not included; packaging HEAD %s\n' "$(git rev-parse --short HEAD)" >&2

version="$(sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p' version.py)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "cannot read version from version.py"
commit="$(git rev-parse HEAD)"
file="boron-${version}.tar.gz"

# The launcher embeds the public key; refuse to sign with a key it would reject.
embedded="$(sed -n "/^readonly SIGNING_PUBLIC_KEY='/,/-----END PUBLIC KEY-----'/p" install.sh \
    | sed "s/^readonly SIGNING_PUBLIC_KEY='//; s/'$//")"
[[ "$embedded" == "$(openssl pkey -in "$signing_key" -pubout | tr -d '\r')" ]] \
    || die 'signing key does not match the public key embedded in install.sh'

out="$(mktemp -d)"
trap 'rm -rf -- "$out"' EXIT
# core.autocrlf=false keeps LF line endings even when packaging on Windows.
git -c core.autocrlf=false archive --format=tar --prefix="boron-${version}/" HEAD | gzip -n -9 > "$out/$file"
sha256="$(sha256sum "$out/$file" | cut -d' ' -f1)"
size="$(wc -c < "$out/$file" | tr -d ' ')"
cat > "$out/boron-${version}.manifest" <<EOF
format=1
product=boron
channel=${channel}
version=${version}
commit=${commit}
file=${file}
sha256=${sha256}
size=${size}
published=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
manifest="$out/boron-${version}.manifest"
openssl pkeyutl -sign -inkey "$signing_key" -rawin -in "$manifest" -out "$manifest.sig"
openssl pkey -in "$signing_key" -pubout > "$out/signing.pub"
openssl pkeyutl -verify -pubin -inkey "$out/signing.pub" -rawin -in "$manifest" -sigfile "$manifest.sig" >/dev/null \
    || die 'self-verification of the new signature failed'

cp install.sh "$out/install.sh"
cat > "$out/apply.sh" <<'REMOTE'
# Runs on the distribution server from the unpacked upload directory.
set -euo pipefail
dist_root="$1" version="$2" file="$3" sha256="$4" channel="$5" update_channel="$6"
echo "${sha256}  ${file}" | sha256sum -c --status - || { echo 'error: upload corrupted' >&2; exit 1; }
chmod 0644 "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" install.sh
remote_dir="${dist_root}/releases/${version}"
if [[ -e "${remote_dir}/${file}" ]]; then
    [[ "$(sha256sum "${remote_dir}/${file}" | cut -d' ' -f1)" == "$sha256" ]] \
        || { echo "error: version ${version} is already published with different bytes; bump version.py" >&2; exit 1; }
    echo "Version ${version} is already published with identical bytes."
else
    install -d -m 0755 "$remote_dir"
    mv "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" "$remote_dir/"
    echo "Published Boron ${version}."
fi
if [[ "$update_channel" == true ]]; then
    # Rename into place so a reader never sees a manifest without its signature for long.
    channels="${dist_root}/channels"
    cp "${remote_dir}/boron-${version}.manifest.sig" "${channels}/.${channel}.manifest.sig.new"
    cp "${remote_dir}/boron-${version}.manifest" "${channels}/.${channel}.manifest.new"
    chmod 0644 "${channels}/.${channel}.manifest.sig.new" "${channels}/.${channel}.manifest.new"
    mv "${channels}/.${channel}.manifest.sig.new" "${channels}/${channel}.manifest.sig"
    mv "${channels}/.${channel}.manifest.new" "${channels}/${channel}.manifest"
    echo "Channel ${channel} now points to ${version}."
fi
mv install.sh "${dist_root}/install.sh"
echo 'Launcher uploaded: https://download.boronpanel.com/install.sh'
REMOTE

# One SSH connection does the whole server side (the server rate-limits SSH).
# All arguments were validated above.
# shellcheck disable=SC2029  # remote arguments expand locally on purpose
tar -C "$out" -cf - "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" install.sh apply.sh \
    | ssh "${ssh_opts[@]}" "$host" "set -e; up=\$(mktemp -d '${dist_root}/.upload-XXXXXXXX'); \
        trap 'rm -rf -- \"\$up\"' EXIT; tar -C \"\$up\" -xf -; cd \"\$up\"; \
        bash apply.sh '${dist_root}' '${version}' '${file}' '${sha256}' '${channel}' '${update_channel}'"
