#!/opt/boron/.venv/bin/python
"""Periodic license refresh (deploy/boron-license.cron, twice a day).

Refreshes the signed license document from license.boronpanel.com. With
``--jitter`` it first sleeps a random 0-30 minutes so installations do not all
call the license server at the same moment.
"""
from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import license_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s license_check: %(message)s")
logger = logging.getLogger("license_check")


def main(argv: list[str]) -> int:
    if "--jitter" in argv:
        time.sleep(random.SystemRandom().uniform(0, 1800))
    result = license_client.refresh()
    logger.info("state=%s reason=%s last_error=%s", result["state"], result["reason"], result["last_error"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
