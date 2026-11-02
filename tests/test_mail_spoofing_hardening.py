"""Regression tests: a tenant must not send mail as another tenant's domain.

Covers the three bypasses of the identity-bound submission design: the
loopback relay on port 25, Postfix's local sendmail/postdrop, and direct
outbound TCP/25, plus the submission (587) filter's own login check.
"""
import os
import shutil
import stat
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon import dkim, mail_egress, mail_hardening, mail_submission
from scripts import update_finalize, upgrade_runtime
from shared.db import write_session
from shared.models import Account, Domain

REPO = Path(__file__).resolve().parents[1]


def _ok(stdout=""):
    return SimpleNamespace(ok=True, returncode=0, stdout=stdout, stderr="")


def _fail():
    return SimpleNamespace(ok=False, returncode=1, stdout="", stderr="missing")


# --- Postfix main.cf policy --------------------------------------------------


def test_port_25_policy_never_relays_for_loopback_and_locks_queue_to_services():
    policy = mail_hardening.POSTFIX_MAIN_POLICY
    assert policy["smtpd_relay_restrictions"] == "reject_unauth_destination"
    assert "permit_mynetworks" not in policy["smtpd_relay_restrictions"]
    assert "127.0.0.0/8" not in policy["mynetworks"]
    users = [item.strip() for item in policy["authorized_submit_users"].split(",")]
    assert users == list(mail_hardening.TRUSTED_SUBMIT_USERS)
    assert "static:anyone" not in policy["authorized_submit_users"]
    # Dovecot vacation (vmail), the signer (opendkim) and spamc must keep
    # reinjecting; nothing in the list is a hosting account.
    assert {"root", "postfix", "opendkim", "debian-spamd", "vmail"} == set(users)


def test_dispatcher_is_installed_before_submit_users_are_narrowed(monkeypatch):
    order = []
    monkeypatch.setattr(mail_hardening, "install_sendmail_dispatcher",
                        lambda run: order.append("dispatcher"))
    mail_hardening.configure_postfix_policy(lambda args, **kw: order.append(args[-1]))
    assert order[0] == "dispatcher"
    assert "authorized_submit_users=root, postfix, opendkim, debian-spamd, vmail" in order
    assert "smtpd_relay_restrictions=reject_unauth_destination" in order


def test_dkim_reconciles_policy_and_passes_sasl_login_to_filter(tmp_path, monkeypatch):
    boron_dir = tmp_path / "postfix-boron"
    boron_dir.mkdir()
    (boron_dir / "mysql-virtual-mailboxes.cf").write_text("user = ro\npassword = x\nquery = SELECT 1\n")
    monkeypatch.setattr(dkim, "POSTFIX_BORON_DIR", boron_dir)
    monkeypatch.setattr(dkim, "SENDER_LOGIN_MAP", boron_dir / "mysql-sender-login.cf")
    monkeypatch.setattr(dkim.pwd, "getpwnam", lambda name: SimpleNamespace(pw_gid=os.getgid()))
    monkeypatch.setattr(dkim.os, "chown", lambda *a: None)
    policy_calls = []
    monkeypatch.setattr(dkim.mail_hardening, "configure_postfix_policy", lambda run: policy_calls.append(run))
    commands = []
    monkeypatch.setattr(dkim, "run", lambda args, **kw: commands.append(args))

    dkim._configure_postfix_milter()

    assert policy_calls == [dkim.run]
    flat = [" ".join(args) for args in commands]
    sign = next(item for item in flat if item.startswith("postconf -Me boron-sign/unix="))
    assert "--login=${sasl_username}" in sign
    assert "postconf -P submission/inet/smtpd_sender_restrictions=reject_authenticated_sender_login_mismatch" in flat
    assert "postconf -P submission/inet/smtpd_relay_restrictions=permit_sasl_authenticated,reject" in flat
    assert f"postconf -P submission/inet/smtpd_sender_login_maps=mysql:{boron_dir / 'mysql-sender-login.cf'}" in flat
    assert "postconf -e smtpd_milters=" in flat and "postconf -e non_smtpd_milters=" in flat


