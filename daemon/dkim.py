"""DKIM key lifecycle and the Boron-managed OpenDKIM signing path."""
from __future__ import annotations

import datetime as dt
import grp
import logging
import os
import pwd
import shutil
from pathlib import Path

from sqlalchemy import select

from daemon import dnsprovider, mail_hardening, mail_identity
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
POSTFIX_BORON_DIR = Path("/etc/postfix/boron")
SENDER_LOGIN_MAP = POSTFIX_BORON_DIR / "mysql-sender-login.cf"
# The SASL login travels with the message so the filter re-checks that the
# authenticated mailbox is the envelope sender before any key is used.
SIGN_SERVICE = (
    "boron-sign/unix=boron-sign unix - n n - 4 pipe flags=Rq user=opendkim "
    "argv=/opt/boron/.venv/bin/python /opt/boron/scripts/mail_sign_filter.py "
    "--sender ${sender} --login=${sasl_username} --recipient ${recipient}"
)
SUBMISSIONS_SERVICE = "submissions/inet"


def submission_policy() -> dict[str, str]:
    """Every authenticated submission port: only an authenticated mailbox may
    relay, only as itself, and every message passes the signing filter."""
    return {
        "smtpd_sasl_auth_enable": "yes",
        "smtpd_client_restrictions": "permit_sasl_authenticated,reject",
        "content_filter": "boron-sign:",
        "smtpd_sender_login_maps": f"mysql:{SENDER_LOGIN_MAP}",
        "smtpd_sender_restrictions": "reject_authenticated_sender_login_mismatch",
        "smtpd_relay_restrictions": "permit_sasl_authenticated,reject",
    }


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


def _ensure_socket_directory() -> None:
    """Create the Postfix-chroot socket directory for the milter owner.

    Postfix only needs group traversal and access to the group-enabled socket.
    OpenDKIM itself must own the directory so it can create that socket while
    the directory stays closed to every other account.
    """
    try:
        account = pwd.getpwnam("opendkim")
    except KeyError as exc:
        raise DkimError("OpenDKIM service account is unavailable") from exc
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chown(SOCKET_PATH.parent, account.pw_uid, account.pw_gid)
    os.chmod(SOCKET_PATH.parent, 0o750)


def generate_keypair(domain: str, selector: str = DEFAULT_SELECTOR) -> Path:
    key_path = _private_key_path(domain, selector)
    if not key_path.exists():
        domain_dir = _domain_dir(domain)
        domain_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        result = run(["openssl", "genrsa", "-out", str(key_path), KEY_BITS], timeout=20)
        if not result.ok:
            raise DkimError(f"openssl genrsa failed for '{domain}': {result.stderr.strip()}")
    gid = _opendkim_gid()
    # The signer needs traversal of the shared parent as well as the domain
    # directory. Keep both restricted to root and the signing service group.
    base = Path(settings.dkim_base_dir)
    os.chmod(base, 0o750 if gid is not None else 0o700)
    if gid is not None:
        os.chown(base, 0, gid)
    os.chmod(_domain_dir(domain), 0o750)
    os.chmod(key_path, 0o600)
    if gid is not None:
        os.chown(_domain_dir(domain), 0, gid)
        # Postfix shares the socket group but must never read signing keys.
        # Owner-only keys also satisfy OpenDKIM's RequireSafeKeys check.
        os.chown(key_path, pwd.getpwnam('opendkim').pw_uid, gid)
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
    source_map = POSTFIX_BORON_DIR / "mysql-virtual-mailboxes.cf"
    if not source_map.exists():
        raise DkimError("Postfix mailbox lookup configuration is unavailable")
    source_lines = [line for line in source_map.read_text().splitlines()
                    if not line.lstrip().startswith("query")]
    sender_query = (
        "query = SELECT CONCAT(u.local_part, '@', d.domain) FROM mail_user u "
        "JOIN mail_domain d ON d.id=u.domain_id WHERE "
        "CONCAT(u.local_part, '@', d.domain)='%s' AND u.active=1 AND d.active=1"
    )
    _atomic_write(SENDER_LOGIN_MAP, "\n".join(source_lines + [sender_query]) + "\n", 0o640)
    try:
        postfix_gid = pwd.getpwnam("postfix").pw_gid
        os.chown(SENDER_LOGIN_MAP, 0, postfix_gid)
    except KeyError as exc:
        raise DkimError("Postfix service account is unavailable") from exc
    values = {
        "milter_default_action": "accept", "milter_protocol": "6",
        # Port 25 and local pickup are shared/inbound paths and never carry a
        # trustworthy tenant identity. Signing happens only in the two
        # identity-bound frontends.
        "smtpd_milters": "",
        "non_smtpd_milters": "",
        "boron-sign_destination_recipient_limit": "1",
    }
    for name, value in values.items():
        run(["postconf", "-e", f"{name}={value}"], timeout=15, check=True)
    # Loopback relay, tenant use of Postfix's sendmail/postdrop and the
    # resulting unsigned-but-SPF-aligned spoofing are closed here, so every
    # install, upgrade and DKIM refresh reconciles the same policy.
    try:
        mail_hardening.configure_postfix_policy(run)
    except mail_hardening.MailHardeningError as exc:
        raise DkimError(str(exc)) from exc
    # HELO, Received headers and bounces use the canonical mail hostname.
    mail_identity.configure_postfix_identity(run)
    run(["postconf", "-Me", SIGN_SERVICE], timeout=15, check=True)
    _configure_submission_services()
    run(["postconf", "-P", "smtp/inet/smtpd_sasl_auth_enable=no"], timeout=15, check=True)


