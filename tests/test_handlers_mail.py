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
    with pytest.raises(RuntimeError, match="requires an owning hosting account"):
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
