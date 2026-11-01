#!/usr/bin/env bash
#
# Boron release preparation -- bumps the version, runs the test suite and
# builds a versioned, checksummed and signed runtime archive locally. See
# docs/RELEASING.md.
#
#   scripts/release.sh --dry-run              # full local rehearsal, changes nothing
#   scripts/release.sh --patch                # bump 1.0.0 -> 1.0.1, test, build, commit
#   scripts/release.sh 2.1.0                  # explicit target version
#
# Pipeline: bump version.py -> run test suite -> build React frontend ->
# stage tracked files only (git archive; secrets/DB/logs are untracked by
# construction) -> tarball boron-{version}.tar.gz + SHA256 checksum ->
# Ed25519-sign with the pinned publisher key -> local commit + tag.
#
# Nothing is pushed or uploaded. Publication to download.boronpanel.com is
# scripts/dist_release.sh, which rebuilds the same artifacts from the
# committed HEAD. Production servers NEVER git-pull: they consume these
# archives through the panel's update system (daemon/updates.py), which
# verifies the SHA256 and publisher signature before extracting anything.

set -euo pipefail

# --- constants ----------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly VERSION_FILE="${REPO_ROOT}/version.py"

# --- runtime flags ------------------------------------------------------------

DRY_RUN=false
SKIP_TESTS=false
SKIP_BUILD=false
RELEASE_CHANNEL="stable"
BUMP_KIND="patch"
EXPLICIT_VERSION=""
OUT_DIR="${REPO_ROOT}/dist"
BUMPED=false
PREPARED_VERSION=false
TESTED_COMMIT=""

# --- output helpers (same shape as scripts/install.sh) -------------------------

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

# --- helpers -------------------------------------------------------------------

usage() {
    cat <<EOF
Boron release preparation (local only; publish with scripts/dist_release.sh)

Usage: scripts/release.sh [OPTIONS] [NEW_VERSION]

  --dry-run          Run the full pipeline locally (tests, frontend build,
                     tarball, checksum) but bump and commit nothing.
  --patch            Bump the patch component (default).
  --minor            Bump the minor component, reset patch.
  --major            Bump the major component, reset minor+patch.
  NEW_VERSION        Explicit x.y.z target instead of a bump flag.
  --output-dir DIR   Where artifacts land (default: dist/ in the repo).
  --skip-tests       Skip tests only for a local dry-run (debugging).
  --skip-build       Reuse static/dist only for a local dry-run (debugging).
  --tested-commit ID Reuse completed tests for this exact full HEAD commit ID.
                     Requires a clean tree and an explicitly prepared version.
                     Still builds the frontend and verifies both signatures.
  -h, --help         Show this help.

Artifacts: boron-{version}.tar.gz, boron-{version}.sha256,
boron-{version}.tar.gz.sig, boron-{version}.metadata.json, and
boron-{version}.metadata.json.sig. Both signatures use the pinned Ed25519
publisher key. The release channel comes from release-metadata.json.
Set BORON_RELEASE_SIGNING_KEY_FILE to the protected private PEM path.

A real run commits the version bump as "release: vX.Y.Z" and creates the
local annotated tag vX.Y.Z. It never pushes or uploads anything; run
scripts/dist_release.sh afterwards to publish to the distribution server.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)    DRY_RUN=true ;;
            --patch)      BUMP_KIND="patch" ;;
            --minor)      BUMP_KIND="minor" ;;
            --major)      BUMP_KIND="major" ;;
            --output-dir) shift; OUT_DIR="${1:-}" ;;
            --skip-tests) SKIP_TESTS=true ;;
            --skip-build) SKIP_BUILD=true ;;
            --tested-commit) shift; TESTED_COMMIT="${1:-}"; [[ -n "$TESTED_COMMIT" ]] || die "--tested-commit needs a full commit ID" ;;
            -h|--help)    usage; exit 0 ;;
            -*)           die "unknown option: $1 (try --help)" ;;
            *)
                [[ -n "$EXPLICIT_VERSION" ]] && die "only one version argument allowed"
                EXPLICIT_VERSION="$1"
                ;;
        esac
        shift
    done
    if [[ -n "$EXPLICIT_VERSION" && ! "$EXPLICIT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        die "version must be x.y.z (got: ${EXPLICIT_VERSION})"
    fi
    [[ -n "$OUT_DIR" ]] || die "--output-dir needs a value"
    if [[ -n "$TESTED_COMMIT" ]]; then
        [[ "$TESTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "--tested-commit requires a full 40-character commit ID"
        if $SKIP_TESTS || $SKIP_BUILD; then
            die "--tested-commit cannot be combined with --skip-tests or --skip-build"
        fi
    fi
}

validate_tested_commit() {
    [[ -n "$TESTED_COMMIT" ]] || return 0
    [[ "$TESTED_COMMIT" == "$(git -C "$REPO_ROOT" rev-parse HEAD)" ]] \
        || die "tested commit does not match HEAD; run tests on the current commit"
    [[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]] \
        || die "tested commit requires a clean working tree"
    [[ -n "$EXPLICIT_VERSION" && "$EXPLICIT_VERSION" == "$(current_version)" ]] \
        || die "tested commit requires an explicit version already prepared in HEAD"
}

current_version() {
    sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p' "$VERSION_FILE"
}

next_version() {
    local cur="$1" major minor patch
    IFS='.' read -r major minor patch <<<"$cur"
    case "$BUMP_KIND" in
        major) printf '%s.0.0' "$((major + 1))" ;;
        minor) printf '%s.%s.0' "$major" "$((minor + 1))" ;;
        patch) printf '%s.%s.%s' "$major" "$minor" "$((patch + 1))" ;;
    esac
}

