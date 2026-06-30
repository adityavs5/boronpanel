#!/opt/forgehost/.venv/bin/python
"""certbot --deploy-hook target (Phase f).

Runs as a standalone process invoked BY certbot -- on every successful
issuance, including the very first one, not just renewals -- so it's the
single place that flips Domain.ssl_status to "active" and triggers a real
OLS reload to pick up the new cert. It is not part of forgehostd's running
process: certbot's own systemd timer fires this independently of whether
the daemon happens to be up, so it must be fully self-contained (its own
DB session, its own import of daemon.ols) rather than calling back into a
running daemon over the RPC socket.

certbot sets RENEWED_DOMAINS (space-separated) and RENEWED_LINEAGE (the
/etc/letsencrypt/live/<name> path) in the environment for deploy-hooks.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from shared.db import write_session  # noqa: E402
from shared.models import Account, Domain  # noqa: E402

from daemon import ols  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ssl_deploy_hook: %(message)s")
logger = logging.getLogger("ssl_deploy_hook")


def main() -> int:
    renewed_domains = os.environ.get("RENEWED_DOMAINS", "").split()
    if not renewed_domains:
        logger.error("RENEWED_DOMAINS not set -- not invoked by certbot?")
        return 1

    exit_code = 0
    for domain_name in renewed_domains:
        try:
            _apply_for_domain(domain_name)
        except Exception:
            logger.exception("failed to apply new certificate for %s", domain_name)
            exit_code = 1
    return exit_code


def _apply_for_domain(domain_name: str) -> None:
    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if domain_row is None:
            logger.warning("domain '%s' not found in Forgehost DB, skipping", domain_name)
            return
        domain_row.ssl_status = "active"
        account = session.get(Account, domain_row.account_id)
        if account is None:
            logger.warning("account for domain '%s' not found, skipping reload", domain_name)
            return
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    logger.info("applied new certificate for %s (account %s)", domain_name, account_snapshot.username)


if __name__ == "__main__":
    raise SystemExit(main())
