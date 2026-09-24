"""QA round 2, item 14: permanent, server-wide IP/CIDR bans."""
import pytest

from daemon import ipban
from daemon.procutil import ProcResult
from shared.validation import ValidationError


@pytest.fixture()
def fake_ufw(monkeypatch):
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ipban, "run", fake_run)
    return calls


def test_ban_ip_inserts_ufw_deny_rule_at_position_one(isolated_db, fake_ufw):
    result = ipban.ban_ip({"value": "203.0.113.5", "reason": "brute force", "actor": "admin"})
    assert result["value"] == "203.0.113.5"
    assert result["reason"] == "brute force"
    assert result["banned_by"] == "admin"
    assert fake_ufw[0] == ["ufw", "insert", "1", "deny", "from", "203.0.113.5"]


def test_ban_ip_accepts_cidr(isolated_db, fake_ufw):
    result = ipban.ban_ip({"value": "203.0.113.0/24", "actor": "admin"})
    assert result["value"] == "203.0.113.0/24"


def test_ban_ip_rejects_invalid_value(isolated_db, fake_ufw):
    with pytest.raises(ValidationError):
        ipban.ban_ip({"value": "not-an-ip", "actor": "admin"})
    assert fake_ufw == []


def test_ban_ip_rejects_duplicate(isolated_db, fake_ufw):
    ipban.ban_ip({"value": "203.0.113.5", "actor": "admin"})
    with pytest.raises(ipban.IpBanError):
        ipban.ban_ip({"value": "203.0.113.5", "actor": "admin"})


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0"])
def test_ban_ip_refuses_ban_everything(isolated_db, fake_ufw, value):
    with pytest.raises(ValidationError, match="entire server"):
        ipban.ban_ip({"value": value, "actor": "admin"})
    assert fake_ufw == []


@pytest.mark.parametrize("value", ["127.0.0.1", "127.0.0.0/8", "::1"])
def test_ban_ip_refuses_loopback(isolated_db, fake_ufw, value):
    with pytest.raises(ValidationError, match="loopback"):
        ipban.ban_ip({"value": value, "actor": "admin"})
    assert fake_ufw == []


def test_ban_ip_allows_private_ranges(isolated_db, fake_ufw):
    """A private range is a legitimate ban target (e.g. a compromised
    internal service) -- only the catastrophic any/loopback cases are
    refused."""
    result = ipban.ban_ip({"value": "10.0.0.5", "actor": "admin"})
    assert result["value"] == "10.0.0.5"


def test_actor_recorded_as_free_text_not_hosting_username(isolated_db, fake_ufw):
    """banned_by is the admin's own panel login, which is NOT required to
    match the hosting-account username regex (lowercase, starts with a
    letter, max 16 chars) -- must not be rejected just because it doesn't."""
    result = ipban.ban_ip({"value": "203.0.113.5", "actor": "Site-Admin_2"})
    assert result["banned_by"] == "Site-Admin_2"


def test_list_bans_returns_all(isolated_db, fake_ufw):
    ipban.ban_ip({"value": "203.0.113.5", "reason": "abuse", "actor": "admin"})
    ipban.ban_ip({"value": "203.0.113.6", "actor": "admin"})
    result = ipban.list_bans({})
    assert {b["value"] for b in result["bans"]} == {"203.0.113.5", "203.0.113.6"}


def test_unban_ip_deletes_ufw_rule_and_row(isolated_db, fake_ufw):
    created = ipban.ban_ip({"value": "203.0.113.5", "actor": "admin"})
    result = ipban.unban_ip({"id": created["id"]})
    assert result["status"] == "unbanned"
    assert ["ufw", "--force", "delete", "deny", "from", "203.0.113.5"] in fake_ufw
    assert ipban.list_bans({})["bans"] == []


def test_unban_ip_missing_id_raises(isolated_db, fake_ufw):
    with pytest.raises(ipban.IpBanError):
        ipban.unban_ip({"id": 999999})



def test_unban_failure_retains_record_for_retry(isolated_db, fake_ufw, monkeypatch):
    created = ipban.ban_ip({'value': '203.0.113.5', 'actor': 'admin'})
    monkeypatch.setattr(ipban, 'run', lambda args, **kwargs: ProcResult(args, 1, '', 'backend unavailable'))
    with pytest.raises(RuntimeError, match='backend unavailable'):
        ipban.unban_ip({'id': created['id']})
    assert ipban.list_bans({})['bans'][0]['id'] == created['id']
