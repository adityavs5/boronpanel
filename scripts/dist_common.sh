# shellcheck shell=bash
#
# Shared distribution-server steps for scripts/dist_publish.sh (public signed
# source archive and launcher) and scripts/dist_release.sh (license-gated
# runtime release plus the public source archive). Sourced, never executed.
# The caller defines die() and runs from the repository root.
#
# Server layout under the web root (default /srv/download):
#   install.sh                                   launcher (public)
#   channels/<channel>.manifest(.sig)            public source pointer
#   releases/<ver>/boron-<ver>.tar.gz            public signed source archive
#   releases/<ver>/boron-<ver>.manifest(.sig)
#   protected/releases/<ver>/...                 runtime release (secure_link only)
#   protected/channels/<channel>.json            runtime release index pointer
#
# An upload directory holds public/ and/or protected/ (each with a SHA256SUMS
# file) plus apply.sh, and is unpacked and applied over ONE ssh connection.

# Build and sign the public source archive for HEAD into $1.
# Arguments: out_dir version channel signing_key
dist_build_public() {
    local out="$1" version="$2" channel="$3" signing_key="$4"
    local file="boron-${version}.tar.gz" commit sha256 size embedded
    [[ -r "$signing_key" ]] || die "signing key not readable: $signing_key"
    command -v openssl >/dev/null 2>&1 || die 'openssl is required'
    # The launcher embeds the public key; refuse to sign with a key it would reject.
    embedded="$(sed -n "/^readonly SIGNING_PUBLIC_KEY='/,/-----END PUBLIC KEY-----'/p" install.sh \
        | sed "s/^readonly SIGNING_PUBLIC_KEY='//; s/'$//")"
    [[ "$embedded" == "$(openssl pkey -in "$signing_key" -pubout | tr -d '\r')" ]] \
        || die 'signing key does not match the public key embedded in install.sh'

    mkdir -p "$out"
    commit="$(git rev-parse HEAD)"
    # core.autocrlf=false keeps LF line endings even when packaging on Windows.
    # gzip -n leaves out the timestamp, so the same commit gives the same bytes.
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
    local manifest="$out/boron-${version}.manifest"
    openssl pkeyutl -sign -inkey "$signing_key" -rawin -in "$manifest" -out "$manifest.sig"
    openssl pkey -in "$signing_key" -pubout > "$out/.signing.pub"
    openssl pkeyutl -verify -pubin -inkey "$out/.signing.pub" -rawin -in "$manifest" -sigfile "$manifest.sig" >/dev/null \
        || die 'self-verification of the new manifest signature failed'
    rm -f "$out/.signing.pub"
    cp install.sh "$out/install.sh"
    (cd "$out" && sha256sum "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" install.sh > SHA256SUMS)
}