# Restore the version bump if anything fails before the release commit is
# made -- never leave a half-released version.py behind.
cleanup_on_exit() {
    local status=$?
    if [[ $status -ne 0 ]] && $BUMPED; then
        git -C "$REPO_ROOT" checkout -q -- version.py frontend/package.json frontend/package-lock.json 2>/dev/null || true
        warn "failure after version bump -- version.py/package.json restored from git"
    fi
    [[ -n "${STAGE_DIR:-}" && -d "${STAGE_DIR:-}" ]] && rm -rf "$STAGE_DIR"
    exit "$status"
}
trap cleanup_on_exit EXIT

require_tool() {
    command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"
}

# --- pipeline steps ------------------------------------------------------------

PY=""
preflight() {
    info "Pre-flight checks"
    require_tool git; require_tool tar; require_tool sha256sum; require_tool sed
    [[ -f "$VERSION_FILE" ]] || die "version.py not found at ${VERSION_FILE}"
    [[ -n "$(current_version)" ]] || die "could not parse BORON_VERSION from version.py"
    validate_tested_commit

    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        PY="${REPO_ROOT}/.venv/bin/python"
    else
        require_tool python3
        PY="python3"
    fi

    if ! $SKIP_BUILD; then
        require_tool npm
        require_tool node
    fi

    if ! $DRY_RUN; then
        if $SKIP_TESTS || $SKIP_BUILD; then
            die "published releases require both the full test suite and a fresh frontend build"
        fi
        # A real release is cut from a clean tree only -- the tarball is built
        # from HEAD, so uncommitted changes would silently not ship.
        if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
            die "working tree is dirty -- commit or stash before releasing"
        fi
    else
        if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
            warn "working tree is dirty -- fine for a dry-run, but the tarball is built from HEAD (+ the freshly built static/dist), so uncommitted changes will not be in it"
        fi
    fi
    ok "pre-flight passed"
}

bump_version() {
    local new="$1"
    if $DRY_RUN; then
        skip "version bump (dry-run): would set version.py to ${new}"
        return
    fi
    info "Bumping version.py to ${new}"
    sed -i "s/^BORON_VERSION = \".*\"$/BORON_VERSION = \"${new}\"/" "$VERSION_FILE"
    # Keep the npm package metadata in sync (not displayed anywhere, but
    # drift is confusing). JSON-rewritten with python, not sed.
    (cd "$REPO_ROOT" && "$PY" - "$new") <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path("frontend/package.json")
data = json.loads(p.read_text())
data["version"] = sys.argv[1]
p.write_text(json.dumps(data, indent=2) + "\n")
lock = pathlib.Path("frontend/package-lock.json")
data = json.loads(lock.read_text())
data["version"] = sys.argv[1]
data["packages"][""]["version"] = sys.argv[1]
lock.write_text(json.dumps(data, indent=2) + "\n")
PYEOF
    BUMPED=true
    [[ "$(current_version)" == "$new" ]] || die "version bump failed to apply"
    ok "version.py -> ${new}"
}

