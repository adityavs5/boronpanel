"""DKIM key lifecycle and the Boron-managed OpenDKIM signing path."""
from __future__ import annotations

import datetime as dt
import grp
import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import select

from daemon import dnsprovider
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import DkimKey

logger = logging.getLogger("borond.dkim")
DEFAULT_SELECTOR = "default"
KEY_BITS = "2048"
BORON_CONFIG = Path("/etc/opendkim/boron.conf")
KEY_TABLE = Path("/etc/opendkim/boron/KeyTable")
SIGNING_TABLE = Path("/etc/opendkim/boron/SigningTable")
TRUSTED_HOSTS = Path("/etc/opendkim/boron/TrustedHosts")
SOCKET_PATH = Path("/var/spool/postfix/opendkim/opendkim.sock")


class DkimError(Exception):
    pass


def _domain_dir(domain: str) -> Path:
    return Path(settings.dkim_base_dir) / domain


def _private_key_path(domain: str, selector: str = DEFAULT_SELECTOR) -> Path:
    return _domain_dir(domain) / f"{selector}.private"


def _public_key_b64(private_key_path: Path) -> str:
    result = run(["openssl", "rsa", "-in", str(private_key_path), "-pubout"], timeout=15, check=True)
    return "".join(line.strip() for line in result.stdout.splitlines()
                   if line.strip() and "PUBLIC KEY" not in line)


def _opendkim_gid() -> int | None:
    try:
        return grp.getgrnam("opendkim").gr_gid
    except KeyError:
        return None


def generate_keypair(domain: str, selector: str = DEFAULT_SELECTOR) -> Path:
    key_path = _private_key_path(domain, selector)
    if not key_path.exists():
        domain_dir = _domain_dir(domain)
        domain_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        result = run(["openssl", "genrsa", "-out", str(key_path), KEY_BITS], timeout=20)
        if not result.ok:
            raise DkimError(f"openssl genrsa failed for '{domain}': {result.stderr.strip()}")
    gid = _opendkim_gid()
    os.chmod(_domain_dir(domain), 0o750)
    os.chmod(key_path, 0o640 if gid is not None else 0o600)
    if gid is not None:
        os.chown(_domain_dir(domain), 0, gid)
        os.chown(key_path, 0, gid)
    return key_path


def dkim_record_name(domain: str, selector: str = DEFAULT_SELECTOR) -> str:
    return f"{selector}._domainkey.{domain}"


def dkim_txt_value(domain: str, selector: str = DEFAULT_SELECTOR) -> str:
    return f"v=DKIM1; k=rsa; p={_public_key_b64(_private_key_path(domain, selector))}"


