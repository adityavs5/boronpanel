from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/crons", tags=["cron"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/crons", tags=["ui:cron"])


class CronJobBody(BaseModel):
    schedule: str
    command: str
    label: str = ""


class CronMailtoBody(BaseModel):
    mailto: str = ""


@api_router.get("/mailto")
def get_mailto(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.mailto.get", identity, username=username)


@api_router.patch("/mailto")
def set_mailto(username: str, body: CronMailtoBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.mailto.set", identity, username=username, mailto=body.mailto)


@api_router.get("")
def list_crons(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.list", identity, username=username)


@api_router.post("")
def add_cron(username: str, body: CronJobBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.add", identity, username=username, **body.model_dump())


@api_router.put("/{job_id}")
def update_cron(username: str, job_id: str, body: CronJobBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.update", identity, username=username, job_id=job_id, **body.model_dump())


@api_router.delete("/{job_id}")
def delete_cron(username: str, job_id: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("cron.delete", identity, username=username, job_id=job_id)


# --- UI: schedule builder (5 plain fields, defaulting to "*") + a raw
# expression override for anyone who just wants to type it directly ---


def _build_schedule(minute: str, hour: str, dom: str, month: str, dow: str, raw: str) -> str:
    raw = (raw or "").strip()
    if raw:
        return raw
    fields = [minute or "*", hour or "*", dom or "*", month or "*", dow or "*"]
    return " ".join(f.strip() or "*" for f in fields)


@ui_router.get("")
def ui_list_crons(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    jobs = call_daemon("cron.list", identity, username=username)["jobs"]
    mailto = call_daemon("cron.mailto.get", identity, username=username)["mailto"]
    return templates.TemplateResponse(request, "crons.html", {"identity": identity, "username": username, "jobs": jobs, "mailto": mailto})


@ui_router.post("/mailto")
def ui_set_mailto(username: str, mailto: str = Form(""), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("cron.mailto.set", identity, username=username, mailto=mailto)
    return RedirectResponse(f"/ui/accounts/{username}/crons", status_code=303)


@ui_router.post("")
def ui_add_cron(
    username: str,
    minute: str = Form("*"),
    hour: str = Form("*"),
    dom: str = Form("*"),
    month: str = Form("*"),
    dow: str = Form("*"),
    raw_schedule: str = Form(""),
    command: str = Form(...),
    label: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    schedule = _build_schedule(minute, hour, dom, month, dow, raw_schedule)
    call_daemon("cron.add", identity, username=username, schedule=schedule, command=command, label=label)
    return RedirectResponse(f"/ui/accounts/{username}/crons", status_code=303)


@ui_router.post("/{job_id}/delete")
def ui_delete_cron(username: str, job_id: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("cron.delete", identity, username=username, job_id=job_id)
    return RedirectResponse(f"/ui/accounts/{username}/crons", status_code=303)
