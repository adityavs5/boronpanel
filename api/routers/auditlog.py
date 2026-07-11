"""Phase 5 feature 6: audit log. Admin-only, read-only -- every admin and
customer action is already recorded by `daemon/audit.py`'s `record()`,
called unconditionally (success or failure) from `daemon/server.py`'s
`dispatch()` for every single RPC op, so nothing new was needed to make
"every action logged" true; this feature only adds a searchable/
filterable view over the table that already exists.

Reads `AuditLog` directly via `read_session()`, the same "boron-api
opens the same SQLite file read-only for fast list/get queries"
data-access pattern ARCHITECTURE.md SS4 already establishes (e.g.
`tokens.py`) -- there is deliberately no write/delete route anywhere in
this file (goal: "cannot delete via UI").
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from starlette.requests import Request

from api.security import Identity, get_identity, require_admin
from api.templates import templates
from shared.db import read_session
from shared.models import AccountEvent, AuditLog

api_router = APIRouter(prefix="/api/v1/audit-log", tags=["audit-log"])
ui_router = APIRouter(prefix="/ui/audit-log", tags=["ui:audit-log"])

MAX_PAGE_SIZE = 500


def _apply_filters(query, actor: str, op: str, result: str, target: str, q: str):
    if actor:
        query = query.where(AuditLog.actor.contains(actor))
    if op:
        query = query.where(AuditLog.op.contains(op))
    if result:
        query = query.where(AuditLog.result == result)
    if target:
        query = query.where(AuditLog.target.contains(target))
    if q:
        like = f"%{q}%"
        query = query.where(
            AuditLog.actor.like(like) | AuditLog.op.like(like) | AuditLog.target.like(like) | AuditLog.detail.like(like)
        )
    return query


def _row_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor": row.actor,
        "role": row.role,
        "op": row.op,
        "target": row.target,
        "params": row.params,
        "result": row.result,
        "detail": row.detail,
    }


def _query_rows(actor: str, op: str, result: str, target: str, q: str, page: int, page_size: int) -> tuple[list[AuditLog], int]:
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = max(1, page)
    with read_session() as db:
        base = select(AuditLog)
        base = _apply_filters(base, actor, op, result, target, q)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return rows, total


@api_router.get("")
def list_audit_log(
    actor: str = "",
    op: str = "",
    result: str = "",
    target: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    rows, total = _query_rows(actor, op, result, target, q, page, page_size)
    return {"entries": [_row_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


ACCOUNT_EVENT_ACTIONS = ("created", "suspended", "unsuspended", "terminated")


def _event_dict(row: AccountEvent) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "action": row.action,
        "username": row.username,
        "actor": row.actor,
        "actor_role": row.actor_role,
        "ip": row.ip,
        "detail": row.detail,
    }


@api_router.get("/account-events")
def list_account_events(
    action: str = "",
    username: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 100,
    identity: Identity = Depends(get_identity),
):
    """Account lifecycle log: who created/suspended/unsuspended/terminated
    which account, when, from which IP. Read-only by design — terminated
    accounts exist nowhere else in the panel."""
    require_admin(identity)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = max(1, page)
    with read_session() as db:
        base = select(AccountEvent)
        if action:
            base = base.where(AccountEvent.action == action)
        if username:
            base = base.where(AccountEvent.username.contains(username))
        if q:
            like = f"%{q}%"
            base = base.where(
                AccountEvent.username.like(like)
                | AccountEvent.actor.like(like)
                | AccountEvent.ip.like(like)
                | AccountEvent.detail.like(like)
            )
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(AccountEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {
            "entries": [_event_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "actions": list(ACCOUNT_EVENT_ACTIONS),
        }


@api_router.get("/account-events/export.csv")
def export_account_events_csv(
    action: str = "",
    username: str = "",
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    with read_session() as db:
        base = select(AccountEvent)
        if action:
            base = base.where(AccountEvent.action == action)
        if username:
            base = base.where(AccountEvent.username.contains(username))
        rows = db.scalars(base.order_by(AccountEvent.id.desc())).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "action", "username", "actor", "actor_role", "ip", "detail"])
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.created_at.isoformat() if row.created_at else "",
                row.action,
                row.username,
                row.actor,
                row.actor_role,
                row.ip or "",
                row.detail or "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=boron-account-events.csv"},
    )


@api_router.get("/export.csv")
def export_csv(
    actor: str = "",
    op: str = "",
    result: str = "",
    target: str = "",
    q: str = "",
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    with read_session() as db:
        base = select(AuditLog)
        base = _apply_filters(base, actor, op, result, target, q)
        rows = db.scalars(base.order_by(AuditLog.id.desc())).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "actor", "role", "op", "target", "result", "detail"])
    for row in rows:
        writer.writerow(
            [row.id, row.created_at.isoformat() if row.created_at else "", row.actor, row.role, row.op, row.target or "", row.result, row.detail or ""]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=boron-audit-log.csv"},
    )


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_audit_log(
    request: Request,
    actor: str = "",
    op: str = "",
    result: str = "",
    target: str = "",
    q: str = "",
    page: int = 1,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    page_size = 50
    rows, total = _query_rows(actor, op, result, target, q, page, page_size)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "identity": identity,
            "entries": [_row_dict(r) for r in rows],
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "actor": actor,
            "op": op,
            "result": result,
            "target": target,
            "q": q,
        },
    )
