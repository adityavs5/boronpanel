from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/stack", tags=["stack-manager"])


class StackOperation(BaseModel):
    component: str
    action: str
    target: str
    confirm: bool = False


@api_router.get("")
def inventory(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.inventory", identity)


@api_router.post("/check-updates")
def check_updates(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.check_updates", identity, refresh=True)


@api_router.post("/preview")
def preview(body: StackOperation, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.preview", identity, **body.model_dump())


@api_router.post("/jobs")
def start(body: StackOperation, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.start", identity, **body.model_dump())


@api_router.get("/jobs/{job_id}")
def job(job_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.job.get", identity, job_id=job_id)


@api_router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.job.cancel", identity, job_id=job_id)
