"""Authenticated Unix-socket RPC to the root daemon."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from shared.config import settings
from shared.rpc import RpcClient, RpcError
from shared.request_context import current_request_id

if TYPE_CHECKING:
    from api.security import Identity

_client = RpcClient(settings.rpc_socket, timeout=200.0)
_python_provision_client = RpcClient(settings.rpc_socket, timeout=450.0)


def _request_kwargs() -> dict:
    request_id = current_request_id()
    return {"request_id": request_id} if request_id else {}


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
        client = _python_provision_client if op == 'apps.python.create' else _client
        return client.call(op, rpc_credential=credential, **_request_kwargs(),
                           _ip=getattr(identity, "ip", None), **params)
    except RpcError as exc:
        status = {
            "bad_request": 400, "forbidden": 403, "unauthenticated": 401,
            "incompatible_protocol": 503,
        }.get(exc.code, 502)
        headers = None
        if exc.diagnostic:
            from api.logsetup import record_operation_error
            record_operation_error(exc.diagnostic, identity.username, getattr(identity, 'ip', None),
                                   request_id=current_request_id())
            headers = {'X-Boron-Error-Reference': exc.diagnostic['reference']}
        raise HTTPException(status_code=status, detail=exc.message, headers=headers) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc


def call_daemon_anonymous(op: str, **params) -> dict:
    """Only the root-verified login challenge operations accept no credential."""
    if op not in ("auth.login.begin", "auth.login.finish"):
        raise ValueError("operation requires a credential")
    try:
        return _client.call(op, **_request_kwargs(), **params)
    except RpcError as exc:
        status = {
            "bad_request": 400, "forbidden": 403, "unauthenticated": 401,
            "incompatible_protocol": 503,
        }.get(exc.code, 502)
        headers = ({'X-Boron-Error-Reference': exc.diagnostic['reference']}
                   if exc.diagnostic and exc.diagnostic.get('reference') else None)
        raise HTTPException(status_code=status, detail=exc.message, headers=headers) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc


def call_daemon_cluster(op: str, token: str, **params) -> dict:
    """Forward a narrow DNS-cluster bearer credential for daemon validation."""
    if op not in ("dnscluster.apply", "dnscluster.ping", "dnscluster.capabilities"):
        raise ValueError("operation is not part of the DNS cluster protocol")
    try:
        return _client.call(op, rpc_credential={"type": "cluster", "value": token},
                            **_request_kwargs(), **params)
    except RpcError as exc:
        status = {
            "bad_request": 400, "forbidden": 403, "unauthenticated": 401,
            "incompatible_protocol": 503,
        }.get(exc.code, 502)
        headers = ({'X-Boron-Error-Reference': exc.diagnostic['reference']}
                   if exc.diagnostic and exc.diagnostic.get('reference') else None)
        raise HTTPException(status_code=status, detail=exc.message, headers=headers) from exc
    except (ConnectionError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"provisioning daemon unreachable: {exc}") from exc
