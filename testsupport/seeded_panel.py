"""Create a small, real Boron control-plane database for integration tests.

The fixture uses production models and password hashing. It creates no Linux
users and touches no service configuration, so it is safe for unprivileged CI
and can later be copied into an older release for upgrade coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SeededPanel:
    db_path: Path
    admin_username: str
    account_username: str
    primary_domain: str
    plan_name: str


def seed_panel_database(
    db_path: Path,
    *,
    admin_password: str,
    customer_password: str,
    admin_username: str = "ciadmin",
    account_username: str = "ciaccount",
    primary_domain: str = "ciaccount.example.test",
) -> SeededPanel:
    """Build an idempotent sample database and return its public identifiers."""
    from shared.config import settings
    import shared.db as db_module
    from shared.models import Account, AccountResourceLimits, Domain, PanelUser, Plan
    from shared.passwords import hash_password

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.db_path = str(db_path)
    db_module._write_engine = None
    db_module._WriteSession = None
    db_module._read_engine = None
    db_module._ReadSession = None
    grant_api_group_read = db_module._grant_api_group_read
    try:
        # A disposable fixture belongs to the invoking test user; it must not
        # depend on a host-level boron-api group or change ownership.
        db_module._grant_api_group_read = lambda: None
        db_module.init_db()
    finally:
        db_module._grant_api_group_read = grant_api_group_read

    with db_module.write_session() as db:
        plan = db.query(Plan).filter_by(name="CI Starter").one_or_none()
        if plan is None:
            plan = Plan(
                name="CI Starter", cpu_pct=100, mem_mb=1024, io_mb=50,
                pids_max=100, quota_soft_mb=4096, quota_hard_mb=5120,
                bandwidth_limit_mb=102400, database_limit=5,
                email_account_limit=10, subdomain_limit=10,
                ftp_account_limit=5, app_limit=3, redis_enabled=False,
            )
            db.add(plan)
            db.flush()

        account = db.query(Account).filter_by(username=account_username).one_or_none()
        if account is None:
            account = Account(
                username=account_username, status="active", uid=20001, gid=20001,
                primary_domain=primary_domain, php_version="8.3",
                quota_soft_mb=4096, quota_hard_mb=5120, cpu_pct=100,
                mem_mb=1024, io_mb=50, pids_max=100, plan_id=plan.id,
            )
            db.add(account)
            db.flush()
            db.add(AccountResourceLimits(
                account_id=account.id, bandwidth_limit_mb=102400,
                database_limit=5, email_account_limit=10, subdomain_limit=10,
                ftp_account_limit=5, app_limit=3,
            ))

        if db.query(Domain).filter_by(domain=primary_domain).one_or_none() is None:
            db.add(Domain(
                account_id=account.id, domain=primary_domain, kind="primary",
                docroot=f"/home/{account_username}/public_html",
            ))

        admin = db.query(PanelUser).filter_by(username=admin_username).one_or_none()
        if admin is None:
            db.add(PanelUser(
                username=admin_username, password_hash=hash_password(admin_password),
                role="admin",
            ))
        customer = db.query(PanelUser).filter_by(username=account_username).one_or_none()
        if customer is None:
            db.add(PanelUser(
                username=account_username, password_hash=hash_password(customer_password),
                role="customer", account_id=account.id,
            ))

    return SeededPanel(
        db_path=db_path,
        admin_username=admin_username,
        account_username=account_username,
        primary_domain=primary_domain,
        plan_name="CI Starter",
    )
