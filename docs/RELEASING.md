# Releasing Boron

Boron ships as **versioned release tarballs attached to GitHub
releases**. Production servers never `git pull` — they consume these
tarballs through the panel's built-in update system (or an operator
downloads one manually). The pinned Ed25519 publisher signature and SHA256
checksum are verified before a single byte is extracted.

## The artifacts

Every release `vX.Y.Z` carries:

| Asset | What it is |
|---|---|
| `boron-X.Y.Z.tar.gz` | The deployable tree: `api/`, `daemon/`, `shared/`, `scripts/`, `deploy/`, `templates/`, `static/` (with the **freshly built** SPA bundle in `static/dist`), `tests/` (used by the updater's pre-flight), `docs/`, `version.py`, `requirements.txt`, `pytest.ini`, `README.md` — all under a single `boron-X.Y.Z/` top-level directory. |
| `boron-X.Y.Z.sha256` | `sha256sum` output for the tarball. The update daemon aborts on any mismatch. |
| `boron-X.Y.Z.tar.gz.sig` | Mandatory detached Ed25519 signature over the release version and archive SHA256 digest. |

What is **never** in the tarball, by construction: the tarball is built
from `git archive HEAD` (tracked files only), so `secrets.env`,
`api-secrets.env`, `*.db`, logs, `.venv`, `node_modules` and `.git` cannot
leak into it — none of those are tracked. `scripts/release.sh` additionally
refuses to package if anything secret/database/log-shaped is found in the
staged tree, and `tests/test_release.py` pins the same invariants. The
`frontend/` source tree is dropped from the tarball (only the built bundle
ships); everything else tracked in git is included.

## Prerequisites (release machine, not production)

- A clean checkout with the `origin` remote pointing at the GitHub repo
  (or pass `--repo OWNER/REPO` / set `FH_RELEASE_REPO`).
- [`gh`](https://cli.github.com/) installed and authenticated
  (`gh auth login`) with permission to create releases.
- Node 20.19+ or 22.12+ / npm (frontend build) and the repo `.venv` (test suite).
- Set `BORON_RELEASE_SIGNING_KEY_FILE` to the protected Ed25519 private PEM
  matching `deploy/release-ed25519-public.hex`. The private PEM must never
  enter Git, release archives, backups shipped with the panel, or logs. Keep
  a protected off-host recovery copy; loss of the key requires an explicit
  operator-managed trust-key migration.

## Cutting a release

```bash
# 1. Rehearse -- runs the FULL pipeline (tests, frontend build, tarball,
#    checksum, self-verification) but bumps nothing and publishes nothing:
scripts/release.sh --dry-run

# 2. Release for real:
scripts/release.sh --patch     # 1.0.0 -> 1.0.1 (default)
scripts/release.sh --minor     # 1.0.0 -> 1.1.0
scripts/release.sh --major     # 1.0.0 -> 2.0.0
scripts/release.sh 2.1.0       # explicit target version
```

A real run, in order:

1. **Pre-flight** — required tools, `gh auth status`, repo resolution, and
   a **clean working tree** (the tarball is built from `HEAD`; uncommitted
   changes would silently not ship).
2. **Bump** — rewrites `version.py` (the single source of truth) and syncs
   `frontend/package.json`. If any later step fails before the release
   commit, the bump is automatically reverted.
3. **Tests** — full `pytest` suite; any failure aborts the release.
4. **Frontend build** — `npm ci && npm run build` into `static/dist`, so
   the bundle always matches the source being released (the SPA bakes the
   version in from `version.py` at build time).
5. **Stage** — `git archive HEAD` into a temp dir under a
   `boron-X.Y.Z/` prefix, overlaying the bumped `version.py` and the
   freshly built `static/dist`; drops `frontend/`; refuses to continue if
   anything secret-shaped is present.
6. **Artifacts** — deterministic-ish tarball (sorted members, root
   ownership) into `dist/` (gitignored; override with `--output-dir`),
   plus the `.sha256`.
7. **Sign** — mandatory detached Ed25519 signature, bound to version and
   digest; aborts if the private key is missing or mismatched.
8. **Self-verify** — Ed25519 signature and `sha256sum -c`, no members outside the version
   prefix, no absolute/`..` paths, packaged `version.py` matches, runtime
   essentials present. These mirror the checks the update daemon runs.
9. **Publish** — commit `release: vX.Y.Z`, annotated tag `vX.Y.Z`, push,
   `gh release create` with all assets attached.

## Flags you should rarely use

- `--skip-tests` / `--skip-build` — local dry-run debugging only (used by
  `tests/test_release.py`, which can't run pytest inside pytest). The release
  command rejects either flag for a published release.
- `--output-dir DIR` — relocate artifacts (default `dist/`).

## How production consumes a release

The daemon's `update.check` op polls
`https://api.github.com/repos/OWNER/REPO/releases/latest` (the repo comes
from `update_github_repo` in `/etc/boron/boron.toml`), compares
`tag_name` against the running `version.py`, and surfaces "update
available" in the admin UI. The one-click update job downloads the tarball
**only** from `https://github.com/OWNER/REPO/releases/download/...`,
verifies the pinned publisher signature and SHA256 against the `.sha256`
asset before extraction,
validates every tarball member against path traversal, stages into
`/opt/boron-X.Y.Z/`, and atomically swaps the `/opt/boron`
symlink. See the update-system checkpoints in `docs/` for the full design.

The updater first runs a focused safety suite against the live install. This
checks update/download rules, transaction boundaries, RPC, finalizer rollback,
and mailbox recovery in about two minutes. It does not repeat the full suite
because the release command has already run that suite against the new code.
Set `update_preflight_full_tests = true` in `boron.toml` to restore the legacy
full live preflight.

## Troubleshooting

- **"gh is not authenticated"** — `gh auth login`, or export
  `GH_TOKEN`.
- **"working tree is dirty"** — commit or stash; the tarball ships `HEAD`.
- **"no 'origin' remote"** — `git remote add origin git@github.com:OWNER/REPO.git`
  or pass `--repo OWNER/REPO`.
- **Signing key missing or mismatched** — provide the correct protected PEM
  through `BORON_RELEASE_SIGNING_KEY_FILE`; unsigned publication is refused.
- A failed release run reverts the version bump and removes its temp
  staging dir; artifacts already written to `dist/` are safe to delete.
