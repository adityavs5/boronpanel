"""forgehostd: the root-privileged provisioning daemon.

Listens only on a Unix domain socket (ARCHITECTURE.md SS2). Every accepted
connection is dispatched to one of OP_TABLE's handlers in a worker thread
(handlers do blocking subprocess/filesystem/SQLite work), and every call is
written to the audit log regardless of success or failure.
"""
from __future__ import annotations

import asyncio
import grp
import logging
import os
import stat
from pathlib import Path

from shared.config import settings
from shared.db import init_db
from shared.rpc import encode_response, read_frame
from shared.validation import ValidationError

from daemon import audit, handlers_account, handlers_database, handlers_dns, handlers_domain, ols

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(settings.log_dir) / "daemon.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("forgehostd")

OP_TABLE = {
    "account.create": handlers_account.create_account,
    "account.get": handlers_account.get_account,
    "account.list": handlers_account.list_accounts,
    "account.suspend": handlers_account.suspend_account,
    "account.unsuspend": handlers_account.unsuspend_account,
    "account.terminate": handlers_account.terminate_account,
    "domain.add": handlers_domain.add_domain,
    "domain.list": handlers_domain.list_domains,
    "system.bootstrap_ols": lambda params: (ols.bootstrap_baseline(), {"status": "ok"})[1],
    "dns.create_zone": handlers_dns.create_zone,
    "dns.delete_zone": handlers_dns.delete_zone,
    "dns.list_records": handlers_dns.list_records,
    "dns.set_record": handlers_dns.set_record,
    "dns.delete_record": handlers_dns.delete_record,
    "db.create": handlers_database.create_database,
    "db.list": handlers_database.list_databases,
    "db.drop": handlers_database.drop_database,
    "db.change_password": handlers_database.change_password,
}

# Each phase wires its own account-scoped teardown/suspend behavior here
# instead of handlers_account.py importing every phase directly (avoids an
# import cycle: ols/dns/db/mail modules all need handlers_account's Account
# type, not the other way around).
handlers_account.SUSPEND_HOOKS.append(lambda account: ols.suspend_vhost(account))
handlers_account.UNSUSPEND_HOOKS.append(lambda account: ols.unsuspend_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: ols.terminate_vhost(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_dns.terminate_account_zones(account))
handlers_account.TERMINATE_HOOKS.append(lambda account: handlers_database.terminate_account_databases(account))


def register_op(name: str, handler) -> None:
    """Used by later phases (vhost/dns/db/mail/ssl) to add ops without
    server.py growing a giant import list at the top -- each phase's
    __init__ calls this once at daemon startup."""
    OP_TABLE[name] = handler


async def dispatch(op: str, params: dict) -> dict:
    actor = params.pop("_actor", "unknown")
    role = params.pop("_role", "unknown")
    handler = OP_TABLE.get(op)
    if handler is None:
        audit.record(actor, role, op, None, params, "failed", "unknown op")
        raise LookupError(f"unknown op '{op}'")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, handler, params)
    except (ValidationError, ValueError) as exc:
        audit.record(actor, role, op, params.get("username"), params, "failed", str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("handler for %s failed", op)
        audit.record(actor, role, op, params.get("username"), params, "failed", str(exc))
        raise
    else:
        audit.record(actor, role, op, params.get("username"), params, "ok")
        return result


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername", "unix-peer")
    try:
        request = await read_frame(reader)
    except (asyncio.IncompleteReadError, ValueError) as exc:
        logger.warning("bad frame from %s: %s", peer, exc)
        writer.close()
        return

    op = request.get("op", "")
    params = request.get("params", {}) or {}

    try:
        result = await dispatch(op, dict(params))
        writer.write(encode_response(True, result=result))
    except (ValidationError, ValueError, LookupError) as exc:
        writer.write(encode_response(False, error_code="bad_request", error_message=str(exc)))
    except Exception as exc:  # noqa: BLE001
        writer.write(encode_response(False, error_code="internal_error", error_message=str(exc)))
    finally:
        await writer.drain()
        writer.close()


async def amain() -> None:
    init_db()
    socket_path = settings.rpc_socket
    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(socket_path).exists():
        os.unlink(socket_path)

    server = await asyncio.start_unix_server(handle_client, path=socket_path)

    # group-readable/writable by forgehost-api, nothing for "other"
    os.chmod(socket_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    try:
        gid = grp.getgrnam("forgehost-api").gr_gid
        os.chown(socket_path, 0, gid)
    except KeyError:
        logger.warning("forgehost-api group not found; socket left root-only")

    logger.info("forgehostd listening on %s", socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("forgehostd must run as root")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
