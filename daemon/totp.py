"""Phase 5 feature 10: TOTP two-factor authentication.

Setup is two steps (goal: "verify before enabling"): `setup_totp`
generates a fresh secret and stores it with `enabled=False`; only
`verify_totp` (a submitted, currently-valid 6-digit code) flips it to
enabled and hands back 8 recovery codes, once, the same one-time-reveal
pattern this project already uses for `ApiToken`'s raw token and
`WordPressJob`'s admin password.
"""
from __future__ import annotations

import hashlib
import secrets

import pyotp
from sqlalchemy import select

from shared.db import write_session
from shared.validation import ValidationError
from daemon import appcrypto

from shared.models import PanelUser, TotpCredential, TotpRecoveryCode

RECOVERY_CODE_COUNT = 8
ISSUER_NAME = "Boron"


def _decrypt_secret(value: str) -> str:
    # Backward-compatible one-time migration for installations that stored
    # base32 seeds before encryption was enabled.
    if value.startswith("gAAAA"):
        return appcrypto.decrypt_secret(value)
    return value


def _migrate_secret(row: TotpCredential) -> str:
    raw = _decrypt_secret(row.secret)
    if not row.secret.startswith("gAAAA"):
        row.secret = appcrypto.encrypt_secret(raw)
    return raw


def _generate_recovery_code() -> str:
    raw = secrets.token_hex(5).upper()
    return f"{raw[:5]}-{raw[5:]}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def get_status(params: dict) -> dict:
    panel_user_id = int(params["panel_user_id"])
    with write_session() as session:
        row = session.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == panel_user_id))
        return {"enabled": bool(row and row.enabled)}


def setup_totp(params: dict) -> dict:
    panel_user_id = int(params["panel_user_id"])
    with write_session() as session:
        user = session.get(PanelUser, panel_user_id)
        if user is None:
            raise ValidationError("panel user not found")
        secret = pyotp.random_base32()
        existing = session.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == panel_user_id))
        if existing is not None and existing.enabled:
            raise ValidationError("2FA is already enabled -- disable it first to generate a new secret")
        if existing is not None:
            existing.secret = appcrypto.encrypt_secret(secret)
        else:
            session.add(TotpCredential(panel_user_id=panel_user_id, secret=appcrypto.encrypt_secret(secret), enabled=False))
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=ISSUER_NAME)
        return {"secret": secret, "otpauth_uri": uri}


def verify_totp(params: dict) -> dict:
    panel_user_id = int(params["panel_user_id"])
    code = str(params["code"]).strip()
    with write_session() as session:
        row = session.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == panel_user_id))
        if row is None:
            raise ValidationError("no pending 2FA setup for this user -- call setup first")
        if not pyotp.TOTP(_migrate_secret(row)).verify(code, valid_window=1):
            raise ValidationError("invalid or expired code")
        row.enabled = True
        # Regenerate recovery codes every time 2FA is (re-)verified/enabled
        # -- any codes from a prior enable/disable cycle for this user are
        # no longer valid, avoiding stale-but-still-accepted codes.
        session.query(TotpRecoveryCode).filter(TotpRecoveryCode.panel_user_id == panel_user_id).delete()
        raw_codes = [_generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        for raw in raw_codes:
            session.add(TotpRecoveryCode(panel_user_id=panel_user_id, code_hash=_hash_code(raw)))
        return {"enabled": True, "recovery_codes": raw_codes}


def disable_totp(params: dict) -> dict:
    panel_user_id = int(params["panel_user_id"])
    with write_session() as session:
        session.query(TotpRecoveryCode).filter(TotpRecoveryCode.panel_user_id == panel_user_id).delete()
        row = session.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == panel_user_id))
        if row is not None:
            session.delete(row)
    return {"enabled": False}


def check_login_code(params: dict) -> dict:
    """Used by the login flow's second step -- accepts either a live TOTP
    code or a single-use recovery code."""
    panel_user_id = int(params["panel_user_id"])
    code = str(params["code"]).strip()
    with write_session() as session:
        cred = session.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == panel_user_id, TotpCredential.enabled == True))  # noqa: E712
        if cred is None:
            raise ValidationError("2FA is not enabled for this user")

        if pyotp.TOTP(_migrate_secret(cred)).verify(code, valid_window=1):
            return {"valid": True, "used_recovery_code": False}

        code_hash = _hash_code(code.upper())
        recovery_row = session.scalar(
            select(TotpRecoveryCode).where(
                TotpRecoveryCode.panel_user_id == panel_user_id,
                TotpRecoveryCode.code_hash == code_hash,
                TotpRecoveryCode.used_at.is_(None),
            )
        )
        if recovery_row is not None:
            from shared.models import utcnow

            recovery_row.used_at = utcnow()
            return {"valid": True, "used_recovery_code": True}

        return {"valid": False, "used_recovery_code": False}
