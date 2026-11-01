"""License business rules, shared by the HTTP API and the admin CLI.

Client calls (activate/check) return a signed document for any real key, even
when the answer is negative (suspended, expired, wrong IP), so a panel can act
on a refusal it can verify. Unknown keys get a plain error and no document.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from boron_license import keys
from boron_license.models import (
    AdminToken,
    Event,
    Installation,
    License,
    Tier,
    aware,
    utcnow,
)
from boron_license.signing import Signer

STATUSES = ("active", "suspended", "cancelled")
CHANNELS = ("stable", "beta")
DEFAULT_TIERS = (("solo", 1), ("admin", 5), ("pro", 30), ("unlimited", None))
INSTALL_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")
DOCUMENT_FORMAT = 1


class LicenseError(Exception):
    def __init__(self, code: str, http_status: int = 400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def normalize_ip(raw: str) -> str:
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError as exc:
        raise LicenseError("invalid_ip") from exc
    # An IPv4 client seen through an IPv6 socket is still the IPv4 address.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.compressed


def record(session: Session, *, actor: str, action: str, license_id: int | None = None,
           ip: str | None = None, **detail) -> None:
    session.add(Event(actor=actor[:80], action=action, license_id=license_id, ip=ip, detail=detail))


# --- Tiers -------------------------------------------------------------------

def seed_tiers(session: Session) -> list[str]:
    created = []
    for name, max_accounts in DEFAULT_TIERS:
        if session.scalar(select(Tier).where(Tier.name == name)) is None:
            session.add(Tier(name=name, max_accounts=max_accounts))
            created.append(name)
    return created


def get_tier(session: Session, name: str) -> Tier:
    tier = session.scalar(select(Tier).where(Tier.name == name.strip().lower()))
    if tier is None:
        raise LicenseError("unknown_tier", 404)
    return tier


def upsert_tier(session: Session, name: str, max_accounts: int | None) -> Tier:
    name = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
        raise LicenseError("invalid_tier_name")
    if max_accounts is not None and max_accounts < 1:
        raise LicenseError("invalid_max_accounts")
    tier = session.scalar(select(Tier).where(Tier.name == name))
    if tier is None:
        tier = Tier(name=name, max_accounts=max_accounts)
        session.add(tier)
    else:
        tier.max_accounts = max_accounts
    session.flush()
    return tier


# --- Licenses ----------------------------------------------------------------

def issue(session: Session, *, tier: str, customer_name: str, customer_email: str, actor: str,
          expires_at: datetime | None = None, bound_ip: str | None = None,
          channel: str = "stable", notes: str = "") -> tuple[License, str]:
    """Create a license. The plain key is returned once and never stored."""
    customer_name = customer_name.strip()
    customer_email = customer_email.strip().lower()
    if not customer_name or len(customer_name) > 200:
        raise LicenseError("invalid_customer_name")
    if not EMAIL.fullmatch(customer_email):
        raise LicenseError("invalid_customer_email")
    if channel not in CHANNELS:
        raise LicenseError("invalid_channel")
    tier_row = get_tier(session, tier)
    key = keys.generate_key()
    license_ = License(key_hash=keys.hash_secret(key), key_hint=keys.key_hint(key),
                       customer_name=customer_name, customer_email=customer_email, tier=tier_row,
                       bound_ip=normalize_ip(bound_ip) if bound_ip else None, channel=channel,
                       expires_at=expires_at, notes=notes)
    session.add(license_)
    session.flush()
    record(session, actor=actor, action="issue", license_id=license_.id, tier=tier_row.name,
           expires_at=expires_at.isoformat() if expires_at else None, bound_ip=license_.bound_ip)
    return license_, key


def get_license(session: Session, license_id: int) -> License:
    license_ = session.get(License, license_id)
    if license_ is None:
        raise LicenseError("license_not_found", 404)
    return license_


def search(session: Session, *, query: str = "", status: str | None = None, limit: int = 100) -> list[License]:
    statement = select(License).order_by(License.id.desc()).limit(max(1, min(limit, 500)))
    if status:
        statement = statement.where(License.status == status)
    query = query.strip()
    if query:
        like = f"%{query.lower()}%"
        conditions = [License.customer_email.like(like), License.customer_name.ilike(like),
                      License.bound_ip == query, License.key_hint == query.upper()[-4:]]
        normalized = keys.normalize_key(query)
        if normalized:
            conditions.append(License.key_hash == keys.hash_secret(normalized))
        if query.isdigit():
            conditions.append(License.id == int(query))
        statement = statement.where(or_(*conditions))
    return list(session.scalars(statement))


def set_status(session: Session, license_id: int, status: str, *, actor: str, reason: str = "") -> License:
    if status not in STATUSES:
        raise LicenseError("invalid_status")
    license_ = get_license(session, license_id)
    previous = license_.status
    license_.status = status
    record(session, actor=actor, action=f"status:{status}", license_id=license_id,
           previous=previous, reason=reason[:500])
    return license_


def rebind(session: Session, license_id: int, ip: str | None, *, actor: str) -> License:
    """Move a license to a new server IP; None lets the next activation bind it."""
    license_ = get_license(session, license_id)
    previous = license_.bound_ip
    license_.bound_ip = normalize_ip(ip) if ip else None
    record(session, actor=actor, action="rebind", license_id=license_id,
           previous=previous, new=license_.bound_ip)
    return license_


def update(session: Session, license_id: int, *, actor: str, tier: str | None = None,
           expires_at: datetime | None = None, clear_expiry: bool = False, channel: str | None = None,
           customer_name: str | None = None, customer_email: str | None = None,
           notes: str | None = None) -> License:
    license_ = get_license(session, license_id)
    changes: dict[str, object] = {}
    if tier is not None:
        license_.tier = get_tier(session, tier)
        changes["tier"] = license_.tier.name
    if clear_expiry:
        license_.expires_at = None
        changes["expires_at"] = None
    elif expires_at is not None:
        license_.expires_at = expires_at
        changes["expires_at"] = expires_at.isoformat()
    if channel is not None:
        if channel not in CHANNELS:
            raise LicenseError("invalid_channel")
        license_.channel = channel
        changes["channel"] = channel
    if customer_name is not None:
        if not customer_name.strip() or len(customer_name) > 200:
            raise LicenseError("invalid_customer_name")
        license_.customer_name = customer_name.strip()
        changes["customer_name"] = license_.customer_name
    if customer_email is not None:
        if not EMAIL.fullmatch(customer_email.strip().lower()):
            raise LicenseError("invalid_customer_email")
        license_.customer_email = customer_email.strip().lower()
        changes["customer_email"] = license_.customer_email
    if notes is not None:
        license_.notes = notes
        changes["notes"] = True
    record(session, actor=actor, action="update", license_id=license_id, **changes)
    return license_


def describe(license_: License) -> dict:
    """Admin view of a license. Never includes the key."""
    expires_at = aware(license_.expires_at)
    return {
        "id": license_.id, "key_hint": license_.key_hint, "customer_name": license_.customer_name,
        "customer_email": license_.customer_email, "tier": license_.tier.name,
        "max_accounts": license_.tier.max_accounts, "status": license_.status,
        "effective_status": effective_status(license_, utcnow()), "bound_ip": license_.bound_ip,
        "channel": license_.channel, "expires_at": expires_at.isoformat() if expires_at else None,
        "notes": license_.notes, "created_at": aware(license_.created_at).isoformat(),
        "last_check_at": aware(license_.last_check_at).isoformat() if license_.last_check_at else None,
    }


def installations(session: Session, license_id: int) -> list[dict]:
    rows = session.scalars(select(Installation).where(Installation.license_id == license_id)
                           .order_by(Installation.last_seen.desc()))
    return [{"install_id": row.install_id, "ip": row.ip, "hostname": row.hostname, "version": row.version,
             "account_count": row.account_count, "first_seen": aware(row.first_seen).isoformat(),
             "last_seen": aware(row.last_seen).isoformat()} for row in rows]


def effective_status(license_: License, now: datetime) -> str:
    if license_.status != "active":
        return license_.status
    expires_at = aware(license_.expires_at)
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"


# --- Client calls ------------------------------------------------------------

def validate(session: Session, signer: Signer, *, raw_key: str, install_id: str, ip: str,
             hostname: str = "", version: str = "", account_count: int | None = None,
             bind: bool, grace_days: int, now: datetime | None = None) -> dict:
    """Handle an activation (``bind=True``) or a periodic check from a panel."""
    now = now or utcnow()
    key = keys.normalize_key(raw_key)
    if key is None:
        raise LicenseError("invalid_license", 403)
    if not INSTALL_ID.fullmatch(install_id or ""):
        raise LicenseError("invalid_install_id")
    ip = normalize_ip(ip)
    license_ = session.scalar(select(License).where(License.key_hash == keys.hash_secret(key)))
    if license_ is None:
        record(session, actor="client", action="unknown_key", ip=ip, hint=keys.key_hint(key))
        raise LicenseError("invalid_license", 403)

    status = effective_status(license_, now)
    if status == "active":
        if license_.bound_ip is None:
            if bind:
                license_.bound_ip = ip
                record(session, actor="client", action="bind", license_id=license_.id, ip=ip,
                       install_id=install_id)
            else:
                status = "not_activated"
        elif license_.bound_ip != ip:
            status = "ip_mismatch"

    license_.last_check_at = now
    if status == "active":
        _touch_installation(session, license_, install_id=install_id, ip=ip, hostname=hostname,
                            version=version, account_count=account_count, now=now)
    record(session, actor="client", action="activate" if bind else "check", license_id=license_.id,
           ip=ip, result=status, install_id=install_id, version=version[:32])
    return signer.sign(_document(license_, status=status, install_id=install_id, ip=ip, now=now,
                                 grace_days=grace_days))


def _touch_installation(session: Session, license_: License, *, install_id: str, ip: str,
                        hostname: str, version: str, account_count: int | None, now: datetime) -> None:
    row = session.scalar(select(Installation).where(Installation.license_id == license_.id,
                                                    Installation.install_id == install_id))
    if row is None:
        row = Installation(license_id=license_.id, install_id=install_id, first_seen=now, ip=ip)
        session.add(row)
    row.ip = ip
    row.hostname = hostname[:253]
    row.version = version[:32]
    row.account_count = account_count
    row.last_seen = now


def _document(license_: License, *, status: str, install_id: str, ip: str, now: datetime,
              grace_days: int) -> dict:
    expires_at = aware(license_.expires_at)
    # A panel may run on this document until valid_until without reaching us,
    # but never past the paid-up date.
    valid_until = now + timedelta(days=grace_days)
    if expires_at is not None:
        valid_until = min(valid_until, expires_at)
    payload = {
        "format": DOCUMENT_FORMAT, "license_id": license_.id, "key_hint": license_.key_hint,
        "status": status, "tier": license_.tier.name, "max_accounts": license_.tier.max_accounts,
        "ip": ip, "bound_ip": license_.bound_ip, "install_id": install_id, "channel": license_.channel,
        "issued_at": _iso(now), "expires_at": _iso(expires_at), "updates_until": _iso(expires_at),
    }
    payload["valid_until"] = _iso(valid_until) if status == "active" else _iso(now)
    return payload


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


# --- Admin tokens ------------------------------------------------------------

def create_admin_token(session: Session, name: str, *, actor: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        raise LicenseError("invalid_token_name")
    if session.scalar(select(AdminToken).where(AdminToken.name == name)) is not None:
        raise LicenseError("token_name_taken", 409)
    token = keys.generate_admin_token()
    session.add(AdminToken(name=name, token_hash=keys.hash_secret(token)))
    record(session, actor=actor, action="token:create", name=name)
    return token


def revoke_admin_token(session: Session, name: str, *, actor: str) -> None:
    row = session.scalar(select(AdminToken).where(AdminToken.name == name, AdminToken.revoked_at.is_(None)))
    if row is None:
        raise LicenseError("token_not_found", 404)
    row.revoked_at = utcnow()
    record(session, actor=actor, action="token:revoke", name=name)


def authenticate_admin(session: Session, token: str) -> AdminToken | None:
    if not token.startswith(keys.ADMIN_TOKEN_PREFIX) or len(token) > 100:
        return None
    row = session.scalar(select(AdminToken).where(AdminToken.token_hash == keys.hash_secret(token),
                                                  AdminToken.revoked_at.is_(None)))
    if row is not None:
        row.last_used_at = utcnow()
    return row
