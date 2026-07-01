import os

import pytest

from daemon import autoresponder as ar


def test_render_no_date_range_is_valid_sieve():
    content = ar.render_sieve_script("Re: your message", "I'll reply soon.", None, None)
    ar._validate_sieve_content(content)


def test_render_with_date_range_is_valid_sieve():
    content = ar.render_sieve_script("Out of office", "Away until next week.", "2026-07-01", "2026-07-15")
    ar._validate_sieve_content(content)
    assert 'currentdate :value "ge" "date" "2026-07-01"' in content
    assert 'currentdate :value "le" "date" "2026-07-15"' in content


def test_render_with_only_start_date_is_valid_sieve():
    content = ar.render_sieve_script("Away", "body text", "2026-01-01", None)
    ar._validate_sieve_content(content)


def test_render_with_only_end_date_is_valid_sieve():
    content = ar.render_sieve_script("Away", "body text", None, "2026-12-31")
    ar._validate_sieve_content(content)


def test_render_escapes_quotes_in_subject():
    content = ar.render_sieve_script('Re: "urgent" issue', "body", None, None)
    ar._validate_sieve_content(content)
    assert '\\"urgent\\"' in content


def test_render_dot_stuffs_lone_dot_lines_in_body():
    content = ar.render_sieve_script("subject", "line one\n.\nline after lone dot", None, None)
    ar._validate_sieve_content(content)
    lines = content.splitlines()
    assert "." in lines  # the block terminator itself
    assert ".." in lines  # the stuffed former lone-dot line


def test_render_includes_addresses_clause_for_mailbox_robustness():
    """Regression test for a real bug found by live testing: without an
    explicit :addresses clause, Dovecot's vacation extension silently
    discards the auto-reply for any message that reached this mailbox
    without the exact address appearing in a To/Cc header (e.g. via a
    forwarder rule, or a raw test message with no headers at all) --
    confirmed live via dovecot logging "discarding vacation response ...
    no known (envelope) recipient address found in message headers"."""
    content = ar.render_sieve_script("subj", "body", None, None, mailbox_address="john@example.com")
    ar._validate_sieve_content(content)
    assert ':addresses ["john@example.com"]' in content


def test_apply_autoresponder_passes_mailbox_address(fake_mailbox_home, monkeypatch):
    monkeypatch.setattr(ar.os, "chown", lambda *a, **k: None)
    ar.apply_autoresponder("demo1.example", "john", "Away", "body text", None, None)
    content = (fake_mailbox_home / ".dovecot.sieve").read_text()
    assert ':addresses ["john@demo1.example"]' in content


def test_invalid_sieve_raises():
    with pytest.raises(ar.AutoresponderError):
        ar._validate_sieve_content("this is not { valid sieve syntax at all")


@pytest.fixture()
def fake_mailbox_home(tmp_path, monkeypatch):
    home = tmp_path / "demo1.example" / "john"
    home.mkdir(parents=True)
    monkeypatch.setattr(ar, "VMAIL_BASE", str(tmp_path))
    return home


def test_apply_autoresponder_writes_file_with_correct_ownership(fake_mailbox_home, monkeypatch):
    monkeypatch.setattr(ar.os, "chown", lambda *a, **k: None)  # not running as root in tests
    ar.apply_autoresponder("demo1.example", "john", "Away", "body text", None, None)
    sieve_path = fake_mailbox_home / ".dovecot.sieve"
    assert sieve_path.exists()
    assert oct(sieve_path.stat().st_mode)[-3:] == "600"
    assert "vacation" in sieve_path.read_text()


def test_apply_autoresponder_requires_existing_mail_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "VMAIL_BASE", str(tmp_path))
    with pytest.raises(ar.AutoresponderError):
        ar.apply_autoresponder("nodomain.example", "nobody", "Away", "body", None, None)


def test_apply_autoresponder_creates_mailbox_home_if_missing(tmp_path, monkeypatch):
    """Regression test for a real bug found by live testing: Dovecot
    creates a mailbox's Maildir home lazily at first delivery/login, not
    at mailbox-creation time -- setting an autoresponder right after
    creating a brand new mailbox (before any mail has ever arrived) must
    still work."""
    domain_dir = tmp_path / "demo1.example"
    domain_dir.mkdir()  # domain provisioned, but this mailbox never received mail yet
    monkeypatch.setattr(ar, "VMAIL_BASE", str(tmp_path))
    monkeypatch.setattr(ar.os, "chown", lambda *a, **k: None)

    ar.apply_autoresponder("demo1.example", "brandnew", "Away", "body", None, None)

    assert (domain_dir / "brandnew" / ".dovecot.sieve").exists()


def test_remove_autoresponder_deletes_file(fake_mailbox_home, monkeypatch):
    monkeypatch.setattr(ar.os, "chown", lambda *a, **k: None)
    ar.apply_autoresponder("demo1.example", "john", "Away", "body text", None, None)
    sieve_path = fake_mailbox_home / ".dovecot.sieve"
    assert sieve_path.exists()
    ar.remove_autoresponder("demo1.example", "john")
    assert not sieve_path.exists()


def test_remove_autoresponder_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "VMAIL_BASE", str(tmp_path))
    ar.remove_autoresponder("demo1.example", "nobody")  # must not raise
