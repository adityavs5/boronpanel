#!/opt/forgehost/.venv/bin/python
"""Daily panel-update check (panel update system, feature 3).

Run via a system cron once a day (deploy/forgehost-update.cron) -- same
root-owned infrastructure-cron category as scripts/monitoring_check.py.
One pass = one forced GitHub releases check (bypasses the 1h cache), an
admin email if a NEW release appeared (deduped per version via
UpdateState.last_notified_version -- one email per release, not one per
day forever), and pruning of /opt/forgehost-X.Y.Z version dirs older than
the rollback window (update_keep_old_days, default 3).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import updates  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s update_check: %(message)s")
logger = logging.getLogger("update_check")


def main() -> int:
    result = updates.notify_if_update_available()
    if not result["configured"]:
        logger.info("update checks not configured (update_github_repo unset) -- nothing to do")
    else:
        logger.info(
            "current=%s latest=%s update_available=%s notified=%s error=%s",
            result["current_version"], result["latest_version"],
            result["update_available"], result["notified"], result["error"],
        )
    cleanup = updates.cleanup_old_versions()
    if cleanup["removed"]:
        logger.info("pruned old version dirs: %s", ", ".join(cleanup["removed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
