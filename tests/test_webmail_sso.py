import datetime as dt
import hashlib

import pytest
from sqlalchemy import select

from daemon import webmail_sso
from shared.db import write_session
from shared.models import Account, MailDomain, MailUser, WebmailLaunch
from shared.validation import ValidationError


class _Cursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, rows):
        for params in rows:
            self.calls.append((sql, params))


class _Connection:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _Cursor(self.calls)

    def close(self):
        return None


@pytest.fixture()
def mailbox(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="mailone", status="active")
        db.add(account)
        db.flush()
        domain = MailDomain(account_id=account.id, domain="example.test")
        db.add(domain)
        db.flush()
        db.add(MailUser(mail_domain_id=domain.id, local_part="hello", domain="example.test"))
    calls = []
    monkeypatch.setattr(webmail_sso, "ensure_schema", lambda: None)
    monkeypatch.setattr(webmail_sso.mail, "_connect", lambda: _Connection(calls))
    monkeypatch.setattr(webmail_sso.mail, "list_mailboxes", lambda domain: [
        {"local_part": "hello", "quota_mb": 1024, "active": True}
    ])
    monkeypatch.setattr(webmail_sso.mail, "hash_password", lambda value: "{TEST}" + hashlib.sha256(value.encode()).hexdigest())
    return account.id, calls


def test_launch_token_is_hash_only_and_redeems_once(mailbox):
    account_id, calls = mailbox
    launch = webmail_sso.create_launch({
        "username": "mailone", "mailbox": "hello@example.test", "source_ip": "192.0.2.8",
    })
    assert launch["token"] not in repr(calls)
    with write_session() as db:
        row = db.scalar(select(WebmailLaunch).where(WebmailLaunch.account_id == account_id))
        assert row.token_hash == hashlib.sha256(launch["token"].encode()).hexdigest()
        assert launch["token"] not in row.credential_enc
        assert row.redeemed_at is None
    result = webmail_sso.redeem(launch["token"])
    assert result["username"] == "hello@example.test"
    assert result["password"] and result["password"] not in repr(calls)
    with pytest.raises(ValidationError, match="already used"):
        webmail_sso.redeem(launch["token"])


def test_new_launch_replaces_older_mailbox_credential(mailbox):
    account_id, calls = mailbox
    first = webmail_sso.create_launch({
        "username": "mailone", "mailbox": "hello@example.test", "source_ip": "192.0.2.8",
    })
    second = webmail_sso.create_launch({
        "username": "mailone", "mailbox": "hello@example.test", "source_ip": "192.0.2.8",
    })

    first_digest = hashlib.sha256(first["token"].encode()).hexdigest()
    second_digest = hashlib.sha256(second["token"].encode()).hexdigest()
    with write_session() as db:
        first_row = db.scalar(select(WebmailLaunch).where(WebmailLaunch.token_hash == first_digest))
        second_row = db.scalar(select(WebmailLaunch).where(WebmailLaunch.token_hash == second_digest))
        assert first_row.account_id == account_id
        assert first_row.revoked_at is not None
        assert second_row.revoked_at is None

    assert sum("UPDATE webmail_session SET revoked=1" in sql for sql, _params in calls) == 2
    with pytest.raises(ValidationError, match="already used"):
        webmail_sso.redeem(first["token"])
    assert webmail_sso.redeem(second["token"])["username"] == "hello@example.test"


def test_redeem_and_revoke_audit_only_non_secret_metadata(mailbox, monkeypatch):
    _account_id, _calls = mailbox
    events = []
    monkeypatch.setattr(webmail_sso.audit, "record", lambda *args: events.append(args))
    launch = webmail_sso.create_launch({
        "username": "mailone", "mailbox": "hello@example.test", "source_ip": "192.0.2.8",
    })
    result = webmail_sso.redeem(launch["token"], "2001:db8::1")
    webmail_sso.revoke(result["launch_id"], "192.0.2.9")

    rendered = repr(events)
    assert [event[2] for event in events] == ["webmail.launch.redeem", "webmail.launch.revoke"]
    assert launch["token"] not in rendered
    assert result["password"] not in rendered
    assert "hello@example.test" in rendered
    assert "2001:db8::1" in rendered


def test_webmail_exchange_rejects_invalid_source_address(mailbox):
    launch = webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})
    with pytest.raises(ValidationError, match="source address"):
        webmail_sso.redeem(launch["token"], "not-an-ip")


def test_launch_expiry_and_disabled_mailbox_fail_closed(mailbox, monkeypatch):
    _account_id, _calls = mailbox
    launch = webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})
    with write_session() as db:
        row = db.scalar(select(WebmailLaunch).where(WebmailLaunch.token_hash == hashlib.sha256(launch["token"].encode()).hexdigest()))
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    with pytest.raises(ValidationError, match="expired"):
        webmail_sso.redeem(launch["token"])

    monkeypatch.setattr(webmail_sso.mail, "list_mailboxes", lambda domain: [
        {"local_part": "hello", "quota_mb": 1024, "active": False}
    ])
    with pytest.raises(ValidationError, match="disabled"):
        webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})


def test_cross_account_mailbox_is_rejected(mailbox):
    with write_session() as db:
        other = Account(username="mailtwo", status="active")
        db.add(other)
    with pytest.raises(ValidationError, match="does not belong"):
        webmail_sso.create_launch({"username": "mailtwo", "mailbox": "hello@example.test"})


def _launch_rows():
    with write_session() as db:
        return sorted((row.account_id, row.mailbox) for row in db.scalars(select(WebmailLaunch)).all())


def test_purge_domain_deletes_launches_and_credentials_of_that_domain_only(mailbox):
    account_id, calls = mailbox
    webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})
    with write_session() as db:
        db.add(WebmailLaunch(token_hash="b" * 64, account_id=account_id, mailbox="hi@other.test",
                             credential_hash="x", credential_enc="x",
                             expires_at=dt.datetime.now(dt.timezone.utc)))
    calls.clear()
    webmail_sso.purge_domain("example.test")
    assert _launch_rows() == [(account_id, "hi@other.test")]
    assert calls == [("DELETE FROM webmail_session WHERE mailbox LIKE %s", ("%@example.test",))]

    calls.clear()
    webmail_sso.purge_domain("example.test")  # nothing left: no MariaDB round trip
    assert calls == []


def test_purge_account_deletes_every_launch_of_the_account(mailbox):
    account_id, calls = mailbox
    first = webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})
    webmail_sso.revoke(webmail_sso.redeem(first["token"])["launch_id"])
    webmail_sso.create_launch({"username": "mailone", "mailbox": "hello@example.test"})
    with write_session() as db:
        ids = sorted(db.scalars(select(WebmailLaunch.id).where(WebmailLaunch.account_id == account_id)).all())
        other = Account(username="mailtwo", status="active")
        db.add(other)
        db.flush()
        db.add(WebmailLaunch(token_hash="c" * 64, account_id=other.id, mailbox="x@example.test",
                             credential_hash="x", credential_enc="x",
                             expires_at=dt.datetime.now(dt.timezone.utc)))
        other_id = other.id
    calls.clear()
    webmail_sso.purge_account(account_id)
    assert _launch_rows() == [(other_id, "x@example.test")]
    assert {sql for sql, _params in calls} == {"DELETE FROM webmail_session WHERE launch_id=%s"}
    assert sorted(params for _sql, params in calls) == [(item,) for item in ids]
