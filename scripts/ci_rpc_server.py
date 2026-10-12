#!/usr/bin/env python3
"""Minimal unprivileged borond transport for browser integration tests.

It deliberately skips production startup reconciliation and service bootstrap.
Requests still pass through the real RPC framing, authority checks, dispatch
table, database handlers and audit path.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import daemon.server as server_module
from shared.config import require_secure_session_secret, settings
from shared.db import init_db


async def serve() -> None:
    require_secure_session_secret()
    init_db()
    # Production verifies the peer against the real boron-api UID. The
    # unprivileged disposable fixture has no system service account, so scope
    # that lookup to this process's own UID without changing production code.
    server_module.pwd.getpwnam = lambda _name: SimpleNamespace(pw_uid=os.getuid())
    socket_path = Path(settings.rpc_socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = await asyncio.start_unix_server(server_module.handle_client, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    print(f"CI RPC fixture listening on {socket_path}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
