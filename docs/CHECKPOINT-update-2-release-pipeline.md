# CHECKPOINT — Panel Update System, feature 2: GitHub releases pipeline

Date: 2026-07-10. `scripts/release.sh` + `docs/RELEASING.md` +
`tests/test_release.py`.

## What was built

`scripts/release.sh` (shellcheck-clean, executable): bump → test → build →
stage → tarball+SHA256 → optional GPG sign → self-verify → publish. Same
helper/dry-run idiom as install.sh. Full step order and asset contract
documented in docs/RELEASING.md.

## Decisions worth recording

- **Tarball = `git archive HEAD` + two overlays** (the bumped `version.py`
  and the freshly built `static/dist`). Tracked-files-only means
  secrets/DB/logs/venv/node_modules *cannot* leak by construction — they
  are untracked. A belt-and-braces `find` still refuses to package
  anything secret/db/log-shaped, and the self-verify step re-checks the
  finished tarball (prefix confinement, no `..`/absolute members, packaged
  version.py matches, runtime essentials present) — deliberately the same
  checks the update daemon (feature 4) runs, so a bad tarball dies at
  build time, not on a production box.
- **`frontend/` source is dropped from the tarball** — only the built
  bundle ships. Runtime never needs the source; install.sh's
  build-if-missing branch is not triggered since `static/dist` ships.
- **`tests/` ARE shipped** — the update job's pre-flight ("abort if test
  suite failing") runs the installed tree's own suite; that requires the
  tests to exist on production. They're code, not secrets.
- **Bump ordering**: version.py is bumped *before* tests/build (artifacts
  must carry the new version — the SPA bakes it in at build time), but the
  release commit happens *last*; an EXIT trap reverts the bump on any
  earlier failure. In `--dry-run` nothing is bumped and rehearsal
  artifacts carry the current version (stated in the output).
- **GPG**: signs only if a secret key exists; a locked/failing key warns
  and continues unsigned rather than blocking a release (goal: "GPG-sign
  if key exists" — optional extra, not a gate).
- **This box has no git remote and no `gh`** (verified) — publish-path
  guards (`gh auth status`, origin/`--repo` resolution, clean-tree check)
  die with actionable messages; `--dry-run` is fully offline and never
  touches gh/git-push/network.
- `dist/` added to .gitignore (default artifact output).

## Tests

`tests/test_release.py` (8): executable, `bash -n`, shellcheck, `--help`,
unknown-flag, malformed-version rejection, **functional offline dry-run**
(with `--skip-tests --skip-build` — pytest can't recurse into itself, npm
not assumed) asserting the tarball verifies against its checksum, is
prefix-confined with no traversal members, contains the runtime essentials
incl. tests/ and static/dist, and leaks no secrets/db/log/dev trees; and a
dry-run-mutates-nothing test pinning that version.py is untouched.

## Honestly open

- The **full** `release.sh --dry-run` (real pytest run + real `npm ci`
  build) is deferred to the end-of-goal verification pass — it takes
  ~15 min and the SPA is still growing (features 3/7 add UI). It is a
  DONE-WHEN item and will be run before STATUS.md is finalized.
- `gh release create` was obviously not executed anywhere (no gh, no
  remote, and publishing is an outward-facing action) — the publish path
  is code-reviewed + guarded, not live-tested. First real release should
  be cut with `--dry-run` first, per RELEASING.md.
