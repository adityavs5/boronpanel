# Releasing Boron

Owner's guide. Boron releases are built and signed on the owner's machine and
published to our own distribution server. GitHub is not used for releases,
installs or updates, and nothing is pushed anywhere.

| Host | Serves |
|---|---|
| `download.boronpanel.com` | `install.sh`, the public signed **source** archives for fresh installs, and the **protected** panel update releases (`/protected/`, reachable only through short-lived `secure_link` URLs) |
| `license.boronpanel.com` | License API. `POST /v1/updates` checks the license and gives a licensed panel short-lived download links for one release |

Both run on `104.234.179.66` (web root `/srv/download`, license server in
`/opt/boron-license`). See [LICENSING-AND-UPDATES.md](LICENSING-AND-UPDATES.md)
for the design.

## What a release contains

`scripts/dist_release.sh` publishes two things from the committed `HEAD`.

**Panel update release** (license-gated) in
`/srv/download/protected/releases/X.Y.Z/`:

| File | What it is |
|---|---|
| `boron-X.Y.Z.tar.gz` | Runtime tree under `boron-X.Y.Z/`: tracked files from `git archive HEAD` plus the prebuilt SPA in `static/dist` |
| `boron-X.Y.Z.sha256` | `sha256sum` output for the archive |
| `boron-X.Y.Z.tar.gz.sig` | 64-byte Ed25519 signature over the version and archive digest |
| `boron-X.Y.Z.metadata.json` | Signed `release-metadata.json`: channel, minimum source version, service reloads, runtime rewrites |
| `boron-X.Y.Z.metadata.json.sig` | Signature of the metadata document |
| `release.json` | Index read by the license server: version, channel, `published_at`, this version's CHANGELOG section, file names, archive size |

`protected/channels/<channel>.json` is a copy of the current `release.json`
for `stable` or `beta`.

**Public source release** (fresh installs) in `/srv/download/releases/X.Y.Z/`:
`boron-X.Y.Z.tar.gz` (plain `git archive HEAD`), `boron-X.Y.Z.manifest` and
its signature, with `channels/<channel>.manifest(.sig)` and `/install.sh`. The
launcher verifies the manifest with the public key embedded in `install.sh`.

Never packaged: the archives come from tracked files only, so `secrets/`,
`master`, `*.db`, logs, `.venv` and `node_modules` cannot get in, and the
build refuses to continue if anything secret-, database- or log-shaped shows
up in the staged tree. `licensing/` is `export-ignore`d.

## Keys

| Key | Where | Used for |
|---|---|---|
| Release signing key | `secrets/release-signing.key` on the owner's machine, plus an offline backup | Both signatures above and the source manifests. Public key `34f15d91f0324d873eb770e668577d36a1d4772232b4f3be938a16a559c43843`, pinned in `deploy/release-ed25519-public.hex` and embedded in `install.sh` |
| Distribution SSH key | `master` on the owner's machine | Root SSH to `104.234.179.66` |

The release key never goes on a server. `dist_release.sh` refuses a key that
does not match `deploy/release-ed25519-public.hex` and `install.sh`.

## Prerequisites

- **Linux or WSL Ubuntu** for the publish step (GNU tar, `sha256sum`,
  `openssl`, `ssh`).
- A Python with the repository dependencies, given as `PYTHON` (for example
  `~/bpaudit/.venv/bin/python` in WSL). The default is `.venv/bin/python`,
  then `python3`.
- Node 20.19+ or 22.12+ where the frontend is built. WSL has no Node, so build
  `static/dist` on Windows and pass `--static-dir static/dist`.
- The SSH key readable only by you. Under WSL, files on `/mnt/c` look
  world-readable and `ssh` rejects them, so copy it once:
  `install -m 600 master ~/.ssh/boron-dist` and set
  `BORON_DIST_SSH_KEY=~/.ssh/boron-dist`.
- The server allows 6 SSH connections per 30 seconds. A publish uses one.

## Cutting a release

