"""Run A feature 4: client onboarding wizard state.

The wizard itself is pure frontend (three dialog steps on the customer
dashboard); this module only owns the once-only gate -- has this account
completed/skipped it? -- plus the server facts step 2 displays (public
IP, the account's vanity NS names). `get` also returns those facts so the
wizard needs exactly one API call and the frontend never hardcodes the
server IP.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, OnboardingState, utcnow
from shared.validation import validate_username


def _get_account(session, username: str) -> Account:
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    return account


def _get_row(session, account_id: int) -> OnboardingState:
    row = session.scalar(select(OnboardingState).where(OnboardingState.account_id == account_id))
    if row is None:
        row = OnboardingState(account_id=account_id)
        session.add(row)
        session.flush()
    return row


def _to_dict(row: OnboardingState, account: Account) -> dict:
    primary = account.primary_domain
    return {
        "completed": row.completed,
        "skipped": row.skipped,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        # Step 2 (DNS/NS) display facts. The vanity NS pair matches what
        # handlers_dns.create_zone actually provisions (ns1/ns2.<zone> A
        # records at the server IP) -- if the account has no primary domain
        # yet, there is no zone, so only the bare A-record path applies.
        "server_ip": settings.server_public_ip,
        "primary_domain": primary,
        "nameservers": [f"ns1.{primary}", f"ns2.{primary}"] if primary else [],
    }


def get_onboarding(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        return _to_dict(_get_row(session, account.id), account)


def set_onboarding(params: dict) -> dict:
    """Marking completed is one-way: the wizard is once-only by the goal's
    own spec, so there is deliberately no un-complete path (an admin who
    really wants to re-trigger it can delete the row manually)."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = _get_account(session, username)
        row = _get_row(session, account.id)
        if params.get("completed") and not row.completed:
            row.completed = True
            row.skipped = bool(params.get("skipped", False))
            row.completed_at = utcnow()
            session.flush()
        return _to_dict(row, account)
