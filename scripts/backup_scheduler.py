#!/opt/boron/.venv/bin/python
"""Periodic scheduled-backup trigger (Phase 2 feature 7).

Run via a system cron (README/CHECKPOINT-phase2-7.md), not a per-account
Boron-managed crontab (feature 2's daemon/cron.py) -- this needs to
run mysqldump/tar across every hosting account and talk to borond's
own backup engine directly, the same trust level as borond itself.

Standalone, not part of borond's running process, same reasoning as
scripts/ssl_deploy_hook.py and scripts/usage_snapshot.py: cron fires this
independently of whether the daemon happens to be up.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import backup  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s backup_scheduler: %(message)s")
logger = logging.getLogger("backup_scheduler")


def main() -> int:
    triggered = backup.run_scheduled_backups()
    logger.info("triggered %d scheduled backup(s)", triggered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
