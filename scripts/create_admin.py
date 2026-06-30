#!/opt/forgehost/.venv/bin/python
"""One-time bootstrap: create the first admin panel user.

Run manually after first install (see README) -- there is no other way to
get an initial login, by design (no hardcoded default credentials, a direct
response to CyberPanel's own documented history of insecure install
defaults, RESEARCH.md SS5).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.rpc import RpcClient, RpcError  # noqa: E402
from shared.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first Forgehost admin user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", help="omit to be prompted (recommended -- avoids shell history)")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password (min 8 chars): ")

    client = RpcClient(settings.rpc_socket)
    try:
        result = client.call("panel_user.create", username=args.username, password=password, role="admin", _actor="bootstrap-cli", _role="admin")
    except RpcError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created admin panel user '{result['username']}' (id={result['id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
