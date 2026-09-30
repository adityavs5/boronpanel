from scripts import upgrade_runtime
import pytest


@pytest.fixture(autouse=True)
def isolate_mail_runtime_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_runtime, 'DOVECOT_BORON_CONFIG', tmp_path / 'mail-dovecot.conf')
    monkeypatch.setattr(upgrade_runtime, 'POSTFIX_MAIN_CONFIG', tmp_path / 'mail-postfix.cf')
    monkeypatch.setattr(upgrade_runtime, 'SSL_DIR', tmp_path / 'mail-ssl')
    monkeypatch.setattr(upgrade_runtime, 'DOVECOT_SQL_CONFIG', tmp_path / 'normal-sql.conf')
    monkeypatch.setattr(upgrade_runtime, 'DOVECOT_SSO_SQL_CONFIG', tmp_path / 'sso-sql.conf')
    monkeypatch.setattr(upgrade_runtime, 'DOVECOT_SSO_PASSDB_CONFIG', tmp_path / '09-sso-auth.conf')
    monkeypatch.setattr(upgrade_runtime, 'DOVECOT_QUOTA_CONFIG', tmp_path / '91-quota.conf')
from shared.config import settings


def test_dovecot_normal_and_temporary_webmail_credentials_are_independent():
    assert 'u.password' in upgrade_runtime.PASSWORD_QUERY
    assert 'webmail_session' not in upgrade_runtime.PASSWORD_QUERY
    assert 's.password' in upgrade_runtime.SSO_PASSWORD_QUERY
    assert 's.revoked=0' in upgrade_runtime.SSO_PASSWORD_QUERY
    assert 's.expires_at>UTC_TIMESTAMP()' in upgrade_runtime.SSO_PASSWORD_QUERY
    assert 'u.active=1 AND d.active=1' in upgrade_runtime.SSO_PASSWORD_QUERY


def test_runtime_secret_write_is_private_before_publish_and_ignores_predictable_symlink(tmp_path, monkeypatch):
    import os
    import stat

    victim = tmp_path / 'unrelated'
    victim.write_text('preserved')
    target = tmp_path / 'runtime-backup.json'
    (tmp_path / '.runtime-backup.json.tmp').symlink_to(victim)
    original_fsync = os.fsync
    observed = []

    def inspect_private_write(fd):
        observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        original_fsync(fd)

    monkeypatch.setattr(upgrade_runtime.os, 'fsync', inspect_private_write)
    previous = os.umask(0)
    try:
        upgrade_runtime._write_private_atomic(target, b'private fixture', 0o600, os.getuid(), os.getgid())
    finally:
        os.umask(previous)
    assert observed == [0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == b'private fixture'
    assert victim.read_text() == 'preserved'


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
    monkeypatch.setattr(upgrade_runtime, "FIREWALL_RECOVER_COMMAND", tmp_path / "firewall-recover")
    monkeypatch.setattr(upgrade_runtime, "OLS_LIFECYCLE_CONFIG", tmp_path / "ols-lifecycle")
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
        ["systemctl", "daemon-reload"],
        ["/usr/local/lsws/bin/openlitespeed", "-t"],
        ["systemctl", "reload", "lshttpd"],
    ]


def test_firewall_recovery_command_is_installed_and_rolled_back(tmp_path, monkeypatch):
    target = tmp_path / 'sbin' / 'boron-firewall-recover'
    manifest = tmp_path / 'runtime.json'
    monkeypatch.setattr(upgrade_runtime, 'FIREWALL_RECOVER_COMMAND', target)
    monkeypatch.setattr(upgrade_runtime, 'RUNTIME_BACKUP', manifest)
    monkeypatch.setattr(upgrade_runtime, '_runtime_paths', lambda: [target])
    upgrade_runtime.create_runtime_backup()
    upgrade_runtime.configure_firewall_recovery()
    assert target.read_bytes() == (upgrade_runtime.ROOT / 'scripts/firewall_recover.py').read_bytes()
    assert target.stat().st_mode & 0o777 == 0o755
    upgrade_runtime.restore_runtime_backup()
    assert not target.exists()


def test_ols_lifecycle_configuration_is_recoverable(tmp_path, monkeypatch):
    target = tmp_path / 'systemd' / 'boron-lifecycle.conf'
    manifest = tmp_path / 'runtime.json'
    target.parent.mkdir();target.write_text('[Service]\nKillMode=none\n')
    monkeypatch.setattr(upgrade_runtime, 'OLS_LIFECYCLE_CONFIG', target)
    monkeypatch.setattr(upgrade_runtime, 'RUNTIME_BACKUP', manifest)
    monkeypatch.setattr(upgrade_runtime, '_runtime_paths', lambda: [target])
    calls=[]
    monkeypatch.setattr(upgrade_runtime, 'command', lambda args, **kw: calls.append(args))
    upgrade_runtime.create_runtime_backup()
    upgrade_runtime.configure_ols_lifecycle()
    assert 'PIDFile=/run/openlitespeed.pid' in target.read_text()
    assert 'KillMode=mixed' in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o644
    assert ['systemctl','daemon-reload'] in calls
    upgrade_runtime.restore_runtime_backup()
    assert target.read_text() == '[Service]\nKillMode=none\n'


