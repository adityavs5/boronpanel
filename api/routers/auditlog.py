"""Read-only administrator audit views.

Writes and failed operations are recorded at the root RPC boundary. Successful
explicitly read-only operations are omitted to keep changes visible; legacy
read entries remain available through the include_reads filter. No delete
route is exposed here.
"""
from __future__ import annotations

import csv
import io
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from starlette.requests import Request

from api.security import Identity, get_identity, require_admin
from api.templates import templates
from shared.audit_redaction import sanitize, sanitize_output
from shared.db import read_session
from shared.models import AccountEvent, ActionStep, AuditLog
from shared.token_policy import READ_ONLY_OPERATIONS

api_router = APIRouter(prefix="/api/v1/audit-log", tags=["audit-log"])
ui_router = APIRouter(prefix="/ui/audit-log", tags=["ui:audit-log"])

MAX_PAGE_SIZE = 500

# Display classification is deliberately separate from API-token permissions.
# These newer inventory reads are safe to hide, but this must not grant a
# read-only credential permission to invoke an additional operation.
AUDIT_READ_OPERATIONS = READ_ONLY_OPERATIONS | frozenset({
    "cf.health",
    "cf.rails_status",
    "cf.zones_overview",
    "dbgovernor.statistics",
    "dbmonitor.connections",
    "dbmonitor.db_sizes",
    "dbmonitor.kill_privilege_status",
    "dbmonitor.processlist",
    "dbmonitor.slow_queries",
    "dnscluster.capabilities",
    "dnscluster.ping",
    "fail2ban.recent_events",
    "firewall.pending",
    "imapsync.list_folders",
    "namespace.health_summary",
    "php_ini.effective",
    "resources.overview",
    "namespace.overview",
    "sessions.list",
    "snapshot.catalog.accounts",
    "snapshot.drive.folders",
    "snapshot.notifications.deliveries",
    "snapshot.restore.configuration",
    "snapshot.restore.databases",
    "snapshot.restore.mailboxes",
    "snapshot.restore.mail_routing",
    "stack.php_policy.get",
    "stack.php_sessions.preview",
    "wpcli.detect",
})


def _apply_filters(query, actor: str, op: str, result: str, target: str, q: str, include_reads: bool = False):
    if not include_reads:
        query = query.where(~((AuditLog.result == 'ok') & AuditLog.op.in_(AUDIT_READ_OPERATIONS)))
    if actor:
        query = query.where(AuditLog.actor.contains(actor))
    if op:
        query = query.where(AuditLog.op.contains(op))
    if result:
        query = query.where(AuditLog.result == ('failed' if result == 'error' else result))
    if target:
        query = query.where(AuditLog.target.contains(target))
    if q:
        like = f"%{q}%"
        query = query.where(
            AuditLog.actor.like(like) | AuditLog.op.like(like) | AuditLog.target.like(like)
            | AuditLog.detail.like(like) | AuditLog.request_id.like(like)
        )
    return query


def _row_dict(row: AuditLog) -> dict:
    match = re.search(r"reference:\s*([a-f0-9]{16})", row.detail or "", re.IGNORECASE)
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor": row.actor,
        "role": row.role,
        "op": row.op,
        "target": row.target,
        "params": sanitize("", row.params),
        "result": row.result,
        "detail": sanitize_output(row.detail, row.params, row.changes),
        "request_id": row.request_id,
        "reference": match.group(1) if match else None,
        "source_ip": row.source_ip,
        "duration_ms": row.duration_ms,
        "changes": sanitize("", row.changes),
    }


def _step_dict(row: ActionStep, params=None, changes=None) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "duration_ms": row.duration_ms,
        "result": row.result,
        "exit_code": row.exit_code,
        "stdout_tail": sanitize_output(row.stdout_tail, params, changes),
        "stderr_tail": sanitize_output(row.stderr_tail, params, changes),
    }


def _query_rows(actor: str, op: str, result: str, target: str, q: str, page: int, page_size: int, include_reads: bool = False) -> tuple[list[AuditLog], int]:
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = max(1, page)
    with read_session() as db:
        base = select(AuditLog)
        base = _apply_filters(base, actor, op, result, target, q, include_reads)
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
    include_reads: bool = False,
    page: int = 1,
    page_size: int = 50,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    rows, total = _query_rows(actor, op, result, target, q, page, page_size, include_reads)
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
        "detail": sanitize("detail", row.detail),
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


def _csv_cell(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    if isinstance(value, str):
        # Quoting a CSV cell does not disable spreadsheet formulas. Preserve
        # audit text as literal data, including whitespace/control prefixes.
        if value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
            return "'" + value
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return '' if value is None else value


def _csv_response(model, query, fields, filename):
    # Use a native async iterator. Starlette wraps a synchronous generator in
    # AnyIO's global worker pool; after TestClient/lifespan teardown that pool
    # can belong to a closed portal and the export then waits forever for an
    # idle worker. Each SQLite read is deliberately bounded to 250 rows, so
    # yielding it directly is short and deterministic while preserving the
    # streaming response contract.
    async def chunks():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(fields)
        yield buffer.getvalue()
        before = None
        while True:
            batch = query if before is None else query.where(model.id < before)
            with read_session() as db:
                rows = db.scalars(batch.order_by(model.id.desc()).limit(250)).all()
            if not rows:
                return
            buffer.seek(0)
            buffer.truncate(0)
            for row in rows:
                writer.writerow([_csv_cell(
                    sanitize_output(getattr(row, field), getattr(row, "params", None), getattr(row, "changes", None))
                    if field == "detail" else sanitize(field, getattr(row, field))
                ) for field in fields])
            before = rows[-1].id
            yield buffer.getvalue()
    return StreamingResponse(chunks(), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename={filename}',
        'Cache-Control': 'no-store',
    })


@api_router.get("/account-events/export.csv")
def export_account_events_csv(
    action: str = "",
    username: str = "",
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    query = select(AccountEvent)
    if action:
        query = query.where(AccountEvent.action == action)
    if username:
        query = query.where(AccountEvent.username.contains(username))
    return _csv_response(AccountEvent, query,
        ['id', 'created_at', 'action', 'username', 'actor', 'actor_role', 'ip', 'detail'],
        'boron-account-events.csv')


@api_router.get("/export.csv")
def export_csv(
    actor: str = "",
    op: str = "",
    result: str = "",
    target: str = "",
    q: str = "",
    include_reads: bool = False,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    query = _apply_filters(select(AuditLog), actor, op, result, target, q, include_reads)
    return _csv_response(AuditLog, query,
        ['id', 'created_at', 'actor', 'role', 'op', 'target', 'result', 'detail', 'request_id',
         'source_ip', 'duration_ms', 'changes'],
        'boron-audit-log.csv')


@api_router.get("/{entry_id}")
def audit_log_detail(entry_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        row = db.get(AuditLog, entry_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Audit entry not found")
        steps = db.scalars(
            select(ActionStep).where(ActionStep.audit_id == row.id).order_by(ActionStep.id)
        ).all()
        result = _row_dict(row)
        result["steps"] = [_step_dict(item, row.params, row.changes) for item in steps]
        return result


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_audit_log(
    request: Request,
    actor: str = "",
    op: str = "",
    result: str = "",
    target: str = "",
    q: str = "",
    include_reads: bool = False,
    page: int = 1,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    page_size = 50
    rows, total = _query_rows(actor, op, result, target, q, page, page_size, include_reads)
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
