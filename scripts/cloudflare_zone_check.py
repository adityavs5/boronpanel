#!/opt/forgehost/.venv/bin/python
"""Periodic Cloudflare pending-zone activation poll (docs/PLAN-cloudflare.md
Phase 1).

Run via system cron every 15 minutes, same trust level and pattern as
scripts/health_snapshot.py. A customer flips their registrar NS whenever
they get around to it -- this poll detects the pending->active transition
(and runs the resync barrier + routing flip via cloudflare_ops.zone_status)
without anyone needing to visit the panel. Exits immediately when no zone
is pending, so the steady-state cost with Cloudflare unused is one SELECT.

Suggested /etc/cron.d/forgehost-cloudflare entry:
  */15 * * * * root /opt/forgehost/scripts/cloudflare_zone_check.py >> /var/log/forgehost/cloudflare-zone-check.log 2>&1
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import cloudflare_ops  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s cloudflare_zone_check: %(message)s")
logger = logging.getLogger("cloudflare_zone_check")


def main() -> int:
    activated = cloudflare_ops.reconcile_pending_zones()
    if activated:
        logger.info("%d zone(s) newly active on Cloudflare", activated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
