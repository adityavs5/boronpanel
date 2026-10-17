#!/usr/bin/env bash
# Download a stable source release, then run its unchanged installer.
# Keep stdin attached to the terminal so interactive setup prompts work.
set -euo pipefail
readonly RELEASE_TAG='v3.2.0'
readonly REPOSITORY='https://github.com/adityavs5/boronpanel.git'

if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then
    printf '%s\n' 'Boron stable installer launcher' \
        "Downloads ${RELEASE_TAG} and runs scripts/install.sh with your arguments." \
        'Usage: sudo bash install.sh [--profile minimal|standard|full] [installer options]' \
        'Requires a fresh Ubuntu 24.04 server. Full is the default profile.' \
        'For installer dry-run or full help, use the documented source checkout.'
    exit 0
fi
# A launcher dry-run must not install packages or download code unexpectedly.
for arg in "$@"; do
    if [[ "$arg" == '--dry-run' ]]; then
        printf '%s\n' 'For a dry run, clone the release and run bash scripts/install.sh --dry-run.' >&2
        exit 2
    fi
done
if [[ "$EUID" -ne 0 ]]; then
    printf '%s\n' 'Run this launcher as root: sudo bash install.sh' >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    printf '%s\n' 'Ubuntu 24.04 LTS is required.' >&2
    exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
    printf '%s\n' 'This launcher supports Ubuntu 24.04 LTS only.' >&2
    exit 1
fi
# Match the documented source-install environment, including restrictive SSH umasks.
umask 022
if ! command -v git >/dev/null 2>&1 || [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then
    apt-get update
    apt-get install -y git ca-certificates
fi
# Root-owned private checkout; never overwrite an existing clone or user's files.
workdir="$(mktemp -d /root/boron-install.XXXXXXXX)"
trap 'rm -rf -- "$workdir"' EXIT
printf 'Downloading Boron %s…\n' "$RELEASE_TAG"
git clone --depth 1 --single-branch --branch "$RELEASE_TAG" "$REPOSITORY" "$workdir/source"
cd "$workdir/source"
bash scripts/install.sh "$@"
