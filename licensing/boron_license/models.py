"""Database schema (SQLAlchemy 2.0). PostgreSQL in production, SQLite in tests."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored value is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class Tier(Base):
    __tablename__ = "tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    # None means unlimited hosting accounts.
    max_accounts: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hint: Mapped[str] = mapped_column(String(4))
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_email: Mapped[str] = mapped_column(String(254), index=True)
    tier_id: Mapped[int] = mapped_column(ForeignKey("tiers.id"))
    tier: Mapped[Tier] = relationship(lazy="joined")
    # active | suspended | cancelled. Expiry is derived from expires_at.
    status: Mapped[str] = mapped_column(String(16), default="active")
    bound_ip: Mapped[str | None] = mapped_column(String(45), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="stable")
    # None means the license does not expire (internal and lifetime licenses).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Installation(Base):
    __tablename__ = "installations"
    __table_args__ = (UniqueConstraint("license_id", "install_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id"), index=True)
    install_id: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(45))
    hostname: Mapped[str] = mapped_column(String(253), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    account_count: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Append-only audit trail of every administrative and client action."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    license_id: Mapped[int | None] = mapped_column(ForeignKey("licenses.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(45))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AdminToken(Base):
    __tablename__ = "admin_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