def test_mail_certificate_and_service_configs_are_restored_together(tmp_path, monkeypatch):
    ssl_dir = upgrade_runtime.SSL_DIR
    ssl_dir.mkdir()
    originals = {upgrade_runtime.DOVECOT_BORON_CONFIG: 'old dovecot',
                 upgrade_runtime.POSTFIX_MAIN_CONFIG: 'old postfix',
                 ssl_dir/'mail.crt': 'old certificate', ssl_dir/'mail.key': 'old key'}
    for path, content in originals.items():
        path.write_text(content)
    (ssl_dir/'mail.key').chmod(0o600)
    monkeypatch.setattr(upgrade_runtime, '_runtime_paths', lambda: list(originals))
    monkeypatch.setattr(upgrade_runtime, 'RUNTIME_BACKUP', tmp_path/'runtime.json')
    calls = []
    monkeypatch.setattr(upgrade_runtime, 'command', lambda args, **kwargs: calls.append(args))
    upgrade_runtime.create_runtime_backup()
    for path in originals:
        path.write_text('changed')
    upgrade_runtime.restore_runtime_backup()
    assert all(path.read_text() == content for path,content in originals.items())
    assert (ssl_dir/'mail.key').stat().st_mode & 0o777 == 0o600
    assert ['doveconf', '-n'] in calls and ['postfix', 'check'] in calls
    assert ['systemctl','reload','dovecot'] in calls and ['systemctl','reload','postfix'] in calls


def test_dovecot_separate_passdb_is_idempotent_and_preserves_private_connection(monkeypatch):
    source=upgrade_runtime.DOVECOT_SQL_CONFIG
    source.write_text('driver = mysql\nconnect = host=127.0.0.1 password=test-only-secret\npassword_query = old union query\nuser_query = original user lookup\n')
    owners=[]
    monkeypatch.setattr(upgrade_runtime.shutil,'chown',lambda path,**kw:owners.append(kw))
    upgrade_runtime.configure_dovecot()
    first=(source.read_text(),upgrade_runtime.DOVECOT_SSO_SQL_CONFIG.read_text(),upgrade_runtime.DOVECOT_SSO_PASSDB_CONFIG.read_text())
    upgrade_runtime.configure_dovecot()
    assert first==(source.read_text(),upgrade_runtime.DOVECOT_SSO_SQL_CONFIG.read_text(),upgrade_runtime.DOVECOT_SSO_PASSDB_CONFIG.read_text())
    normal,sso,passdb=first
    assert upgrade_runtime.PASSWORD_QUERY in normal and 'old union' not in normal
    assert upgrade_runtime.SSO_PASSWORD_QUERY in sso
    assert 'connect = host=127.0.0.1 password=test-only-secret' in normal and 'connect = host=127.0.0.1 password=test-only-secret' in sso
    assert 'FROM (original user lookup) AS original' in normal
    assert 'u.quota_mb' in normal and 'AS quota_rule' in normal
    assert 'quota = count:User quota' in upgrade_runtime.DOVECOT_QUOTA_CONFIG.read_text()
    assert 'lmtp_rcpt_check_quota = yes' in upgrade_runtime.DOVECOT_QUOTA_CONFIG.read_text()
    assert 'result_failure = continue' in passdb and 'result_success = return-ok' in passdb
    assert 'result_internalfail = return-fail' in passdb and 'test-only-secret' not in passdb
    assert source.stat().st_mode & 0o777==0o640
    assert upgrade_runtime.DOVECOT_SSO_SQL_CONFIG.stat().st_mode & 0o777==0o640


def test_dovecot_quota_wrap_preserves_extra_user_fields_and_existing_rules():
    original="user_query = SELECT uid,gid,home,mail,custom_setting FROM mail_user WHERE email='%u';\n"
    updated=upgrade_runtime._with_mailbox_quota(original)
    assert 'SELECT uid,gid,home,mail,custom_setting' in updated
    assert 'COALESCE' in updated and 'AS quota_rule' in updated
    assert updated==upgrade_runtime._with_mailbox_quota(updated)
    custom="user_query = SELECT uid,home,quota AS quota_rule FROM custom_users\n"
    assert upgrade_runtime._with_mailbox_quota(custom)==custom
