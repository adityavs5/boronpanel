"""Phase 8 feature 11: admin-only append-only account notes."""
import pytest

from daemon import handlers_notes
from shared.db import write_session
from shared.models import Account
from shared.validation import ValidationError


def _account(username="demo1"):
    with write_session() as db:
        db.add(Account(username=username, status="active", uid=5001, gid=5001))


def test_add_and_list_note(isolated_db):
    _account()
    handlers_notes.add_note({"username": "demo1", "author": "admin", "body": "Called about billing"})
    result = handlers_notes.list_notes({"username": "demo1"})
    assert len(result["notes"]) == 1
    note = result["notes"][0]
    assert note["author"] == "admin"
    assert note["body"] == "Called about billing"
    assert note["created_at"] is not None


def test_notes_are_newest_first(isolated_db):
    _account()
    handlers_notes.add_note({"username": "demo1", "author": "admin", "body": "first"})
    handlers_notes.add_note({"username": "demo1", "author": "admin", "body": "second"})
    notes = handlers_notes.list_notes({"username": "demo1"})["notes"]
    assert notes[0]["body"] == "second"
    assert notes[1]["body"] == "first"


def test_empty_body_rejected(isolated_db):
    _account()
    with pytest.raises(ValidationError):
        handlers_notes.add_note({"username": "demo1", "author": "admin", "body": "   "})


def test_add_note_missing_account(isolated_db):
    with pytest.raises(RuntimeError, match="not found"):
        handlers_notes.add_note({"username": "nope", "author": "admin", "body": "x"})
