"""The server's own mail identity: its hostname and TLS certificate.

One canonical name (mail_hostname, else webmail_hostname, else
panel_hostname) is the MX target customers publish, the IMAP/POP3/SMTP
server their clients use, the subject of the certificate Postfix and Dovecot
present (/etc/boron/ssl/mail.crt, deployed by scripts/ssl_deploy_hook.py) and
Postfix's myhostname: the HELO name, the Received headers and the domain of
MAILER-DAEMON bounces. Left at the VM's bare hostname, remote servers see a
non-FQDN HELO and bounces come from an unroutable address.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
from pathlib import Path
import shutil
import socket

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from sqlalchemy import select

from daemon.procutil import run as _run
from shared.config import settings
from shared.db import write_session
from shared.models import MailDomain
from shared.validation import ValidationError, validate_domain

logger = logging.getLogger("borond.mail_identity")
LETSENCRYPT_LIVE_DIR = Path("/etc/letsencrypt/live")
MAIL_CERT_PATH = Path("/etc/boron/ssl/mail.crt")


def canonical_hostname() -> str | None:
    """The configured canonical mail hostname, or None when none is set."""
    value = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    if not value:
        return None
    try:
        return validate_domain(value)
    except ValidationError:
        return None


def _hosted_mail_domain(hostname: str) -> bool:
    with write_session() as session:
        return session.scalar(select(MailDomain.id).where(MailDomain.domain == hostname)) is not None


def _postconf(run, name: str) -> str:
    return run(["postconf", "-h", name], timeout=15, check=True).stdout.strip()


def configure_postfix_identity(run=None) -> dict:
    """Make Postfix use the canonical mail hostname (the caller reloads).

    Postfix delivers mail for $myhostname to system users (mydestination),
    so a name that is also a hosted mail domain is left alone rather than
    taking that domain's mailboxes away; handlers_mail refuses new ones.
    """
    run = run or _run
    hostname = canonical_hostname()
    if hostname is None:
        return {"myhostname": None, "changed": False, "detail": "No mail hostname is configured"}
    if _hosted_mail_domain(hostname):
        return {"myhostname": None, "changed": False, "detail": f"{hostname} is also a hosted mail domain"}
    changed = False
    if _postconf(run, "myhostname") != hostname:
        run(["postconf", "-e", f"myhostname={hostname}"], timeout=15, check=True)
        changed = True
    if _postconf(run, "smtp_helo_name") != "$myhostname":
        run(["postconf", "-e", "smtp_helo_name=$myhostname"], timeout=15, check=True)
        changed = True
    return {"myhostname": hostname, "changed": changed}


IDENTITY_SETTINGS = frozenset({"mail_hostname", "webmail_hostname", "panel_hostname"})


def apply_postfix_identity(changed=None) -> dict:
    """Server Setup hook: when a setting the canonical name derives from
    changed, reconcile Postfix and reload it.

    Best effort: a Postfix problem must not undo a service hostname change;
    the mail_identity diagnostics row reports a name left behind.
    """
    if changed is not None and not IDENTITY_SETTINGS & set(changed):
        return {"myhostname": None, "changed": False, "detail": "No mail identity setting changed"}
    if shutil.which("postconf") is None:
        return {"myhostname": None, "changed": False, "detail": "Postfix is not installed"}
    try:
        result = configure_postfix_identity()
        if result["changed"]:
            _run(["postfix", "check"], timeout=20, check=True)
            _run(["systemctl", "reload", "postfix"], timeout=30, check=True)
            logger.info("Postfix now identifies as %s", result["myhostname"])
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("could not apply the mail hostname to Postfix")
        return {"myhostname": None, "changed": False, "detail": "Postfix could not be updated"}
    return result


def _server_addresses() -> set[str]:
    from daemon.mail_local_delivery import server_addresses
    return server_addresses()


def _resolved(hostname: str) -> list[str]:
    try:
        rows = socket.getaddrinfo(hostname, 25, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({ipaddress.ip_address(row[4][0].split("%", 1)[0]).compressed for row in rows})


def _certificate(path: Path) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return None


def _issue_step(hostname: str) -> str:
    """Where an administrator issues the certificate for this hostname."""
    if hostname == settings.webmail_hostname:
        return (f"Issue the Webmail certificate for {hostname} in Server Setup step 6 or under "
                "SSL > Service certificates; Postfix and Dovecot switch to it automatically.")
    if hostname == settings.panel_hostname:
        return ("The panel certificate is not deployed to Postfix and Dovecot: set a webmail hostname in "
                "Server Setup step 4 (the mail hostname follows it) and issue its certificate in step 6.")
    # Only the webmail vhost answers HTTP ACME requests for a service name;
    # ssl._challenge_plan uses DNS-01 for a separate mail hostname.
    return (f"Let's Encrypt cannot validate {hostname} over HTTP: it is not the webmail hostname, which is "
            "the only service name this server answers certificate checks for. If its DNS zone is hosted "
            "on this server (and delegated to it publicly) or on Cloudflare, issue the Mail certificate "
            "in Server Setup step 6 or under SSL > Service certificates; it is validated through DNS. "
            "Otherwise save the service hostnames in Server Setup step 4 (the mail hostname then follows "
            "the webmail hostname) and issue the Webmail certificate in step 6.")


def readiness_check() -> dict:
    """Diagnostics row (and so an update advisory) for the mail identity."""
    from daemon.diagnostics import _result
    status, explanation, fix = _readiness()
    return _result("mail_identity", "Mail hostname and certificate", status, explanation, fix)


def _readiness() -> tuple[str, str, str]:
    """(status, explanation, suggested fix) for the next missing step, in the
    order an administrator completes them."""
    hostname = canonical_hostname()
    if hostname is None:
        return ("warn", "No mail hostname is configured: mail clients see a self-signed certificate and "
                "Postfix greets other servers with the machine name.",
                "Set the webmail hostname in Server Setup step 4; the mail hostname follows it.")
    if shutil.which("postconf") is None:
        return ("unknown", "Postfix is not installed on this server.", "")
    addresses = _resolved(hostname)
    target = settings.server_public_ip or "this server's public address"
    if not addresses:
        return ("fail", f"{hostname} does not resolve, so mail clients cannot reach it and no certificate "
                "can be issued for it.",
                f"Create an A record for {hostname} pointing to {target}. Then: {_issue_step(hostname)}")
    local = _server_addresses()
    if local and not set(addresses) & local:
        return ("fail", f"{hostname} resolves to {', '.join(addresses)}, not to this server.",
                f"Point {hostname} at {target}. Then: {_issue_step(hostname)}")
    lineage = _certificate(LETSENCRYPT_LIVE_DIR / hostname / "fullchain.pem")
    deployed = _certificate(MAIL_CERT_PATH)
    presented = deployed.subject.rfc4514_string() if deployed is not None else "no certificate"
    if lineage is None:
        return ("fail", f"No Let's Encrypt certificate exists for {hostname}; IMAP, POP3 and SMTP "
                f"present {presented}.", _issue_step(hostname))
    if deployed is None or deployed.public_bytes(Encoding.DER) != lineage.public_bytes(Encoding.DER):
        return ("fail", f"A Let's Encrypt certificate for {hostname} exists, but Postfix and Dovecot still "
                f"present {presented} ({MAIL_CERT_PATH}).",
                _issue_step(hostname) if hostname == settings.panel_hostname else
                f"Renew {hostname} under SSL > Service certificates; that deploys it to Postfix and Dovecot.")
    remaining = (lineage.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).days
    if remaining < 0:
        return ("fail", f"The mail certificate for {hostname} has expired.",
                f"Renew {hostname} under SSL > Service certificates.")
    current = _postconf(_run, "myhostname")
    if current != hostname:
        if _hosted_mail_domain(hostname):
            return ("warn", f"Postfix identifies itself as {current} instead of {hostname}, because "
                    f"{hostname} is also a hosted mail domain.",
                    f"Delete the mail domain {hostname}, or choose another webmail hostname in Server Setup step 4.")
        return ("warn", f"Postfix identifies itself as {current} (HELO and bounces) instead of {hostname}.",
                "Save the service hostnames again in Server Setup step 4, or run the panel update.")
    return ("pass", f"{hostname} resolves to this server, Postfix uses it as its hostname, and IMAP, POP3 "
            f"and SMTP present its Let's Encrypt certificate ({remaining} days remaining).", "")
