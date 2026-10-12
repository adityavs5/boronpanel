#!/opt/boron/.venv/bin/python
"""Daily, bounded retention sweep for control-plane history."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import retention  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s audit_retention: %(message)s")
logger = logging.getLogger("audit_retention")


def main() -> int:
    result = retention.prune()
    if result["held"]:
        logger.warning("retention skipped: investigation hold is active")
    else:
        logger.info("bounded retention completed: removed=%s batch=%d", result["removed"], result["batch_size"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
