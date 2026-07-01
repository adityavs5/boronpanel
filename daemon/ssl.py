"""SSL automation via certbot (Phase f).

ARCHITECTURE.md SS8: DNS-01 (via the `certbot-dns-powerdns` plugin) when
Forgehost manages the domain's zone, HTTP-01 webroot otherwise -- the
webroot path already exists from Phase b (every vhost declares a
`/.well-known/acme-challenge/` context pointed at the account's docroot, no
extra vhost config needed here). `--deploy-hook` points at
`scripts/ssl_deploy_hook.py`, a standalone script (not a daemon method)
because certbot's own renewal timer invokes it outside forgehostd's
process -- it reuses the same shared/daemon modules directly rather than
going through the Unix-socket RPC (it already runs as root, same as
forgehostd, so there's no privilege boundary to cross).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from cryptography import x509
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, DnsZone
from shared.validation import validate_domain, validate_username

from daemon.ols import letsencrypt_cert_paths
from daemon.procutil import run

DEPLOY_HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "ssl_deploy_hook.py")
_VENV_PYTHON = str(Path(settings.certbot_bin).parent / "python")


class SslError(Exception):
    pass


def _challenge_plan(domain: str) -> tuple[str, list[str]]:
    """Returns (mode, certbot_args). mode is "dns-01" or "http-01", purely
    for logging/the RPC response -- the actual behavior is in the args."""
    # Phase 2 feature 3: Roundcube's static webmail hostname isn't a
    # per-account Domain row -- it's server infrastructure, same category
    # as the panel's own TLS cert. Its webroot is settings.webmail_docroot,
    # not something looked up from the accounts/domains tables.
    if domain == settings.webmail_hostname:
        return "http-01", ["--webroot", "-w", settings.webmail_docroot]

    with write_session() as session:
        zone_managed = session.scalar(select(DnsZone).where(DnsZone.zone == domain)) is not None

    if zone_managed:
        return "dns-01", [
            "--authenticator", "dns-powerdns",
            "--dns-powerdns-credentials", settings.powerdns_credentials_file,
            "--dns-powerdns-propagation-seconds", "30",
        ]

    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
    if domain_row is None:
        raise SslError(f"domain '{domain}' is not provisioned in Forgehost")
    return "http-01", ["--webroot", "-w", domain_row.docroot]


def issue_certificate(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    if not settings.letsencrypt_email:
        raise SslError("letsencrypt_email is not set in forgehost.toml")

    mode, challenge_args = _challenge_plan(domain)
    # Phase 3 feature 8: the SSL dashboard's "renew" button targets a
    # domain that may already have a perfectly valid, non-expiring-soon
    # cert -- certbot's own default behavior is to silently skip
    # reissuing in that case ("Certificate not yet due for renewal"),
    # which would make a "one-click renew" button appear to do nothing.
    # `--force-renewal` is exactly certbot's own documented escape hatch
    # for "the operator explicitly asked for this, do it regardless of
    # the normal expiry-window check" -- only ever passed when the
    # caller explicitly asks (never on the plain "issue" path, where a
    # domain has no cert yet and there's nothing to force).
    force_args = ["--force-renewal"] if params.get("force") else []

    args = [
        settings.certbot_bin, "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email", settings.letsencrypt_email,
        "--cert-name", domain,
        "-d", domain,
        "--deploy-hook", f"{_VENV_PYTHON} {DEPLOY_HOOK_SCRIPT}",
        *force_args,
        *challenge_args,
    ]
    result = run(args, timeout=180)
    if not result.ok:
        raise SslError(f"certbot failed ({mode}): {result.stderr.strip() or result.stdout.strip()}")

    return {"domain": domain, "challenge": mode, "status": "issued"}


def certificate_status(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    if domain == settings.webmail_hostname:
        key, cert = letsencrypt_cert_paths(domain)
        exists = Path(key).exists() and Path(cert).exists()
        return {"domain": domain, "ssl_status": "active" if exists else "none"}
    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
        if domain_row is None:
            raise SslError(f"domain '{domain}' is not provisioned in Forgehost")
        return {"domain": domain, "ssl_status": domain_row.ssl_status}


def terminate_account_certs(account) -> None:
    """TERMINATE_HOOKS entry: revoke and delete certbot's local copy of any
    certificate issued for this account's domains. Without this, terminated
    accounts leave orphaned cert/key material under /etc/letsencrypt and
    certbot's renewal timer keeps trying (and failing, since the vhost/
    webroot is gone) to renew a cert nobody needs anymore -- exactly the
    "no orphaned configs/processes" requirement on termination. Idempotent:
    certbot delete on a cert-name that doesn't exist is a safe no-op
    (checked via `certbot certificates` first rather than assumed)."""
    with write_session() as session:
        domains = session.scalars(
            select(Domain.domain).where(Domain.account_id == account.id, Domain.ssl_status == "active")
        ).all()
    for domain_name in domains:
        if not Path(f"/etc/letsencrypt/live/{domain_name}").exists():
            continue
        run([settings.certbot_bin, "delete", "--cert-name", domain_name, "--non-interactive"], timeout=30)


# --- SSL dashboard (Phase 3 feature 8) ---------------------------------

EXPIRING_SOON_DAYS = 30
RENEWAL_CONF_DIR = Path("/etc/letsencrypt/renewal")


def _cert_file_details(domain: str) -> dict | None:
    """Real, on-disk certificate inspection via the `cryptography` library
    (not `openssl x509` text-parsing, which this project avoids shelling
    out for anything a proper library can parse instead) -- returns None
    if no certificate file exists for this domain at all ("missing" per
    the goal's own status vocabulary), otherwise the actual expiry date
    and issuer straight from the X.509 certificate itself, not from
    Forgehost's own DB (Domain.ssl_status only records "did Forgehost's
    own issue flow succeed", not the certificate's real, independently-
    verifiable expiry -- the dashboard's whole point is showing the
    latter)."""
    _key_path, cert_path = letsencrypt_cert_paths(domain)
    if not Path(cert_path).exists():
        return None
    try:
        cert_bytes = Path(cert_path).read_bytes()
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except (OSError, ValueError):
        return None

    not_after = cert.not_valid_after_utc
    days_remaining = (not_after - dt.datetime.now(dt.timezone.utc)).days
    issuer = cert.issuer.rfc4514_string()

    if days_remaining < 0:
        status = "expired"
    elif days_remaining <= EXPIRING_SOON_DAYS:
        status = "expiring"
    else:
        status = "valid"

    return {
        "cert_status": status,
        "expiry_date": not_after.date().isoformat(),
        "days_remaining": days_remaining,
        "issuer": issuer,
    }


def _auto_renew_enabled(domain: str) -> bool:
    """certbot's own renewal timer (`certbot.timer`, stock package unit --
    Forgehost does not reimplement a renewal scheduler, ARCHITECTURE.md
    SS8) handles every certificate with a renewal config under
    /etc/letsencrypt/renewal/ automatically; this just reports whether
    that's true for this specific domain, not whether the timer itself
    is enabled (a per-domain "will this actually renew" answer -- the
    timer being globally active is a server-wide fact checked once at
    dashboard-render time by the caller, not per domain)."""
    return (RENEWAL_CONF_DIR / f"{domain}.conf").exists()


def get_ssl_dashboard(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise SslError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        domain_data = [(d.domain, d.ssl_status) for d in domains]

    timer_active = run(["systemctl", "is-active", "certbot.timer"], timeout=10).stdout.strip() == "active"

    entries = []
    for domain_name, ssl_status in domain_data:
        details = _cert_file_details(domain_name)
        entries.append(
            {
                "domain": domain_name,
                "ssl_status": ssl_status,
                "cert_status": details["cert_status"] if details else "missing",
                "expiry_date": details["expiry_date"] if details else None,
                "days_remaining": details["days_remaining"] if details else None,
                "issuer": details["issuer"] if details else None,
                "auto_renew": bool(details) and timer_active and _auto_renew_enabled(domain_name),
            }
        )

    return {"username": username, "certbot_timer_active": timer_active, "domains": entries}
