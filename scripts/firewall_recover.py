#!/opt/boron/.venv/bin/python
"""Revert Boron's pending firewall change from a local root console."""
from __future__ import annotations

import json
import os
import sys


if os.geteuid() != 0:
    raise SystemExit("boron-firewall-recover must be run as root")

# The deployed command lives in /usr/local/sbin while the importable panel
# tree is the stable /opt/boron symlink managed by the updater.
sys.path.insert(0, "/opt/boron")

from daemon.firewall import recover_pending_changes  # noqa: E402


def main() -> int:
    result = recover_pending_changes()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