def test_dkim_surfaces_hardening_failure(tmp_path, monkeypatch):
    boron_dir = tmp_path / "postfix-boron"
    boron_dir.mkdir()
    (boron_dir / "mysql-virtual-mailboxes.cf").write_text("query = SELECT 1\n")
    monkeypatch.setattr(dkim, "POSTFIX_BORON_DIR", boron_dir)
    monkeypatch.setattr(dkim, "SENDER_LOGIN_MAP", boron_dir / "mysql-sender-login.cf")
    monkeypatch.setattr(dkim.pwd, "getpwnam", lambda name: SimpleNamespace(pw_gid=os.getgid()))
    monkeypatch.setattr(dkim.os, "chown", lambda *a: None)
    monkeypatch.setattr(dkim, "run", lambda args, **kw: None)

    def broken(run):
        raise mail_hardening.MailHardeningError("dpkg-divert is unavailable")

    monkeypatch.setattr(dkim.mail_hardening, "configure_postfix_policy", broken)
    with pytest.raises(dkim.DkimError, match="dpkg-divert"):
        dkim._configure_postfix_milter()


# --- local sendmail dispatcher -------------------------------------------------


@pytest.fixture()
def sendmail_paths(tmp_path, monkeypatch):
    sendmail = tmp_path / "sbin" / "sendmail"
    sendmail.parent.mkdir()
    real = tmp_path / "sbin" / "sendmail.postfix"
    monkeypatch.setattr(mail_hardening, "SENDMAIL_PATH", sendmail)
    monkeypatch.setattr(mail_hardening, "POSTFIX_SENDMAIL", real)
    monkeypatch.setattr(mail_hardening.shutil, "which", lambda name: "/usr/bin/" + name)
    return sendmail, real


def test_dispatcher_diverts_postfix_once_then_rewrites_itself(sendmail_paths):
    sendmail, real = sendmail_paths
    sendmail.write_bytes(b"\x7fELF postfix sendmail")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert args[:3] == ["dpkg-divert", "--local", "--rename"]
        assert args[args.index("--divert") + 1] == str(real)
        assert args[args.index("--add") + 1] == str(sendmail)
        sendmail.rename(real)

    mail_hardening.install_sendmail_dispatcher(fake_run)
    mail_hardening.install_sendmail_dispatcher(fake_run)

    assert len(calls) == 1
    assert real.read_bytes() == b"\x7fELF postfix sendmail"
    assert mail_hardening.dispatcher_installed()
    assert stat.S_IMODE(sendmail.stat().st_mode) == 0o755
    assert sendmail.read_text() == mail_hardening.render_dispatcher()


def test_dispatcher_refuses_to_divert_itself(sendmail_paths):
    sendmail, _real = sendmail_paths
    sendmail.write_text(mail_hardening.render_dispatcher())
    with pytest.raises(mail_hardening.MailHardeningError, match="missing"):
        mail_hardening.install_sendmail_dispatcher(lambda args, **kw: pytest.fail("must not divert"))


@pytest.mark.skipif(shutil.which("sh") is None or os.geteuid() == 0,
                    reason="needs a POSIX shell and an unprivileged test user")
