#!/opt/boron/.venv/bin/python
"""Periodic usage-snapshot refresh (Phase 2 feature 5).

Run via a system cron (README/CHECKPOINT-phase2-5.md), not a per-account
Boron-managed crontab (daemon/cron.py, feature 2) -- this is server
infrastructure, same category as the SSL deploy-hook script, not a
customer-facing resource. Keeps usage_snapshots/bandwidth_daily
accumulating on a real ~15-min cadence even if nobody opens the usage
page in the panel (get_usage()'s own lazy on-read refresh only fires when
someone actually looks).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import usage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s usage_snapshot: %(message)s")
logger = logging.getLogger("usage_snapshot")


def main() -> int:
    count = usage.refresh_all_accounts()
    logger.info("refreshed usage snapshots for %d account(s)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
