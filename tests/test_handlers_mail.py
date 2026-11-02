import pytest

from daemon import handlers_account as ha
from daemon import handlers_mail as hm
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def stub_dkim(tmp_path, monkeypatch):
    """Real openssl calls (fast, offline, no root needed) into a throwaway
    dir, with the DNS-publish side skipped (no PowerDNS zone in these
    tests) -- same "real local computation, mocked system/network calls"
    split test_dkim.py uses."""
    monkeypatch.setattr(settings, "dkim_base_dir", str(tmp_path / "dkim"))
    monkeypatch.setattr(hm.dkim, "find_managed_zone", lambda domain: None)
    # Unit tests use a temporary tree which is not owned by the real
    # OpenDKIM service account (and may run inside a user namespace).
    monkeypatch.setattr(hm.dkim, "_opendkim_gid", lambda: None)
    # Handler tests generate real disposable keys, but must never rewrite
    # the host's OpenDKIM/Postfix configuration or restart customer mail.
    monkeypatch.setattr(hm.dkim, "configure_signer", lambda: {'active': True})


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

    def create_mail_domain(domain, active=True):
        calls.append(("create_mail_domain", domain))
        state.setdefault("active", {})[domain] = active
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
        existed = (domain, local_part) in state["mailboxes"]
        state["mailboxes"].discard((domain, local_part))
        return existed

    def list_mailboxes(domain):
        return [{"local_part": lp} for d, lp in state["mailboxes"] if d == domain]

    def change_mailbox_password(domain, local_part, new_password):
        calls.append(("change_mailbox_password", domain, local_part))
        return (domain, local_part) in state["mailboxes"]

    def set_mailbox_active(domain, local_part, active):
        calls.append(("set_mailbox_active", domain, local_part, active))
        return (domain, local_part) in state["mailboxes"]

    def revoke_webmail_sessions(mailbox):
        calls.append(("revoke_webmail_sessions", mailbox))

    monkeypatch.setattr(hm.mail, "domain_exists", domain_exists)
    monkeypatch.setattr(hm.mail, "create_mail_domain", create_mail_domain)
    monkeypatch.setattr(hm.mail, "delete_mail_domain", delete_mail_domain)
    monkeypatch.setattr(hm.mail, "create_mailbox", create_mailbox)
    monkeypatch.setattr(hm.mail, "delete_mailbox", delete_mailbox)
    monkeypatch.setattr(hm.mail, "list_mailboxes", list_mailboxes)
    monkeypatch.setattr(hm.mail, "change_mailbox_password", change_mailbox_password)
    monkeypatch.setattr(hm.mail, "set_mailbox_active", set_mailbox_active)
    monkeypatch.setattr(hm.webmail_sso, "revoke_mailbox", revoke_webmail_sessions)

    forwards = {}
    catchall = {}
    autoresponders = {}

    def create_forward(domain, local_part, destination):
        forwards.setdefault(domain, []).append((local_part, destination))
        return {"id": 1, "domain": domain, "local_part": local_part, "destination": destination}

    def delete_forward(domain, local_part, destination):
        before = forwards.get(domain, [])
        forwards[domain] = [f for f in before if f != (local_part, destination)]
        return len(forwards[domain]) != len(before)

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


def test_create_mail_domain_without_username_uses_hosting_domain_owner(isolated_db, stub_mail):
    from shared.db import write_session
    from shared.models import Account, Domain

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(
            Domain(
                account_id=account.id,
                domain="demo1.example",
                docroot="/home/demo1/public_html",
                kind="primary",
            )
        )
        account_id = account.id

    result = hm.create_mail_domain({"domain": "demo1.example"})

    assert result["account_id"] == account_id
    assert ("create_mail_domain", "demo1.example") in stub_mail


def test_create_mail_domain_without_owner_fails_before_external_create(isolated_db, stub_mail):
    with pytest.raises(ValidationError, match="requires an owning hosting account"):
        hm.create_mail_domain({"domain": "orphan.example"})

    assert ("create_mail_domain", "orphan.example") not in stub_mail
    assert hm.mail.domain_exists("orphan.example") is False


def test_create_mail_domain_rolls_back_external_when_cache_write_fails(
    isolated_db, stub_sysops, stub_mail, monkeypatch
):
    from contextlib import contextmanager

    from sqlalchemy import select

    from shared.db import write_session as real_write_session
    from shared.models import MailDomain

    ha.create_account({"username": "demo1"})
    calls = 0

    @contextmanager
    def flaky_write_session():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cache write failed")
        with real_write_session() as session:
            yield session

    monkeypatch.setattr(hm, "write_session", flaky_write_session)

    with pytest.raises(RuntimeError, match="cache write failed"):
        hm.create_mail_domain({"username": "demo1", "domain": "rollback.example"})

    assert ("create_mail_domain", "rollback.example") in stub_mail
    assert ("delete_mail_domain", "rollback.example") in stub_mail
    assert hm.mail.domain_exists("rollback.example") is False
    with real_write_session() as session:
        assert session.scalar(select(MailDomain).where(MailDomain.domain == "rollback.example")) is None


def test_create_mailbox_requires_domain(isolated_db, stub_sysops, stub_mail):
    with pytest.raises(RuntimeError):
        hm.create_mailbox({"domain": "nope.example", "local_part": "john", "password": "Secret123!Pass"})


