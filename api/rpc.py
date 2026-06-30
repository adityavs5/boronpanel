"""forgehost-api's only path to any privileged/write action: the Unix
socket RPC to forgehostd (ARCHITECTURE.md SS2). Every call is stamped with
the authenticated caller's identity so the daemon's audit log records who
actually did what, even though authorization itself was already decided
here, before the call was made.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from shared.config import settings
from shared.rpc import RpcClient, RpcError

if TYPE_CHECKING:
    from api.security import Identity

_client = RpcClient(settings.rpc_socket, timeout=200.0)


def call_daemon(op: str, identity: "Identity", **params) -> dict:
    """Takes the whole Identity rather than separate actor/role strings on
    purpose: a couple of op params (e.g. creating an API token) are
    themselves named "role", which collided with a same-named actor-role
    kwarg here in an earlier version of this function -- caught by the
    first real attempt to create a token through the UI, which crashed with
    `TypeError: call_daemon() got multiple values for argument 'role'`.
    Bundling identity into one object that can never appear as an op
    param's name removes the whole collision class structurally instead of
    just renaming around it.
    """
    try:
        return _client.call(op, _actor=identity.username, _role=identity.role, **params)
    except RpcError as exc:
        status = 400 if exc.code == "bad_request" else 502
        raise HTTPException(status_code=status, detail=exc.message) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc
