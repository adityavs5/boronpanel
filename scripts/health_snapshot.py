#!/opt/boron/.venv/bin/python
"""Periodic server-health snapshot (Phase 5 feature 1).

Run via a system cron, same trust level and pattern as
scripts/usage_snapshot.py: appends one HealthSnapshot row every ~60s so
the health dashboard's 24h graphs have real history to draw even if
nobody has the dashboard open (its own live view never writes a row --
only this script does).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import health  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s health_snapshot: %(message)s")
logger = logging.getLogger("health_snapshot")


def main() -> int:
    health.take_snapshot()
    logger.info("took health snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
