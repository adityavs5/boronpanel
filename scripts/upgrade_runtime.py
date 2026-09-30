#!/usr/bin/env python3
"""Idempotent host integration for self-updates that add system services."""
from __future__ import annotations

import re
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSWORD_QUERY = "password_query = SELECT CONCAT(u.local_part, '@', d.domain) AS user, u.password FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1"
SSO_PASSWORD_QUERY = "password_query = SELECT s.mailbox AS user, s.password FROM webmail_session s JOIN mail_user u ON CONCAT(u.local_part, '@', (SELECT domain FROM mail_domain WHERE id=u.domain_id))=s.mailbox JOIN mail_domain d ON d.id=u.domain_id WHERE s.mailbox='%u' AND s.revoked=0 AND s.expires_at>UTC_TIMESTAMP() AND u.active=1 AND d.active=1"
DOVECOT_SQL_CONFIG = Path('/etc/dovecot/dovecot-sql.conf.ext')
DOVECOT_SSO_SQL_CONFIG = Path('/etc/dovecot/boron-sso-sql.conf.ext')
DOVECOT_SSO_PASSDB_CONFIG = Path('/etc/dovecot/conf.d/09-boron-sso-auth.conf')
DOVECOT_QUOTA_CONFIG = Path('/etc/dovecot/conf.d/91-boron-quota.conf')

SSL_DIR = Path("/etc/boron/ssl")
DOVECOT_BORON_CONFIG = Path("/etc/dovecot/conf.d/90-boron.conf")
POSTFIX_MAIN_CONFIG = Path('/etc/postfix/main.cf')
MSMTP_CONFIG = Path("/etc/boron/msmtprc")
APPARMOR_BWRAP_PROFILE = Path("/etc/apparmor.d/boron-bwrap")
OLS_CONFIG = Path("/usr/local/lsws/conf/httpd_config.conf")
OLS_VHOST_ROOT = Path("/usr/local/lsws/conf/vhosts")
OLS_WAF_RUNTIME = Path("/etc/modsecurity/boron-runtime.conf")
RUNTIME_BACKUP = Path("/var/lib/boron/update-runtime-backup.json")
FIREWALL_RECOVER_COMMAND = Path("/usr/local/sbin/boron-firewall-recover")
OLS_LIFECYCLE_CONFIG = Path("/etc/systemd/system/lshttpd.service.d/boron-lifecycle.conf")


