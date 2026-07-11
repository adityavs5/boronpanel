"""Missing-features batch, goal feature 1: IMAPSync migrations.
POST /accounts/{u}/email/imap-migrate + GET status (the goal's own literal
shape), plus a folder-listing helper for the "select mailboxes" UI step and
an admin-wide view of every active job."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/email/imap-migrate", tags=["imapsync"])
admin_api_router = APIRouter(prefix="/api/v1/admin/imap-migrations", tags=["imapsync"])


class StartMigrationBody(BaseModel):
    domain: str
    local_part: str
    source_host: str
    source_port: int = 993
    source_email: str
    source_password: str
    source_ssl: bool = True
    dest_password: str
    folders: list[str] = []


class ListFoldersBody(BaseModel):
    source_host: str
    source_port: int = 993
    source_email: str
    source_password: str
    source_ssl: bool = True


@api_router.post("")
def start_migration(username: str, body: StartMigrationBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    return call_daemon("imapsync.start", identity, **body.model_dump())


@api_router.post("/list-folders")
def list_source_folders(username: str, body: ListFoldersBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("imapsync.list_folders", identity, **body.model_dump())


@api_router.get("")
def list_migrations(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("imapsync.list", identity, username=username)


@api_router.get("/{job_id}")
def get_migration_status(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("imapsync.status", identity, id=job_id, username=username)


@api_router.post("/{job_id}/cancel")
def cancel_migration(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("imapsync.cancel", identity, id=job_id, username=username)


@admin_api_router.get("")
def list_active_migrations(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("imapsync.list_active_admin", identity)
