# Checkpoint — release pipeline and secret sweep (2026-08-06)

No GitHub push or live deployment was performed.

## Release pipeline

- `version.py` remains `BORON_VERSION = "1.0.0"`.
- `scripts/release.sh` produces `boron-{version}.tar.gz`, a matching
  `.sha256`, and an optional `.asc` when a GPG secret key is available.
- Staging uses `git archive` plus the current built SPA and rejects secret-,
  database-, log-, dependency-, and traversal-shaped paths before packaging.
- The update system repository is `adityavs5/boronpanel` in the runtime
  default, `deploy/boron.toml.example`, and README instructions.
- The dry-run rehearsal reached artifact creation and self-verification in
  this restricted runner; the long full-suite/build process is separately
  constrained by the runner's short command lifetime. A `--skip-tests
  --skip-build` rehearsal was used only for local artifact inspection.

## Secret sweep

- Current tracked files were scanned for private-key headers, cloud/API token
  formats, and literal credential assignments.
- Git history (94 reachable commits) was searched for the same high-confidence
  credential signatures.
- No real credentials were found. Test fixtures and `*.example` templates
  contain only generated/test values or placeholders.

## Configuration template and ignore policy

- Added root `.env.example` with placeholders for paths, ports, repository,
  integrations, and secret names.
- `.gitignore` covers `.env`/`secrets.env`, key/certificate files, SQLite and
  database sidecars, Python/Node dependencies, built dist/release artifacts,
  and `/var/lib`/`/var/log` runtime paths.
