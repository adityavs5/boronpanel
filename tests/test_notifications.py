import pytest
from sqlalchemy import select

from daemon import notifications as nt
from shared.db import write_session
from shared.models import Account, AccountNotificationPrefs, NotificationSettings


def _make_account(username="demo1"):
    with write_session() as session:
        account = Account(username=username, status="active")
        session.add(account)
        session.flush()
        return account.id


@pytest.fixture()
def sent_emails(monkeypatch):
    sent = []

    def fake_send(sender, recipient, subject, body):
        sent.append({"sender": sender, "recipient": recipient, "subject": subject, "body": body})

    monkeypatch.setattr(nt, "_send_email", fake_send)
    return sent


def _account(account_id):
    with write_session() as session:
        return session.get(Account, account_id)


def test_maybe_send_noop_when_sender_not_configured(isolated_db, sent_emails):
    account_id = _make_account()
    with write_session() as session:
        session.add(AccountNotificationPrefs(account_id=account_id, customer_email="c@example.com"))
    result = nt.maybe_send("account.created", _account(account_id))
    assert result is False
    assert sent_emails == []


def test_maybe_send_noop_when_event_disabled_globally(isolated_db, sent_emails):
    account_id = _make_account()
    with write_session() as session:
        session.add(NotificationSettings(id=1, sender_address="boron@example.com", events={"account.created": False}))
        session.add(AccountNotificationPrefs(account_id=account_id, customer_email="c@example.com"))
    result = nt.maybe_send("account.created", _account(account_id))
    assert result is False
    assert sent_emails == []


def test_maybe_send_noop_when_no_customer_email(isolated_db, sent_emails):
    account_id = _make_account()
    with write_session() as session:
        session.add(NotificationSettings(id=1, sender_address="boron@example.com"))
    result = nt.maybe_send("account.created", _account(account_id))
    assert result is False
    assert sent_emails == []


def test_maybe_send_noop_when_event_disabled_per_account(isolated_db, sent_emails):
    account_id = _make_account()
    with write_session() as session:
        session.add(NotificationSettings(id=1, sender_address="boron@example.com"))
        session.add(AccountNotificationPrefs(account_id=account_id, customer_email="c@example.com", events={"account.created": False}))
    result = nt.maybe_send("account.created", _account(account_id))
    assert result is False
    assert sent_emails == []


def test_maybe_send_sends_when_fully_enabled(isolated_db, sent_emails):
    account_id = _make_account()
    with write_session() as session:
        session.add(NotificationSettings(id=1, sender_address="boron@example.com"))
        session.add(AccountNotificationPrefs(account_id=account_id, customer_email="c@example.com"))
    result = nt.maybe_send("account.created", _account(account_id), initial_password="s3cret!Passw0rd")
    assert result is True
    assert len(sent_emails) == 1
    assert sent_emails[0]["recipient"] == "c@example.com"
    assert sent_emails[0]["sender"] == "boron@example.com"
    assert "s3cret!Passw0rd" in sent_emails[0]["body"]


def test_maybe_send_returns_false_none_account(isolated_db, sent_emails):
    assert nt.maybe_send("account.created", None) is False
    assert sent_emails == []


def test_maybe_send_handles_smtp_failure_gracefully(isolated_db, monkeypatch):
    account_id = _make_account()
    with write_session() as session:
        session.add(NotificationSettings(id=1, sender_address="boron@example.com"))
        session.add(AccountNotificationPrefs(account_id=account_id, customer_email="c@example.com"))

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(nt, "_send_email", boom)
    result = nt.maybe_send("account.created", _account(account_id))
    assert result is False


# --- admin settings -----------------------------------------------------


def test_get_settings_creates_default_row_lazily(isolated_db):
    data = nt.get_settings()
    assert data["sender_address"] == ""
    assert data["events"]["account.created"] is True
    with write_session() as session:
        assert session.get(NotificationSettings, 1) is not None


def test_set_settings_updates_sender_and_events(isolated_db):
    nt.set_settings({"sender_address": "ops@example.com", "events": {"account.created": False}})
    data = nt.get_settings()
    assert data["sender_address"] == "ops@example.com"
    assert data["events"]["account.created"] is False
    # Untouched events must not be affected by a partial update.
    assert data["events"]["backup.completed"] is True


def test_set_settings_rejects_unknown_event_type(isolated_db):
    with pytest.raises(nt.ValidationError):
        nt.set_settings({"events": {"not.a.real.event": True}})


def test_set_settings_rejects_invalid_sender_email(isolated_db):
    with pytest.raises(Exception):
        nt.set_settings({"sender_address": "not-an-email"})


def test_set_settings_events_only_update_preserves_sender_address(isolated_db):
    """Regression test for a real bug found by adversarial review,
    confirmed live against an isolated SQLite DB: the original
    `params.get("sender_address", "")` unconditionally overwrote the
    sender on every call, so an events-only update (e.g. from the UI's own
    per-event checkboxes) silently wiped it to "" -- which `maybe_send`
    treats as "notifications globally disabled," silently turning off
    every notification for every account."""
    nt.set_settings({"sender_address": "ops@example.com"})
    nt.set_settings({"events": {"account.created": False}})
    data = nt.get_settings()
    assert data["sender_address"] == "ops@example.com"
    assert data["events"]["account.created"] is False


def test_set_settings_can_still_explicitly_clear_sender_address(isolated_db):
    nt.set_settings({"sender_address": "ops@example.com"})
    nt.set_settings({"sender_address": ""})
    assert nt.get_settings()["sender_address"] == ""


# --- per-account prefs ----------------------------------------------------


def test_get_prefs_unknown_account_raises(isolated_db):
    with pytest.raises(RuntimeError):
        nt.get_prefs({"username": "ghost1"})


def test_set_prefs_creates_row_and_updates(isolated_db):
    _make_account("demo1")
    result = nt.set_prefs({"username": "demo1", "customer_email": "c@example.com", "events": {"login.new": False}})
    assert result["customer_email"] == "c@example.com"
    assert result["events"]["login.new"] is False
    assert result["events"]["account.created"] is True

    again = nt.get_prefs({"username": "demo1"})
    assert again["customer_email"] == "c@example.com"


def test_set_prefs_clears_email_with_empty_string(isolated_db):
    _make_account("demo1")
    nt.set_prefs({"username": "demo1", "customer_email": "c@example.com"})
    result = nt.set_prefs({"username": "demo1", "customer_email": ""})
    assert result["customer_email"] is None


def test_set_prefs_events_only_update_preserves_customer_email(isolated_db):
    """Same real bug class as set_settings's own regression test above,
    found in the same adversarial review pass: an events-only update must
    not silently clear the customer's email address as a side effect."""
    _make_account("demo1")
    nt.set_prefs({"username": "demo1", "customer_email": "c@example.com"})
    nt.set_prefs({"username": "demo1", "events": {"login.new": False}})
    result = nt.get_prefs({"username": "demo1"})
    assert result["customer_email"] == "c@example.com"
    assert result["events"]["login.new"] is False
