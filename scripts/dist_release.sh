#!/usr/bin/env bash
#
# Publish a Boron release to the distribution server -- the owner's one
# command for shipping a version. See docs/RELEASING.md.
#
#   scripts/dist_release.sh --dry-run --static-dir static/dist   # rehearse, upload nothing
#   scripts/dist_release.sh --static-dir static/dist             # publish HEAD
#
# From the committed HEAD it builds:
#   1. The license-gated panel update release (the same five artifacts
#      scripts/release.sh builds: runtime archive with the prebuilt frontend,
#      .sha256, archive signature, signed metadata) plus release.json, for
#      download.boronpanel.com/protected/ (served only through short-lived
#      links issued by license.boronpanel.com /v1/updates).
#   2. The public signed source archive, manifest and install.sh launcher for
#      fresh installs (exactly what scripts/dist_publish.sh publishes).
# Both are signed locally and self-verified, then uploaded over ONE ssh
# connection (the server rate-limits SSH) into a staging directory, verified
# there and moved into place. Published versions are immutable. The channel
# pointers are updated last and never move to an older version unless
# --allow-downgrade is given. Nothing is pushed anywhere and GitHub is not used.

set -euo pipefail
umask 022

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly VERSION_FILE="${REPO_ROOT}/version.py"
# Paths given on the command line or in the environment are relative to where
# the owner ran the command; the defaults are relative to the repository.
CALLER_PWD="$PWD"
readonly CALLER_PWD
cd "$REPO_ROOT"

# --- runtime flags ------------------------------------------------------------

DRY_RUN=false
ALLOW_DOWNGRADE=false
CHANNEL=""
STATIC_DIR=""
OUT_DIR_ARG=""