def command(args: list[str], timeout: int = 900) -> None:
    subprocess.run(args, check=True, timeout=timeout, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def _runtime_paths() -> list[Path]:
    paths = [MSMTP_CONFIG, APPARMOR_BWRAP_PROFILE, OLS_CONFIG, OLS_WAF_RUNTIME, FIREWALL_RECOVER_COMMAND, OLS_LIFECYCLE_CONFIG,
             DOVECOT_BORON_CONFIG, POSTFIX_MAIN_CONFIG, SSL_DIR / 'mail.crt', SSL_DIR / 'mail.key',
             DOVECOT_SQL_CONFIG, DOVECOT_SSO_SQL_CONFIG, DOVECOT_SSO_PASSDB_CONFIG, DOVECOT_QUOTA_CONFIG]
    if OLS_VHOST_ROOT.exists():
        paths.extend(sorted(OLS_VHOST_ROOT.glob("*/vhconf.conf")))
    return paths


def create_runtime_backup() -> None:
    """Snapshot every host file this isolation migration can change."""
    if RUNTIME_BACKUP.exists():
        raise RuntimeError(
            f"unresolved runtime migration backup exists at {RUNTIME_BACKUP}; run upgrade_runtime.py --rollback"
        )
    records = []
    for path in _runtime_paths():
        exists = path.is_file()
        stat = path.stat() if exists else None
        records.append({
            "path": str(path),
            "exists": exists,
            "mode": stat.st_mode & 0o7777 if stat else None,
            "uid": stat.st_uid if stat else None,
            "gid": stat.st_gid if stat else None,
            "data": base64.b64encode(path.read_bytes()).decode("ascii") if exists else "",
        })
    RUNTIME_BACKUP.parent.mkdir(parents=True, exist_ok=True)
    _write_private_atomic(RUNTIME_BACKUP,
                          json.dumps({"version": 1, "records": records}, separators=(",", ":")).encode(),
                          0o600, os.getuid(), os.getgid())


def _write_private_atomic(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    """Create private before writing any configuration or backup secrets."""
    with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def restore_runtime_backup() -> None:
    """Compensate host mutations when the application update rolls back."""
    if not RUNTIME_BACKUP.exists():
        return
    manifest = json.loads(RUNTIME_BACKUP.read_text())
    if manifest.get("version") != 1 or not isinstance(manifest.get("records"), list):
        raise RuntimeError("runtime migration backup has an unsupported format")
    records = manifest["records"]
    apparmor_record = next((row for row in records if row["path"] == str(APPARMOR_BWRAP_PROFILE)), None)
    if apparmor_record and not apparmor_record["exists"] and APPARMOR_BWRAP_PROFILE.exists():
        command(["apparmor_parser", "-R", str(APPARMOR_BWRAP_PROFILE)], timeout=30)
    for row in records:
        path = Path(row["path"])
        if not row["exists"]:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_private_atomic(path, base64.b64decode(row["data"], validate=True),
                              int(row["mode"]), int(row["uid"]), int(row["gid"]))
    if apparmor_record and apparmor_record["exists"]:
        command(["apparmor_parser", "-r", str(APPARMOR_BWRAP_PROFILE)], timeout=30)
    if any(row["path"] == str(OLS_LIFECYCLE_CONFIG) for row in records):
        command(["systemctl", "daemon-reload"], timeout=30)
    if any(row["path"] == str(OLS_CONFIG) and row["exists"] for row in records):
        command(["/usr/local/lsws/bin/openlitespeed", "-t"], timeout=30)
        command(["systemctl", "reload", "lshttpd"], timeout=60)
    if any(row['path'] in {str(DOVECOT_BORON_CONFIG), str(DOVECOT_SQL_CONFIG), str(DOVECOT_SSO_SQL_CONFIG), str(DOVECOT_SSO_PASSDB_CONFIG), str(DOVECOT_QUOTA_CONFIG)} and row['exists'] for row in records):
        command(['doveconf', '-n'], timeout=30)
        command(['systemctl', 'reload', 'dovecot'], timeout=60)
    if any(row['path'] == str(POSTFIX_MAIN_CONFIG) and row['exists'] for row in records):
        command(['postfix', 'check'], timeout=30)
        command(['systemctl', 'reload', 'postfix'], timeout=60)
    RUNTIME_BACKUP.unlink()


def commit_runtime_backup() -> None:
    RUNTIME_BACKUP.unlink(missing_ok=True)


def configure_firewall_recovery() -> None:
    """Updates need the same independent recovery command as fresh installs."""
    source = ROOT / "scripts" / "firewall_recover.py"
    FIREWALL_RECOVER_COMMAND.parent.mkdir(parents=True, exist_ok=True)
    temporary = FIREWALL_RECOVER_COMMAND.with_name(".boron-firewall-recover.upgrade")
    try:
        with temporary.open("wb") as target:
            target.write(source.read_bytes())
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o755)
        os.chown(temporary, 0, 0)
        temporary.replace(FIREWALL_RECOVER_COMMAND)
    finally:
        temporary.unlink(missing_ok=True)


def configure_ols_lifecycle() -> None:
    """Bring upgraded servers in line with the fresh-install service unit."""
    OLS_LIFECYCLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = OLS_LIFECYCLE_CONFIG.with_suffix(".tmp")
    try:
        temporary.write_text("[Service]\nKillMode=mixed\nTimeoutStopSec=20s\nPIDFile=/run/openlitespeed.pid\n")
        temporary.chmod(0o644)
        os.chown(temporary, 0, 0)
        temporary.replace(OLS_LIFECYCLE_CONFIG)
    finally:
        temporary.unlink(missing_ok=True)
    command(["systemctl", "daemon-reload"], timeout=30)


def _write_dovecot_config(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    # SQL connection secrets remain private from the moment of creation.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=f'.{path.name}.', delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content);file.flush();os.fsync(file.fileno())
            os.fchmod(file.fileno(), mode)
            shutil.chown(temporary, user='root', group='dovecot' if mode == 0o640 else 'root')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _with_mailbox_quota(content):
    def quota_query(match):
        query = match.group(1).strip().rstrip(';')
        if re.search(r'\bAS\s+quota_rule\b', query, re.IGNORECASE):
            return match.group(0)
        # Preserve the operator's existing uid/gid/home/mail/extra fields.
        limit = ("SELECT u.quota_mb FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id "
                 "WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1")
        return ("user_query = SELECT original.*, CONCAT('*:storage=', COALESCE((" + limit +
                "),0), 'M') AS quota_rule FROM (" + query + ") AS original")
    updated, count = re.subn(r'(?m)^user_query\s*=\s*(.*)$', quota_query, content, count=1)
    if count != 1:
        raise RuntimeError('Dovecot SQL user_query was not found')
    return updated



def configure_dovecot() -> None:
    path = DOVECOT_SQL_CONFIG
    if not path.exists():
        return
    content = path.read_text()
    updated, count = re.subn(r'(?m)^password_query\s*=.*$', PASSWORD_QUERY, content, count=1)
    if count != 1:
        raise RuntimeError('Dovecot SQL password_query was not found')
    updated = _with_mailbox_quota(updated)
    sso_content = re.sub(r'(?m)^password_query\s*=.*$', SSO_PASSWORD_QUERY, updated, count=1)
    # A separate password database lets Dovecot verify either password. A SQL
    # UNION/LIMIT selects just one hash and breaks normal mail clients while
    # a temporary webmail credential is active.
    passdb = ('# Managed Boron temporary webmail authentication.\n'
              'passdb {\n  driver = sql\n'
              f'  args = {DOVECOT_SSO_SQL_CONFIG}\n'
              '  result_success = return-ok\n'
              '  result_failure = continue\n'
              '  result_internalfail = return-fail\n}\n')
    _write_dovecot_config(DOVECOT_SSO_SQL_CONFIG, sso_content, 0o640)
    if updated != content:
        _write_dovecot_config(path, updated, 0o640)
    _write_dovecot_config(DOVECOT_SSO_PASSDB_CONFIG, passdb, 0o644)
    _write_dovecot_config(DOVECOT_QUOTA_CONFIG, """# Managed Boron per-mailbox limits, supplied by SQL userdb.
mail_plugins = $mail_plugins quota
protocol imap {
  mail_plugins = $mail_plugins imap_quota
}
protocol lmtp {
  lmtp_rcpt_check_quota = yes
}
plugin {
  quota = count:User quota
  quota_vsizes = yes
  quota_rule = *:storage=0
}
""", 0o644)


def _atomic_copy(source: Path, target: Path, mode: int) -> None:
    temporary = target.with_name(f".{target.name}.boron-upgrade")
    shutil.copyfile(source, temporary)
    temporary.chmod(mode)
    os.replace(temporary, target)


def configure_mail_tls() -> None:
    """Move Postfix/Dovecot to a service-specific certificate pair.

    Existing servers start with a matching copy of the previous default
    certificate. Future certbot deploys replace only mail.crt/mail.key.
    """
    SSL_DIR.mkdir(parents=True, exist_ok=True)
    default_cert, default_key = SSL_DIR / "default.crt", SSL_DIR / "default.key"
    mail_cert, mail_key = SSL_DIR / "mail.crt", SSL_DIR / "mail.key"
    if (not mail_cert.exists() or not mail_key.exists()) and default_cert.exists() and default_key.exists():
        _atomic_copy(default_cert, mail_cert, 0o644)
        _atomic_copy(default_key, mail_key, 0o600)

    if DOVECOT_BORON_CONFIG.exists():
        content = DOVECOT_BORON_CONFIG.read_text()
        updated, cert_count = re.subn(
            r"(?m)^ssl_cert\s*=.*$", f"ssl_cert = <{mail_cert}", content, count=1,
        )
        updated, key_count = re.subn(
            r"(?m)^ssl_key\s*=.*$", f"ssl_key = <{mail_key}", updated, count=1,
        )
        if cert_count != 1 or key_count != 1:
            raise RuntimeError("Dovecot Boron TLS settings were not found")
        if updated != content:
            metadata = DOVECOT_BORON_CONFIG.stat()
            temporary = DOVECOT_BORON_CONFIG.with_name(f".{DOVECOT_BORON_CONFIG.name}.boron-upgrade")
            temporary.write_text(updated)
            os.chmod(temporary, metadata.st_mode & 0o777)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
            os.replace(temporary, DOVECOT_BORON_CONFIG)

    if shutil.which("postconf"):
        command(["postconf", "-e", f"smtpd_tls_cert_file = {mail_cert}"], timeout=30)
        command(["postconf", "-e", f"smtpd_tls_key_file = {mail_key}"], timeout=30)

    from shared.config import settings
    from scripts.ssl_deploy_hook import LETSENCRYPT_LIVE_DIR, _deploy_mail_service_certificate
    canonical = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    lineage = LETSENCRYPT_LIVE_DIR / canonical if canonical else None
    if lineage is not None and (lineage / 'fullchain.pem').exists() and (lineage / 'privkey.pem').exists():
        _deploy_mail_service_certificate(canonical)


def configure_roundcube() -> None:
    root = Path("/var/www/roundcube")
    config = root / "config/config.inc.php"
    source = ROOT / "integrations/roundcube/boron_sso"
    if not root.exists() or not config.exists():
        return
    target = root / "plugins/boron_sso"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("boron_sso.php", "package.xml"):
        shutil.copy2(source / name, target / name)
    content = config.read_text()
    if "'boron_sso'" not in content and '"boron_sso"' not in content:
        pattern = r"(\$config\['plugins'\]\s*=\s*\[)([^\]]*)(\]\s*;)"
        content, count = re.subn(pattern, lambda m: m.group(1) + m.group(2).rstrip() + (", " if m.group(2).strip() else "") + "'boron_sso'" + m.group(3), content, count=1)
        if count != 1:
            raise RuntimeError("Roundcube plugin configuration was not found")
        config.write_text(content)


def configure_account_resource_parent() -> None:
    """Enable accounting on the common ancestor used by OLS and logind."""
    if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        raise RuntimeError("Boron resource limits require cgroups v2")
    command([
        "systemctl", "set-property", "user.slice",
        "CPUAccounting=yes", "MemoryAccounting=yes", "IOAccounting=yes", "TasksAccounting=yes",
    ], timeout=30)


def configure_bubblewrap_mail() -> None:
    """Install the sandbox-safe localhost mail submission configuration."""
    from shared.config import settings

    hostname = settings.panel_hostname or "localhost"
    content = (
        "defaults\n"
        "auth off\n"
        "tls off\n"
        "syslog off\n\n"
        "account default\n"
        "host 127.0.0.1\n"
        "port 25\n"
        "auto_from on\n"
        f"maildomain {hostname}\n"
    )
    MSMTP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = MSMTP_CONFIG.with_name(f".{MSMTP_CONFIG.name}.boron-upgrade")
    temporary.write_text(content)
    temporary.chmod(0o644)
    temporary.replace(MSMTP_CONFIG)


def configure_bubblewrap_apparmor() -> None:
    """Allow only Bubblewrap through Ubuntu 24.04's userns AppArmor gate."""
    source = ROOT / "deploy" / "boron-bwrap.apparmor"
    content = source.read_text()
    APPARMOR_BWRAP_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = APPARMOR_BWRAP_PROFILE.with_name(f".{APPARMOR_BWRAP_PROFILE.name}.boron-upgrade")
    temporary.write_text(content)
    temporary.chmod(0o644)
    temporary.replace(APPARMOR_BWRAP_PROFILE)
    command(["apparmor_parser", "-r", str(APPARMOR_BWRAP_PROFILE)], timeout=30)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--rollback"]:
        restore_runtime_backup()
        return 0
    if argv == ["--commit"]:
        commit_runtime_backup()
        return 0
    if argv:
        raise RuntimeError("usage: upgrade_runtime.py [--rollback|--commit]")
    missing_packages = []
    if shutil.which("opendkim") is None or shutil.which("opendkim-testkey") is None:
        missing_packages.extend(["opendkim", "opendkim-tools"])
    if shutil.which("bwrap") is None:
        missing_packages.append("bubblewrap")
    if shutil.which("msmtp") is None:
        missing_packages.append("msmtp")
    if shutil.which("apparmor_parser") is None:
        missing_packages.append("apparmor")
    if missing_packages:
        command(["apt-get", "update"])
        command(["apt-get", "install", "-y", *missing_packages])
    # Rotate historically readable keys before loading settings into memory.
    command([sys.executable, str(ROOT / 'scripts/reconcile_powerdns_credentials.py'),
             '--defer-panel-restart'], timeout=180)
    from shared.db import init_db
    from daemon import dkim, ols, webmail_sso

    create_runtime_backup()
    configure_firewall_recovery()
    configure_ols_lifecycle()
    init_db()
    webmail_sso.ensure_schema()
    configure_dovecot()
    configure_mail_tls()
    configure_roundcube()
    configure_account_resource_parent()
    configure_bubblewrap_mail()
    configure_bubblewrap_apparmor()
    # Server policy is Off-by-default; refreshing main first keeps every
    # existing vhost on the native namespace until its own atomic refresh
    # enables Bubblewrap. A mid-migration failure therefore degrades to the
    # already-verified compatibility backend instead of breaking PHP.
    ols.refresh_main_config()
    ols.refresh_all_vhosts()
    dkim.configure_signer()
    if Path("/etc/dovecot/dovecot.conf").exists():
        command(["doveconf", "-n"], timeout=30)
        command(["systemctl", "reload", "dovecot"], timeout=60)
    command(["postfix", "check"], timeout=30)
    command(["systemctl", "reload", "postfix"], timeout=60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
