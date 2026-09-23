"""Legacy active cookies survive startup migration without DB replay material."""
import datetime as dt

from cryptography.fernet import Fernet
from sqlalchemy import select

from api.security import _identity_from_session_cookie, sign_session_id
from daemon import appcrypto, handlers_auth
from shared import db as control_db
from shared.db import write_session
from shared.models import Account, ImpersonationSession, Session, utcnow
from shared.session_ids import session_digest


def test_startup_rehashes_legacy_sessions_once(isolated_db, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(appcrypto, "get_key", lambda: key)
    user = handlers_auth.create_panel_user({
        "username": "admin", "password": "SuperSecret123!", "role": "admin",
    })
    raw_admin = "legacy-admin-session"
    raw_impersonation = "legacy-impersonation-session"
    with write_session() as db:
        account = Account(username="example")
        db.add(account)
        db.flush()
        expiration = utcnow() + dt.timedelta(hours=1)
        db.add_all([
            Session(session_id=raw_admin, panel_user_id=user["id"], expires_at=expiration),
            Session(session_id=raw_impersonation, panel_user_id=user["id"], expires_at=expiration),
            ImpersonationSession(session_id=raw_impersonation, account_id=account.id,
                                 admin_panel_user_id=user["id"], admin_username="admin",
                                 admin_session_id=raw_admin),
        ])

    control_db._migrate_session_identifiers(control_db._write_engine)
    control_db._migrate_session_identifiers(control_db._write_engine)

    with write_session() as db:
        sessions = db.scalars(select(Session)).all()
        assert {row.session_id for row in sessions} == {
            session_digest(raw_admin), session_digest(raw_impersonation),
        }
        imp = db.scalar(select(ImpersonationSession))
        assert imp.session_id == session_digest(raw_impersonation)
        assert imp.admin_session_id is None
        assert appcrypto.decrypt_secret(imp.admin_session_enc) == raw_admin
    assert _identity_from_session_cookie(sign_session_id(raw_admin)).role == "admin"
    impersonated = _identity_from_session_cookie(sign_session_id(raw_impersonation))
    assert impersonated.role == "customer"
    assert impersonated.account_id == account.id
