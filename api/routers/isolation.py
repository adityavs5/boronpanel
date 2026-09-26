from fastapi import APIRouter, Depends

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/isolation", tags=["isolation"])


@api_router.get("")
def overview(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.overview", identity)


@api_router.post("/{username}/rebuild")
def rebuild(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.rebuild", identity, username=username)


@api_router.post("/{username}/self-test")
def self_test(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.self_test", identity, username=username)