def test_create_mailbox_happy_path(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    result = hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    assert result["email"] == "john@demo1.example"
    assert ("create_mailbox", "demo1.example", "john") in stub_mail


def test_create_mailbox_rejects_duplicate(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    with pytest.raises(RuntimeError):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret456!Pass"})


def test_create_mailbox_rejects_invalid_local_part(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(ValidationError):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "John Doe", "password": "Secret123!Pass"})


def test_create_mailbox_rolls_back_external_when_cache_write_fails(isolated_db, stub_sysops, stub_mail, monkeypatch):
    from contextlib import contextmanager

    from sqlalchemy import select

    from shared.db import write_session as real_write_session
    from shared.models import MailUser

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    calls = 0

    @contextmanager
    def flaky_write_session():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("mailbox cache write failed")
        with real_write_session() as session:
            yield session

    monkeypatch.setattr(hm, "write_session", flaky_write_session)

    with pytest.raises(RuntimeError, match="mailbox cache write failed"):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})

    assert ("create_mailbox", "demo1.example", "john") in stub_mail
    assert ("delete_mailbox", "demo1.example", "john") in stub_mail
    assert hm.mail.list_mailboxes("demo1.example") == []
    with real_write_session() as session:
        assert session.scalar(
            select(MailUser).where(MailUser.domain == "demo1.example", MailUser.local_part == "john")
        ) is None


def test_delete_mailbox(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    result = hm.delete_mailbox({"domain": "demo1.example", "local_part": "john"})
    assert result["status"] == "deleted"
    assert hm.list_mailboxes({"domain": "demo1.example"})["mailboxes"] == []
    assert stub_mail.index(("revoke_webmail_sessions", "john@demo1.example")) < stub_mail.index(
        ("delete_mailbox", "demo1.example", "john")
    )


def test_suspend_mailbox_revokes_webmail_before_disabling(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})

    result = hm.set_mailbox_active({"domain": "demo1.example", "local_part": "john", "active": False})

    assert result["status"] == "suspended"
    assert stub_mail.index(("revoke_webmail_sessions", "john@demo1.example")) < stub_mail.index(
        ("set_mailbox_active", "demo1.example", "john", False)
    )


def test_unsuspend_mailbox_does_not_revoke_webmail(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})

    result = hm.set_mailbox_active({"domain": "demo1.example", "local_part": "john", "active": True})

    assert result["status"] == "active"
    assert ("set_mailbox_active", "demo1.example", "john", True) in stub_mail
    assert ("revoke_webmail_sessions", "john@demo1.example") not in stub_mail


def test_delete_mailbox_fails_closed_when_webmail_revocation_fails(
    isolated_db, stub_sysops, stub_mail, monkeypatch
):
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import MailUser

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})

    def fail_revocation(_mailbox):
        raise RuntimeError("session revocation failed")

    monkeypatch.setattr(hm.webmail_sso, "revoke_mailbox", fail_revocation)
    with pytest.raises(RuntimeError, match="session revocation failed"):
        hm.delete_mailbox({"domain": "demo1.example", "local_part": "john"})

    assert {item["local_part"] for item in hm.list_mailboxes({"domain": "demo1.example"})["mailboxes"]} == {"john"}
    with write_session() as session:
        assert session.scalar(select(MailUser).where(MailUser.domain == "demo1.example")) is not None


def test_terminate_account_drops_mail_domains_and_mailboxes(isolated_db, stub_sysops, stub_mail):
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        hm.terminate_account_mail(account)

    assert ("delete_mail_domain", "demo1.example") in stub_mail


# --- Forwarders ----------------------------------------------------------