def test_dispatcher_routes_hosting_users_to_identity_socket(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    fake = "#!/bin/sh\nprintf '%s|%s\\n' \"$(basename \"$0\")\" \"$*\" >> " + str(log) + "\n"
    postfix = tmp_path / "sendmail.postfix"
    msmtp = tmp_path / "msmtp"
    for path in (postfix, msmtp):
        path.write_text(fake)
        path.chmod(0o755)
    monkeypatch.setattr(mail_hardening, "POSTFIX_SENDMAIL", postfix)
    monkeypatch.setattr(mail_hardening, "MSMTP_BINARY", str(msmtp))
    dispatcher = tmp_path / "sendmail"
    dispatcher.write_text(mail_hardening.render_dispatcher())
    dispatcher.chmod(0o755)
    mailq = tmp_path / "mailq"
    mailq.symlink_to(dispatcher)

    # The test user is not a trusted service account: like a tenant's PHP,
    # `sendmail -f ceo@victim.example` must never reach Postfix's queue.
    subprocess.run([str(dispatcher), "-t", "-i", "-f", "ceo@victim.example"],
                   input=b"Subject: x\n\nbody\n", check=True)
    subprocess.run([str(mailq)], check=True)

    lines = log.read_text().splitlines()
    assert lines[0] == f"msmtp|-C {mail_hardening.MSMTP_CONFIG} -t -i -f ceo@victim.example"
    assert lines[1] == "sendmail.postfix|-bp"


def test_dispatcher_trusts_exactly_the_authorized_submit_users():
    script = mail_hardening.render_dispatcher()
    case_line = next(line.strip() for line in script.splitlines() if line.strip().startswith("root|"))
    assert case_line.split(")")[0].split("|") == list(mail_hardening.TRUSTED_SUBMIT_USERS)
    assert "id -run" in script  # Postfix also authorizes by the real UID


def test_identity_socket_queues_with_postfix_binary_not_dispatcher(sendmail_paths, monkeypatch):
    sendmail, real = sendmail_paths
    real.write_text("postfix")
    monkeypatch.setattr(mail_submission, "postfix_sendmail", mail_hardening.postfix_sendmail)
    calls = []
    monkeypatch.setattr(mail_submission.subprocess, "run",
                        lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, b"", b""))
    mail_submission.submit_via_postfix(b"Subject: x\r\n\r\nx\r\n", "demo1@demo.example", ["user@remote.example"])
    assert calls[0][0] == str(real)


# --- identity checks the dispatcher and filter rely on ------------------------


@pytest.fixture()
def mail_account(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(mail_submission.settings, "panel_hostname", "panel.example")
    monkeypatch.setattr(mail_submission.settings, "dkim_base_dir", str(tmp_path / "dkim"))
    with write_session() as db:
        for uid, name, domain in ((5011, "demo1", "demo.example"), (5012, "victim", "victim.example")):
            account = Account(username=name, status="active", uid=uid, gid=uid, primary_domain=domain)
            db.add(account)
            db.flush()
            db.add(Domain(account_id=account.id, domain=domain, kind="primary",
                          docroot=f"/home/{name}/public_html", suspended=False))
    return tmp_path


def _message(sender):
    return f"From: {sender}\r\nTo: user@remote.example\r\nSubject: Test\r\n\r\nHello\r\n".encode()


def test_local_sendmail_cannot_use_another_tenants_domain(mail_account):
    with pytest.raises(mail_submission.MailSubmissionError, match="owned"):
        mail_submission.prepare_account_message(5011, _message("ceo@victim.example"), "ceo@victim.example")
    raw, envelope = mail_submission.prepare_account_message(
        5012, _message("ceo@victim.example"), "ceo@victim.example")
    assert envelope == "ceo@victim.example"


def test_cron_style_bare_from_is_qualified_with_the_accounts_own_domain(mail_account):
    raw = b"From: root (Cron Daemon)\r\nTo: user@remote.example\r\nSubject: cron\r\n\r\nout\r\n"
    signed, envelope = mail_submission.prepare_account_message(5011, raw, "demo1@panel.example")
    assert envelope == "demo1@demo.example"
    assert b"From: demo1@demo.example" in signed
    with pytest.raises(mail_submission.MailSubmissionError):
        mail_submission.prepare_account_message(
            5011, b"From: victim (x)\r\nTo: a@b.example\r\n\r\nx\r\n", "demo1@panel.example")


def test_submission_filter_requires_sasl_login_to_be_the_envelope_sender(monkeypatch):
    monkeypatch.setattr(mail_submission, "sign_from_opendkim_table", lambda raw, domain: raw)
    with pytest.raises(mail_submission.MailSubmissionError, match="authenticated mailbox"):
        mail_submission.prepare_submission_filter(
            _message("ceo@victim.example"), "ceo@victim.example", login="demo1@demo.example")
    with pytest.raises(mail_submission.MailSubmissionError, match="authenticated mailbox"):
        mail_submission.prepare_submission_filter(
            _message("demo1@demo.example"), "demo1@demo.example", login="")
    raw, envelope = mail_submission.prepare_submission_filter(
        _message("demo1@demo.example"), "demo1@demo.example", login="Demo1@Demo.Example")
    assert envelope == "demo1@demo.example"


def test_sign_filter_cli_forwards_login(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mail_sign_filter_cli", REPO / "scripts" / "mail_sign_filter.py")
    saved = {key: os.environ.get(key) for key in ("BORON_CONFIG", "BORON_SECRETS", "BORON_API_SECRETS")}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    seen = {}

    def fake_prepare(raw, sender, login=None):
        seen["login"] = login
        raise mail_submission.MailSubmissionError("blocked")

    monkeypatch.setattr(module.mail_submission, "prepare_submission_filter", fake_prepare)
    monkeypatch.setattr(module.sys, "argv", ["filter", "--sender", "ceo@victim.example",
                                             "--login=demo1@demo.example", "--recipient", "a@b.example"])
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(read=lambda n: b"x")))
    assert module.main() == 77  # EX_NOPERM: 5.7.0 policy refusal, not 5.1.1 "user unknown"
    assert seen["login"] == "demo1@demo.example"


