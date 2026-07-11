#!/usr/bin/env bash
#
# Boron release pipeline -- builds a versioned, checksummed release
# tarball and publishes it as a GitHub release. See docs/RELEASING.md.
#
#   scripts/release.sh --dry-run              # full local rehearsal, publishes nothing
#   scripts/release.sh --patch                # bump 1.0.0 -> 1.0.1, test, build, publish
#   scripts/release.sh 2.1.0                  # explicit target version
#
# Pipeline: bump version.py -> run test suite -> build React frontend ->
# stage tracked files only (git archive; secrets/DB/logs are untracked by
# construction) -> tarball boron-{version}.tar.gz + SHA256 checksum ->
# GPG-sign if a secret key exists -> git commit + tag + push + gh release.
#
# Production servers NEVER git-pull: they consume these tarballs through the
# panel's update system (daemon/updates.py), which verifies the SHA256
# before extracting anything.

set -euo pipefail

# --- constants ----------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly VERSION_FILE="${REPO_ROOT}/version.py"

# --- runtime flags ------------------------------------------------------------

DRY_RUN=false
SKIP_TESTS=false
SKIP_BUILD=false
BUMP_KIND="patch"
EXPLICIT_VERSION=""
GITHUB_REPO="${FH_RELEASE_REPO:-}"   # owner/repo; derived from origin if empty
OUT_DIR="${REPO_ROOT}/dist"
BUMPED=false

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
Boron release pipeline

Usage: scripts/release.sh [OPTIONS] [NEW_VERSION]

  --dry-run          Run the full pipeline locally (tests, frontend build,
                     tarball, checksum) but bump nothing and publish nothing.
  --patch            Bump the patch component (default).
  --minor            Bump the minor component, reset patch.
  --major            Bump the major component, reset minor+patch.
  NEW_VERSION        Explicit x.y.z target instead of a bump flag.
  --repo OWNER/REPO  GitHub repository for the release (default: derived
                     from the 'origin' remote, or \$FH_RELEASE_REPO).
  --output-dir DIR   Where artifacts land (default: dist/ in the repo).
  --skip-tests       Skip the test suite (CI/debug escape hatch -- a real
                     release must never use this).
  --skip-build       Reuse the existing static/dist instead of rebuilding.
  -h, --help         Show this help.

