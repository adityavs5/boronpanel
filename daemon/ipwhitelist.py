"""Phase 5 feature 9: panel-login IP/CIDR whitelist.

Enforcement itself lives in `api/main.py` (a request-time middleware
checking the caller's IP against this table before any route runs,
including `/login`) -- this module is just the CRUD + the
lockout-prevention guarantee. An empty table means "no restriction"
(goal's explicit default), so this feature is inert until an admin adds
its first entry.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.validation import ValidationError, validate_ip_or_cidr

from shared.models import IpWhitelistEntry


def list_entries(params: dict) -> dict:
    with write_session() as session:
        rows = session.scalars(select(IpWhitelistEntry).order_by(IpWhitelistEntry.id)).all()
        return {"entries": [{"id": r.id, "value": r.value, "note": r.note} for r in rows]}


def _upsert(session, value: str, note: str | None) -> None:
    existing = session.scalar(select(IpWhitelistEntry).where(IpWhitelistEntry.value == value))
    if existing is None:
        session.add(IpWhitelistEntry(value=value, note=note))


def add_entry(params: dict) -> dict:
    """`requester_ip` is supplied by the API layer (the only place that
    actually knows the calling admin's real client IP, since this daemon
    only ever sees Unix-socket RPC calls with no IP concept at all) --
    always upserted alongside `value`, structurally preventing the admin
    making this exact request from ever locking themselves out, no
    matter what `value` they asked to add."""
    value = validate_ip_or_cidr(params["value"])
    note = (params.get("note") or "").strip()[:200] or None
    requester_ip = params.get("requester_ip")

    with write_session() as session:
        _upsert(session, value, note)
        if requester_ip:
            requester_ip = validate_ip_or_cidr(requester_ip)
            _upsert(session, requester_ip, "auto-added: the admin who created this whitelist")
        session.flush()
        rows = session.scalars(select(IpWhitelistEntry).order_by(IpWhitelistEntry.id)).all()
        return {"entries": [{"id": r.id, "value": r.value, "note": r.note} for r in rows]}


def delete_entry(params: dict) -> dict:
    entry_id = int(params["id"])
    with write_session() as session:
        row = session.get(IpWhitelistEntry, entry_id)
        if row is None:
            raise ValidationError(f"whitelist entry {entry_id} not found")
        session.delete(row)
    return {"id": entry_id, "status": "deleted"}
