#!/opt/boron/.venv/bin/python
"""Apply hosting-account inode limits after their warning window."""
import sys
from pathlib import Path

# cron runs this as `/opt/boron/.venv/bin/python /opt/boron/scripts/...`,
# which puts scripts/ (not the release root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon.inode_quotas import enforce_due  # noqa: E402
from shared.db import init_db  # noqa: E402


def main() -> int:
    init_db()
    result = enforce_due({})
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
