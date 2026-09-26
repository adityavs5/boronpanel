"""Authenticated Unix-socket RPC to the root daemon."""
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
        credential = {"type": identity.auth_method, "value": identity.rpc_credential or ""}
        return _client.call(op, rpc_credential=credential, _ip=getattr(identity, "ip", None), **params)
    except RpcError as exc:
        status = {"bad_request": 400, "forbidden": 403, "unauthenticated": 401}.get(exc.code, 502)
        raise HTTPException(status_code=status, detail=exc.message) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc


def call_daemon_anonymous(op: str, **params) -> dict:
    """Only the root-verified login challenge operations accept no credential."""
    if op not in ("auth.login.begin", "auth.login.finish"):
        raise ValueError("operation requires a credential")
    try:
        return _client.call(op, **params)
    except RpcError as exc:
        status = {"bad_request": 400, "forbidden": 403, "unauthenticated": 401}.get(exc.code, 502)
        raise HTTPException(status_code=status, detail=exc.message) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc


def call_daemon_cluster(op: str, token: str, **params) -> dict:
    """Forward a narrow DNS-cluster bearer credential for daemon validation."""
    if op not in ("dnscluster.apply", "dnscluster.ping"):
        raise ValueError("operation is not part of the DNS cluster protocol")
    try:
        return _client.call(op, rpc_credential={"type": "cluster", "value": token}, **params)
    except RpcError as exc:
        status = {"bad_request": 400, "forbidden": 403, "unauthenticated": 401}.get(exc.code, 502)
        raise HTTPException(status_code=status, detail=exc.message) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc
