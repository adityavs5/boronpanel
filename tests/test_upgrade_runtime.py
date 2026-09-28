from scripts import upgrade_runtime
from shared.config import settings


def test_dovecot_prefers_temporary_webmail_credential():
    query = upgrade_runtime.PASSWORD_QUERY
    assert "s.password, 0 AS priority" in query
    assert "u.password, 1 AS priority" in query
    assert "ORDER BY auth.priority ASC LIMIT 1" in query


def test_configure_account_resource_parent_enables_all_accounting(monkeypatch):
    commands = []
    monkeypatch.setattr(upgrade_runtime.Path, "exists", lambda path: True)
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))

    upgrade_runtime.configure_account_resource_parent()

    assert commands == [[
        "systemctl", "set-property", "user.slice",
        "CPUAccounting=yes", "MemoryAccounting=yes", "IOAccounting=yes", "TasksAccounting=yes",
    ]]


def test_configure_mail_tls_separates_existing_mail_services(tmp_path, monkeypatch):
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    (ssl_dir / "default.crt").write_text("default certificate")
    (ssl_dir / "default.key").write_text("default key")
    dovecot = tmp_path / "90-boron.conf"
    dovecot.write_text(
        f"ssl_cert = <{ssl_dir}/default.crt\n"
        f"ssl_key = <{ssl_dir}/default.key\n"
    )
    commands = []
    monkeypatch.setattr(upgrade_runtime, "SSL_DIR", ssl_dir)
    monkeypatch.setattr(upgrade_runtime, "DOVECOT_BORON_CONFIG", dovecot)
    monkeypatch.setattr(upgrade_runtime.shutil, "which", lambda name: "/usr/sbin/postconf" if name == "postconf" else None)
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))

    upgrade_runtime.configure_mail_tls()

    assert (ssl_dir / "mail.crt").read_text() == "default certificate"
    assert (ssl_dir / "mail.key").read_text() == "default key"
    assert f"ssl_cert = <{ssl_dir}/mail.crt" in dovecot.read_text()
    assert f"ssl_key = <{ssl_dir}/mail.key" in dovecot.read_text()
    assert commands == [
        ["postconf", "-e", f"smtpd_tls_cert_file = {ssl_dir}/mail.crt"],
        ["postconf", "-e", f"smtpd_tls_key_file = {ssl_dir}/mail.key"],
    ]


def test_configure_bubblewrap_mail_uses_loopback_submission(tmp_path, monkeypatch):
    target = tmp_path / "msmtprc"
    monkeypatch.setattr(upgrade_runtime, "MSMTP_CONFIG", target)
    monkeypatch.setattr(settings, "panel_hostname", "panel.example.test")

    upgrade_runtime.configure_bubblewrap_mail()

    assert target.stat().st_mode & 0o777 == 0o644
    assert target.read_text() == (
        "defaults\n"
        "auth off\n"
        "tls off\n"
        "syslog off\n\n"
        "account default\n"
        "host 127.0.0.1\n"
        "port 25\n"
        "auto_from on\n"
        "maildomain panel.example.test\n"
    )


def test_configure_bubblewrap_apparmor_keeps_global_userns_gate(tmp_path, monkeypatch):
    source = tmp_path / "deploy" / "boron-bwrap.apparmor"
    source.parent.mkdir()
    source.write_text("profile boron-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n")
    target = tmp_path / "etc" / "apparmor.d" / "boron-bwrap"
    commands = []
    monkeypatch.setattr(upgrade_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(upgrade_runtime, "APPARMOR_BWRAP_PROFILE", target)
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))

    upgrade_runtime.configure_bubblewrap_apparmor()

    assert target.read_text() == source.read_text()
    assert target.stat().st_mode & 0o777 == 0o644
    assert commands == [["apparmor_parser", "-r", str(target)]]


def test_runtime_backup_restores_ols_mail_and_apparmor(tmp_path, monkeypatch):
    msmtp = tmp_path / "etc" / "boron" / "msmtprc"
    apparmor = tmp_path / "etc" / "apparmor.d" / "boron-bwrap"
    ols_config = tmp_path / "lsws" / "conf" / "httpd_config.conf"
    waf_runtime = tmp_path / "etc" / "modsecurity" / "boron-runtime.conf"
    vhost_root = tmp_path / "lsws" / "conf" / "vhosts"
    vhost = vhost_root / "example" / "vhconf.conf"
    backup = tmp_path / "state" / "runtime-backup.json"
    for path, content in (
        (msmtp, "old mail\n"),
        (apparmor, "old profile\n"),
        (ols_config, "old main\n"),
        (waf_runtime, "old waf\n"),
        (vhost, "old vhost\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(upgrade_runtime, "MSMTP_CONFIG", msmtp)
    monkeypatch.setattr(upgrade_runtime, "APPARMOR_BWRAP_PROFILE", apparmor)
    monkeypatch.setattr(upgrade_runtime, "OLS_CONFIG", ols_config)
    monkeypatch.setattr(upgrade_runtime, "OLS_WAF_RUNTIME", waf_runtime)
    monkeypatch.setattr(upgrade_runtime, "OLS_VHOST_ROOT", vhost_root)
    monkeypatch.setattr(upgrade_runtime, "RUNTIME_BACKUP", backup)
    commands = []
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))

    upgrade_runtime.create_runtime_backup()
    for path in (msmtp, apparmor, ols_config, waf_runtime, vhost):
        path.write_text("changed\n")
    upgrade_runtime.restore_runtime_backup()

    assert msmtp.read_text() == "old mail\n"
    assert apparmor.read_text() == "old profile\n"
    assert ols_config.read_text() == "old main\n"
    assert waf_runtime.read_text() == "old waf\n"
    assert vhost.read_text() == "old vhost\n"
    assert not backup.exists()
    assert commands == [
        ["apparmor_parser", "-r", str(apparmor)],
        ["/usr/local/lsws/bin/openlitespeed", "-t"],
        ["systemctl", "reload", "lshttpd"],
    ]
