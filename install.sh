#!/usr/bin/env bash
# Download a signed Boron release from the Boron distribution server, verify it,
# then run its unchanged installer. Keep stdin attached to the terminal so
# interactive setup prompts work.
set -euo pipefail
readonly BASE_URL='https://download.boronpanel.com'
# Release-signing public key. A manifest must carry a valid Ed25519 signature
# from the matching private key, and the archive must match the manifest's
# SHA-256, before anything from the download is executed.
readonly SIGNING_PUBLIC_KEY='-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEANPFdkfAyTYc+t3DmaFd9NqHUdyIytPO+k4oWpVnEOEM=
-----END PUBLIC KEY-----'

usage() {
    printf '%s\n' 'Boron installer launcher' \
        'Downloads a signed Boron release, verifies it and runs scripts/install.sh.' \
        '' \
        'Usage: sudo bash install.sh [--channel stable|beta] [--version X.Y.Z] [--verify-only]' \
        '                            [--license KEY] [--profile minimal|standard|full] [installer options]' \
        '' \
        '  --channel NAME  Release channel to install (default: stable).' \
        '  --version X.Y.Z Install this exact release instead of the channel default.' \
        '  --verify-only   Download and verify only; install nothing.' \
        '' \
        'Other options, such as --license, --profile and --dry-run, are passed to the installer.' \
        'Requires a fresh Ubuntu 24.04 server. Full is the default profile.'
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

channel='stable'
version=''
verify_only=false
installer_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --channel) [[ $# -ge 2 ]] || die '--channel requires a value'; channel="$2"; shift 2 ;;
        --version) [[ $# -ge 2 ]] || die '--version requires a value'; version="$2"; shift 2 ;;
        --verify-only) verify_only=true; shift ;;
        *) installer_args+=("$1"); shift ;;
    esac
done
[[ "$channel" =~ ^(stable|beta)$ ]] || die "unknown channel: $channel"
[[ -z "$version" || "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid version: $version"

if ! $verify_only; then
    [[ "$EUID" -eq 0 ]] || die 'run this launcher as root: sudo bash install.sh'
    [[ -r /etc/os-release ]] || die 'Ubuntu 24.04 LTS is required.'
    . /etc/os-release
    [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || die 'this launcher supports Ubuntu 24.04 LTS only.'
fi
for tool in curl openssl sha256sum tar; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required (apt-get install -y curl openssl ca-certificates)"
done

# Match the documented source-install environment, including restrictive SSH umasks.
umask 022
if [[ "$EUID" -eq 0 ]]; then
    workdir="$(mktemp -d /root/boron-install.XXXXXXXX)"
else
    workdir="$(mktemp -d)"
fi
trap 'rm -rf -- "$workdir"' EXIT

fetch() { curl -fsS --proto '=https' --tlsv1.2 --retry 3 -o "$2" "$1" || die "download failed: $1"; }

if [[ -n "$version" ]]; then
    manifest_path="releases/${version}/boron-${version}.manifest"
else
    manifest_path="channels/${channel}.manifest"
fi
fetch "${BASE_URL}/${manifest_path}" "$workdir/manifest"
fetch "${BASE_URL}/${manifest_path}.sig" "$workdir/manifest.sig"
printf '%s\n' "$SIGNING_PUBLIC_KEY" > "$workdir/signing.pub"
openssl pkeyutl -verify -pubin -inkey "$workdir/signing.pub" -rawin \
    -in "$workdir/manifest" -sigfile "$workdir/manifest.sig" >/dev/null 2>&1 \
    || die 'release manifest signature is invalid; refusing to continue'

field() { sed -n "s/^$1=//p" "$workdir/manifest" | head -n 1; }
[[ "$(field format)" == 1 && "$(field product)" == boron ]] || die 'unsupported release manifest'
rel_version="$(field version)"
rel_file="$(field file)"
rel_sha256="$(field sha256)"
[[ "$rel_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'manifest has an invalid version'
[[ "$rel_file" == "boron-${rel_version}.tar.gz" ]] || die 'manifest names an unexpected archive'
[[ "$rel_sha256" =~ ^[0-9a-f]{64}$ ]] || die 'manifest has an invalid checksum'
if [[ -n "$version" ]]; then
    [[ "$rel_version" == "$version" ]] || die 'manifest version does not match the requested version'
else
    [[ "$(field channel)" == "$channel" ]] || die 'manifest channel does not match the requested channel'
fi

printf 'Downloading Boron %s…\n' "$rel_version"
fetch "${BASE_URL}/releases/${rel_version}/${rel_file}" "$workdir/$rel_file"
printf '%s  %s\n' "$rel_sha256" "$workdir/$rel_file" | sha256sum -c --status - \
    || die 'archive checksum does not match the signed manifest; refusing to continue'
printf 'Verified Boron %s (signature and SHA-256).\n' "$rel_version"
if $verify_only; then
    exit 0
fi

tar -xzf "$workdir/$rel_file" -C "$workdir" --no-same-owner
cd "$workdir/boron-${rel_version}"
bash scripts/install.sh "${installer_args[@]}"