@pytest.mark.parametrize("terminate", [False, True])
def test_domain_delete_cleans_rules_and_prefs(isolated_db, stub_sysops, stub_mail, stub_dkim, monkeypatch, tmp_path, terminate):
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import Account, MailDomain, SpamFilterEntry

    monkeypatch.setattr(hm.spamfilter, "VIRTUAL_CONFIG_BASE", str(tmp_path / "prefs"))
    monkeypatch.setattr(hm.spamfilter, "_refresh_global_sieve", lambda: None)
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "orphan", "password": "Secret123!Pass"})
    hm.spamfilter.apply_domain_spam_settings("demo1.example", enabled=False, threshold=None)
    hm.spamfilter.add_entry({"domain": "demo1.example", "local_part": "orphan", "kind": "whitelist", "pattern": "vip.com"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
    if terminate:
        hm.terminate_account_mail(account)
    else:
        hm.delete_mail_domain({"domain": "demo1.example"})
    with write_session() as session:
        assert session.scalar(select(MailDomain)) is None
        assert session.scalar(select(SpamFilterEntry)) is None
    assert not (tmp_path / "prefs" / "demo1.example").exists()


def test_domain_cleanup_failure_retains_owner_for_retry(isolated_db, stub_sysops, stub_mail, stub_dkim, monkeypatch, tmp_path):
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import MailDomain

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    monkeypatch.setattr(hm.spamfilter, "VIRTUAL_CONFIG_BASE", str(tmp_path / "prefs"))
    cleanup = hm.spamfilter.remove_domain_spam_settings

    def fail(domain):
        raise PermissionError("prefs cleanup failed")

    monkeypatch.setattr(hm.spamfilter, "remove_domain_spam_settings", fail)
    with pytest.raises(PermissionError, match="prefs cleanup failed"):
        hm.delete_mail_domain({"domain": "demo1.example"})
    with write_session() as session:
        assert session.scalar(select(MailDomain)).account_id is not None
    monkeypatch.setattr(hm.spamfilter, "remove_domain_spam_settings", cleanup)
    hm.delete_mail_domain({"domain": "demo1.example"})
    with write_session() as session:
        assert session.scalar(select(MailDomain)) is None


def test_mailbox_cleanup_failure_retains_cache_for_retry(isolated_db, stub_sysops, stub_mail, stub_dkim, monkeypatch):
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import MailUser

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    cleanup = hm.spamfilter.delete_entries_for_mailbox

    def fail(*args):
        raise RuntimeError("rule cleanup failed")

    monkeypatch.setattr(hm.spamfilter, "delete_entries_for_mailbox", fail)
    with pytest.raises(RuntimeError, match="rule cleanup failed"):
        hm.delete_mailbox({"domain": "demo1.example", "local_part": "john"})
    with write_session() as session:
        assert session.scalar(select(MailUser)) is not None
    monkeypatch.setattr(hm.spamfilter, "delete_entries_for_mailbox", cleanup)
    hm.delete_mailbox({"domain": "demo1.example", "local_part": "john"})
    with write_session() as session:
        assert session.scalar(select(MailUser)) is None



@pytest.fixture()
def forward_owner(isolated_db):
    from shared.db import write_session
    from shared.models import Account, Domain
    with write_session() as db:
        account = Account(username='demo1',status='active')
        db.add(account);db.flush()
        db.add(Domain(account_id=account.id,domain='demo1.example',docroot='/home/demo1/public_html',kind='primary'))


def test_create_and_list_forward(forward_owner, stub_mail):
    result = hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    assert result["destination"] == "ext@gmail.com"
    forwards = hm.list_forwards({"domain": "demo1.example"})["forwards"]
    assert {"local_part": "sales", "destination": "ext@gmail.com", "active": 1} in forwards


def test_create_forward_rejects_invalid_destination(isolated_db, stub_mail):
    with pytest.raises(ValidationError):
        hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "not-an-email"})


def test_delete_forward(forward_owner, stub_mail):
    hm.create_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    result = hm.delete_forward({"domain": "demo1.example", "local_part": "sales", "destination": "ext@gmail.com"})
    assert result["status"] == "deleted"
    assert hm.list_forwards({"domain": "demo1.example"})["forwards"] == []


# --- Catch-all -------------------------------------------------------------


def test_set_get_delete_catchall(forward_owner, stub_mail):
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


def test_set_get_delete_autoresponder(isolated_db, stub_mail, tmp_path, monkeypatch):
    _fake_autoresponder_file(tmp_path, monkeypatch)
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


def _fake_autoresponder_file(tmp_path, monkeypatch):
    base = tmp_path / "vmail"
    monkeypatch.setattr(hm.os, "chown", lambda *a, **k: None)

    def sieve_path(domain, local_part):
        return base / domain / local_part / ".dovecot.sieve"

    def apply(domain, local_part, subject, body, start_date, end_date):
        path = sieve_path(domain, local_part)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"subject={subject}\\nbody={body}\\n")
        path.chmod(0o600)

    def remove(domain, local_part):
        path = sieve_path(domain, local_part)
        path.unlink(missing_ok=True)
        path.with_suffix(".svbin").unlink(missing_ok=True)

    monkeypatch.setattr(hm.autoresponder, "_sieve_path", sieve_path)
    monkeypatch.setattr(hm.autoresponder, "apply_autoresponder", apply)
    monkeypatch.setattr(hm.autoresponder, "remove_autoresponder", remove)
    return sieve_path("demo1.example", "john")


def test_set_autoresponder_row_failure_removes_new_sieve_file(isolated_db, stub_mail, tmp_path, monkeypatch):
    sieve_path = _fake_autoresponder_file(tmp_path, monkeypatch)
    monkeypatch.setattr(
        hm.mail,
        "set_autoresponder",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("autoresponder row failed")),
    )

    with pytest.raises(RuntimeError, match="autoresponder row failed"):
        hm.set_autoresponder({"domain": "demo1.example", "local_part": "john", "subject": "Away", "body": "new"})

    assert not sieve_path.exists()


def test_set_autoresponder_row_failure_restores_previous_sieve_file(isolated_db, stub_mail, tmp_path, monkeypatch):
    sieve_path = _fake_autoresponder_file(tmp_path, monkeypatch)
    sieve_path.parent.mkdir(parents=True)
    sieve_path.write_text("old sieve")
    sieve_path.chmod(0o600)
    monkeypatch.setattr(
        hm.mail,
        "set_autoresponder",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("autoresponder row failed")),
    )

    with pytest.raises(RuntimeError, match="autoresponder row failed"):
        hm.set_autoresponder({"domain": "demo1.example", "local_part": "john", "subject": "Away", "body": "new"})

    assert sieve_path.read_text() == "old sieve"


def test_delete_autoresponder_row_failure_restores_sieve_file(isolated_db, stub_mail, tmp_path, monkeypatch):
    sieve_path = _fake_autoresponder_file(tmp_path, monkeypatch)
    sieve_path.parent.mkdir(parents=True)
    sieve_path.write_text("old sieve")
    sieve_path.chmod(0o600)
    monkeypatch.setattr(
        hm.mail,
        "delete_autoresponder",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("autoresponder row delete failed")),
    )

    with pytest.raises(RuntimeError, match="autoresponder row delete failed"):
        hm.delete_autoresponder({"domain": "demo1.example", "local_part": "john"})

    assert sieve_path.read_text() == "old sieve"


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


