from scripts import upgrade_runtime


def test_dovecot_prefers_temporary_webmail_credential():
    query = upgrade_runtime.PASSWORD_QUERY
    assert "s.password, 0 AS priority" in query
    assert "u.password, 1 AS priority" in query
    assert "ORDER BY auth.priority ASC LIMIT 1" in query


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
