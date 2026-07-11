#!/opt/forgehost/.venv/bin/python
"""Daily site-statistics refresh (missing-features batch, goal feature 6).

Run via a system cron (deploy/forgehost-sitestats.cron), same "server
infrastructure, not a per-account crontab" category as
scripts/usage_snapshot.py -- keeps SiteStatsDaily accumulating on a real
daily cadence even if nobody opens the stats tab in the panel.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import sitestats  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s sitestats_snapshot: %(message)s")
logger = logging.getLogger("sitestats_snapshot")


def main() -> int:
    count = sitestats.refresh_all()
    logger.info("refreshed site stats for %d domain(s)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