# --- Spam filter (Phase 4 feature 1) --------------------------------------


@pytest.fixture()
def mail_domain_row(isolated_db, tmp_path, monkeypatch):
    """get_spam_filter/set_spam_filter read/write shared.models.MailDomain
    directly (the SQLite control-plane cache), not the MariaDB-backed
    daemon/mail.py helpers stub_mail mocks -- these settings are consumed
    only by Boron's own code (daemon/spamfilter.py), never queried by
    Postfix/Dovecot at delivery time, so there's no reason to duplicate
    them into boron_mail (see daemon/spamfilter.py's module
    docstring)."""
    from shared.db import write_session
    from shared.models import Account, MailDomain

    monkeypatch.setattr(hm.spamfilter, "VIRTUAL_CONFIG_BASE", str(tmp_path / "spamassassin"))
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(row)
        session.flush()
    return row


def test_get_spam_filter_defaults_enabled_no_override(mail_domain_row):
    result = hm.get_spam_filter({"domain": "demo1.example"})
    assert result["enabled"] is True
    assert result["threshold"] is None
    assert result["effective_threshold"] == 5.0  # server default, unset


def test_set_spam_filter_custom_threshold(mail_domain_row):
    result = hm.set_spam_filter({"domain": "demo1.example", "enabled": True, "threshold": 3.5})
    assert result["threshold"] == 3.5
    assert result["effective_threshold"] == 3.5
    prefs = hm.spamfilter._user_prefs_path("demo1.example").read_text()
    assert "required_score 3.5" in prefs


def test_set_spam_filter_disabled_writes_high_threshold_to_disk(mail_domain_row):
    hm.set_spam_filter({"domain": "demo1.example", "enabled": False})
    result = hm.get_spam_filter({"domain": "demo1.example"})
    assert result["enabled"] is False
    prefs = hm.spamfilter._user_prefs_path("demo1.example").read_text()
    assert f"required_score {hm.spamfilter.DISABLED_THRESHOLD}" in prefs


def test_set_spam_filter_disk_failure_leaves_cache_unchanged(mail_domain_row, monkeypatch):
    from shared.db import write_session
    from shared.models import MailDomain

    def fail_apply(domain, enabled, threshold):
        raise OSError("prefs write failed")

    monkeypatch.setattr(hm.spamfilter, "apply_domain_spam_settings", fail_apply)

    with pytest.raises(OSError, match="prefs write failed"):
        hm.set_spam_filter({"domain": "demo1.example", "enabled": True, "threshold": 3.5})

    with write_session() as session:
        row = session.get(MailDomain, mail_domain_row.id)
        assert row.spam_filter_enabled is True
        assert row.spam_filter_threshold is None


def test_set_spam_filter_cache_failure_restores_previous_prefs(mail_domain_row, monkeypatch):
    from contextlib import contextmanager

    from shared.db import write_session as real_write_session
    from shared.models import MailDomain

    with real_write_session() as session:
        row = session.get(MailDomain, mail_domain_row.id)
        row.spam_filter_enabled = True
        row.spam_filter_threshold = 4.0
    hm.spamfilter.apply_domain_spam_settings("demo1.example", True, 4.0)

    calls = 0

    @contextmanager
    def flaky_write_session():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cache update failed")
        with real_write_session() as session:
            yield session

    monkeypatch.setattr(hm, "write_session", flaky_write_session)

    with pytest.raises(RuntimeError, match="cache update failed"):
        hm.set_spam_filter({"domain": "demo1.example", "enabled": True, "threshold": 3.0})

    prefs = hm.spamfilter._user_prefs_path("demo1.example").read_text()
    assert "required_score 4.0" in prefs
    with real_write_session() as session:
        row = session.get(MailDomain, mail_domain_row.id)
        assert row.spam_filter_enabled is True
        assert row.spam_filter_threshold == 4.0


def test_set_spam_filter_rejects_out_of_range_threshold(mail_domain_row):
    with pytest.raises(ValidationError):
        hm.set_spam_filter({"domain": "demo1.example", "enabled": True, "threshold": 500})


def test_get_spam_filter_rejects_unprovisioned_domain(isolated_db):
    with pytest.raises(ValidationError):
        hm.get_spam_filter({"domain": "never-provisioned.example"})


