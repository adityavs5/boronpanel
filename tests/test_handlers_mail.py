import pytest

from daemon import handlers_account as ha
from daemon import handlers_mail as hm
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture()
def stub_dkim(tmp_path, monkeypatch):
    """Real openssl calls (fast, offline, no root needed) into a throwaway
    dir, with the DNS-publish side skipped (no PowerDNS zone in these
    tests) -- same "real local computation, mocked system/network calls"
    split test_dkim.py uses."""
    monkeypatch.setattr(settings, "dkim_base_dir", str(tmp_path / "dkim"))
    monkeypatch.setattr(hm.dkim, "find_managed_zone", lambda domain: None)


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_mail(monkeypatch):
    calls = []
    state = {"domains": set(), "mailboxes": set()}

    def domain_exists(domain):
        return domain in state["domains"]

    def create_mail_domain(domain):
        calls.append(("create_mail_domain", domain))
        state["domains"].add(domain)
        return 1

    def delete_mail_domain(domain):
        calls.append(("delete_mail_domain", domain))
        state["domains"].discard(domain)

    def create_mailbox(domain, local_part, password, quota_mb=1024):
        calls.append(("create_mailbox", domain, local_part))
        state["mailboxes"].add((domain, local_part))
        return {"domain": domain, "local_part": local_part, "email": f"{local_part}@{domain}", "quota_mb": quota_mb}

    def delete_mailbox(domain, local_part):
        calls.append(("delete_mailbox", domain, local_part))
        state["mailboxes"].discard((domain, local_part))

    def list_mailboxes(domain):
        return [{"local_part": lp} for d, lp in state["mailboxes"] if d == domain]

    def change_mailbox_password(domain, local_part, new_password):
        calls.append(("change_mailbox_password", domain, local_part))

    monkeypatch.setattr(hm.mail, "domain_exists", domain_exists)
    monkeypatch.setattr(hm.mail, "create_mail_domain", create_mail_domain)
    monkeypatch.setattr(hm.mail, "delete_mail_domain", delete_mail_domain)
    monkeypatch.setattr(hm.mail, "create_mailbox", create_mailbox)
    monkeypatch.setattr(hm.mail, "delete_mailbox", delete_mailbox)
    monkeypatch.setattr(hm.mail, "list_mailboxes", list_mailboxes)
    monkeypatch.setattr(hm.mail, "change_mailbox_password", change_mailbox_password)

    forwards = {}
    catchall = {}
    autoresponders = {}

    def create_forward(domain, local_part, destination):
        forwards.setdefault(domain, []).append((local_part, destination))
        return {"id": 1, "domain": domain, "local_part": local_part, "destination": destination}

    def delete_forward(domain, local_part, destination):
        forwards[domain] = [f for f in forwards.get(domain, []) if f != (local_part, destination)]

    def list_forwards(domain):
        return [{"local_part": lp, "destination": d, "active": 1} for lp, d in forwards.get(domain, [])]

    def set_catchall(domain, destination):
        catchall[domain] = destination
        return {"domain": domain, "destination": destination}

    def get_catchall(domain):
        return {"destination": catchall[domain], "active": 1} if domain in catchall else None

    def delete_catchall(domain):
        catchall.pop(domain, None)

    def set_autoresponder_stub(domain, local_part, subject, body, start_date, end_date):
        autoresponders[(domain, local_part)] = {
            "subject": subject, "body": body, "start_date": start_date, "end_date": end_date, "active": 1,
        }
        return {"domain": domain, "local_part": local_part, "subject": subject, "body": body, "start_date": start_date, "end_date": end_date}

    def get_autoresponder_stub(domain, local_part):
        return autoresponders.get((domain, local_part))

    def delete_autoresponder_stub(domain, local_part):
        autoresponders.pop((domain, local_part), None)

    monkeypatch.setattr(hm.mail, "create_forward", create_forward)
    monkeypatch.setattr(hm.mail, "delete_forward", delete_forward)
    monkeypatch.setattr(hm.mail, "list_forwards", list_forwards)
    monkeypatch.setattr(hm.mail, "set_catchall", set_catchall)
    monkeypatch.setattr(hm.mail, "get_catchall", get_catchall)
    monkeypatch.setattr(hm.mail, "delete_catchall", delete_catchall)
    monkeypatch.setattr(hm.mail, "set_autoresponder", set_autoresponder_stub)
    monkeypatch.setattr(hm.mail, "get_autoresponder", get_autoresponder_stub)
    monkeypatch.setattr(hm.mail, "delete_autoresponder", delete_autoresponder_stub)

    monkeypatch.setattr(hm.autoresponder, "apply_autoresponder", lambda *a, **k: None)
    monkeypatch.setattr(hm.autoresponder, "remove_autoresponder", lambda *a, **k: None)
    return calls


