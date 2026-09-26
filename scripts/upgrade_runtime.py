#!/usr/bin/env python3
"""Idempotent host integration for self-updates that add system services."""
from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSWORD_QUERY = "password_query = SELECT auth.user, auth.password FROM (SELECT CONCAT(u.local_part, '@', d.domain) AS user, u.password FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1 UNION ALL SELECT s.mailbox AS user, s.password FROM webmail_session s JOIN mail_user u ON CONCAT(u.local_part, '@', (SELECT domain FROM mail_domain WHERE id=u.domain_id))=s.mailbox JOIN mail_domain d ON d.id=u.domain_id WHERE s.mailbox='%u' AND s.revoked=0 AND s.expires_at>UTC_TIMESTAMP() AND u.active=1 AND d.active=1) auth LIMIT 1"
SSL_DIR = Path("/etc/boron/ssl")
DOVECOT_BORON_CONFIG = Path("/etc/dovecot/conf.d/90-boron.conf")


def command(args: list[str], timeout: int = 900) -> None:
    subprocess.run(args, check=True, timeout=timeout, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def configure_dovecot() -> None:
    path = Path("/etc/dovecot/dovecot-sql.conf.ext")
    if not path.exists():
        return
    content = path.read_text()
    updated, count = re.subn(r"(?m)^password_query\s*=.*$", PASSWORD_QUERY, content, count=1)
    if count != 1:
        raise RuntimeError("Dovecot SQL password_query was not found")
    if updated != content:
        temporary = path.with_name(f".{path.name}.boron-upgrade")
        temporary.write_text(updated)
        temporary.chmod(0o640)
        shutil.chown(temporary, user="root", group="dovecot")
        temporary.replace(path)


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


def main() -> int:
    if shutil.which("opendkim") is None or shutil.which("opendkim-testkey") is None:
        command(["apt-get", "update"])
        command(["apt-get", "install", "-y", "opendkim", "opendkim-tools"])
    from shared.db import init_db
    from daemon import dkim, webmail_sso

    init_db()
    webmail_sso.ensure_schema()
    configure_dovecot()
    configure_mail_tls()
    configure_roundcube()
    dkim.configure_signer()
    if Path("/etc/dovecot/dovecot.conf").exists():
        command(["doveconf", "-n"], timeout=30)
        command(["systemctl", "reload", "dovecot"], timeout=60)
    command(["postfix", "check"], timeout=30)
    command(["systemctl", "reload", "postfix"], timeout=60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