def test_mailbox_provisions_owned_hosting_domain_on_demand(isolated_db, stub_mail, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain, MailDomain
    from sqlalchemy import select
    with write_session() as session:
        a=Account(username='mailqa',status='active');session.add(a);session.flush()
        session.add(Domain(account_id=a.id,domain='mailqa.example',docroot='/home/mailqa/public_html',kind='primary'))
    monkeypatch.setattr(hm.dkim,'setup_dns_signing',lambda domain:{})
    result=hm.create_mailbox({'domain':'mailqa.example','local_part':'hello','password':'StrongMailPassword_123!'})
    assert result['email']=='hello@mailqa.example'
    with write_session() as session:
        assert session.scalar(select(MailDomain).where(MailDomain.domain=='mailqa.example')).account_id is not None
    hm.create_mailbox({'domain':'mailqa.example','local_part':'second','password':'StrongMailPassword_123!'})


def test_auto_mail_provisioning_rejects_unowned_domain(isolated_db, stub_mail):
    with pytest.raises(RuntimeError,match='active hosting account'):
        hm.ensure_mail_domain('unknown.example')


def test_mailbox_quota_edit_updates_cache_after_external_success(isolated_db, stub_sysops, stub_mail, monkeypatch):
    from shared.db import write_session
    from shared.models import MailUser
    from sqlalchemy import select
    ha.create_account({'username':'demo1'})
    hm.create_mail_domain({'username':'demo1','domain':'demo1.example'})
    hm.create_mailbox({'domain':'demo1.example','local_part':'john','password':'Secret123!Pass'})
    calls=[]
    monkeypatch.setattr(hm.mail,'set_mailbox_quota',lambda domain,local,quota:calls.append((domain,local,quota)))
    hm.set_mailbox_quota({'domain':'demo1.example','local_part':'john','quota_mb':2048})
    assert calls == [('demo1.example','john',2048)]
    with write_session() as db:
        assert db.scalar(select(MailUser).where(MailUser.local_part=='john')).quota_mb == 2048
    def unavailable(*args):raise RuntimeError('mail database unavailable')
    monkeypatch.setattr(hm.mail,'set_mailbox_quota',unavailable)
    with pytest.raises(RuntimeError):
        hm.set_mailbox_quota({'domain':'demo1.example','local_part':'john','quota_mb':4096})
    with write_session() as db:
        assert db.scalar(select(MailUser).where(MailUser.local_part=='john')).quota_mb == 2048


# --- live end-to-end regressions (mail group) ---------------------------------


def test_duplicate_mailbox_and_domain_are_conflicts_not_internal_failures(isolated_db, stub_sysops, stub_mail):
    from shared.validation import ConflictError

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(ConflictError, match="already provisioned"):
        hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    with pytest.raises(ConflictError, match="already exists"):
        hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret456!Pass"})


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self.lastrowid = 7
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.statements.append(sql)
        result = self.conn.respond(sql, params)
        if isinstance(result, Exception):
            raise result
        self._row, self.rowcount = result

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, respond):
        self.respond = respond
        self.statements = []

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        pass


def _fake_mariadb(monkeypatch, respond):
    conn = _FakeConnection(respond)
    monkeypatch.setattr(hm.mail, "_connect", lambda: conn)
    monkeypatch.setattr(hm.mail, "_domain_id", lambda domain: 1)
    return conn


def test_duplicate_forwarder_is_a_conflict_found_before_the_insert(isolated_db, monkeypatch):
    from shared.validation import ConflictError

    conn = _fake_mariadb(monkeypatch, lambda sql, params: ({"id": 3}, 1) if sql.startswith("SELECT") else (None, 1))
    with pytest.raises(ConflictError, match="already exists"):
        hm.mail.create_forward("demo1.example", "alice", "carol@demo1.example")
    assert not any(sql.startswith("INSERT") for sql in conn.statements)


def test_concurrent_duplicate_forwarder_is_a_conflict(isolated_db, monkeypatch):
    import pymysql

    from shared.validation import ConflictError

    def respond(sql, params):
        if sql.startswith("SELECT"):
            return None, 0
        return pymysql.err.IntegrityError(1062, "Duplicate entry")

    _fake_mariadb(monkeypatch, respond)
    with pytest.raises(ConflictError, match="already exists"):
        hm.mail.create_forward("demo1.example", "alice", "carol@demo1.example")


def test_forwarder_constraint_failure_is_not_reported_as_a_duplicate(isolated_db, monkeypatch):
    import pymysql

    def respond(sql, params):
        if sql.startswith("SELECT"):
            return None, 0
        return pymysql.err.IntegrityError(1452, "Cannot add or update a child row")

    _fake_mariadb(monkeypatch, respond)
    with pytest.raises(pymysql.err.IntegrityError):
        hm.mail.create_forward("demo1.example", "alice", "carol@demo1.example")


def test_password_change_on_missing_mailbox_changes_nothing(isolated_db, monkeypatch):
    conn = _fake_mariadb(monkeypatch, lambda sql, params: (None, 0))
    monkeypatch.setattr(hm.mail, "hash_password", lambda password: pytest.fail("no hash for a missing mailbox"))
    assert hm.mail.change_mailbox_password("demo1.example", "nobody", "Secret123!Pass") is False
    assert not any(sql.startswith("UPDATE") for sql in conn.statements)


def test_delete_forward_reports_whether_a_row_existed(isolated_db, monkeypatch):
    _fake_mariadb(monkeypatch, lambda sql, params: (None, 0))
    assert hm.mail.delete_forward("demo1.example", "zz", "carol@demo1.example") is False
    _fake_mariadb(monkeypatch, lambda sql, params: (None, 1))
    assert hm.mail.delete_forward("demo1.example", "zz", "carol@demo1.example") is True


def test_missing_mailbox_password_delete_and_forward_fail(isolated_db, stub_sysops, stub_mail):
    from shared.validation import NotFoundError

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(NotFoundError, match="does not exist"):
        hm.change_mailbox_password({"domain": "demo1.example", "local_part": "nobody",
                                    "password": "Secret123!Pass"})
    with pytest.raises(NotFoundError, match="does not exist"):
        hm.delete_mailbox({"domain": "demo1.example", "local_part": "nobody"})
    with pytest.raises(NotFoundError, match="does not exist"):
        hm.delete_forward({"domain": "demo1.example", "local_part": "zz", "destination": "carol@demo1.example"})
    hm.create_mailbox({"domain": "demo1.example", "local_part": "john", "password": "Secret123!Pass"})
    assert hm.change_mailbox_password({"domain": "demo1.example", "local_part": "john",
                                       "password": "Secret456!Pass"})["status"] == "password_changed"


