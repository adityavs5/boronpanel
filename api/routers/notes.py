"""Phase 8 feature 11: admin-only account notes (append-only, never shown to
the customer). CRUD is intentionally add+list only (append-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/accounts/{username}/notes", tags=["account-notes"])


class NoteBody(BaseModel):
    body: str


@api_router.get("")
def list_notes(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("notes.list", identity, username=username)


@api_router.post("")
def add_note(username: str, body: NoteBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # author = the acting admin; recorded server-side, never taken from input.
    return call_daemon("notes.add", identity, username=username, author=identity.username, body=body.body)