# Write the server-side script that applies an upload directory.
dist_write_apply() {
    cat > "$1" <<'REMOTE'
# Runs on the distribution server from the unpacked upload directory.
# Arguments: dist_root version channel update_channel
# Everything is verified before anything is published. Published versions
# are immutable: identical bytes are accepted, different bytes are refused.
set -euo pipefail
dist_root="$1" version="$2" channel="$3" update_channel="$4"
file="boron-${version}.tar.gz"
fail() { echo "error: $*" >&2; exit 1; }
artifacts=("$file" "boron-${version}.sha256" "${file}.sig"
           "boron-${version}.metadata.json" "boron-${version}.metadata.json.sig")

# 1. Verify the upload and check immutability.
if [[ -d protected ]]; then
    (cd protected && sha256sum -c --status SHA256SUMS) || fail 'protected upload corrupted'
    for f in "${artifacts[@]}" release.json; do
        [[ -f "protected/$f" ]] || fail "protected upload is missing $f"
    done
    prot_dir="${dist_root}/protected/releases/${version}"
    for f in "${artifacts[@]}"; do
        if [[ -e "${prot_dir}/$f" ]] && ! cmp -s "${prot_dir}/$f" "protected/$f"; then
            fail "version ${version} is already published with different bytes ($f); bump version.py"
        fi
    done
fi
if [[ -d public ]]; then
    (cd public && sha256sum -c --status SHA256SUMS) || fail 'public upload corrupted'
    pub_dir="${dist_root}/releases/${version}"
    if [[ -e "${pub_dir}/${file}" ]] && ! cmp -s "${pub_dir}/${file}" "public/${file}"; then
        fail "source archive ${version} is already published with different bytes; bump version.py"
    fi
fi

# 2. Protected runtime release, then its channel pointer.
if [[ -d protected ]]; then
    install -d -m 0755 "${dist_root}/protected" "${dist_root}/protected/releases" "${dist_root}/protected/channels"
    rm -f protected/SHA256SUMS
    chmod 0644 protected/*
    chmod 0755 protected
    if [[ ! -e "$prot_dir" ]]; then
        # One rename makes the whole version directory appear at once.
        mv -T protected "$prot_dir"
        echo "Published protected release ${version}."
    else
        # Complete a partially published directory; never replace a file.
        for f in "${artifacts[@]}" release.json; do
            [[ -e "${prot_dir}/$f" ]] || mv "protected/$f" "${prot_dir}/$f"
        done
        echo "Protected release ${version} is already published with identical bytes."
    fi
    if [[ "$update_channel" == true ]]; then
        channels="${dist_root}/protected/channels"
        cp "${prot_dir}/release.json" "${channels}/.${channel}.json.new"
        chmod 0644 "${channels}/.${channel}.json.new"
        mv -f "${channels}/.${channel}.json.new" "${channels}/${channel}.json"
        echo "Update channel ${channel} now points to ${version}."
    fi
fi

# 3. Public source archive, its channel pointer and the launcher.
if [[ -d public ]]; then
    cd public
    chmod 0644 "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" install.sh
    if [[ -e "${pub_dir}/${file}" ]]; then
        echo "Source archive ${version} is already published with identical bytes."
    else
        install -d -m 0755 "${dist_root}/releases" "$pub_dir"
        mv "$file" "boron-${version}.manifest" "boron-${version}.manifest.sig" "$pub_dir/"
        echo "Published source archive ${version}."
    fi
    if [[ "$update_channel" == true ]]; then
        # Rename into place so a reader never sees a manifest without its signature for long.
        channels="${dist_root}/channels"
        install -d -m 0755 "$channels"
        cp "${pub_dir}/boron-${version}.manifest.sig" "${channels}/.${channel}.manifest.sig.new"
        cp "${pub_dir}/boron-${version}.manifest" "${channels}/.${channel}.manifest.new"
        chmod 0644 "${channels}/.${channel}.manifest.sig.new" "${channels}/.${channel}.manifest.new"
        mv -f "${channels}/.${channel}.manifest.sig.new" "${channels}/${channel}.manifest.sig"
        mv -f "${channels}/.${channel}.manifest.new" "${channels}/${channel}.manifest"
        echo "Install channel ${channel} now points to ${version}."
    fi
    mv -f install.sh "${dist_root}/install.sh"
    echo 'Launcher uploaded: https://download.boronpanel.com/install.sh'
fi
REMOTE
}

# Upload an upload directory and apply it over a single ssh connection (the
# server rate-limits SSH). Arguments: upload_dir host ssh_key dist_root version
# channel update_channel. All arguments must already be validated.
dist_upload() {
    local up="$1" host="$2" ssh_key="$3" dist_root="$4" version="$5" channel="$6" update_channel="$7"
    local members=(apply.sh)
    [[ -d "$up/protected" ]] && members+=(protected)
    [[ -d "$up/public" ]] && members+=(public)
    # shellcheck disable=SC2029  # remote arguments expand locally on purpose
    tar -C "$up" --owner=0 --group=0 --numeric-owner -cf - "${members[@]}" \
        | ssh -i "$ssh_key" -o IdentitiesOnly=yes -o BatchMode=yes "$host" \
            "set -e; up=\$(mktemp -d '${dist_root}/.upload-XXXXXXXX'); \
            trap 'rm -rf -- \"\$up\"' EXIT; tar -C \"\$up\" --no-same-owner -xf -; cd \"\$up\"; \
            bash apply.sh '${dist_root}' '${version}' '${channel}' '${update_channel}'"
}
