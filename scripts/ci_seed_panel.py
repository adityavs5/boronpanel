#!/usr/bin/env python3
"""Seed the disposable CI database without printing credentials."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from testsupport.seeded_panel import seed_panel_database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    admin_password = os.environ.get("BORON_CI_ADMIN_PASSWORD", "")
    customer_password = os.environ.get("BORON_CI_CUSTOMER_PASSWORD", "")
    if not admin_password or not customer_password:
        parser.error("BORON_CI_ADMIN_PASSWORD and BORON_CI_CUSTOMER_PASSWORD are required")
    seeded = seed_panel_database(
        args.database,
        admin_password=admin_password,
        customer_password=customer_password,
    )
    print(
        f"Seeded database with admin {seeded.admin_username!r}, "
        f"account {seeded.account_username!r}, and domain {seeded.primary_domain!r}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