# --- outbound TCP/25 owner restriction -----------------------------------------


def test_egress_chain_allows_only_root_and_postfix(monkeypatch):
    monkeypatch.setattr(mail_egress.pwd, "getpwnam",
                        lambda name: SimpleNamespace(pw_uid={"root": 0, "postfix": 107}[name]))
    payload = mail_egress.render_chain(mail_egress.exempt_uids())
    assert payload.splitlines() == [
        "*filter",
        ":BORON-SMTP-OUT - [0:0]",
        "-A BORON-SMTP-OUT -m owner --uid-owner 0 -j RETURN",
        "-A BORON-SMTP-OUT -m owner --uid-owner 107 -j RETURN",
        "-A BORON-SMTP-OUT -j REJECT --reject-with tcp-reset",
        "COMMIT",
    ]
    assert mail_egress.JUMP == ["-p", "tcp", "--dport", "25", "-j", "BORON-SMTP-OUT"]


def test_egress_rule_also_covers_loopback():
    """Local port 25 accepts any sender for hosted domains, and a tenant owns
    forwarders on its own domains. Exempting lo would let a tenant hand
    MAIL FROM:<ceo@other-tenant> to 127.0.0.1:25 (or the server's own public
    address, which also routes over lo) and have it relayed, SPF-aligned."""
    assert "lo" not in mail_egress.JUMP
    assert "-o" not in mail_egress.JUMP and "-d" not in mail_egress.JUMP


def test_egress_apply_is_atomic_and_jumps_first_once(monkeypatch):
    monkeypatch.setattr(mail_egress, "exempt_uids", lambda: [0, 107])
    monkeypatch.setattr(mail_egress, "_ipv6_enabled", lambda: True)
    present = set()
    calls = []

    def fake_run(args, input_text=None, timeout=None):
        calls.append((args, input_text))
        if args[1] == "-C":
            return _ok() if args[0] in present else _fail()
        if args[1] == "-I":
            present.add(args[0])
        return _ok()

    monkeypatch.setattr(mail_egress, "run", fake_run)
    mail_egress.apply()
    mail_egress.apply()

    restores = [(args, payload) for args, payload in calls if args[0].endswith("-restore")]
    assert [args for args, _ in restores] == [["iptables-restore", "--noflush"], ["ip6tables-restore", "--noflush"]] * 2
    assert all("--uid-owner 107 -j RETURN" in payload for _, payload in restores)
    inserts = [args for args, _ in calls if args[1] == "-I"]
    assert inserts == [[tool, "-I", "OUTPUT", "1", *mail_egress.JUMP] for tool in ("iptables", "ip6tables")]


def test_egress_apply_failure_is_reported(monkeypatch):
    monkeypatch.setattr(mail_egress, "exempt_uids", lambda: [0])
    monkeypatch.setattr(mail_egress, "_ipv6_enabled", lambda: False)
    monkeypatch.setattr(mail_egress, "run", lambda args, **kw: _fail())
    assert mail_egress.main(["apply"]) == 1


