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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from shared.config import settings  # noqa: E402
from shared.db import write_session  # noqa: E402
from shared.models import Account, Domain  # noqa: E402

from daemon import cloudflare_ops, ols  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ssl_deploy_hook: %(message)s")
logger = logging.getLogger("ssl_deploy_hook")


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
        logger.info("applied new certificate for webmail host %s", domain_name)
        return

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


if __name__ == "__main__":
    raise SystemExit(main())