run_tests() {
    if [[ -n "$TESTED_COMMIT" ]]; then
        validate_tested_commit
        skip "test suite: owner attests completed tests for exact commit ${TESTED_COMMIT}"
        return
    fi
    if $SKIP_TESTS; then
        skip "test suite (--skip-tests) -- a real release must not skip this"
        return
    fi
    info "Running test suite (this takes several minutes)"
    # Use the same bounded workspace as development and CI. Full runs refuse
    # to share the installed panel's data/log filesystem unless an operator
    # explicitly supplies a separate BORON_TEST_TMPDIR.
    (cd "$REPO_ROOT" && scripts/test.sh full) || die "test suite failing -- release aborted"
    ok "test suite green"
}

build_frontend() {
    if $SKIP_BUILD; then
        [[ -f "${REPO_ROOT}/static/dist/index.html" ]] || die "--skip-build but no existing static/dist bundle"
        skip "frontend build (--skip-build): reusing existing static/dist"
        return
    fi
    info "Building React frontend (npm ci && npm run build)"
    (cd "${REPO_ROOT}/frontend" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null) \
        || die "frontend build failed"
    [[ -f "${REPO_ROOT}/static/dist/index.html" ]] || die "frontend build produced no static/dist/index.html"
    ok "frontend built into static/dist"
}

# Staging, tarball, signing and self-verification are shared with
# scripts/dist_release.sh so both always produce the same artifacts.
# shellcheck source=scripts/release_lib.sh
. "${REPO_ROOT}/scripts/release_lib.sh"

finish_release() {
    local version="$1"
    if $DRY_RUN; then
        # NB: in a dry-run nothing was bumped, so the rehearsal artifacts
        # carry the CURRENT version; a real run would produce ${NEW_VERSION}.
        skip "commit (dry-run): a real run would commit the bump as 'release: v${NEW_VERSION}' and tag v${NEW_VERSION} locally"
        return
    fi
    info "Recording v${version} locally"
    if ! $PREPARED_VERSION; then
        git -C "$REPO_ROOT" add version.py frontend/package.json frontend/package-lock.json
        git -C "$REPO_ROOT" commit -m "release: v${version}"
        BUMPED=false   # the bump is committed now; the failure trap must not revert it
    else
        skip "version commit already prepared at v${version}"
    fi
    if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/v${version}" >/dev/null; then
        skip "tag v${version} already exists"
    else
        git -C "$REPO_ROOT" tag -a "v${version}" -m "Boron v${version}"
    fi
    ok "v${version} committed and tagged locally; nothing was pushed or uploaded"
    printf '       publish it with: scripts/dist_release.sh --channel %s\n' "$RELEASE_CHANNEL"
}

# --- main -----------------------------------------------------------------------

NEW_VERSION=""
main() {
    parse_args "$@"

    local cur new
    cur="$(current_version)"
    if [[ -n "$EXPLICIT_VERSION" ]]; then
        new="$EXPLICIT_VERSION"
    else
        new="$(next_version "$cur")"
    fi
    NEW_VERSION="$new"
    local dry_marker=""
    $DRY_RUN && dry_marker=" ${C_YELLOW}(dry-run)${C_RESET}"
    printf '%s\n' "${C_BOLD}Boron release: v${cur} -> v${new}${C_RESET}${dry_marker}"

    if ! $DRY_RUN && [[ "$new" == "$cur" ]]; then
        [[ -n "$EXPLICIT_VERSION" ]] || die "target version ${new} equals the current version"
        PREPARED_VERSION=true
        info "Using prepared version v${new} from the current clean HEAD"
    fi

    preflight
    $PREPARED_VERSION || bump_version "$new"
    run_tests
    build_frontend
    validate_tested_commit
    # Dry-run artifacts carry the CURRENT version (nothing was bumped).
    local artifact_version="$new"
    $DRY_RUN && artifact_version="$cur"
    # The signed document, not a mutable label, binds the release tier.
    RELEASE_CHANNEL="$(cd "$REPO_ROOT" && "$PY" -c 'from shared.release_metadata import load_release_metadata; print(load_release_metadata("release-metadata.json")["channel"])')"
    case "$RELEASE_CHANNEL" in
        stable|beta|development) ;;
        *) die "invalid signed release channel" ;;
    esac
    stage_tree "$artifact_version"
    build_artifacts "$artifact_version"
    sign_artifacts "$artifact_version"
    verify_artifacts "$artifact_version"
    finish_release "$artifact_version"

    ok "done"
}

main "$@"
