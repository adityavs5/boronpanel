import subprocess
from pathlib import Path

import pytest

from daemon import mail_submission
from shared.db import write_session
from shared.models import Account, DkimKey, Domain


@pytest.fixture()
def mail_account(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(mail_submission.settings, "panel_hostname", "panel.example")
    monkeypatch.setattr(mail_submission.settings, "dkim_base_dir", str(tmp_path / "dkim"))
    with write_session() as db:
        account = Account(username="demo1", status="active", uid=5011, gid=5011,
                          primary_domain="demo.example")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="demo.example", kind="primary",
                      docroot="/home/demo1/public_html", suspended=False))
    return tmp_path


def _message(sender="demo1@demo.example"):
    return f"From: Demo <{sender}>\r\nTo: user@remote.example\r\nSubject: Test\r\n\r\nHello\r\n".encode()


def test_account_submission_preserves_owned_sender(mail_account):
    raw, envelope = mail_submission.prepare_account_message(
        5011, _message(), "demo1@demo.example")
    assert envelope == "demo1@demo.example"
    assert b"From: Demo <demo1@demo.example>" in raw


@pytest.mark.parametrize("envelope,header", [
    ("attacker@other.example", "demo1@demo.example"),
    ("demo1@demo.example", "attacker@other.example"),
    ("demo1@demo.example", "demo1@other.example"),
])
def test_account_submission_rejects_foreign_envelope_or_from(mail_account, envelope, header):
    with pytest.raises(mail_submission.MailSubmissionError, match="owned|same hosted"):
        mail_submission.prepare_account_message(5011, _message(header), envelope)


def test_php_placeholder_sender_is_canonicalized(mail_account):
    raw, envelope = mail_submission.prepare_account_message(
        5011, _message("demo1@panel.example"), "demo1@panel.example")
    assert envelope == "demo1@demo.example"
    assert b"demo1@demo.example" in raw
    assert b"demo1@panel.example" not in raw


def test_duplicate_from_is_rejected(mail_account):
    raw = _message().replace(b"To:", b"From: demo1@demo.example\r\nTo:")
    with pytest.raises(mail_submission.MailSubmissionError, match="duplicate|exactly one"):
        mail_submission.authorize_message(
            raw, "demo1@demo.example", username="demo1", primary_domain="demo.example",
            owned_domains={"demo.example"}, allow_local_placeholder=False)


def test_dkim_signer_uses_authorized_domain_key(mail_account, monkeypatch):
    key = mail_account / "dkim" / "demo.example" / "default.private"
    key.parent.mkdir(parents=True)
    key.write_text("private")
    with write_session() as db:
        db.add(DkimKey(domain="demo.example", selector="default", signing_active=True))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"DKIM-Signature: safe\r\n" + kwargs["input"], b"")

    monkeypatch.setattr(mail_submission.subprocess, "run", fake_run)
    signed = mail_submission.sign_if_configured(_message(), "demo.example")
    assert signed.startswith(b"DKIM-Signature: safe")
    assert calls[0][calls[0].index("-d") + 1] == "demo.example"
    assert calls[0][calls[0].index("-k") + 1] == str(key)


def test_submission_filter_requires_envelope_from_alignment(monkeypatch):
    monkeypatch.setattr(mail_submission, "sign_from_opendkim_table", lambda raw, domain: raw)
    with pytest.raises(mail_submission.MailSubmissionError, match="owned|same hosted"):
        mail_submission.prepare_submission_filter(
            _message("other@foreign.example"), "demo1@demo.example")


def test_mail_submission_unit_limits_writes_to_postfix_queue_paths():
    unit = (Path(__file__).parents[1] / "deploy" / "boron-mail-submit@.service").read_text()
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/spool/postfix/maildrop" in unit
    assert "ReadWritePaths=/var/spool/postfix/public" in unit