@pytest.mark.parametrize("override,message", [
    ({"subject": ""}, "subject must be 1-255 characters"),
    ({"body": "x" * 10_001}, "body must be 1-10000 characters"),
    ({"start_date": "2026-07-15", "end_date": "2026-07-01"}, "end_date must not be before start_date"),
    ({"subject": None}, "subject and body must be text"),
])
def test_autoresponder_validation_messages_reach_the_caller(isolated_db, stub_mail, override, message):
    params = {"domain": "demo1.example", "local_part": "john", "subject": "Away", "body": "Back soon"}
    with pytest.raises(ValidationError, match=message):
        hm.set_autoresponder({**params, **override})


def test_termination_removes_routing_rows(isolated_db, stub_sysops, stub_mail, monkeypatch):
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account, EmailRouting

    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    with write_session() as session:
        session.add(EmailRouting(domain="demo1.example", mode="local"))
        session.add(EmailRouting(domain="other.example", mode="local"))
    reloads = []
    monkeypatch.setattr(hm.handlers_email_routing, "regenerate_relay_domains_map", lambda: reloads.append(1))
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        hm.terminate_account_mail(account)
    with write_session() as session:
        remaining = set(session.scalars(select(EmailRouting.domain)).all())
    assert remaining == {"other.example"}
    assert reloads == []  # no backup-mode domain left the relay map


def test_new_mail_domain_publishes_default_dmarc_once(isolated_db, monkeypatch, tmp_path):
    records = {}
    monkeypatch.setattr(hm.dkim, "find_managed_zone", lambda domain: "demo1.example")
    monkeypatch.setattr(hm.dkim.dnsprovider, "list_records", lambda zone: [
        {"name": f"{label}.{zone}" if label != "@" else zone, "type": rtype, "values": values}
        for (label, rtype), values in records.items()])
    monkeypatch.setattr(hm.dkim.dnsprovider, "upsert_record",
                        lambda zone, label, rtype, values, **kw: records.__setitem__((label, rtype), values))
    monkeypatch.setattr(hm.dkim.dnsprovider, "delete_record",
                        lambda zone, label, rtype: records.pop((label, rtype), None))
    monkeypatch.setattr(hm.dkim.shutil, "which", lambda name: None)
    result = hm.dkim.setup_dns_signing("demo1.example")
    assert result["dmarc_published"] is True
    assert records[("_dmarc", "TXT")] == ['"v=DMARC1; p=none"']
    records[("_dmarc", "TXT")] = ['"v=DMARC1; p=reject; rua=mailto:dmarc@demo1.example"']
    assert hm.dkim.setup_dns_signing("demo1.example")["dmarc_published"] is False
    assert "p=reject" in records[("_dmarc", "TXT")][0]
    hm.dkim.teardown_dns_signing("demo1.example")
    assert ("_dmarc", "TXT") in records  # a customer policy is never removed
    records[("_dmarc", "TXT")] = ['"v=DMARC1; p=none"']
    hm.dkim.teardown_dns_signing("demo1.example")
    assert ("_dmarc", "TXT") not in records


def test_default_dmarc_never_shadows_a_parent_policy(isolated_db, monkeypatch):
    # RFC 7489: _dmarc.<sub> overrides the organizational domain's p/sp, so a
    # default p=none under a parent p=reject would weaken it.
    records = {("_dmarc", "TXT"): ['"v=DMARC1; p=reject"']}
    monkeypatch.setattr(hm.dkim.dnsprovider, "list_records", lambda zone: [
        {"name": f"{label}.{zone}" if label != "@" else zone, "type": rtype, "values": values}
        for (label, rtype), values in records.items()])
    upserts = []
    monkeypatch.setattr(hm.dkim.dnsprovider, "upsert_record",
                        lambda zone, label, rtype, values, **kw: upserts.append(label))
    assert hm.dkim._ensure_default_dmarc("shop.demo1.example", "demo1.example") is False
    records.clear()
    assert hm.dkim._ensure_default_dmarc("shop.demo1.example", "demo1.example") is True
    assert upserts == ["_dmarc.shop"]

    # Delegated sub-zone: the parent lives outside this server, so public DNS
    # decides, and an unanswered lookup publishes nothing.
    upserts.clear()
    answers = {"demo1.example": True}
    monkeypatch.setattr(hm.dkim, "_public_dmarc_exists", lambda name: answers.get(name, False))
    assert hm.dkim._ensure_default_dmarc("shop.demo1.example", "shop.demo1.example") is False
    answers["demo1.example"] = None
    assert hm.dkim._ensure_default_dmarc("shop.demo1.example", "shop.demo1.example") is False
    answers["demo1.example"] = False
    assert hm.dkim._ensure_default_dmarc("shop.demo1.example", "shop.demo1.example") is True
    assert upserts == ["_dmarc"]


# --- Round 2: webmail purge, suspended logins, mail hostname ---------------


def _account(username):
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        return session.scalar(select(Account).where(Account.username == username))


