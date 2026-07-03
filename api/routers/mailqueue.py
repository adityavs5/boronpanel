"""Phase 5 feature 3: mail queue viewer. Admin-only -- the Postfix queue
spans every hosted mail domain at once, not a single account's resource.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/mail-queue", tags=["mail-queue"])
ui_router = APIRouter(prefix="/ui/mail-queue", tags=["ui:mail-queue"])


@api_router.get("")
def list_queue(search: str = "", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("mailqueue.list", identity, search=search)


@api_router.post("/flush")
def flush_all(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("mailqueue.flush_all", identity)


@api_router.post("/{queue_id}/flush")
def flush_one(queue_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("mailqueue.flush", identity, queue_id=queue_id)


@api_router.post("/{queue_id}/delete")
def delete_one(queue_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("mailqueue.delete", identity, queue_id=queue_id)


@api_router.post("/delete-all")
def delete_all(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("mailqueue.delete_all", identity)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_mail_queue(request: Request, search: str = "", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    data = call_daemon("mailqueue.list", identity, search=search)
    return templates.TemplateResponse(
        request, "mail_queue.html", {"identity": identity, "entries": data["entries"], "count": data["count"], "search": search}
    )


@ui_router.post("/flush")
def ui_flush_all(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("mailqueue.flush_all", identity)
    return RedirectResponse("/ui/mail-queue", status_code=303)


@ui_router.post("/{queue_id}/flush")
def ui_flush_one(queue_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("mailqueue.flush", identity, queue_id=queue_id)
    return RedirectResponse("/ui/mail-queue", status_code=303)


@ui_router.post("/{queue_id}/delete")
def ui_delete_one(queue_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("mailqueue.delete", identity, queue_id=queue_id)
    return RedirectResponse("/ui/mail-queue", status_code=303)


@ui_router.post("/delete-all")
def ui_delete_all(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("mailqueue.delete_all", identity)
    return RedirectResponse("/ui/mail-queue", status_code=303)