def _atomic_write(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _ensure_main_include() -> None:
    main = Path("/etc/opendkim.conf")
    include = f"Include {BORON_CONFIG}"
    content = main.read_text() if main.exists() else ""
    if include not in content.splitlines():
        _atomic_write(main, content.rstrip() + "\n\n# Managed by Boron\n" + include + "\n", 0o644)


def _signer_config() -> str:
    return """# Managed by Boron. Per-domain material lives below /etc/boron/dkim.
Syslog                  yes
UMask                   007
Mode                    sv
Canonicalization        relaxed/simple
OversignHeaders         From
UserID                  opendkim:opendkim
Socket                  local:/var/spool/postfix/opendkim/opendkim.sock
PidFile                 /run/opendkim/opendkim.pid
KeyTable                /etc/opendkim/boron/KeyTable
SigningTable            refile:/etc/opendkim/boron/SigningTable
ExternalIgnoreList      refile:/etc/opendkim/boron/TrustedHosts
InternalHosts           refile:/etc/opendkim/boron/TrustedHosts
"""


def _write_signer_tables() -> None:
    with write_session() as db:
        rows = db.scalars(select(DkimKey).order_by(DkimKey.domain)).all()
        snapshot = [(row.domain, row.selector) for row in rows]
    key_lines, signing_lines = [], []
    for domain, selector in snapshot:
        key_path = generate_keypair(domain, selector)
        key_lines.append(f"{selector}._domainkey.{domain} {domain}:{selector}:{key_path}")
        signing_lines.append(f"*@{domain} {selector}._domainkey.{domain}")
    gid = _opendkim_gid()
    KEY_TABLE.parent.mkdir(parents=True, exist_ok=True)
    if gid is not None:
        os.chown(KEY_TABLE.parent, 0, gid)
    os.chmod(KEY_TABLE.parent, 0o750)
    _atomic_write(KEY_TABLE, "\n".join(key_lines) + ("\n" if key_lines else ""))
    _atomic_write(SIGNING_TABLE, "\n".join(signing_lines) + ("\n" if signing_lines else ""))
    _atomic_write(TRUSTED_HOSTS, "127.0.0.1\n::1\nlocalhost\n")
    _atomic_write(BORON_CONFIG, _signer_config())
    if gid is not None:
        for path in (KEY_TABLE, SIGNING_TABLE, TRUSTED_HOSTS, BORON_CONFIG):
            os.chown(path, 0, gid)


def _configure_postfix_milter() -> None:
    values = {
        "milter_default_action": "accept", "milter_protocol": "6",
        "smtpd_milters": "unix:opendkim/opendkim.sock",
        "non_smtpd_milters": "unix:opendkim/opendkim.sock",
    }
    for name, value in values.items():
        run(["postconf", "-e", f"{name}={value}"], timeout=15, check=True)


def configure_signer() -> dict:
    """Refresh tables, validate, restart and verify the local milter."""
    if shutil.which("opendkim") is None:
        raise DkimError("OpenDKIM is not installed")
    if shutil.which("postconf") is None:
        raise DkimError("Postfix is not installed")
    # The milter socket lives inside Postfix's chroot and is group-readable
    # only. Give the local Postfix service account that single group instead
    # of widening the socket or key-directory permissions.
    membership = run(["id", "-nG", "postfix"], timeout=10)
    if not membership.ok:
        raise DkimError("Postfix service account is unavailable")
    if "opendkim" not in membership.stdout.split():
        run(["usermod", "-a", "-G", "opendkim", "postfix"], timeout=20, check=True)
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    gid = _opendkim_gid()
    if gid is not None:
        os.chown(SOCKET_PATH.parent, 0, gid)
    os.chmod(SOCKET_PATH.parent, 0o750)
    _write_signer_tables()
    _ensure_main_include()
    validation = run(["opendkim", "-n", "-x", "/etc/opendkim.conf"], timeout=20)
    if not validation.ok:
        raise DkimError(validation.stderr.strip() or "OpenDKIM configuration validation failed")
    _configure_postfix_milter()
    run(["systemctl", "restart", "opendkim"], timeout=30, check=True)
    run(["postfix", "check"], timeout=20, check=True)
    run(["systemctl", "reload", "postfix"], timeout=30, check=True)
    active = run(["systemctl", "is-active", "opendkim"], timeout=10)
    if not active.ok or not SOCKET_PATH.exists():
        raise DkimError("OpenDKIM did not become active or its Postfix socket is missing")
    return {"active": True, "socket": str(SOCKET_PATH)}


def signer_status(domain: str | None = None) -> dict:
    active = False
    detail = "OpenDKIM is not installed"
    if shutil.which("opendkim"):
        service = run(["systemctl", "is-active", "opendkim"], timeout=10)
        active = service.ok and SOCKET_PATH.exists()
        detail = "Signing service and Postfix socket are active" if active else "OpenDKIM is not active"
    result = {"active": active, "detail": detail, "socket": str(SOCKET_PATH)}
    if domain:
        with write_session() as db:
            row = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
            result.update({
                "domain": domain, "selector": row.selector if row else DEFAULT_SELECTOR,
                "dns_published": bool(row and row.dns_published),
                "last_error": row.last_error if row else None,
            })
    return result


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def setup_dns_signing(domain: str, selector: str = DEFAULT_SELECTOR) -> dict:
    """Activate signing first; publish a public key only after it is live."""
    with write_session() as db:
        row = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
        if row is None:
            row = DkimKey(domain=domain, selector=selector)
            db.add(row)
        else:
            selector = row.selector
    generate_keypair(domain, selector)

    signing_active, error = False, None
    try:
        configure_signer()
        signing_active = True
    except Exception as exc:
        error = str(exc)[:1000]
        logger.exception("DKIM signing activation failed for '%s'", domain)

    dns_published = False
    zone = find_managed_zone(domain)
    dkim_name = dkim_record_name(domain, selector)
    dkim_value = dkim_txt_value(domain, selector)
    if signing_active and zone:
        label = label_within_zone(domain, zone)
        dkim_label = f"{selector}._domainkey" if label == "@" else f"{selector}._domainkey.{label}"
        try:
            with dnsprovider.batch_cluster_notifications():
                dnsprovider.upsert_record(zone, dkim_label, "TXT", [_quote(dkim_value)])
            dns_published = True
        except dnsprovider.DnsError as exc:
            error = f"Signing is active but DNS publication failed: {exc}"[:1000]
            logger.exception("failed to publish DKIM record for '%s'", domain)

    verified_at = None
    if dns_published and shutil.which("opendkim-testkey"):
        checked = run(["opendkim-testkey", "-x", "/etc/opendkim.conf", "-d", domain,
                       "-s", selector], timeout=20)
        if checked.ok:
            verified_at = dt.datetime.now(dt.timezone.utc)
        else:
            error = (checked.stderr.strip() or "Published DKIM key could not be verified")[:1000]

    with write_session() as db:
        row = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
        row.signing_active = signing_active
        row.dns_published = dns_published
        row.last_verified_at = verified_at
        row.last_error = error

    return {
        "selector": selector, "dkim_record_name": dkim_name,
        "dkim_record_value": dkim_value, "spf_record_value": "v=spf1 mx a ~all",
        "dns_published": dns_published, "signing_active": signing_active,
        "last_error": error,
    }


def teardown_dns_signing(domain: str) -> None:
    with write_session() as db:
        row = db.scalar(select(DkimKey).where(DkimKey.domain == domain))
        selector = row.selector if row else DEFAULT_SELECTOR
        if row:
            db.delete(row)
    zone = find_managed_zone(domain)
    if zone:
        label = label_within_zone(domain, zone)
        dkim_label = f"{selector}._domainkey" if label == "@" else f"{selector}._domainkey.{label}"
        try:
            dnsprovider.delete_record(zone, dkim_label, "TXT")
        except dnsprovider.DnsError:
            pass
    shutil.rmtree(_domain_dir(domain), ignore_errors=True)
    if shutil.which("opendkim"):
        try:
            configure_signer()
        except Exception:
            logger.exception("failed to refresh OpenDKIM tables after removing '%s'", domain)
