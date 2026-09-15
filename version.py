"""Boron version — the single source of truth.

Everything that displays or compares a panel version imports this constant:
the API (`GET /api/v1/version`, sidebar footer, admin dashboard), the daemon
(`update.check` comparison against GitHub releases), the installer
(`scripts/install.sh` greps it out at print time), and the release pipeline
(`scripts/release.sh` bumps it). Kept at the repo root, dependency-free and
import-side-effect-free, so any process (including the stdlib-only update
finalizer) can read it without pulling in the rest of the codebase.

Format: semantic versioning MAJOR.MINOR.PATCH. scripts/release.sh rewrites
the line below with a plain regex — keep it a simple single-line string
assignment.
"""

BORON_VERSION = "1.4.0"