def test_egress_unit_runs_apply_and_removes_on_stop():
    unit = (REPO / "deploy" / "boron-mail-egress.service").read_text()
    assert "ExecStart=/opt/boron/.venv/bin/python -m daemon.mail_egress apply" in unit
    assert "ExecStop=/opt/boron/.venv/bin/python -m daemon.mail_egress remove" in unit
    assert "RemainAfterExit=yes" in unit
    assert "After=ufw.service" in unit
    assert "WantedBy=multi-user.target" in unit


# --- installer and upgrade reconciliation --------------------------------------


def test_installer_installs_hardened_policy_on_every_profile():
    source = (REPO / "scripts" / "install.sh").read_text()
    assert 'postconf -e "smtpd_relay_restrictions = reject_unauth_destination"' in source
    assert 'postconf -e "mynetworks = 127.0.0.1/32 [::1]/128"' in source
    assert "--login=\\${sasl_username}" in source
    assert 'deploy/boron-mail-egress.service" /etc/systemd/system/' in source
    enable = "    run systemctl enable --now boron-mail-egress.service"
    assert enable in source.splitlines()  # not gated on the mail profile
    # configure_signer, which installs the dispatcher and narrows
    # authorized_submit_users, still runs on fresh installs.
    assert "from daemon.dkim import configure_signer; configure_signer()" in source


def test_upgrade_installs_and_starts_egress_unit(tmp_path, monkeypatch):
    target = tmp_path / "boron-mail-egress.service"
    monkeypatch.setattr(upgrade_runtime, "MAIL_EGRESS_UNIT", target)
    commands = []
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))
    upgrade_runtime.configure_mail_egress()
    assert target.read_text() == (REPO / "deploy" / "boron-mail-egress.service").read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "boron-mail-egress.service"],
        ["systemctl", "restart", "boron-mail-egress.service"],
    ]


def test_upgrade_main_reconciles_egress_and_postfix_policy():
    source = (REPO / "scripts" / "upgrade_runtime.py").read_text()
    main = source[source.index("def main("):]
    assert main.index("configure_mail_egress()") < main.index("dkim.configure_signer()")
    assert upgrade_runtime.MAIL_EGRESS_UNIT in upgrade_runtime._runtime_paths()


def test_rollback_removes_egress_unit_introduced_by_the_update(tmp_path, monkeypatch):
    unit = tmp_path / "boron-mail-egress.service"
    backup = tmp_path / "runtime-backup.json"
    monkeypatch.setattr(upgrade_runtime, "MAIL_EGRESS_UNIT", unit)
    monkeypatch.setattr(upgrade_runtime, "RUNTIME_BACKUP", backup)
    monkeypatch.setattr(upgrade_runtime, "_runtime_paths", lambda: [unit])
    monkeypatch.setattr(upgrade_runtime, "_app_runtime_records", lambda: [])
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: None)
    stopped = []
    monkeypatch.setattr(upgrade_runtime.subprocess, "run",
                        lambda args, **kw: stopped.append((args, unit.exists())))

    upgrade_runtime.create_runtime_backup()
    unit.write_text("new unit\n")
    upgrade_runtime.restore_runtime_backup()

    assert not unit.exists()
    # Stopped while the unit file (and its ExecStop) still existed.
    assert (["systemctl", "disable", "--now", "boron-mail-egress.service"], True) in stopped


def test_finalizer_alert_does_not_use_the_loopback_relay(monkeypatch):
    finalizer = update_finalize.Finalizer.__new__(update_finalize.Finalizer)
    finalizer.args = Namespace(alert_sender="panel@panel.example", alert_recipient="ops@remote.example")
    logged = []
    finalizer.log = lambda *args: logged.append(args)
    calls = []
    monkeypatch.setattr(update_finalize.subprocess, "run",
                        lambda args, **kw: calls.append((args, kw)) or subprocess.CompletedProcess(args, 0))
    finalizer.alert_admin("Update failed", "details")
    args, kwargs = calls[0]
    assert Path(args[0]).name in {"sendmail", "sendmail.postfix"}
    assert args[1:] == ["-G", "-i", "-f", "panel@panel.example", "--", "ops@remote.example"]
    assert b"Subject: Update failed" in kwargs["input"]
    assert logged == []