# --- output helpers (same shape as scripts/release.sh) -------------------------

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
    C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'; C_BOLD=$'\033[1m'
else
    C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

info() { printf '%s==>%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok()   { printf '%s[ OK ]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
skip() { printf '%s[SKIP]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Boron release publication (download.boronpanel.com)

Usage: scripts/dist_release.sh [OPTIONS]

Builds the committed HEAD into a license-gated panel update release and the
public signed source archive, signs and self-verifies both locally, and
uploads them to the distribution server over one SSH connection.

  --channel NAME     stable or beta. Must equal the channel signed into
                     release-metadata.json (the default).
  --static-dir DIR   Package this already built frontend (e.g. static/dist
                     built on Windows) instead of running npm ci and
                     npm run build. Must be built from the same version.
  --dry-run          Build, sign and verify everything into the output
                     directory; connect to nothing and upload nothing.
  --allow-downgrade  Let the channel pointers move to an older version than
                     the one they point to now (refused by default).
  --output-dir DIR   Where the artifacts are written (default:
                     dist/release-<version>; must be empty if given).
  -h, --help         Show this help.

Environment:
  BORON_RELEASE_SIGNING_KEY_FILE  Ed25519 release signing key PEM
                     (default: secrets/release-signing.key). Its public key
                     must equal deploy/release-ed25519-public.hex and the key
                     embedded in install.sh.
  PYTHON             Python with the repository dependencies (default:
                     .venv/bin/python, then python3).
  BORON_DIST_HOST    SSH destination (default: root@104.234.179.66).
  BORON_DIST_SSH_KEY SSH identity file (default: ./master; it must not be
                     group/world readable, so copy it to ~/.ssh under WSL).
  BORON_DIST_ROOT    Web root on the server (default: /srv/download).

Requirements: a clean tracked tree (HEAD is packaged), version.py and
frontend/package.json at the release version, and a CHANGELOG.md section for
that version. Run under Linux or WSL (GNU tar, sha256sum, openssl, ssh).

Server result:
  protected/releases/<ver>/boron-<ver>.{tar.gz,sha256,tar.gz.sig,metadata.json,metadata.json.sig}
  protected/releases/<ver>/release.json and protected/channels/<channel>.json
  releases/<ver>/boron-<ver>.{tar.gz,manifest,manifest.sig}, channels/<channel>.manifest(.sig)
  install.sh
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)    DRY_RUN=true ;;
            --allow-downgrade) ALLOW_DOWNGRADE=true ;;
            --channel)    [[ $# -ge 2 ]] || die "--channel needs a value"; shift; CHANNEL="$1" ;;
            --static-dir) [[ $# -ge 2 ]] || die "--static-dir needs a directory"; shift; STATIC_DIR="$1" ;;
            --output-dir) [[ $# -ge 2 ]] || die "--output-dir needs a directory"; shift; OUT_DIR_ARG="$1" ;;
            -h|--help)    usage; exit 0 ;;
            *)            die "unknown option: $1 (try --help)" ;;
        esac
        shift
    done
    [[ -z "$CHANNEL" || "$CHANNEL" =~ ^(stable|beta)$ ]] || die "channel must be stable or beta (got: ${CHANNEL})"
    if [[ -n "$STATIC_DIR" ]]; then
        STATIC_DIR="$(from_caller "$STATIC_DIR")"
        [[ -d "$STATIC_DIR" ]] || die "--static-dir is not a directory: ${STATIC_DIR}"
        STATIC_DIR="$(cd "$STATIC_DIR" && pwd)"
    fi
}

cleanup_on_exit() {
    local status=$?
    [[ -n "${STAGE_DIR:-}" && -d "${STAGE_DIR:-}" ]] && rm -rf "$STAGE_DIR"
    exit "$status"
}
trap cleanup_on_exit EXIT

require_tool() {
    command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"
}

from_caller() {
    case "$1" in
        /*) printf '%s' "$1" ;;
        *)  printf '%s/%s' "$CALLER_PWD" "$1" ;;
    esac
}

current_version() {
    sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p' "$VERSION_FILE"
}

# Shared runtime-archive steps (stage_tree, build_artifacts, sign_artifacts,
# verify_artifacts) and distribution-server steps (dist_build_public,
# dist_write_apply, dist_upload).
# shellcheck source=scripts/release_lib.sh
. "${REPO_ROOT}/scripts/release_lib.sh"
# shellcheck source=scripts/dist_common.sh
. "${REPO_ROOT}/scripts/dist_common.sh"

# --- pipeline steps ------------------------------------------------------------

PY=""
VERSION=""
SIGNING_KEY=""
DIST_HOST=""
DIST_SSH_KEY=""
DIST_ROOT=""
OUT=""
preflight() {
    info "Pre-flight checks"
    local tool
    for tool in git tar gzip sha256sum sed openssl cmp; do require_tool "$tool"; done
    $DRY_RUN || require_tool ssh

    if [[ -n "${PYTHON:-}" ]]; then
        PY="$PYTHON"
    elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        PY="${REPO_ROOT}/.venv/bin/python"
    else
        require_tool python3
        PY="python3"
    fi
    "$PY" -c 'import cryptography' 2>/dev/null \
        || die "${PY} cannot import cryptography; set PYTHON to a Python with the repository dependencies"

    # Only committed content is published: the archives are built from HEAD.
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
        || die "tracked files have uncommitted changes -- commit or stash them; HEAD is what ships"

    VERSION="$(current_version)"
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "cannot read BORON_VERSION from version.py"
    local npm_version
    npm_version="$("$PY" -c 'import json; print(json.load(open("frontend/package.json"))["version"])')"
    [[ "$npm_version" == "$VERSION" ]] \
        || die "frontend/package.json is at ${npm_version}, version.py at ${VERSION}"
    grep -Eq "^## ${VERSION//./\\.}([[:space:]]|$)" CHANGELOG.md \
        || die "CHANGELOG.md has no '## ${VERSION}' section"

    # The signed metadata, not a command-line label, binds the update tier.
    local signed_channel
    signed_channel="$("$PY" -c 'from shared.release_metadata import load_release_metadata; print(load_release_metadata("release-metadata.json")["channel"])')" \
        || die "release-metadata.json is invalid"
    [[ "$signed_channel" =~ ^(stable|beta)$ ]] \
        || die "release-metadata.json channel '${signed_channel}' cannot be distributed (stable or beta only)"
    if [[ -z "$CHANNEL" ]]; then
        CHANNEL="$signed_channel"
    elif [[ "$CHANNEL" != "$signed_channel" ]]; then
        die "--channel ${CHANNEL} does not match release-metadata.json (${signed_channel}); change and commit the metadata instead"
    fi

    if [[ -n "${BORON_RELEASE_SIGNING_KEY_FILE:-}" ]]; then
        SIGNING_KEY="$(from_caller "$BORON_RELEASE_SIGNING_KEY_FILE")"
    else
        SIGNING_KEY="${REPO_ROOT}/secrets/release-signing.key"
    fi
    [[ -r "$SIGNING_KEY" ]] || die "release signing key not readable: ${SIGNING_KEY} (set BORON_RELEASE_SIGNING_KEY_FILE)"
    export BORON_RELEASE_SIGNING_KEY_FILE="$SIGNING_KEY"
    # Fail before building anything if the key is not the one panels pin.
    "$PY" - "$SIGNING_KEY" <<'PYEOF' || die "signing key does not match deploy/release-ed25519-public.hex"
import sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = serialization.load_pem_private_key(Path(sys.argv[1]).read_bytes(), password=None)
if not isinstance(key, Ed25519PrivateKey):
    sys.exit(1)
raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
pinned = bytes.fromhex(Path("deploy/release-ed25519-public.hex").read_text().strip())
sys.exit(0 if raw == pinned else 1)
PYEOF

    DIST_HOST="${BORON_DIST_HOST:-root@104.234.179.66}"
    if [[ -n "${BORON_DIST_SSH_KEY:-}" ]]; then
        DIST_SSH_KEY="$(from_caller "$BORON_DIST_SSH_KEY")"
    else
        DIST_SSH_KEY="${REPO_ROOT}/master"
    fi
    DIST_ROOT="${BORON_DIST_ROOT:-/srv/download}"
    [[ "$DIST_HOST" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || die "invalid BORON_DIST_HOST: ${DIST_HOST}"
    [[ "$DIST_ROOT" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "invalid BORON_DIST_ROOT: ${DIST_ROOT}"
    if ! $DRY_RUN; then
        [[ -r "$DIST_SSH_KEY" ]] || die "SSH key not readable: ${DIST_SSH_KEY} (set BORON_DIST_SSH_KEY)"
    fi

    if [[ -n "$OUT_DIR_ARG" ]]; then
        OUT="$(from_caller "$OUT_DIR_ARG")"
        if [[ -e "$OUT" ]]; then
            [[ -d "$OUT" && -z "$(ls -A "$OUT")" ]] || die "--output-dir must be empty or absent: ${OUT}"
        fi
    else
        OUT="${REPO_ROOT}/dist/release-${VERSION}"
        rm -rf -- "$OUT"
    fi
    mkdir -p "$OUT"
    OUT="$(cd "$OUT" && pwd)"
    OUT_DIR="${OUT}/protected"
    ok "pre-flight passed: v${VERSION}, channel ${CHANNEL}, HEAD $(git rev-parse --short HEAD)"
}

build_frontend() {
    if [[ -n "$STATIC_DIR" ]]; then
        skip "frontend build (--static-dir): packaging ${STATIC_DIR}"
    else
        require_tool npm
        require_tool node
        info "Building React frontend (npm ci && npm run build)"
        (cd "${REPO_ROOT}/frontend" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null) \
            || die "frontend build failed"
        STATIC_DIR="${REPO_ROOT}/static/dist"
    fi
    [[ -f "${STATIC_DIR}/index.html" ]] || die "no index.html in ${STATIC_DIR}"
    # The SPA bakes version.py in at build time; a stale bundle would show the
    # wrong version on the login page.
    grep -rqF --include='*.js' -- "$VERSION" "$STATIC_DIR" \
        || die "the frontend in ${STATIC_DIR} was not built for ${VERSION}; rebuild it (npm run build)"
    ok "frontend bundle ready"
}

write_index() {
    info "Writing release.json"
    "$PY" - "$VERSION" "$CHANNEL" "$TARBALL" CHANGELOG.md "${OUT_DIR}/release.json" <<'PYEOF' || die "could not write release.json"
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

version, channel, archive, changelog, out = sys.argv[1:]
section, inside = [], False
for line in Path(changelog).read_text(encoding="utf-8").splitlines():
    if line.startswith("## "):
        if inside:
            break
        inside = re.match(rf"## {re.escape(version)}(\s|$)", line) is not None
        continue
    if inside:
        section.append(line)
notes = "\n".join(section).strip()
if not notes:
    sys.exit(f"CHANGELOG.md section {version} is empty")
base = f"boron-{version}"
index = {
    "format": 1,
    "channel": channel,
    "version": version,
    "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "changelog": notes,
    "files": {
        "archive": f"{base}.tar.gz",
        "checksum": f"{base}.sha256",
        "signature": f"{base}.tar.gz.sig",
        "metadata": f"{base}.metadata.json",
        "metadata_signature": f"{base}.metadata.json.sig",
    },
    "sizes": {"archive": Path(archive).stat().st_size},
}
Path(out).write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PYEOF
    (cd "$OUT_DIR" && sha256sum "boron-${VERSION}.tar.gz" "boron-${VERSION}.sha256" \
        "boron-${VERSION}.tar.gz.sig" "boron-${VERSION}.metadata.json" \
        "boron-${VERSION}.metadata.json.sig" release.json > SHA256SUMS)
    ok "release.json written"
}

build_public() {
    info "Building the public signed source archive and launcher"
    dist_build_public "${OUT}/public" "$VERSION" "$CHANNEL" "$SIGNING_KEY"
    dist_write_apply "${OUT}/apply.sh"
    ok "source archive signed and self-verified"
}

upload() {
    if $DRY_RUN; then
        skip "upload (dry-run): a real run would publish to ${DIST_HOST}:${DIST_ROOT}:"
        (cd "$OUT" && find protected public -type f ! -name SHA256SUMS -printf '       %p (%s bytes)\n' | sort)
        printf '       then point protected/channels/%s.json and channels/%s.manifest at %s\n' \
            "$CHANNEL" "$CHANNEL" "$VERSION"
        return
    fi
    info "Uploading to ${DIST_HOST} (one SSH connection)"
    dist_upload "$OUT" "$DIST_HOST" "$DIST_SSH_KEY" "$DIST_ROOT" "$VERSION" "$CHANNEL" true "$ALLOW_DOWNGRADE" \
        || die "upload failed; nothing after the failing step was published (re-run is safe)"
    ok "published Boron ${VERSION} to the ${CHANNEL} channel"
}

# --- main -----------------------------------------------------------------------

main() {
    parse_args "$@"
    local dry_marker=""
    $DRY_RUN && dry_marker=" ${C_YELLOW}(dry-run)${C_RESET}"
    printf '%s\n' "${C_BOLD}Boron distribution release${C_RESET}${dry_marker}"
    preflight
    build_frontend
    stage_tree "$VERSION"
    build_artifacts "$VERSION"
    sign_artifacts "$VERSION"
    verify_artifacts "$VERSION"
    write_index
    build_public
    upload
    ok "done (artifacts in ${OUT})"
}

main "$@"
