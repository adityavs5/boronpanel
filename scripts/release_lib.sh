# shellcheck shell=bash
#
# Shared build steps for Boron runtime release archives. Sourced by
# scripts/release.sh (local release preparation) and scripts/dist_release.sh
# (publication to the distribution server); never executed on its own.
#
# The caller defines:
#   REPO_ROOT   repository root
#   VERSION_FILE  path of version.py copied into the archive
#   PY          python interpreter with the repository dependencies
#   OUT_DIR     directory the five artifacts are written to
#   STATIC_DIR  built SPA bundle to package (default: REPO_ROOT/static/dist)
#   info/ok/die output helpers
# and removes STAGE_DIR in its EXIT trap.
#
# Artifacts: boron-{version}.tar.gz, .sha256, .tar.gz.sig, .metadata.json and
# .metadata.json.sig. daemon/updates.py verifies exactly these.

STAGE_DIR=""
TARBALL=""
CHECKSUM_FILE=""
METADATA_FILE=""

stage_tree() {
    local version="$1"
    local static_dir="${STATIC_DIR:-${REPO_ROOT}/static/dist}"
    info "Staging release tree (tracked files only)"
    [[ -f "${static_dir}/index.html" ]] || die "no built frontend at ${static_dir} (index.html missing)"
    STAGE_DIR="$(mktemp -d /tmp/boron-release.XXXXXX)"
    # git archive ships ONLY tracked files: secrets.env / *.db / logs /
    # node_modules / .venv are untracked or gitignored, so they cannot leak
    # into the tarball by construction. The two overlays below are the only
    # not-from-HEAD content. core.autocrlf=false keeps LF line endings.
    git -C "$REPO_ROOT" -c core.autocrlf=false archive --format=tar --prefix="boron-${version}/" HEAD \
        | tar -x -C "$STAGE_DIR"
    local root="${STAGE_DIR}/boron-${version}"
    [[ -d "$root" ]] || die "git archive produced no staging tree"

    # Overlay 1: version.py (release.sh bumps it before committing, so HEAD
    # may still carry the old version here; for a clean tree it is identical).
    cp "$VERSION_FILE" "${root}/version.py"
    # Overlay 2: the built SPA bundle (the committed static/dist may lag the
    # source; releases always ship a bundle built for this version). Modes are
    # normalised because a bundle copied from a Windows mount reads as 0777.
    rm -rf "${root}/static/dist"
    mkdir -p "${root}/static"
    cp -R "$static_dir" "${root}/static/dist"
    find "${root}/static/dist" -type d -exec chmod 0755 {} + -o -type f -exec chmod 0644 {} +

    # Keep the tracked frontend source alongside the signed, freshly built
    # bundle.  The same archive is also a supported fresh-install input and
    # scripts/install.sh deliberately rebuilds the UI from this source.  Git
    # archive already excludes node_modules and other ignored build state.

    # Belt-and-braces: nothing secret-, database- or log-shaped may exist in
    # the staged tree, whatever future .gitignore edits do.
    local leaks
    leaks="$(find "$root" \( -name 'secrets*.env' -o -name '*secrets.env' -o -name '*.db' \
        -o -name '*.db-wal' -o -name '*.db-shm' -o -name '.env' -o -name '*.log' \
        -o -name '.git' -o -name 'node_modules' -o -name '.venv' \) -print | head -5)"
    [[ -z "$leaks" ]] || die "refusing to package -- secret/db/log-shaped paths in stage: ${leaks}"

    ok "staged $(find "$root" -type f | wc -l) files"
}

build_artifacts() {
    local version="$1"
    info "Building tarball + checksum"
    mkdir -p "$OUT_DIR"
    TARBALL="${OUT_DIR}/boron-${version}.tar.gz"
    CHECKSUM_FILE="${OUT_DIR}/boron-${version}.sha256"
    METADATA_FILE="${OUT_DIR}/boron-${version}.metadata.json"
    # Reproducible tarball: stable member order, root ownership, the HEAD
    # commit time on every member and no gzip timestamp, so rebuilding the
    # same commit with the same bundle gives identical bytes.
    local epoch
    epoch="$(git -C "$REPO_ROOT" log -1 --format=%ct HEAD)"
    tar -C "$STAGE_DIR" --sort=name --owner=0 --group=0 --numeric-owner \
        --mtime="@${epoch}" --mode='go-w' -cf - "boron-${version}" | gzip -n -9 > "$TARBALL"
    (cd "$OUT_DIR" && sha256sum "boron-${version}.tar.gz" > "boron-${version}.sha256")
    cp "${REPO_ROOT}/release-metadata.json" "$METADATA_FILE"
    ok "artifacts: ${TARBALL} ($(du -h "$TARBALL" | cut -f1)), $(basename "$CHECKSUM_FILE"), $(basename "$METADATA_FILE")"
}

