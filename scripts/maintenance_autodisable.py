#!/opt/boron/.venv/bin/python
"""Maintenance-mode auto-disable sweep (missing-features batch, goal
feature 2's "auto-disable timer" requirement).

Run via a system cron (deploy/boron-maintenance.cron), same "server
infrastructure, not a per-account crontab" category as
scripts/usage_snapshot.py. Frequent (every 5 minutes) since the goal's own
shortest preset is 1 hour -- a 5-minute sweep interval keeps the "site comes
back automatically" promise reasonably tight without polling excessively.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import handlers_maintenance  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s maintenance_autodisable: %(message)s")
logger = logging.getLogger("maintenance_autodisable")


def main() -> int:
    result = handlers_maintenance.sweep_expired()
    if result["disabled"]:
        logger.info("auto-disabled maintenance mode for: %s", ", ".join(result["disabled"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
