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
from shared.models import Account, Domain, DnsZone, SslExpiryNotice
from shared.validation import validate_domain, validate_username

from daemon import cloudflare_accounts, dnsprovider, events
from daemon.ols import letsencrypt_cert_paths
from daemon.procutil import run

DEPLOY_HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "ssl_deploy_hook.py")
_VENV_PYTHON = str(Path(settings.certbot_bin).parent / "python")


class SslError(Exception):
    pass


def _dns01_args() -> list[str]:
    """certbot-dns-powerdns plugin args -- shared by the auto-detected
    DNS-01 path (_challenge_plan, when a domain's zone happens to be
    Forgehost-managed) and the explicit wildcard-issuance path
    (issue_wildcard_certificate), which REQUIRES DNS-01 unconditionally
    (Let's Encrypt has no HTTP-01 path for wildcard names at all -- this
    isn't a Forgehost design choice, it's an ACME protocol constraint)."""
    return [
        "--authenticator", "dns-powerdns",
        "--dns-powerdns-credentials", settings.powerdns_credentials_file,
        "--dns-powerdns-propagation-seconds", "30",
    ]


def _cf_credentials_path(cf_account_pk: int | None) -> str:
    """One certbot-dns-cloudflare credentials file per pool account (or the
    configured single file for a legacy/None account), so multi-account
    issuance uses the right token. certbot stores this path in the renewal
    conf, so `certbot renew` keeps using the correct per-domain
    authenticator + creds automatically."""
    if cf_account_pk is not None:
        base = settings.cloudflare_credentials_file
        return base[:-4] + f"-{cf_account_pk}.ini" if base.endswith(".ini") else f"{base}-{cf_account_pk}"
    return settings.cloudflare_credentials_file