sign_artifacts() {
    local version="$1"
    local signing_key="${BORON_RELEASE_SIGNING_KEY_FILE:-}"
    [[ -n "$signing_key" && -f "$signing_key" ]] \
        || die "BORON_RELEASE_SIGNING_KEY_FILE must name the protected Ed25519 private key"
    info "Signing release with the pinned Ed25519 publisher key"
    (cd "$REPO_ROOT" && "$PY" -m scripts.sign_release "$version" "$TARBALL" "$signing_key") \
        || die "publisher signature failed -- refusing unsigned release"
    (cd "$REPO_ROOT" && "$PY" -m scripts.sign_release "$version" "$METADATA_FILE" "$signing_key") \
        || die "metadata signature failed -- refusing unsigned release metadata"
    ok "signed: boron-${version}.tar.gz.sig and boron-${version}.metadata.json.sig"
}

verify_artifacts() {
    local version="$1"
    info "Self-verifying artifacts (the same checks the updater will run)"
    (cd "$OUT_DIR" && sha256sum -c "boron-${version}.sha256" >/dev/null) \
        || die "checksum self-verification failed"
    (cd "$REPO_ROOT" && "$PY" - "$version" "$TARBALL" <<'PYEOF'
import sys
from pathlib import Path
from shared.release_signature import archive_digest, verify_signature
version, path = sys.argv[1:]
verify_signature(version, archive_digest(path), Path(path + ".sig").read_bytes())
PYEOF
    ) || die "publisher signature self-verification failed"
    (cd "$REPO_ROOT" && "$PY" - "$version" "$METADATA_FILE" <<'PYEOF'
import sys
from pathlib import Path
from shared.release_metadata import load_release_metadata
from shared.release_signature import archive_digest, verify_signature
version, path = sys.argv[1:]
load_release_metadata(path)
verify_signature(version, archive_digest(path), Path(path + ".sig").read_bytes())
PYEOF
    ) || die "release metadata validation/signature verification failed"
    # Every member must live under the version prefix with no traversal --
    # the update daemon rejects violations, so catch them at build time.
    local bad members
    # Read the compressed listing once; repeatedly invoking tar for every
    # member makes dry-run verification unnecessarily slow on constrained CI.
    members="$(tar -tzf "$TARBALL")"
    bad="$(printf '%s\n' "$members" | grep -Ev "^boron-${version}(/|$)" | head -3 || true)"
    [[ -z "$bad" ]] || die "tarball contains members outside boron-${version}/: ${bad}"
    bad="$(printf '%s\n' "$members" | grep -E '(^/|(^|/)\.\.(/|$))' | head -3 || true)"
    [[ -z "$bad" ]] || die "tarball contains absolute/traversal paths: ${bad}"
    # The packaged version.py must carry exactly the released version.
    local packaged
    packaged="$(tar -xzOf "$TARBALL" "boron-${version}/version.py" \
        | sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p')"
    [[ "$packaged" == "$version" ]] || die "packaged version.py says '${packaged}', expected '${version}'"
    tar -tzf "$TARBALL" "boron-${version}/schema-compat.json" >/dev/null \
        || die "release is missing schema-compat.json"
    tar -tzf "$TARBALL" "boron-${version}/release-metadata.json" >/dev/null \
        || die "release is missing release-metadata.json"
    # And the runtime essentials must be present.
    local member
    for member in api/main.py daemon/server.py shared/config.py \
                  shared/release_signature.py deploy/release-ed25519-public.hex requirements.txt \
                  static/dist/index.html frontend/package.json frontend/package-lock.json \
                  tests/conftest.py deploy/boron-api.service; do
        grep -qxF "boron-${version}/${member}" <<<"$members" \
            || die "tarball is missing ${member}"
    done
    ok "artifacts verified"
}
