"""Unified task history for administrators and account owners."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.routers.accounts import CreateAccountBody
from api.routers.domains import AddDomainBody
from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin

api_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
control_api_router = APIRouter(prefix="/api/v1/task-operations", tags=["task-operations"])


class RenameTaskBody(BaseModel):
    new_username: str


@api_router.get("")
def list_tasks(
    query: str = "",
    status: str = "",
    kind: str = "",
    account: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = Query(100, ge=1, le=500),
    identity: Identity = Depends(get_identity),
):
    return call_daemon(
        "tasks.list", identity, query=query, status=status, kind=kind,
        account=account, date_from=date_from, date_to=date_to, limit=limit,
    )


@api_router.get("/{kind}/{task_id}")
def get_task(kind: str, task_id: int, identity: Identity = Depends(get_identity)):
    return call_daemon("tasks.get", identity, kind=kind, task_id=task_id)


@api_router.post("/{kind}/{task_id}/retry")
def retry_task(kind: str, task_id: int, identity: Identity = Depends(get_identity)):
    return call_daemon("tasks.retry", identity, kind=kind, task_id=task_id)


@api_router.post("/{kind}/{task_id}/cancel")
def cancel_task(kind: str, task_id: int, identity: Identity = Depends(get_identity)):
    return call_daemon("tasks.cancel", identity, kind=kind, task_id=task_id)


@control_api_router.post("/account-create", status_code=202)
def create_account_task(body: CreateAccountBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("taskops.account.create", identity, **body.model_dump(exclude_none=True))


@control_api_router.post("/accounts/{username}/domains", status_code=202)
def add_domain_task(
    username: str, body: AddDomainBody, identity: Identity = Depends(get_identity)
):
    require_account_access(identity, username)
    return call_daemon("taskops.domain.add", identity, username=username, **body.model_dump())


@control_api_router.post("/accounts/{username}/rename", status_code=202)
def rename_account_task(
    username: str, body: RenameTaskBody, identity: Identity = Depends(get_identity)
):
    require_admin(identity)
    return call_daemon(
        "taskops.account.rename", identity,
        username=username, new_username=body.new_username,
    )


@control_api_router.post("/accounts/{username}/terminate", status_code=202)
def terminate_account_task(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("taskops.account.terminate", identity, username=username)


@control_api_router.get("/{task_id}")
def get_control_task(task_id: int, identity: Identity = Depends(get_identity)):
    return call_daemon("taskops.get", identity, task_id=task_id)


@control_api_router.post("/{task_id}/reveal")
def reveal_control_task(task_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("taskops.reveal", identity, task_id=task_id)
