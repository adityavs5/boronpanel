#!/opt/boron/.venv/bin/python
"""Periodic Cloudflare edge-range refresh (docs/PLAN-cloudflare.md Phase 2,
features 3+4).

Run via system cron daily, same trust level and pattern as
scripts/cloudflare_zone_check.py. Fetches Cloudflare's published edge IP
ranges (GET /ips), materializes them to settings.cloudflare_ranges_file, and
-- only when they changed -- re-renders the OLS real-IP trusted list and the
fail2ban ignoreip and reloads both. Cloudflare changes these ranges rarely,
so the steady-state cost is one HTTPS GET and a file compare.

The ranges file is written BEFORE either consumer is reloaded (goal rule:
"CF ranges file must exist before OLS reload or fail2ban restart").

Suggested /etc/cron.d/boron-cloudflare entry (see also the zone-check
line in scripts/cloudflare_zone_check.py):
  17 4 * * * root /opt/boron/scripts/cloudflare_ranges.py >> /var/log/boron/cloudflare-ranges.log 2>&1
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import cloudflare_ops  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s cloudflare_ranges: %(message)s")
logger = logging.getLogger("cloudflare_ranges")


def main() -> int:
    result = cloudflare_ops.refresh_ranges({})
    logger.info(
        "ranges: %d ipv4, %d ipv6, changed=%s, ols_reloaded=%s, fail2ban_updated=%s",
        result["ipv4_count"], result["ipv6_count"], result["changed"],
        result["ols_reloaded"], result["fail2ban_updated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
