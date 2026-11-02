#!/opt/boron/.venv/bin/python
"""Periodic mail local-delivery sweep (deploy/boron-mail-routing.cron).

Switches a pending mail domain to local delivery once its public MX points
at this server, and switches off any mail domain on the public-provider
deny-list. See daemon/mail_local_delivery.py for the rule and
docs/ADMIN-GUIDE.md for the operator view.

  --recheck-active  also switch off active local-mode domains whose public
                    MX definitively points elsewhere (one-off audit; DNS
                    lookup errors never change a domain)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon import mail_local_delivery  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s mail_local_delivery: %(message)s")
logger = logging.getLogger("mail_local_delivery_reconcile")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recheck-active", action="store_true")
    args = parser.parse_args(argv)
    report = mail_local_delivery.reconcile(recheck_active=args.recheck_active)
    if report["activated"] or report["deactivated"] or report["errors"] or args.recheck_active:
        logger.info("%s", json.dumps(report, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