def test_create_mail_domain(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    result = hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    assert result["domain"] == "demo1.example"
    assert ("create_mail_domain", "demo1.example") in stub_mail


def test_create_mail_domain_generates_dkim_key(isolated_db, stub_sysops, stub_mail, stub_dkim):
    ha.create_account({"username": "demo1"})
    result = hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    assert result["dkim"]["selector"] == "default"
    assert result["dkim"]["dns_published"] is False
    assert dkim_private_key_exists("demo1.example")


def dkim_private_key_exists(domain: str) -> bool:
    from daemon import dkim

    return dkim._private_key_path(domain).exists()


def test_delete_mail_domain_tears_down_dkim_key(isolated_db, stub_sysops, stub_mail, stub_dkim):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    assert dkim_private_key_exists("demo1.example")
    hm.delete_mail_domain({"domain": "demo1.example"})
    assert not dkim_private_key_exists("demo1.example")


def test_create_mail_domain_rejects_duplicate(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(RuntimeError):
        hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})


def test_create_mailbox_requires_domain(isolated_db, stub_sysops, stub_mail):
    with pytest.raises(RuntimeError):
        hm.create_mailbox({"domain": "nope.example", "local_part": "john", "password": "secret123"})


def test_create_mailbox_happy_path(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    result = hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "secret123"})
    assert result["email"] == "john@demo1.example"
    assert ("create_mailbox", "demo1.example", "john") in stub_mail


def test_create_mailbox_rejects_duplicate(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "secret123"})
    with pytest.raises(RuntimeError):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "secret456"})


def test_create_mailbox_rejects_invalid_local_part(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(ValidationError):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "John Doe", "password": "secret123"})


def test_delete_mailbox(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "secret123"})
    result = hm.delete_mailbox({"domain": "demo1.example", "local_part": "john"})
    assert result["status"] == "deleted"
    assert hm.list_mailboxes({"domain": "demo1.example"})["mailboxes"] == []


def test_terminate_account_drops_mail_domains_and_mailboxes(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "secret123"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        hm.terminate_account_mail(account)

    assert ("delete_mail_domain", "demo1.example") in stub_mail


# --- Forwarders ----------------------------------------------------------


def test_create_and_list_forward(isolated_db, stub_mail):
    result = hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    assert result["destination"] == "ext@gmail.com"
    forwards = hm.list_forwards({"domain": "demo1.example"})["forwards"]
    assert {"local_part": "sales", "destination": "ext@gmail.com", "active": 1} in forwards


def test_create_forward_rejects_invalid_destination(isolated_db, stub_mail):
    with pytest.raises(ValidationError):
        hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "not-an-email"})


def test_delete_forward(isolated_db, stub_mail):
    hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    result = hm.delete_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    assert result["status"] == "deleted"
    assert hm.list_forwards({"domain": "demo1.example"})["forwards"] == []


# --- Catch-all -------------------------------------------------------------


def test_set_get_delete_catchall(isolated_db, stub_mail):
    assert hm.get_catchall({"domain": "demo1.example"})["catchall"] is None
    hm.set_catchall({"domain": "demo1.example", "destination": "catchall@gmail.com"})
    result = hm.get_catchall({"domain": "demo1.example"})["catchall"]
    assert result["destination"] == "catchall@gmail.com"
    hm.delete_catchall({"domain": "demo1.example"})
    assert hm.get_catchall({"domain": "demo1.example"})["catchall"] is None


def test_set_catchall_rejects_invalid_destination(isolated_db, stub_mail):
    with pytest.raises(ValidationError):
        hm.set_catchall({"domain": "demo1.example", "destination": "not-an-email"})


# --- Autoresponders ----------------------------------------------------


def test_set_get_delete_autoresponder(isolated_db, stub_mail):
    result = hm.set_autoresponder(
        {
            "domain": "demo1.example",
            "local_part": "john",
            "subject": "Out of office",
            "body": "I am away.",
            "start_date": "2026-07-01",
            "end_date": "2026-07-15",
        }
    )
    assert result["subject"] == "Out of office"
    current = hm.get_autoresponder({"domain": "demo1.example", "local_part": "john"})["autoresponder"]
    assert current["start_date"] == "2026-07-01"
    hm.delete_autoresponder({"domain": "demo1.example", "local_part": "john"})
    assert hm.get_autoresponder({"domain": "demo1.example", "local_part": "john"})["autoresponder"] is None


def test_set_autoresponder_rejects_empty_subject(isolated_db, stub_mail):
    with pytest.raises(ValueError):
        hm.set_autoresponder({"domain": "demo1.example", "local_part": "john", "subject": "  ", "body": "body"})


def test_set_autoresponder_rejects_end_before_start(isolated_db, stub_mail):
    with pytest.raises(ValueError):
        hm.set_autoresponder(
            {
                "domain": "demo1.example",
                "local_part": "john",
                "subject": "s",
                "body": "b",
                "start_date": "2026-07-15",
                "end_date": "2026-07-01",
            }
        )


def test_set_autoresponder_rejects_bad_date_format(isolated_db, stub_mail):
    with pytest.raises(ValidationError):
        hm.set_autoresponder(
            {"domain": "demo1.example", "local_part": "john", "subject": "s", "body": "b", "start_date": "07/01/2026"}
        )
