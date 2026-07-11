#!/opt/boron/.venv/bin/python
"""Periodic account usage-alert check (Phase 7b feature 5).

Run via a system cron (README), same "server infrastructure" category as
scripts/usage_snapshot.py -- usage.get_usage() (called internally per
account) already lazily refreshes stale usage data itself, so this needs
no explicit ordering relative to usage_snapshot.py's own cron entry.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import usage_alerts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s usage_alert_check: %(message)s")
logger = logging.getLogger("usage_alert_check")


def main() -> int:
    triggered = usage_alerts.check_all_accounts()
    logger.info("%d account(s) got a new usage alert this pass", triggered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
