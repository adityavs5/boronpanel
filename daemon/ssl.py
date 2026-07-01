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

from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Domain, DnsZone
from shared.validation import validate_domain

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

    args = [
        settings.certbot_bin, "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email", settings.letsencrypt_email,
        "--cert-name", domain,
        "-d", domain,
        "--deploy-hook", f"{_VENV_PYTHON} {DEPLOY_HOOK_SCRIPT}",
        *challenge_args,
    ]
    result = run(args, timeout=180)
    if not result.ok:
        raise SslError(f"certbot failed ({mode}): {result.stderr.strip() or result.stdout.strip()}")

    return {"domain": domain, "challenge": mode, "status": "issued"}


def certificate_status(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    if domain == settings.webmail_hostname:
        from daemon.ols import letsencrypt_cert_paths

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
