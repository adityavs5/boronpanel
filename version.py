"""Boron version — the single source of truth.

Everything that displays or compares a panel version imports this constant:
the API (`GET /api/v1/version`, sidebar footer, admin dashboard), the daemon
(`update.check` comparison against the release offered by the license
server), the installer (`scripts/install.sh` greps it out at print time), and
the release tooling (`scripts/release.sh` bumps it, `scripts/dist_release.sh`
publishes it). Kept at the repo root, dependency-free and
import-side-effect-free, so any process (including the stdlib-only update
finalizer) can read it without pulling in the rest of the codebase.

Format: semantic versioning MAJOR.MINOR.PATCH. scripts/release.sh rewrites
the line below with a plain regex — keep it a simple single-line string
assignment.
"""

BORON_VERSION = "3.6.4"
