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
    monkeypatch.setattr(upgrade_runtime, '_app_runtime_records', lambda: [])
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


def test_atomic_mail_key_copy_is_private_before_first_byte(tmp_path, monkeypatch):
    import os
    import stat

    source = tmp_path / "default.key"
    source.write_bytes(b"private key fixture")
    target = tmp_path / "mail.key"
    canary = tmp_path / "canary"
    canary.write_text("keep")
    (tmp_path / ".mail.key.boron-upgrade").symlink_to(canary)
    observed = []
    real_fsync = os.fsync

    def inspect(fd):
        observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        real_fsync(fd)

    monkeypatch.setattr(upgrade_runtime.os, "fsync", inspect)
    previous = os.umask(0)
    try:
        upgrade_runtime._atomic_copy(source, target, 0o600)
    finally:
        os.umask(previous)

    assert observed == [0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == source.read_bytes()
    assert canary.read_text() == "keep"


def test_configure_bubblewrap_mail_uses_loopback_submission(tmp_path, monkeypatch):
    target = tmp_path / "msmtprc"
    monkeypatch.setattr(upgrade_runtime, "MSMTP_CONFIG", target)
    monkeypatch.setattr(upgrade_runtime, "MAIL_SUBMIT_SOCKET_UNIT", tmp_path / "boron-mail-submit.socket")
    monkeypatch.setattr(upgrade_runtime, "MAIL_SUBMIT_SERVICE_UNIT", tmp_path / "boron-mail-submit@.service")
    monkeypatch.setattr(settings, "panel_hostname", "panel.example.test")
    commands = []
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))

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
        "socket /run/boron-mail-submit.sock\n"
        "auto_from on\n"
        "maildomain panel.example.test\n"
    )
    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "boron-mail-submit.socket"],
        ["systemctl", "restart", "boron-mail-submit.socket"],
    ]


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
    monkeypatch.setattr(
        upgrade_runtime,
        "_runtime_paths",
        lambda: [msmtp, apparmor, ols_config, waf_runtime, vhost, upgrade_runtime.OLS_LIFECYCLE_CONFIG],
    )
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


def test_runtime_rollback_restores_application_units_mode_and_process_shape(isolated_db, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import Account, NodeApp

    unit_dir = tmp_path / "systemd"
    env_dir = tmp_path / "env"
    unit_dir.mkdir(); env_dir.mkdir()
    with write_session() as session:
        account = Account(username="demo", status="active")
        session.add(account); session.flush()
        app = NodeApp(account_id=account.id, domain="app.example", name="app", entry_point="server.js",
                      port=31001, node_version="20", enabled=True, backend_mode="legacy")
        session.add(app); session.flush(); app_id = app.id
    service = f"boron-node-demo-{app_id}.service"
    socket = service[:-8] + ".socket"
    env = env_dir / f"{service}.env"
    (unit_dir / service).write_text("legacy tcp unit\n")
    env.write_text("PORT=31001\n")
    app_state = [{"kind":"node", "id":app_id, "backend_mode":"legacy", "service":service,
                  "socket":socket, "env":str(env), "enabled":True, "active":True}]
    monkeypatch.setattr(upgrade_runtime, "SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(upgrade_runtime, "RUNTIME_BACKUP", tmp_path / "runtime.json")
    monkeypatch.setattr(upgrade_runtime, "_runtime_paths", lambda: [])
    monkeypatch.setattr(upgrade_runtime, "_app_runtime_records", lambda: app_state)
    subprocess_calls = []
    command_calls = []
    monkeypatch.setattr(upgrade_runtime.subprocess, "run", lambda args, **kwargs:
                        (subprocess_calls.append(args) or SimpleNamespace(returncode=0, stdout="", stderr="")))
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, **kwargs: command_calls.append(args))

    upgrade_runtime.create_runtime_backup()
    (unit_dir / service).write_text("socket unit\n")
    (unit_dir / socket).write_text("activation socket\n")
    env.write_text("PORT=changed\n")
    with write_session() as session:
        session.get(NodeApp, app_id).backend_mode = "socket"
    upgrade_runtime.restore_runtime_backup()

    assert (unit_dir / service).read_text() == "legacy tcp unit\n"
    assert not (unit_dir / socket).exists()
    assert env.read_text() == "PORT=31001\n"
    with write_session() as session:
        assert session.get(NodeApp, app_id).backend_mode == "legacy"
    assert ["systemctl", "daemon-reload"] in command_calls
    assert ["systemctl", "enable", service] in command_calls
    assert ["systemctl", "restart", service] in command_calls
    assert ["systemctl", "stop", socket] in subprocess_calls


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


def test_ols_namespace_policy_is_root_owned_and_recoverable(tmp_path, monkeypatch):
    target = tmp_path / 'lsns/conf/boron-nsconf.conf'
    manifest = tmp_path / 'runtime.json'
    target.parent.mkdir(parents=True)
    target.write_text('legacy policy\n')
    monkeypatch.setattr(upgrade_runtime, 'OLS_NAMESPACE_POLICY', target)
    monkeypatch.setattr(upgrade_runtime, 'RUNTIME_BACKUP', manifest)
    monkeypatch.setattr(upgrade_runtime, '_runtime_paths', lambda: [target])

    upgrade_runtime.create_runtime_backup()
    upgrade_runtime.configure_ols_namespace_policy()

    assert target.read_bytes() == (upgrade_runtime.ROOT / 'deploy/ols-nsconf.conf').read_bytes()
    assert target.stat().st_uid == 0
    assert target.stat().st_gid == 0
    assert target.stat().st_mode & 0o777 == 0o644
    upgrade_runtime.restore_runtime_backup()
    assert target.read_text() == 'legacy policy\n'


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