def test_mail_domain_delete_and_termination_purge_webmail_rows(isolated_db, stub_sysops, stub_mail, monkeypatch):
    order = []
    monkeypatch.setattr(hm.webmail_sso, "revoke_domain", lambda domain: order.append(("revoke", domain)))
    monkeypatch.setattr(hm.webmail_sso, "purge_domain", lambda domain: order.append(("purge", domain)))
    monkeypatch.setattr(hm.webmail_sso, "purge_account", lambda account_id: order.append(("purge_account", account_id)))
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    hm.delete_mail_domain({"domain": "demo1.example"})
    assert order == [("revoke", "demo1.example"), ("purge", "demo1.example")]
    assert ("delete_mail_domain", "demo1.example") in stub_mail

    order.clear()
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    account = _account("demo1")
    hm.terminate_account_mail(account)
    assert order == [("revoke", "demo1.example"), ("purge", "demo1.example"), ("purge_account", account.id)]


@pytest.fixture(autouse=True)
def no_linux_logins(monkeypatch):
    """Suspension also expires the Linux login; these accounts are fictional."""
    monkeypatch.setattr(ha, "block_system_logins", lambda username: None)
    monkeypatch.setattr(ha, "allow_system_logins", lambda username: None)


@pytest.fixture()
def login_block(tmp_path, monkeypatch):
    """The Dovecot suspended-login list in a throwaway directory."""
    from types import SimpleNamespace

    passdb = tmp_path / "08-boron-suspended-auth.conf"
    passdb.write_text("passdb {}\n")
    listing = tmp_path / "boron-suspended-mail-domains"
    listing.write_text("")
    monkeypatch.setattr(hm.mail, "SUSPENDED_PASSDB_CONFIG", passdb)
    monkeypatch.setattr(hm.mail, "SUSPENDED_DOMAINS_FILE", listing)
    kicked = []
    monkeypatch.setattr(hm.mail, "end_login_sessions", lambda domains: kicked.extend(domains))
    return SimpleNamespace(listing=listing, kicked=kicked)


def test_suspension_refuses_mail_logins_and_unsuspension_restores_them(
        isolated_db, stub_sysops, stub_mail, login_block, monkeypatch):
    """Live finding: IMAP still worked while the account was suspended."""
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [hm.suspend_account_mail])
    monkeypatch.setattr(ha, "UNSUSPEND_HOOKS", [hm.unsuspend_account_mail])
    for name in ("demo1", "demo2"):
        ha.create_account({"username": name})
        hm.create_mail_domain({"username": name, "domain": f"{name}.example"})
    hm.create_mail_domain({"username": "demo1", "domain": "shop.demo1.example"})

    ha.suspend_account({"username": "demo1"})
    assert login_block.listing.read_text() == "demo1.example\nshop.demo1.example\n"
    assert sorted(login_block.kicked) == ["demo1.example", "shop.demo1.example"]
    assert login_block.listing.stat().st_mode & 0o777 == 0o640
    ha.suspend_account({"username": "demo2"})
    assert hm.mail.suspended_login_domains() == {"demo1.example", "shop.demo1.example", "demo2.example"}

    ha.unsuspend_account({"username": "demo1"})
    assert login_block.listing.read_text() == "demo2.example\n"
    # Delivery is untouched: the mail domains themselves stay in place.
    assert ("delete_mail_domain", "demo1.example") not in stub_mail


def test_domain_created_while_suspended_is_blocked_and_freed_on_delete(
        isolated_db, stub_sysops, stub_mail, login_block, monkeypatch):
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [hm.suspend_account_mail])
    ha.create_account({"username": "demo1"})
    ha.suspend_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "late.example"})
    assert hm.mail.suspended_login_domains() == {"late.example"}
    # A later owner of the name must not inherit the suspension.
    hm.delete_mail_domain({"domain": "late.example"})
    assert hm.mail.suspended_login_domains() == set()


def test_failed_mail_domain_create_releases_its_login_block(isolated_db, stub_sysops, stub_mail, login_block, monkeypatch):
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [])
    ha.create_account({"username": "demo1"})
    ha.suspend_account({"username": "demo1"})

    def broken(domain, active=True):
        raise RuntimeError("mariadb unavailable")

    monkeypatch.setattr(hm.mail, "create_mail_domain", broken)
    with pytest.raises(RuntimeError, match="mariadb unavailable"):
        hm.create_mail_domain({"username": "demo1", "domain": "late.example"})
    assert hm.mail.suspended_login_domains() == set()


def test_upgrade_reconcile_blocks_exactly_the_suspended_accounts(isolated_db, stub_sysops, stub_mail, login_block, monkeypatch):
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [])
    for name in ("demo1", "demo2"):
        ha.create_account({"username": name})
        hm.create_mail_domain({"username": name, "domain": f"{name}.example"})
    ha.suspend_account({"username": "demo1"})  # as a release without the list did
    login_block.listing.write_text("stale.example\n")
    assert hm.reconcile_suspended_mail_logins() == ["demo1.example"]
    assert login_block.listing.read_text() == "demo1.example\n"


def test_login_list_is_not_managed_without_the_dovecot_passdb(tmp_path, monkeypatch):
    monkeypatch.setattr(hm.mail, "SUSPENDED_PASSDB_CONFIG", tmp_path / "missing.conf")
    monkeypatch.setattr(hm.mail, "SUSPENDED_DOMAINS_FILE", tmp_path / "list")
    assert hm.mail.update_suspended_login_domains(add=["demo1.example"]) is False
    assert not (tmp_path / "list").exists()


