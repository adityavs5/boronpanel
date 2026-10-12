#!/usr/bin/env python3
"""Apply hosting-account inode limits after their warning window."""
from daemon.inode_quotas import enforce_due
from shared.db import init_db


def main() -> int:
    init_db()
    result = enforce_due({})
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
