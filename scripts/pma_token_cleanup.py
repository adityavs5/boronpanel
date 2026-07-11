#!/opt/boron/.venv/bin/python
"""Periodic phpMyAdmin signon-token cleanup (Phase 3 feature 3).

Run via a system cron (README), same category as usage_snapshot.py/
backup_scheduler.py -- server infrastructure, not a customer-facing
resource. Drops the ephemeral MariaDB user (and any still-present token
file) for every expired PmaToken row, whether or not it was ever actually
redeemed -- otherwise a never-redeemed token leaves a throwaway MariaDB
user behind forever.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import pma  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s pma_token_cleanup: %(message)s")
logger = logging.getLogger("pma_token_cleanup")


def main() -> int:
    count = pma.cleanup_expired_tokens()
    logger.info("cleaned up %d expired phpMyAdmin token(s)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