1. **Prepare the version.** Write the `## X.Y.Z` section at the top of
   `CHANGELOG.md`; `release.json` carries it to the panels. Review
   `release-metadata.json`: `channel` (`stable` or `beta`),
   `minimum_source_version`, and the declared reloads and rewrites. Then
   either let `scripts/release.sh X.Y.Z` bump `version.py`,
   `frontend/package.json` and `frontend/package-lock.json`, run the full test
   suite, build and verify the artifacts, and commit `release: vX.Y.Z` with a
   local tag; or make those edits by hand, run the tests and commit.
   `release.sh` never pushes or uploads.
2. **Build the frontend** for that commit (on Windows):

   ```bash
   cd frontend && npm ci && npm run build   # writes ../static/dist
   ```

3. **Rehearse** in WSL from the repository root. This builds, signs and
   verifies everything into `dist/release-X.Y.Z/` and connects to nothing:

   ```bash
   export PYTHON=~/bpaudit/.venv/bin/python
   export BORON_DIST_SSH_KEY=~/.ssh/boron-dist
   scripts/dist_release.sh --dry-run --static-dir static/dist
   ```

4. **Publish:**

   ```bash
   scripts/dist_release.sh --static-dir static/dist
   ```

5. **Check it.** `bash install.sh --verify-only` on any Linux machine verifies
   the public channel. On the demo panel (`demo.boronpanel.com`), **Updates →
   Check now** must offer the new version and the update must complete.

Without `--static-dir`, `dist_release.sh` runs `npm ci` and `npm run build`
itself. `--channel` is only a confirmation: it must equal the channel signed
into `release-metadata.json`. `--output-dir DIR` writes the artifacts to an
empty directory instead of `dist/release-X.Y.Z/`. `--help` lists everything,
including `BORON_RELEASE_SIGNING_KEY_FILE`, `BORON_DIST_HOST` and
`BORON_DIST_ROOT`.

## What `dist_release.sh` does

1. **Pre-flight.** Tracked files must be committed; `HEAD` is what ships.
   `version.py` and `frontend/package.json` must agree, `CHANGELOG.md` must
   have the version's section, and the signing key must match the pinned
   public key. The bundle in `--static-dir` must contain the version string,
   so a stale build is refused.
2. **Build** the runtime archive with the steps shared with `release.sh`
   (`scripts/release_lib.sh`): stage `git archive HEAD`, add the SPA, write a
   reproducible tarball (sorted members, root ownership, commit timestamps,
   no gzip timestamp) and its `.sha256`.
3. **Sign** archive and metadata with `scripts/sign_release.py`, then
   **self-verify** both signatures against
   `deploy/release-ed25519-public.hex`, the checksum, member paths, the
   packaged `version.py` and the runtime essentials.
4. **Write `release.json`.**
5. **Build the public source archive and manifest** with the same code as
   `scripts/dist_publish.sh` (`scripts/dist_common.sh`) and self-verify the
   manifest signature.
6. **Upload** everything over one SSH connection into a private staging
   directory under `/srv/download`. The server checks every checksum and
   refuses a version that is already published with different bytes before
   it changes anything. It then moves the protected version directory into
   place in one rename, replaces `protected/channels/<channel>.json`
   atomically, and finally publishes the source archive, its channel manifest
   and `install.sh`.

Published versions are immutable. Re-running the same commit is safe: the
archives are byte-identical, so the server reports "already published with
identical bytes" and keeps the original `release.json`. Any change, even to
the CHANGELOG, needs a new version number. To move a release from beta to
stable, publish a new version with `"channel": "stable"`; the channel is part
of the signed metadata.

## Only the launcher or source archive

`scripts/dist_publish.sh [--channel stable|beta] [--no-channel]` publishes
just the public source archive and `install.sh` for the current `HEAD`, with
the same immutability rules. Use it to ship a launcher fix. It reads the key
from `BORON_SIGNING_KEY`, then `BORON_RELEASE_SIGNING_KEY_FILE`, then
`secrets/release-signing.key`.

## License server

