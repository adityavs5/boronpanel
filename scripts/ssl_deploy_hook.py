#!/opt/boron/.venv/bin/python
"""certbot --deploy-hook target (Phase f).

Runs as a standalone process invoked BY certbot -- on every successful
issuance, including the very first one, not just renewals -- so it's the
single place that flips Domain.ssl_status to "active" and triggers a real
OLS reload to pick up the new cert. It is not part of borond's running
process: certbot's own systemd timer fires this independently of whether
the daemon happens to be up, so it must be fully self-contained (its own
DB session, its own import of daemon.ols) rather than calling back into a
running daemon over the RPC socket.

certbot sets RENEWED_DOMAINS (space-separated) and RENEWED_LINEAGE (the
/etc/letsencrypt/live/<name> path) in the environment for deploy-hooks.

Phase 7a feature 5 (wildcard SSL): a wildcard cert's RENEWED_DOMAINS
contains BOTH "example.com" and "*.example.com" in the same invocation --
only the bare name is ever a real Domain row (Boron never stores a
"*."-prefixed domain), so the "*."-prefixed entry is used only to detect
that this issuance covers a wildcard SAN, not looked up as its own row.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from shared.config import settings  # noqa: E402
from shared.db import write_session  # noqa: E402
from shared.models import Account, Domain  # noqa: E402

from daemon import cloudflare_ops, ols  # noqa: E402
from daemon.procutil import run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ssl_deploy_hook: %(message)s")
logger = logging.getLogger("ssl_deploy_hook")

LETSENCRYPT_LIVE_DIR = Path("/etc/letsencrypt/live")
MAIL_CERT_PATH = Path("/etc/boron/ssl/mail.crt")
MAIL_KEY_PATH = Path("/etc/boron/ssl/mail.key")


def main() -> int:
    renewed_domains = os.environ.get("RENEWED_DOMAINS", "").split()
    if not renewed_domains:
        logger.error("RENEWED_DOMAINS not set -- not invoked by certbot?")
        return 1

    non_wildcard_domains = [d for d in renewed_domains if not d.startswith("*.")]
    wildcard_bases = {d[2:] for d in renewed_domains if d.startswith("*.")}

    exit_code = 0
    for domain_name in non_wildcard_domains:
        try:
            _apply_for_domain(domain_name, is_wildcard=domain_name in wildcard_bases)
        except Exception:
            logger.exception("failed to apply new certificate for %s", domain_name)
            exit_code = 1
    return exit_code


def _apply_for_domain(domain_name: str, is_wildcard: bool = False) -> None:
    # Phase 2 feature 3: the static webmail hostname isn't a Domain row --
    # same reasoning as ssl.py's _challenge_plan special case.
    if domain_name == settings.webmail_hostname:
        ols.refresh_webmail_vhost()
        _deploy_mail_service_certificate(domain_name)
        logger.info("applied new certificate for webmail host %s", domain_name)
        return

    if domain_name == settings.pma_hostname:
        ols.refresh_pma_vhost()
        logger.info("applied new certificate for phpMyAdmin host %s", domain_name)
        return

    if domain_name == (settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname):
        _deploy_mail_service_certificate(domain_name)

    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if domain_row is None:
            logger.warning("domain '%s' not found in Boron DB, skipping", domain_name)
            return
        domain_row.ssl_status = "active"
        domain_row.ssl_is_wildcard = is_wildcard
        account = session.get(Account, domain_row.account_id)
        if account is None:
            logger.warning("account for domain '%s' not found, skipping reload", domain_name)
            return
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    logger.info(
        "applied new certificate for %s (account %s, wildcard=%s)",
        domain_name, account_snapshot.username, is_wildcard,
    )

    # Phase 2+3 feature 5: a browser-trusted origin cert now exists, so if the
    # zone is on Cloudflare, upgrade edge SSL to Full (strict). Best-effort --
    # an unreachable Cloudflare must never fail cert deployment.
    try:
        if cloudflare_ops.upgrade_ssl_strict(domain_name):
            logger.info("upgraded Cloudflare edge SSL to strict for %s", domain_name)
    except Exception:
        logger.exception("could not upgrade Cloudflare SSL mode for %s", domain_name)


def _deploy_mail_service_certificate(domain_name: str) -> None:
    """Install the canonical host certificate for IMAP/SMTP atomically."""
    canonical = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    if not canonical or domain_name != canonical:
        return
    lineage = LETSENCRYPT_LIVE_DIR / domain_name
    source_cert, source_key = lineage / "fullchain.pem", lineage / "privkey.pem"
    if not source_cert.exists() or not source_key.exists():
        raise RuntimeError("canonical mail certificate lineage is incomplete")
    destination = MAIL_CERT_PATH.parent
    destination.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path | None] = {}
    for source, target, mode in ((source_cert, MAIL_CERT_PATH, 0o644), (source_key, MAIL_KEY_PATH, 0o600)):
        temporary = destination / f".{target.name}.tmp.{os.getpid()}"
        shutil.copyfile(source, temporary)
        os.chmod(temporary, mode)
        staged.append((temporary, target))
    try:
        _validate_certificate_pair(staged[0][0], staged[1][0])
        for _temporary, target in staged:
            if target.exists():
                backup = destination / f".{target.name}.bak.{os.getpid()}"
                shutil.copyfile(target, backup)
                os.chmod(backup, target.stat().st_mode & 0o777)
                backups[target] = backup
            else:
                backups[target] = None
        for temporary, target in staged:
            os.replace(temporary, target)
        run(["doveconf", "-n"], timeout=20, check=True)
        run(["postfix", "check"], timeout=20, check=True)
        run(["systemctl", "reload", "dovecot"], timeout=30, check=True)
        run(["systemctl", "reload", "postfix"], timeout=30, check=True)
    except Exception:
        for target, backup in backups.items():
            if backup is None:
                target.unlink(missing_ok=True)
            elif backup.exists():
                os.replace(backup, target)
        # If one service already accepted the new pair, bring both back to the
        # restored pair. This is best-effort; the original exception remains
        # the actionable deploy-hook failure.
        for service in ("dovecot", "postfix"):
            try:
                run(["systemctl", "reload", service], timeout=30, check=True)
            except Exception:
                logger.exception("could not reload %s after restoring its prior mail certificate", service)
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)
    logger.info("deployed canonical mail certificate for %s", domain_name)


def _validate_certificate_pair(cert_path: Path, key_path: Path) -> None:
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    certificate_public = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_public = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if certificate_public != key_public:
        raise RuntimeError("canonical mail certificate and private key do not match")


if __name__ == "__main__":
    raise SystemExit(main())
