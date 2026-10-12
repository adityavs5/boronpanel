#!/opt/boron/.venv/bin/python
"""Delete expired one-time support bundles in bounded batches."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import support_bundle  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s support_bundle_retention: %(message)s")


def main() -> int:
    result = support_bundle.prune_expired()
    logging.info("expired %d support bundle job(s); removed %d archive(s)",
                 result["expired"], result["files_removed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
