#!/opt/boron/.venv/bin/python
"""One-time bootstrap: create the first admin panel user.

Run manually after first install (see README) -- there is no other way to
get an initial login, by design (no hardcoded default credentials, a direct
response to CyberPanel's own documented history of insecure install
defaults, RESEARCH.md SS5).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import init_db  # noqa: E402
from daemon.handlers_auth import create_panel_user  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first Boron admin user")
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--password-stdin", action="store_true",
        help="read the password from stdin instead of prompting (for unattended installation)",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("Failed: initial administrator creation requires root", file=sys.stderr)
        return 1

    if args.password_stdin:
        password = sys.stdin.read(257)
        if len(password) > 256:
            parser.error("password from stdin exceeds 256 characters")
    else:
        password = getpass.getpass("Password (min 12 chars): ")

    try:
        init_db()
        result = create_panel_user({"username": args.username, "password": password, "role": "admin"})
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created admin panel user '{result['username']}' (id={result['id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
