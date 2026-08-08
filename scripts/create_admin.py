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
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.rpc import RpcClient, RpcError  # noqa: E402
from shared.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first Boron admin user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", help="omit to be prompted (recommended -- avoids shell history)")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password (min 12 chars): ")

    client = RpcClient(settings.rpc_socket)
    try:
        result = client.call("panel_user.create", username=args.username, password=password, role="admin", _actor="bootstrap-cli", _role="admin")
    except (ConnectionResetError, PermissionError):
        if os.geteuid() == 0:
            command = f"sudo -u boron-api python3 {shlex.quote(str(Path(__file__).resolve()))} --username {shlex.quote(args.username)}"
            print(
                "Failed: the provisioning socket rejected the root caller; "
                f"run this as boron-api: {command}",
                file=sys.stderr,
            )
        else:
            print("Failed: the provisioning socket reset the connection", file=sys.stderr)
        return 1
    except RpcError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created admin panel user '{result['username']}' (id={result['id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
