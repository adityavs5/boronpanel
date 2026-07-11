#!/opt/boron/.venv/bin/python
"""Periodic SSL-expiry notification check (Phase 7b feature 3).

Run via a system cron (README), same "server infrastructure, not a
per-account Boron-managed crontab" category as scripts/usage_snapshot.py/
scripts/backup_scheduler.py. Daily is enough: the notification window is 14
days, so even a missed day or two of a down cron still leaves ample warning.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import ssl  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ssl_expiry_check: %(message)s")
logger = logging.getLogger("ssl_expiry_check")


def main() -> int:
    sent = ssl.check_expiring_certificates()
    logger.info("sent %d expiry notice(s)", sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
