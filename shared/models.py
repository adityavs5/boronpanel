"""SQLAlchemy ORM models for Forgehost's control-plane SQLite database.

forgehostd is the only writer (see ARCHITECTURE.md SS4); forgehost-api opens
the same file read-only for fast list/get queries and forwards every mutation
to forgehostd over the RPC socket.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    # active | suspended | terminating | terminated | error
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    php_version: Mapped[str] = mapped_column(String(8), default="8.3")
    quota_soft_mb: Mapped[int] = mapped_column(Integer, default=5120)
    quota_hard_mb: Mapped[int] = mapped_column(Integer, default=6144)
    # Phase 2 feature 6: cgroups v2 resource limits, one systemd slice per
    # account (daemon/cgroups.py). Defaults match the goal's stated
    # defaults exactly: CPU 25% of one core, 512MB RAM (no swap), 50MB/s
    # IO, 50 pids.
    cpu_pct: Mapped[int] = mapped_column(Integer, default=25)
    mem_mb: Mapped[int] = mapped_column(Integer, default=512)
    io_mb: Mapped[int] = mapped_column(Integer, default=50)
    pids_max: Mapped[int] = mapped_column(Integer, default=50)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    suspended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    domains: Mapped[list["Domain"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Domain(Base):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("domain", name="uq_domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    domain: Mapped[str] = mapped_column(String(253), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="primary")  # primary | addon | subdomain
    docroot: Mapped[str] = mapped_column(String(512))
    ssl_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|active|error
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship(back_populates="domains")


class DnsZone(Base):
    __tablename__ = "dns_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    zone: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatabaseGrant(Base):
    """A hosted-account MariaDB database + the users granted to it."""

    __tablename__ = "database_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    db_name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    db_user: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailDomain(Base):
    __tablename__ = "mail_domains_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailUser(Base):
    __tablename__ = "mail_users_cache"
    __table_args__ = (UniqueConstraint("local_part", "domain", name="uq_mailbox"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mail_domain_id: Mapped[int] = mapped_column(ForeignKey("mail_domains_cache.id"))
    local_part: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(253))
    quota_mb: Mapped[int] = mapped_column(Integer, default=1024)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PanelUser(Base):
    """Human login identity -- admin or customer."""

    __tablename__ = "panel_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # admin | customer
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    disabled: Mapped[bool] = mapped_column(default=False)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    panel_user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))  # admin | customer
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    op: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[str] = mapped_column(String(16))  # ok | failed
    detail: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageSnapshot(Base):
    """Phase 2 feature 5: point-in-time gauge metrics (disk/inodes/process
    count), refreshed at most every 15 min (daemon/usage.py) -- never
    overwritten, always appended, so the history itself IS the trend data
    for the UI's usage-over-time display."""

    __tablename__ = "usage_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    taken_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    disk_home_bytes: Mapped[int] = mapped_column(Integer, default=0)
    disk_mail_bytes: Mapped[int] = mapped_column(Integer, default=0)
    disk_db_bytes: Mapped[int] = mapped_column(Integer, default=0)
    inode_count: Mapped[int] = mapped_column(Integer, default=0)
    process_count: Mapped[int] = mapped_column(Integer, default=0)


class BandwidthDaily(Base):
    """Phase 2 feature 5: one row per (account, calendar day), aggregated
    from OLS access logs. Upserted, not appended -- each refresh
    recomputes and replaces the day's total for as long as that day's
    traffic is still fully present in on-disk logs; once a day's log
    content rotates away, its last-computed row is simply never touched
    again, becoming the durable historical value."""

    __tablename__ = "bandwidth_daily"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_bandwidth_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    bytes_served: Mapped[int] = mapped_column(Integer, default=0)