def test_open_sessions_are_ended_best_effort(monkeypatch):
    from types import SimpleNamespace

    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[-1] == "*@broken.example":
            raise TimeoutError("doveadm hung")
        return SimpleNamespace(returncode=68, stderr="", stdout="")

    monkeypatch.setattr(hm.mail.shutil, "which", lambda name: "/usr/bin/doveadm")
    monkeypatch.setattr(hm.mail, "run", run)
    hm.mail.end_login_sessions(["broken.example", "demo1.example"])
    assert commands == [["doveadm", "kick", "*@broken.example"], ["doveadm", "kick", "*@demo1.example"]]


def test_suspend_hooks_are_registered_by_the_daemon():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "daemon" / "server.py").read_text()
    assert "SUSPEND_HOOKS.append(lambda account: handlers_mail.suspend_account_mail(account))" in source
    assert "UNSUSPEND_HOOKS.append(lambda account: handlers_mail.unsuspend_account_mail(account))" in source


def test_webmail_sso_refuses_suspended_accounts(isolated_db, stub_sysops, stub_mail, monkeypatch):
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [])
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    ha.suspend_account({"username": "demo1"})
    with pytest.raises(ValidationError, match="not active"):
        hm.webmail_sso.create_launch({"username": "demo1", "mailbox": "john@demo1.example"})


def test_mail_hostname_cannot_become_a_mail_domain(isolated_db, stub_sysops, stub_mail, monkeypatch):
    monkeypatch.setattr(settings, "mail_hostname", "mail.demo1.example")
    ha.create_account({"username": "demo1"})
    with pytest.raises(ValidationError, match="mail hostname"):
        hm.create_mail_domain({"username": "demo1", "domain": "mail.demo1.example"})
    assert ("create_mail_domain", "mail.demo1.example") not in stub_mail


def test_mailbox_list_carries_mail_client_settings(isolated_db, stub_sysops, stub_mail, monkeypatch):
    monkeypatch.setattr(settings, "mail_hostname", "mail.host.test")
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    client = hm.list_mailboxes({"domain": "demo1.example"})["client_settings"]
    assert client["hostname"] == "mail.host.test" and client["username"] == "full_address"
    assert [(row["protocol"], row["port"], row["security"]) for row in client["incoming"]] == [
        ("IMAP", 993, "SSL/TLS"), ("IMAP", 143, "STARTTLS"), ("POP3", 995, "SSL/TLS"), ("POP3", 110, "STARTTLS")]
    assert [(row["protocol"], row["port"], row["security"]) for row in client["outgoing"]] == [
        ("SMTP", 465, "SSL/TLS"), ("SMTP", 587, "STARTTLS")]


def test_first_mailbox_reports_the_mail_domain_it_created(isolated_db, stub_mail, monkeypatch):
    """Round 2 (fe-customer): the SPA creates the mail domain through the
    first mailbox, so that mailbox's result carries the DKIM/DMARC outcome."""
    from shared.db import write_session
    from shared.models import Account, Domain
    with write_session() as session:
        a = Account(username='mailqa', status='active'); session.add(a); session.flush()
        session.add(Domain(account_id=a.id, domain='mailqa.example', docroot='/home/mailqa/public_html', kind='primary'))
    dkim_result = {"dmarc_published": True, "dmarc_record_value": "v=DMARC1; p=none"}
    monkeypatch.setattr(hm.dkim, 'setup_dns_signing', lambda domain: dict(dkim_result))
    first = hm.create_mailbox({'domain': 'mailqa.example', 'local_part': 'hello', 'password': 'StrongMailPassword_123!'})
    assert first['mail_domain'] == {"created": True, "dkim": dkim_result}
    second = hm.create_mailbox({'domain': 'mailqa.example', 'local_part': 'second', 'password': 'StrongMailPassword_123!'})
    assert 'mail_domain' not in second


def test_suspension_ends_sessions_even_when_the_deny_list_already_named_the_domain(
        isolated_db, stub_sysops, stub_mail, login_block, monkeypatch):
    """An interrupted earlier suspension left the domain on the list: the
    sessions opened since must still be ended."""
    monkeypatch.setattr(ha, "SUSPEND_HOOKS", [hm.suspend_account_mail])
    ha.create_account({"username": "demo1"})
    hm.create_mail_domain({"username": "demo1", "domain": "demo1.example"})
    login_block.listing.write_text("demo1.example\n")
    ha.suspend_account({"username": "demo1"})
    assert login_block.kicked == ["demo1.example"]


def test_autoresponder_dates_are_returned_as_iso_strings(monkeypatch):
    """Round 2 (fe-customer): pymysql decodes DATE columns to datetime.date,
    which the RPC frame cannot encode (502 'Object of type date ...')."""
    import datetime as dt
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace

    row = {"subject": "Away", "body": "Back soon", "start_date": dt.date(2026, 12, 1),
           "end_date": None, "active": 1}

    @contextmanager
    def cursor():
        yield SimpleNamespace(execute=lambda *a: None, fetchone=lambda: dict(row))

    monkeypatch.setattr(hm.mail, "_domain_id", lambda domain: 1)
    monkeypatch.setattr(hm.mail, "_mail_user_id", lambda domain_id, local_part: 2)
    monkeypatch.setattr(hm.mail, "_connect", lambda: SimpleNamespace(cursor=cursor, close=lambda: None))
    result = hm.mail.get_autoresponder("demo1.example", "john")
    assert result["start_date"] == "2026-12-01" and result["end_date"] is None
    json.dumps(result)
    row["start_date"] = None
    assert hm.mail.get_autoresponder("demo1.example", "john")["start_date"] is None
