"""Phase 8 feature 5: email delivery log (scoped, no cross-account leakage)."""
from daemon import maillog
from shared.db import write_session
from shared.models import Account, Domain, MailDomain

SAMPLE = [
    "Jul  6 12:00:01 host postfix/qmgr[999]: A1B2C3: from=<alice@mydomain.com>, size=1024, nrcpt=1 (queue active)",
    "Jul  6 12:00:02 host postfix/smtp[1000]: A1B2C3: to=<bob@external.com>, relay=mx[1.2.3.4]:25, delay=1.2, status=sent (250 2.0.0 OK)",
    "Jul  6 12:01:00 host postfix/qmgr[999]: D4E5F6: from=<charlie@otheracct.com>, size=2048, nrcpt=1 (queue active)",
    "Jul  6 12:01:01 host postfix/lmtp[1001]: D4E5F6: to=<dave@otheracct.com>, relay=x, status=sent (delivered)",
    "Jul  6 12:02:00 host postfix/qmgr[999]: 778899: from=<ext@sender.net>, size=500, nrcpt=1 (queue active)",
    "Jul  6 12:02:01 host postfix/lmtp[1001]: 778899: to=<alice@mydomain.com>, relay=x, status=sent (delivered to maildir)",
    "Jul  6 12:03:00 host postfix/smtp[1002]: BADBAD: to=<nobody@mydomain.com>, relay=none, status=bounced (host said: 550 No such user)",
]


def test_parse_scopes_to_account_domains():
    out = maillog.parse_maillog(SAMPLE, {"mydomain.com"})
    tos = [e["to"] for e in out]
    # The otheracct.com <-> otheracct.com message must NOT appear.
    assert "dave@otheracct.com" not in tos
    assert "bob@external.com" in tos  # outbound from account's domain
    assert "alice@mydomain.com" in tos  # inbound to account's domain


def test_parse_most_recent_first():
    out = maillog.parse_maillog(SAMPLE, {"mydomain.com"})
    # BADBAD bounce is the last log line -> first in the result.
    assert out[0]["status"] == "bounced"
    assert "550" in out[0]["reason"]


def test_parse_captures_from_via_queue_correlation():
    out = maillog.parse_maillog(SAMPLE, {"mydomain.com"})
    outbound = next(e for e in out if e["to"] == "bob@external.com")
    assert outbound["from"] == "alice@mydomain.com"  # from the earlier qmgr line
    assert outbound["status"] == "sent"


def test_parse_noqueue_reject_inline_from_to():
    line = ("Jul  6 13:00:00 host postfix/smtpd[555]: NOQUEUE: reject: RCPT from x[1.2.3.4]: "
            "554 5.7.1 blocked; from=<spam@evil.com> to=<victim@mydomain.com> proto=ESMTP status=rejected")
    out = maillog.parse_maillog([line], {"mydomain.com"})
    assert len(out) == 1
    assert out[0]["to"] == "victim@mydomain.com"
    assert out[0]["from"] == "spam@evil.com"
    assert out[0]["queue_id"] is None  # NOQUEUE normalized to None


def test_parse_empty_domains_returns_nothing():
    assert maillog.parse_maillog(SAMPLE, set()) == []


def test_parse_limit():
    out = maillog.parse_maillog(SAMPLE, {"mydomain.com"}, limit=1)
    assert len(out) == 1


# --- get_delivery_log (with account setup + monkeypatched file) -------------


def _setup_account(monkeypatch, tmp_path):
    with write_session() as db:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="mydomain.com", kind="primary", docroot="/home/demo1/public_html"))
        db.add(MailDomain(account_id=account.id, domain="mail.mydomain.com"))
    logfile = tmp_path / "mail.log"
    logfile.write_text("\n".join(SAMPLE) + "\n")
    monkeypatch.setattr(maillog.settings, "mail_log_path", str(logfile))


def test_get_delivery_log_scopes(isolated_db, monkeypatch, tmp_path):
    _setup_account(monkeypatch, tmp_path)
    result = maillog.get_delivery_log({"username": "demo1"})
    tos = [e["to"] for e in result["entries"]]
    assert "dave@otheracct.com" not in tos
    assert "bob@external.com" in tos
    assert "mydomain.com" in result["scoped_domains"]
    assert "mail.mydomain.com" in result["scoped_domains"]


def test_get_delivery_log_search(isolated_db, monkeypatch, tmp_path):
    _setup_account(monkeypatch, tmp_path)
    result = maillog.get_delivery_log({"username": "demo1", "search": "bounced"})
    assert len(result["entries"]) == 1
    assert result["entries"][0]["status"] == "bounced"


def test_get_delivery_log_missing_file(isolated_db, monkeypatch, tmp_path):
    _setup_account(monkeypatch, tmp_path)
    monkeypatch.setattr(maillog.settings, "mail_log_path", str(tmp_path / "nope.log"))
    result = maillog.get_delivery_log({"username": "demo1"})
    assert result["entries"] == []


def test_parse_attributes_local_submission_uid():
    lines = [
        "Sep 24 10:00:00 host postfix/pickup[1]: ABC123: uid=5001 from=<site>",
        "Sep 24 10:00:01 host postfix/qmgr[2]: ABC123: from=<wordpress@mydomain.com>, size=10, nrcpt=1 (queue active)",
        "Sep 24 10:00:02 host postfix/smtp[3]: ABC123: to=<person@remote.test>, relay=x, status=sent (250 OK)",
    ]
    result = maillog.parse_maillog(lines, {"mydomain.com"}, uid_to_username={5001: "demo1"})
    assert result[0]["source_user"] == "demo1"


def test_php_mail_script_attribution_stays_inside_account_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    script = home / "demo1" / "public_html" / "wp-content" / "plugin.php"
    script.parent.mkdir(parents=True)
    script.write_text("<?php")
    monkeypatch.setattr(maillog.settings, "home_base", str(home))
    lines = [
        f"[24-Sep-2026] mail() on [{script}:42]: To: victim@example.net -- Headers: From: x",
        "[24-Sep-2026] mail() on [/tmp/foreign.php:1]: To: victim@example.net",
    ]
    result = maillog.parse_php_mail_log(lines, "demo1")
    assert result == [{"username": "demo1", "script": str(script), "line": 42, "to": "victim@example.net"}]


def test_admin_stats_aggregates_account_sender_domain_and_script(isolated_db, monkeypatch, tmp_path):
    _setup_account(monkeypatch, tmp_path)
    home = tmp_path / "home"
    script = home / "demo1" / "public_html" / "send.php"
    script.parent.mkdir(parents=True)
    script.write_text("<?php")
    logs = home / "demo1" / "logs"
    logs.mkdir()
    (logs / "php-mail.log").write_text(
        f"[24-Sep-2026] mail() on [{script}:7]: To: bob@external.com -- Headers: From: alice@mydomain.com\n"
    )
    monkeypatch.setattr(maillog.settings, "home_base", str(home))
    result = maillog.get_admin_stats({})
    assert result["summary"]["sent"] >= 1
    assert {row["account"] for row in result["by_account"]} == {"demo1"}
    assert any(row["sender"] == "alice@mydomain.com" for row in result["by_sender"])
    assert result["scripts"][0]["script"] == str(script)
