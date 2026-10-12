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


class TuningBody(BaseModel):
    confirm: bool = False


class PhpPolicyBody(BaseModel):
    hide_eol_for_new_domains_after: str | None = None


class ConfirmBody(BaseModel):
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


@api_router.get("/tuning")
def tuning_preview(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.tuning.preview", identity)


@api_router.post("/tuning")
def tuning_apply(body: TuningBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.tuning.apply", identity, **body.model_dump())


@api_router.get("/php-policy")
def php_policy(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.php_policy.get", identity)


@api_router.put("/php-policy")
def update_php_policy(body: PhpPolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.php_policy.set", identity, **body.model_dump())


@api_router.get("/php-sessions/migration")
def php_session_migration(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.php_sessions.preview", identity)


@api_router.post("/php-sessions/migration")
def migrate_php_sessions(body: ConfirmBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("stack.php_sessions.migrate", identity, **body.model_dump())
