#!/opt/boron/.venv/bin/python
"""Revert Boron's pending firewall change from a local root console."""
from __future__ import annotations

import json
import os
import sys
import argparse


if os.geteuid() != 0:
    raise SystemExit("boron-firewall-recover must be run as root")

# The deployed command lives in /usr/local/sbin while the importable panel
# tree is the stable /opt/boron symlink managed by the updater.
sys.path.insert(0, "/opt/boron")

from daemon.firewall import disable_boron_managed_blocks, recover_pending_changes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover Boron-managed UFW changes")
    parser.add_argument(
        "--disable-boron-blocks", action="store_true",
        help="also remove Boron temporary/permanent bans and Cloudflare-only lockdown while leaving UFW enabled",
    )
    args = parser.parse_args()
    results = {"pending_change": recover_pending_changes()}
    if args.disable_boron_blocks:
        results["managed_blocks"] = disable_boron_managed_blocks()
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
