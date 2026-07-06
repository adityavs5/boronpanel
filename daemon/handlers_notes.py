"""Phase 8 feature 11: admin-only, append-only account notes.

Never exposed on any customer endpoint (the router is admin-only). Append-only
by design: there is only add + list, no update/delete op, so the note history is
a durable record. `author` is the admin who wrote it, captured at write time.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, AccountNote
from shared.validation import ValidationError, validate_username

MAX_NOTE_LEN = 8000


def _note_to_dict(note: AccountNote) -> dict:
    return {
        "id": note.id,
        "author": note.author,
        "body": note.body,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


def add_note(params: dict) -> dict:
    username = validate_username(params["username"])
    author = params.get("author") or "admin"
    body = (params.get("body") or "").strip()
    if not body:
        raise ValidationError("note body must not be empty")
    if len(body) > MAX_NOTE_LEN:
        raise ValidationError(f"note body must be at most {MAX_NOTE_LEN} characters")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        note = AccountNote(account_id=account.id, author=author, body=body)
        session.add(note)
        session.flush()
        return _note_to_dict(note)


def list_notes(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        notes = session.scalars(
            select(AccountNote).where(AccountNote.account_id == account.id).order_by(AccountNote.id.desc())
        ).all()
        return {"notes": [_note_to_dict(n) for n in notes]}
