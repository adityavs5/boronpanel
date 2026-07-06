"""Phase 8 feature 6: per-domain email routing (Local/Remote/Backup)."""
import pytest
from sqlalchemy import select

from daemon import handlers_email_routing as er
from shared.db import write_session
from shared.models import Account, Domain, EmailRouting
from shared.validation import ValidationError


def _setup(monkeypatch, tmp_path, mail_exists=True):
    calls = {"active": []}
    monkeypatch.setattr(er.mail, "domain_exists", lambda d: mail_exists)
    monkeypatch.setattr(er.mail, "set_domain_active", lambda d, a: (calls["active"].append((d, a)), True)[1])
    monkeypatch.setattr(er, "run", lambda *a, **k: None)
    monkeypatch.setattr(er.settings, "postfix_relay_domains_map", str(tmp_path / "relay"))
    with write_session() as db:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="site.com", kind="primary", docroot="/home/demo1/public_html"))
    return calls


def test_default_mode_is_local(isolated_db, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert er.get_routing({"username": "demo1", "domain": "site.com"})["mode"] == "local"


def test_set_remote_stops_acceptance(isolated_db, monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    result = er.set_routing({"username": "demo1", "domain": "site.com", "mode": "remote"})
    assert result["mode"] == "remote"
    assert result["accepting_locally"] is False
    # Postfix acceptance toggled OFF (active=False) -> the Done-When mechanism.
    assert ("site.com", False) in calls["active"]
    with write_session() as db:
        assert db.scalar(select(EmailRouting).where(EmailRouting.domain == "site.com")).mode == "remote"


def test_set_local_enables_acceptance(isolated_db, monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    er.set_routing({"username": "demo1", "domain": "site.com", "mode": "remote"})
    er.set_routing({"username": "demo1", "domain": "site.com", "mode": "local"})
    assert ("site.com", True) in calls["active"]


def test_set_backup_writes_relay_map(isolated_db, monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    er.set_routing({"username": "demo1", "domain": "site.com", "mode": "backup"})
    # backup does not accept as a local mailbox domain
    assert ("site.com", False) in calls["active"]
    relay = (tmp_path / "relay").read_text()
    assert "site.com OK" in relay


def test_backup_removed_leaves_relay_map(isolated_db, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    er.set_routing({"username": "demo1", "domain": "site.com", "mode": "backup"})
    er.set_routing({"username": "demo1", "domain": "site.com", "mode": "local"})
    relay = (tmp_path / "relay").read_text()
    assert "site.com" not in relay  # no longer a backup domain


def test_set_rejects_bad_mode(isolated_db, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(ValidationError):
        er.set_routing({"username": "demo1", "domain": "site.com", "mode": "bogus"})


def test_set_rejects_foreign_domain(isolated_db, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="not found"):
        er.set_routing({"username": "demo1", "domain": "notmine.com", "mode": "remote"})


def test_no_mail_domain_still_records_mode(isolated_db, monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, mail_exists=False)
    result = er.set_routing({"username": "demo1", "domain": "site.com", "mode": "remote"})
    assert result["mail_domain_affected"] is False
    assert calls["active"] == []  # never toggled -- no mail domain to toggle
    assert er.get_routing({"username": "demo1", "domain": "site.com"})["mode"] == "remote"