def _implicit_tls_service() -> str:
    """The master.cf entry for port 465: an operator's existing smtps entry
    is reused (two entries would both bind the port), else submissions."""
    for service in ("submissions/inet", "smtps/inet"):
        if run(["postconf", "-M", service], timeout=15).stdout.strip():
            return service
    run(["postconf", "-Me", f"{SUBMISSIONS_SERVICE}=submissions inet n - y - - smtpd"], timeout=15, check=True)
    return SUBMISSIONS_SERVICE


def _configure_submission_services() -> None:
    """Port 587 (STARTTLS) and 465 (implicit TLS, RFC 8314) get exactly the
    same authentication, sender-identity and DKIM signing rules."""
    services = {
        "submission/inet": {"syslog_name": "postfix/submission", "smtpd_tls_security_level": "encrypt"},
        _implicit_tls_service(): {"syslog_name": "postfix/submissions", "smtpd_tls_wrappermode": "yes"},
    }
    for service, transport in services.items():
        for name, value in {**transport, **submission_policy()}.items():
            run(["postconf", "-P", f"{service}/{name}={value}"], timeout=15, check=True)


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
    _ensure_socket_directory()
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
    """TXT presentation in strings of at most 255 octets (a 2048-bit key
    is ~410 octets); dnsprovider normalizes every write the same way."""
    return dnsprovider.txt_presentation(value.encode("utf-8"))


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

    dmarc_published = False
    if zone:
        try:
            dmarc_published = _ensure_default_dmarc(domain, zone)
        except Exception:  # best effort, like DKIM publication: never undo mail setup
            logger.exception("failed to publish the default DMARC record for '%s'", domain)

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
        "dmarc_record_value": DEFAULT_DMARC, "dmarc_published": dmarc_published,
        "last_error": error,
    }


# Monitoring-only policy: it never changes delivery, but lets receivers
# evaluate the SPF/DKIM alignment the panel already provides. Customers
# tighten it through the DMARC editor (daemon/mail_dns.set_dmarc).
DEFAULT_DMARC = "v=DMARC1; p=none"


def _dmarc_label(domain: str, zone: str) -> str:
    label = label_within_zone(domain, zone)
    return "_dmarc" if label == "@" else f"_dmarc.{label}"


def _existing_txt(zone: str, label: str) -> list[str] | None:
    fqdn = zone if label == "@" else f"{label}.{zone}"
    for record in dnsprovider.list_records(zone):
        name = str(record.get("name", "")).lower().rstrip(".")
        if name == fqdn and str(record.get("type", "")).upper() == "TXT":
            return [str(value) for value in (record.get("values") or [])]
    return None


def _public_dmarc_exists(name: str) -> bool | None:
    """True/False when public DNS answers definitively, None when unsure."""
    import dns.exception
    import dns.resolver

    from daemon.mail_local_delivery import _resolver
    try:
        answer = _resolver().resolve(f"_dmarc.{name}", "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    except dns.exception.DNSException:
        return None
    return any(b"".join(row.strings).lower().startswith(b"v=dmarc1") for row in answer)


def _ancestor_has_dmarc(domain: str, zone: str) -> bool:
    """A subdomain's own record overrides its organizational domain's
    policy (RFC 7489 section 6.6.3), so p=none published under a parent
    with p=reject or sp=reject would weaken it. Ancestors inside the zone
    are read from the zone; ancestors above a delegated zone from public
    DNS, treating an unanswered lookup as "present" (publish nothing)."""
    labels = domain.lower().rstrip(".").split(".")
    zone = zone.lower().rstrip(".")
    for index in range(1, len(labels) - 1):  # never the domain itself or the TLD
        ancestor = ".".join(labels[index:])
        if ancestor == zone or ancestor.endswith("." + zone):
            if _existing_txt(zone, _dmarc_label(ancestor, zone)) is not None:
                return True
        elif _public_dmarc_exists(ancestor) is not False:
            return True
    return False


def _ensure_default_dmarc(domain: str, zone: str) -> bool:
    """Publish p=none unless the domain or a parent already has DMARC.

    An existing record (customer edited, or imported with the zone) is the
    customer's policy and is never overwritten, and a parent's policy is
    never shadowed by a weaker subdomain record.
    """
    label = _dmarc_label(domain, zone)
    if _existing_txt(zone, label) is not None:
        return False
    if _ancestor_has_dmarc(domain, zone):
        return False
    with dnsprovider.batch_cluster_notifications():
        dnsprovider.upsert_record(zone, label, "TXT", [_quote(DEFAULT_DMARC)])
    return True


def _remove_default_dmarc(domain: str, zone: str) -> None:
    """Remove only the record this module published, never a custom one."""
    label = _dmarc_label(domain, zone)
    values = _existing_txt(zone, label)
    if values is None:
        return
    if [dnsprovider.txt_text(value.strip()) for value in values] == [DEFAULT_DMARC]:
        dnsprovider.delete_record(zone, label, "TXT")


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
        try:
            _remove_default_dmarc(domain, zone)
        except Exception:  # best effort: the domain is being removed regardless
            logger.exception("failed to remove the default DMARC record for '%s'", domain)
    shutil.rmtree(_domain_dir(domain), ignore_errors=True)
    if shutil.which("opendkim"):
        try:
            configure_signer()
        except Exception:
            logger.exception("failed to refresh OpenDKIM tables after removing '%s'", domain)