Customer licenses are managed on the server with the `boron-license` CLI
(`issue`, `list`, `show`, `suspend`, `resume`, `rebind`, `update`, `tier`,
`token`); see [licensing/README.md](../licensing/README.md). Deploy changes to
the service by uploading `licensing/` and running `bash
deploy/install_server.sh` there.

`POST /v1/updates` reads `BORON_LICENSE_RELEASES_DIR` (default
`/srv/download/protected`) and signs links with
`BORON_LICENSE_DOWNLOAD_SECRET`, which nginx shares through
`secure_link_md5`. Links last 15 minutes and are bound to the caller's IP.
Without the secret the endpoint answers `503 updates_unavailable`. A
`stable` license gets stable releases; a `beta` license may ask for either.

## How a panel updates

**Updates → Check now** sends the license key and install ID from
`/etc/boron` to `POST https://license.boronpanel.com/v1/updates` for the
panel's channel. A panel without a key reports that a license is needed. The
answer names the version and gives one short-lived URL per file, all under
`update_download_prefix` (`https://download.boronpanel.com/protected/`). The
updater downloads over HTTPS without following redirects, checks size limits,
the SHA-256, the publisher signature with the pinned key and the signed
metadata, then extracts into a staging tree, runs its pre-flight, swaps
`/opt/boron` and rolls back automatically if the health checks fail.

The pre-flight is a focused safety suite (update rules, transactions, RPC,
finalizer rollback and mailbox recovery, about two minutes). The full suite
was already run before the release. Set `update_preflight_full_tests = true`
in `boron.toml` to run the full suite on the panel instead.

`update_channel` in `/etc/boron/boron.toml` (Stable or Beta on the Updates
page) selects the channel. Changing it installs nothing, and switching back
to Stable never downgrades.

## Panels still on 3.2.x

3.2.x panels look for updates on GitHub and trust only the previous release
key, so they never offer 3.3.0 by themselves. 3.2.x was a pre-commercial beta.
Lab and demo servers can be moved with the root-only loopback test source
(`/etc/boron/test-update-override.enabled` plus
`/etc/boron/test-update-source.json` with the new public key, as in
`tests/test_test_update_override.py`) serving the files from
`dist/release-3.3.0/protected/`. Any other 3.2.x server needs a support-led
move; it cannot pick up 3.3.0 from the new servers alone.

## Checks without CI

The repository no longer has hosted CI. Run the same scripts locally (Linux or
WSL) before a release:

```bash
scripts/ci_lint.sh            # ruff correctness rules, schema migrations, JS syntax
scripts/ci_secret_scan.sh     # detect-secrets against .secrets.baseline
scripts/ci_backend.sh         # full backend suite with coverage; BORON_TEST_TMPDIR on a separate filesystem
BORON_CI_ADMIN_PASSWORD="$(openssl rand -base64 30)" \
BORON_CI_CUSTOMER_PASSWORD="$(openssl rand -base64 30)" \
  scripts/ci_e2e.sh           # seeded Playwright smoke (after npm ci, npm run build, npx playwright install chromium)
cd licensing && python -m pytest   # license server
```

`scripts/ci_backend.sh` accepts `BORON_TEST_SHARD` and `BORON_TEST_SHARDS`
to split the suite. `scripts/ci_nightly_privileged.sh` runs the real
installer and host checks and is only for a disposable Ubuntu 24.04 VM, as
root. `scripts/test.sh full` is the usual full run; see
[TESTING.md](TESTING.md).

## Troubleshooting

- **"tracked files have uncommitted changes"**: commit or stash. Untracked
  files are ignored.
- **"signing key does not match deploy/release-ed25519-public.hex"**: wrong
  PEM in `BORON_RELEASE_SIGNING_KEY_FILE`.
- **"the frontend in … was not built for X.Y.Z"**: rebuild `static/dist`
  after the version bump.
- **"already published with different bytes"**: that version is live and
  immutable; bump the version.
- **`ssh` says the key has bad permissions**: copy it to `~/.ssh` with mode
  0600 (see Prerequisites).
- **Upload interrupted**: re-run the same command. Nothing is published until
  every file has been checked, and identical files are accepted.