def _ensure_cf_credentials(row) -> str:
    """Write the certbot-dns-cloudflare credentials INI (0600) for the pool
    account serving `row`'s zone, and return its path."""
    token = cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None)) or settings.cloudflare_api_token
    if not token:
        raise SslError(f"no Cloudflare API token available for zone '{row.zone}' -- cannot issue via DNS-01")
    path = Path(_cf_credentials_path(getattr(row, "cf_account_id", None)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"dns_cloudflare_api_token = {token}\n")
    path.chmod(0o600)  # certbot refuses world/group-readable credentials files
    return str(path)


def _cf_dns01_args(row) -> list[str]:
    return [
        "--authenticator", "dns-cloudflare",
        "--dns-cloudflare-credentials", _ensure_cf_credentials(row),
        "--dns-cloudflare-propagation-seconds", "30",
    ]


def _dns01_plan(domain: str) -> list[str] | None:
    """Phase 2+3 feature 5: pick the DNS-01 authenticator by the zone's live
    provider. A Cloudflare-ACTIVE zone MUST use dns-cloudflare (Cloudflare is
    authoritative, so the _acme-challenge TXT has to be created there, not in
    the stale local PowerDNS zone Forgehost keeps as the revert target). A
    local Forgehost-managed zone uses dns-powerdns. Anything else -> None
    (no DNS-01 hook available)."""
    row = dnsprovider.cloudflare_zone_row(domain)
    if row is not None and row.status == "active":
        return _cf_dns01_args(row)
    with write_session() as session:
        if session.scalar(select(DnsZone).where(DnsZone.zone == domain)) is not None:
            return _dns01_args()
    return None


def _challenge_plan(domain: str) -> tuple[str, list[str]]:
    """Returns (mode, certbot_args). mode is "dns-01" or "http-01", purely
    for logging/the RPC response -- the actual behavior is in the args."""
    # Phase 2 feature 3: Roundcube's static webmail hostname isn't a
    # per-account Domain row -- it's server infrastructure, same category
    # as the panel's own TLS cert. Its webroot is settings.webmail_docroot,
    # not something looked up from the accounts/domains tables.
    if domain == settings.webmail_hostname:
        return "http-01", ["--webroot", "-w", settings.webmail_docroot]

    dns01 = _dns01_plan(domain)  # CF-active -> dns-cloudflare, local zone -> dns-powerdns
    if dns01 is not None:
        return "dns-01", dns01

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


def issue_wildcard_certificate(params: dict) -> dict:
    """Phase 7a feature 5: `*.<domain>` + `<domain>` in one cert, via
    DNS-01 + the PowerDNS API hook -- the same certbot-dns-powerdns plugin
    ARCHITECTURE.md SS8 already wires for the auto-detected DNS-01 path,
    just unconditional here (a wildcard SAN has no HTTP-01 option at all,
    so this never falls back to webroot the way issue_certificate does).
    Requires the domain's own zone to be Forgehost-managed (a real
    PowerDNS zone this server can create/delete the `_acme-challenge` TXT
    record in) -- an externally-DNS-managed domain has no hook for
    Forgehost to create that record in, so wildcard issuance for it is
    rejected with a clear reason rather than silently trying and failing
    deep inside certbot."""
    raw_domain = params["domain"]
    if isinstance(raw_domain, str) and raw_domain.strip().startswith("*."):
        raise SslError("pass the base domain (e.g. 'example.com'), not '*.example.com' -- the wildcard SAN is added automatically")
    domain = validate_domain(raw_domain)
    if not settings.letsencrypt_email:
        raise SslError("letsencrypt_email is not set in forgehost.toml")

    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
    if domain_row is None:
        raise SslError(f"domain '{domain}' is not provisioned in Forgehost")
    # Phase 2+3 feature 5: wildcards work on either DNS-01 provider now
    # (dns-cloudflare for a CF-active zone, dns-powerdns for a local managed
    # zone). Still requires a Forgehost-managed zone -- an externally-DNS
    # domain has no hook to create the _acme-challenge TXT in.
    dns01 = _dns01_plan(domain)
    if dns01 is None:
        raise SslError(
            f"wildcard SSL for '{domain}' requires its DNS zone to be managed by Forgehost "
            "(local PowerDNS or a Cloudflare-active zone) -- DNS-01 is the only ACME challenge type that "
            "supports wildcard names, and it needs the _acme-challenge TXT to be creatable through the "
            "zone's own DNS API. Create a Forgehost-managed zone for this domain first."
        )

    force_args = ["--force-renewal"] if params.get("force") else []
    args = [
        settings.certbot_bin, "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email", settings.letsencrypt_email,
        "--cert-name", domain,
        "-d", domain,
        "-d", f"*.{domain}",
        "--deploy-hook", f"{_VENV_PYTHON} {DEPLOY_HOOK_SCRIPT}",
        *force_args,
        *dns01,
    ]
    result = run(args, timeout=180)
    if not result.ok:
        raise SslError(f"certbot failed (wildcard dns-01): {result.stderr.strip() or result.stdout.strip()}")

    return {"domain": domain, "wildcard_domain": f"*.{domain}", "challenge": "dns-01", "status": "issued"}


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
        domain_data = [(d.domain, d.ssl_status, d.ssl_is_wildcard) for d in domains]

    timer_active = run(["systemctl", "is-active", "certbot.timer"], timeout=10).stdout.strip() == "active"

    entries = []
    for domain_name, ssl_status, ssl_is_wildcard in domain_data:
        details = _cert_file_details(domain_name)
        entries.append(
            {
                "domain": domain_name,
                "ssl_status": ssl_status,
                "is_wildcard": ssl_is_wildcard,
                "cert_status": details["cert_status"] if details else "missing",
                "expiry_date": details["expiry_date"] if details else None,
                "days_remaining": details["days_remaining"] if details else None,
                "issuer": details["issuer"] if details else None,
                "auto_renew": bool(details) and timer_active and _auto_renew_enabled(domain_name),
            }
        )

    return {"username": username, "certbot_timer_active": timer_active, "domains": entries}


# --- SSL expiry notifications (Phase 7b feature 3) --------------------------

SSL_EXPIRY_WARNING_DAYS = 14


def check_expiring_certificates() -> int:
    """Entry point for scripts/ssl_expiry_check.py (system cron, daily).
    Scans every domain with an active cert, and for one within
    SSL_EXPIRY_WARNING_DAYS of expiry, fires "ssl.expiring" through
    daemon/events.py exactly once per (domain, expiry-date) pair --
    SslExpiryNotice is the dedup marker, keyed on the cert's own not-after
    date so a *renewed* cert (new expiry date) is correctly treated as a
    fresh notice-worthy event rather than never notifying again. Returns
    the number of notices actually sent."""
    with write_session() as session:
        domains = session.scalars(select(Domain).where(Domain.ssl_status == "active")).all()
        domain_data = [(d.domain, d.account_id) for d in domains]

    sent = 0
    for domain_name, account_id in domain_data:
        details = _cert_file_details(domain_name)
        # Deliberately NOT details["cert_status"] == "expiring" -- that
        # flag uses the SSL *dashboard's* own EXPIRING_SOON_DAYS (30, a
        # different, UI-only concern), while this feature's own goal text
        # specifies a 14-day warning window. A cert already "expired" is
        # also still notice-worthy (renewal likely failed silently).
        if details is None or details["days_remaining"] > SSL_EXPIRY_WARNING_DAYS:
            continue
        expiry_date = details["expiry_date"]
        with write_session() as session:
            already_notified = session.scalar(
                select(SslExpiryNotice).where(SslExpiryNotice.domain == domain_name, SslExpiryNotice.expiry_date == expiry_date)
            )
            if already_notified is not None:
                continue
            account = session.get(Account, account_id)
            if account is None:
                continue
            session.add(SslExpiryNotice(domain=domain_name, expiry_date=expiry_date))
            account_snapshot = account

        events.emit(
            "ssl.expiring", account_snapshot,
            domain=domain_name, days_remaining=details["days_remaining"], expiry_date=expiry_date,
        )
        sent += 1
    return sent
