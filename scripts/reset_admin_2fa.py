#!/opt/boron/.venv/bin/python
"""Root-only break-glass reset of one administrator's TOTP credentials."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from shared.db import init_db, write_session  # noqa: E402
from shared.models import LoginChallenge, PanelUser, SecurityPolicySettings, Session, TotpCredential, TotpRecoveryCode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset 2FA for one Boron administrator")
    parser.add_argument("username")
    parser.add_argument("--confirm", action="store_true", help="perform the reset")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("this break-glass command must run as root")
    if not args.confirm:
        parser.error("add --confirm after verifying the administrator name")
    init_db()
    policy_disabled = False
    with write_session() as db:
        user = db.scalar(select(PanelUser).where(
            PanelUser.username == args.username, PanelUser.role == "admin",
        ))
        if user is None:
            parser.error("administrator not found")
        policy = db.get(SecurityPolicySettings, 1)
        protected = db.scalar(select(func.count()).select_from(PanelUser).join(
            TotpCredential, TotpCredential.panel_user_id == PanelUser.id,
        ).where(
            PanelUser.role == "admin", PanelUser.disabled.is_(False),
            TotpCredential.enabled.is_(True), PanelUser.id != user.id,
        )) or 0
        if policy and policy.require_admin_2fa and protected == 0:
            # A reset of the last protected administrator must restore a usable
            # login path.  The operator can re-enable the policy after this
            # administrator enrolls again.
            policy.require_admin_2fa = False
            policy.admin_2fa_enabled_at = None
            policy_disabled = True
        db.execute(delete(TotpRecoveryCode).where(TotpRecoveryCode.panel_user_id == user.id))
        db.execute(delete(TotpCredential).where(TotpCredential.panel_user_id == user.id))
        db.execute(delete(LoginChallenge).where(LoginChallenge.panel_user_id == user.id))
        for session in db.scalars(select(Session).where(Session.panel_user_id == user.id)).all():
            session.revoked = True
    suffix = "; administrator 2FA policy was disabled to prevent lockout" if policy_disabled else ""
    print(f"2FA reset for administrator {args.username}; existing sessions were revoked{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
