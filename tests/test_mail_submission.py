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
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = mail_account / "dkim" / "demo.example" / "default.private"
    key.parent.mkdir(parents=True)
    key.write_bytes(rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with write_session() as db:
        db.add(DkimKey(domain="demo.example", selector="default", signing_active=True))

    def no_subprocess(*args, **kwargs):
        raise AssertionError("signing must not depend on an external program or PATH")

    monkeypatch.setattr(mail_submission.subprocess, "run", no_subprocess)
    signed = mail_submission.sign_if_configured(_message(), "demo.example")
    first = signed.split(b"\r\n\r\n", 1)[0]
    assert signed.startswith(b"DKIM-Signature: v=1;")
    assert b"a=rsa-sha256" in first and b"d=demo.example" in first and b"s=default" in first
    assert signed.endswith(_message())


def test_missing_dkim_key_is_a_temporary_failure(mail_account):
    with write_session() as db:
        db.add(DkimKey(domain="demo.example", selector="default", signing_active=True))
    with pytest.raises(mail_submission.MailSubmissionUnavailable, match="key is unavailable"):
        mail_submission.sign_if_configured(_message(), "demo.example")


def test_submission_adds_date_and_message_id_before_signing(mail_account):
    raw, _envelope = mail_submission.prepare_account_message(5011, _message(), "demo1@demo.example")
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert b"\r\nDate: " in head
    assert b"\r\nMessage-ID: <" in head and b"@demo.example>" in head
    dated = _message().replace(b"Subject:", b"Date: Mon, 02 Nov 2026 10:00:00 +0000\r\nSubject:")
    raw, _envelope = mail_submission.prepare_account_message(5011, dated, "demo1@demo.example")
    assert raw.count(b"Date:") == 1 and b"Date: Mon, 02 Nov 2026 10:00:00 +0000" in raw


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


def test_invalid_recipient_is_a_permanent_refusal_not_a_retry(monkeypatch):
    # The pipe(8) filter maps unexpected exceptions to EX_TEMPFAIL; a
    # recipient the queue can never accept must bounce, not sit in the queue.
    monkeypatch.setattr(mail_submission.subprocess, "run",
                        lambda *a, **k: pytest.fail("nothing may be queued"))
    with pytest.raises(mail_submission.MailSubmissionError) as caught:
        mail_submission.submit_via_postfix(b"From: a@demo.example\r\n\r\nx", "a@demo.example", ["not an address"])
    assert not isinstance(caught.value, mail_submission.MailSubmissionUnavailable)
