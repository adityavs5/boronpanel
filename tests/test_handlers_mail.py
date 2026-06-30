import pytest

from daemon import handlers_account as ha
from daemon import handlers_mail as hm
from shared.validation import ValidationError


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
    return calls


def test_create_mail_domain(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    result = hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    assert result["domain"] == "demo1.example"
    assert ("create_mail_domain", "demo1.example") in stub_mail


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