Artifacts: boron-{version}.tar.gz, boron-{version}.sha256, and
boron-{version}.tar.gz.asc when a GPG secret key is available.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)    DRY_RUN=true ;;
            --patch)      BUMP_KIND="patch" ;;
            --minor)      BUMP_KIND="minor" ;;
            --major)      BUMP_KIND="major" ;;
            --repo)       shift; GITHUB_REPO="${1:-}" ;;
            --output-dir) shift; OUT_DIR="${1:-}" ;;
            --skip-tests) SKIP_TESTS=true ;;
            --skip-build) SKIP_BUILD=true ;;
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
        git -C "$REPO_ROOT" checkout -q -- version.py frontend/package.json 2>/dev/null || true
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
        require_tool gh
        gh auth status >/dev/null 2>&1 || die "gh is not authenticated (run: gh auth login)"
        if [[ -z "$GITHUB_REPO" ]]; then
            local origin_url
            origin_url="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
            [[ -n "$origin_url" ]] || die "no 'origin' remote and no --repo given -- cannot publish"
            GITHUB_REPO="$(printf '%s' "$origin_url" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
        fi
        [[ "$GITHUB_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "bad GitHub repo: ${GITHUB_REPO}"
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
    "$PY" - "$new" <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path("frontend/package.json")
data = json.loads(p.read_text())
data["version"] = sys.argv[1]
p.write_text(json.dumps(data, indent=2) + "\n")
PYEOF
    BUMPED=true
    [[ "$(current_version)" == "$new" ]] || die "version bump failed to apply"
    ok "version.py -> ${new}"
}

run_tests() {
    if $SKIP_TESTS; then
        skip "test suite (--skip-tests) -- a real release must not skip this"
        return
    fi
    info "Running test suite (this takes several minutes)"
    (cd "$REPO_ROOT" && "$PY" -m pytest -q) || die "test suite failing -- release aborted"
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

STAGE_DIR=""
stage_tree() {
    local version="$1"
    info "Staging release tree (tracked files only)"
    STAGE_DIR="$(mktemp -d /tmp/boron-release.XXXXXX)"
    # git archive ships ONLY tracked files: secrets.env / *.db / logs /
    # node_modules / .venv are untracked or gitignored, so they cannot leak
    # into the tarball by construction. The two overlays below are the only
    # not-from-HEAD content.
    git -C "$REPO_ROOT" archive --format=tar --prefix="boron-${version}/" HEAD \
        | tar -x -C "$STAGE_DIR"
    local root="${STAGE_DIR}/boron-${version}"
    [[ -d "$root" ]] || die "git archive produced no staging tree"

    # Overlay 1: the bumped version.py (the bump commit happens after the
    # artifacts are built, so HEAD still carries the old version here).
    cp "$VERSION_FILE" "${root}/version.py"
    # Overlay 2: the freshly built SPA bundle (the committed static/dist may
    # lag the source; releases always ship the bundle built in this run).
    rm -rf "${root}/static/dist"
    mkdir -p "${root}/static"
    cp -a "${REPO_ROOT}/static/dist" "${root}/static/dist"

    # The frontend source isn't needed at runtime (the built bundle ships);
    # dropping it keeps the tarball small and the update download fast.
    rm -rf "${root}/frontend"

    # Belt-and-braces: nothing secret-, database- or log-shaped may exist in
    # the staged tree, whatever future .gitignore edits do.
    local leaks
    leaks="$(find "$root" \( -name 'secrets*.env' -o -name '*secrets.env' -o -name '*.db' \
        -o -name '*.db-wal' -o -name '*.db-shm' -o -name '.env' -o -name '*.log' \
        -o -name '.git' -o -name 'node_modules' -o -name '.venv' \) -print | head -5)"
    [[ -z "$leaks" ]] || die "refusing to package -- secret/db/log-shaped paths in stage: ${leaks}"

    ok "staged $(find "$root" -type f | wc -l) files"
}

TARBALL=""
CHECKSUM_FILE=""
build_artifacts() {
    local version="$1"
    info "Building tarball + checksum"
    mkdir -p "$OUT_DIR"
    TARBALL="${OUT_DIR}/boron-${version}.tar.gz"
    CHECKSUM_FILE="${OUT_DIR}/boron-${version}.sha256"
    # Deterministic-ish tarball: stable member order, root ownership.
    tar -C "$STAGE_DIR" --sort=name --owner=0 --group=0 --numeric-owner \
        -czf "$TARBALL" "boron-${version}"
    (cd "$OUT_DIR" && sha256sum "boron-${version}.tar.gz" > "boron-${version}.sha256")
    ok "artifacts: ${TARBALL} ($(du -h "$TARBALL" | cut -f1)), $(basename "$CHECKSUM_FILE")"
}

sign_artifacts() {
    local version="$1"
    if ! command -v gpg >/dev/null 2>&1; then
        skip "GPG signing (gpg not installed)"
        return
    fi
    if ! gpg --list-secret-keys 2>/dev/null | grep -q '^sec'; then
        skip "GPG signing (no secret key available)"
        return
    fi
    info "GPG-signing tarball"
    rm -f "${TARBALL}.asc"
    if gpg --batch --yes --armor --detach-sign --output "${TARBALL}.asc" "$TARBALL"; then
        ok "signed: boron-${version}.tar.gz.asc"
    else
        # A signature is an optional extra; a locked key must not block a release.
        warn "GPG signing failed (locked/no-pinentry key?) -- continuing unsigned"
        rm -f "${TARBALL}.asc"
    fi
}

verify_artifacts() {
    local version="$1"
    info "Self-verifying artifacts (the same checks the updater will run)"
    (cd "$OUT_DIR" && sha256sum -c "boron-${version}.sha256" >/dev/null) \
        || die "checksum self-verification failed"
    # Every member must live under the version prefix with no traversal --
    # the update daemon rejects violations, so catch them at build time.
    local bad
    bad="$(tar -tzf "$TARBALL" | grep -Ev "^boron-${version}(/|$)" | head -3 || true)"
    [[ -z "$bad" ]] || die "tarball contains members outside boron-${version}/: ${bad}"
    bad="$(tar -tzf "$TARBALL" | grep -E '(^/|(^|/)\.\.(/|$))' | head -3 || true)"
    [[ -z "$bad" ]] || die "tarball contains absolute/traversal paths: ${bad}"
    # The packaged version.py must carry exactly the released version.
    local packaged
    packaged="$(tar -xzOf "$TARBALL" "boron-${version}/version.py" \
        | sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p')"
    [[ "$packaged" == "$version" ]] || die "packaged version.py says '${packaged}', expected '${version}'"
    # And the runtime essentials must be present.
    local member
    for member in api/main.py daemon/server.py shared/config.py requirements.txt \
                  static/dist/index.html tests/conftest.py deploy/boron-api.service; do
        tar -tzf "$TARBALL" "boron-${version}/${member}" >/dev/null 2>&1 \
            || die "tarball is missing ${member}"
    done
    ok "artifacts verified"
}

publish() {
    local version="$1"
    if $DRY_RUN; then
        # NB: in a dry-run nothing was bumped, so the rehearsal artifacts
        # carry the CURRENT version; a real run would produce ${NEW_VERSION}.
        skip "publish (dry-run): a real run would commit the bump, tag v${NEW_VERSION}, push, and run:"
        printf '       gh release create v%s --repo %s --title "Boron v%s" boron-%s.tar.gz boron-%s.sha256 [.asc]\n' \
            "$NEW_VERSION" "${GITHUB_REPO:-<origin>}" "$NEW_VERSION" "$NEW_VERSION" "$NEW_VERSION"
        return
    fi
    info "Publishing v${version}"
    git -C "$REPO_ROOT" add version.py frontend/package.json
    git -C "$REPO_ROOT" commit -m "release: v${version}"
    BUMPED=false   # the bump is committed now; the failure trap must not revert it
    git -C "$REPO_ROOT" tag -a "v${version}" -m "Boron v${version}"
    git -C "$REPO_ROOT" push origin HEAD "v${version}"
    local assets=("$TARBALL" "$CHECKSUM_FILE")
    [[ -f "${TARBALL}.asc" ]] && assets+=("${TARBALL}.asc")
    gh release create "v${version}" --repo "$GITHUB_REPO" \
        --title "Boron v${version}" \
        --notes "Boron v${version}. Install/update via the panel's update system (verifies the SHA256 checksum before applying) -- see docs/RELEASING.md." \
        "${assets[@]}"
    ok "release v${version} published to ${GITHUB_REPO}"
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
        die "target version ${new} equals the current version"
    fi

    preflight
    bump_version "$new"
    run_tests
    build_frontend
    # Dry-run artifacts carry the CURRENT version (nothing was bumped).
    local artifact_version="$new"
    $DRY_RUN && artifact_version="$cur"
    stage_tree "$artifact_version"
    build_artifacts "$artifact_version"
    sign_artifacts "$artifact_version"
    verify_artifacts "$artifact_version"
    publish "$artifact_version"

    ok "done"
}

main "$@"
