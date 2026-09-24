from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from api.rpc import call_daemon, call_daemon_cluster
from api.security import Identity, get_identity, require_admin

admin_router = APIRouter(prefix="/api/v1/admin/dns-cluster", tags=["dns-cluster"])
peer_router = APIRouter(prefix="/api/v1/dns-cluster", tags=["dns-cluster"])


class PeerBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    peer_type: str
    endpoint: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4096)
    verify_tls: bool = True
    enabled: bool = True


class PeerUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    peer_type: str | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4096)
    verify_tls: bool | None = None
    enabled: bool | None = None


class ApplyBody(BaseModel):
    event_id: str = Field(min_length=32, max_length=32)
    action: str
    zone: str
    payload: dict | None = None


def _admin(identity: Identity) -> None:
    require_admin(identity)


@admin_router.get("")
def list_cluster(identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.peer.list", identity)


@admin_router.post("/peers")
def create_peer(body: PeerBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.peer.create", identity, **body.model_dump())


@admin_router.patch("/peers/{peer_id}")
def update_peer(peer_id: int, body: PeerUpdateBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.peer.update", identity, peer_id=peer_id,
                       **body.model_dump(exclude_unset=True))


@admin_router.delete("/peers/{peer_id}")
def delete_peer(peer_id: int, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.peer.delete", identity, peer_id=peer_id)


@admin_router.post("/peers/{peer_id}/test")
def test_peer(peer_id: int, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.peer.test", identity, peer_id=peer_id)


@admin_router.post("/sync-all")
def sync_all(identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnscluster.sync_all", identity)


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="DNS cluster bearer credential required")
    token = authorization[7:]
    if not token or len(token) > 4096:
        raise HTTPException(status_code=401, detail="Invalid DNS cluster credential")
    return token


@peer_router.get("/ping")
def ping(authorization: str | None = Header(default=None)):
    return call_daemon_cluster("dnscluster.ping", _bearer(authorization))


@peer_router.post("/apply")
def apply(body: ApplyBody, authorization: str | None = Header(default=None)):
    return call_daemon_cluster("dnscluster.apply", _bearer(authorization), **body.model_dump())
