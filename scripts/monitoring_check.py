#!/opt/boron/.venv/bin/python
"""Periodic service health check (Run A feature 5).

Run via a system cron every 5 minutes (deploy/boron-monitoring.cron)
-- same root-owned infrastructure-cron category as
scripts/health_snapshot.py. One pass = one `systemctl is-active` per
monitored service, one ServiceCheck history row each, and up<->down
transition alert emails to the configured admin address.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import monitoring  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s monitoring_check: %(message)s")
logger = logging.getLogger("monitoring_check")


def main() -> int:
    result = monitoring.check_services()
    down = [s for s, active in result["services"].items() if not active]
    logger.info(
        "checked %d services; down: %s; alerts sent: %s",
        len(result["services"]),
        ", ".join(down) or "none",
        ", ".join(result["alerts_sent"]) or "none",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
