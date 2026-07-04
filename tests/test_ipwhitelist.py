import pytest

from daemon import ipwhitelist
from shared.db import write_session
from shared.models import IpWhitelistEntry
from shared.validation import ValidationError


def test_list_entries_empty_by_default(isolated_db):
    assert ipwhitelist.list_entries({}) == {"entries": []}


def test_add_entry_stores_value_and_note(isolated_db):
    result = ipwhitelist.add_entry({"value": "203.0.113.5", "note": "office"})
    values = {e["value"] for e in result["entries"]}
    assert "203.0.113.5" in values
    with write_session() as session:
        row = session.query(IpWhitelistEntry).filter_by(value="203.0.113.5").first()
        assert row.note == "office"


def test_add_entry_always_also_adds_requester_ip(isolated_db):
    result = ipwhitelist.add_entry({"value": "203.0.113.5", "requester_ip": "198.51.100.9"})
    values = {e["value"] for e in result["entries"]}
    assert "203.0.113.5" in values
    assert "198.51.100.9" in values


def test_add_entry_requester_ip_not_duplicated_if_already_present(isolated_db):
    ipwhitelist.add_entry({"value": "198.51.100.9"})
    result = ipwhitelist.add_entry({"value": "203.0.113.5", "requester_ip": "198.51.100.9"})
    values = [e["value"] for e in result["entries"]]
    assert values.count("198.51.100.9") == 1


def test_add_entry_rejects_invalid_ip(isolated_db):
    with pytest.raises(ValidationError):
        ipwhitelist.add_entry({"value": "not-an-ip"})


def test_add_entry_accepts_cidr(isolated_db):
    result = ipwhitelist.add_entry({"value": "203.0.113.0/24"})
    assert any(e["value"] == "203.0.113.0/24" for e in result["entries"])


def test_add_entry_no_duplicate_rows_for_same_value(isolated_db):
    ipwhitelist.add_entry({"value": "203.0.113.5"})
    result = ipwhitelist.add_entry({"value": "203.0.113.5"})
    values = [e["value"] for e in result["entries"]]
    assert values.count("203.0.113.5") == 1


def test_delete_entry_removes_row(isolated_db):
    ipwhitelist.add_entry({"value": "203.0.113.5"})
    with write_session() as session:
        entry_id = session.query(IpWhitelistEntry).filter_by(value="203.0.113.5").first().id
    result = ipwhitelist.delete_entry({"id": entry_id})
    assert result["status"] == "deleted"
    with write_session() as session:
        assert session.get(IpWhitelistEntry, entry_id) is None


def test_delete_entry_rejects_unknown_id(isolated_db):
    with pytest.raises(ValidationError):
        ipwhitelist.delete_entry({"id": 999999})
