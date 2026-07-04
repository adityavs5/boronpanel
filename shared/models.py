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
    Float,
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
    # Phase 4 feature 2: hotlink protection. Off by default -- this is an
    # opt-in feature that can break legitimate embedding (the account's
    # own other domains, a CDN, etc.), so a fresh domain must not suddenly
    # start blocking image requests nobody asked to block.
    hotlink_protection_enabled: Mapped[bool] = mapped_column(default=False)
    hotlink_allowed_domains: Mapped[list] = mapped_column(JSON, default=list)
    # Phase 4 feature 3: IP/CIDR deny list, rendered into OLS's native
    # per-vhost accessControl block.
    ip_block_list: Mapped[list] = mapped_column(JSON, default=list)

    account: Mapped[Account] = relationship(back_populates="domains")


class DnsZone(Base):
    __tablename__ = "dns_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    zone: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DkimKey(Base):
    """Phase 3 feature 1: one DKIM signing keypair per mail domain,
    generated automatically the first time a mail domain is created
    (daemon/dkim.py). The private key itself lives on disk
    (/etc/forgehost/dkim/<domain>/<selector>.private, root-only) -- this
    row is bookkeeping only (which selector is active, so repeat calls
    reuse rather than silently rotate the key) plus whether the public key
    was actually published to a Forgehost-managed DNS zone or just
    generated for the operator to publish elsewhere."""

    __tablename__ = "dkim_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    selector: Mapped[str] = mapped_column(String(63), default="default")
    dns_published: Mapped[bool] = mapped_column(default=False)
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
    # Phase 4 feature 1: SpamAssassin. Domain-wide (not per-mailbox --
    # matches this project's existing domain-scoped email-feature API
    # shape from Phase 3 feature 4: catchall/forwarders/autoresponders are
    # all per-domain, not per-mailbox). threshold=None means "use the
    # server-wide admin default" (SpamGlobalSettings) -- no per-domain
    # SpamAssassin config file is written for that case at all (daemon/
    # spamfilter.py), so a later change to the global default is picked up
    # automatically rather than needing every "using the default" domain's
    # file rewritten.
    spam_filter_enabled: Mapped[bool] = mapped_column(default=True)
    spam_filter_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)


class MailUser(Base):
    __tablename__ = "mail_users_cache"
    __table_args__ = (UniqueConstraint("local_part", "domain", name="uq_mailbox"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mail_domain_id: Mapped[int] = mapped_column(ForeignKey("mail_domains_cache.id"))
    local_part: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(253))
    quota_mb: Mapped[int] = mapped_column(Integer, default=1024)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SpamGlobalSettings(Base):
    """Single-row (id=1) table: the admin-configurable, server-wide default
    SpamAssassin `required_score` (Phase 4 feature 1). A dedicated table
    rather than a generic key-value settings store -- no other server-wide
    admin setting exists yet in this project to justify that abstraction,
    and this is simpler to reason about (one row, one column that matters)
    until a second such setting actually shows up."""

    __tablename__ = "spam_global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    default_threshold: Mapped[float] = mapped_column(Float, default=5.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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


class LoginAttempt(Base):
    """Security audit finding F2: brute-force throttling for /login, one
    row per username (created lazily on first failure). A new table
    rather than new columns on PanelUser -- Base.metadata.create_all()
    only creates missing tables, never adds columns to an existing one
    (a recurring gap in this project's own migration story), so this
    avoids needing a manual ALTER TABLE against the live DB."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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


class Redirect(Base):
    """Phase 3 feature 7: a per-domain 301/302 path redirect, rendered
    into that domain's own vhost as an OLS/mod_rewrite-compatible
    RewriteRule (daemon/ols.py). Keyed by domain NAME (string), not a
    Domain.id foreign key -- matches this project's existing convention
    for domain-scoped features (DnsZone.zone, MailDomain.domain) rather
    than introducing the first FK to `domains.id` in the schema; cleanup
    on domain removal is explicit application code
    (handlers_domain.remove_domain), the same manual-cascade pattern
    already used for MailDomain/MailUser."""

    __tablename__ = "redirects"
    __table_args__ = (UniqueConstraint("domain", "path", name="uq_redirect_domain_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    path: Mapped[str] = mapped_column(String(512))
    target_url: Mapped[str] = mapped_column(String(2048))
    status_code: Mapped[int] = mapped_column(Integer, default=301)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PhpIniOverride(Base):
    """Phase 3 feature 6: per-account php.ini overrides. A NEW table
    (not new columns on Account) deliberately -- this project uses
    Base.metadata.create_all(), which only creates new tables, never
    ALTERs existing ones (Phase 2 feature 6 hit this the hard way over
    cgroup columns added directly to Account). A new table needs no
    manual migration step on an existing install. Rendered into each of
    the account's own domain-vhosts' phpIniOverride block
    (daemon/ols.py) -- OLS's own native per-context PHP ini mechanism,
    not a hand-rolled separate php.ini file, and not system-wide: no
    other account's vhost references this row."""

    __tablename__ = "php_ini_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    memory_limit: Mapped[str] = mapped_column(String(16), default="256M")
    upload_max_filesize: Mapped[str] = mapped_column(String(16), default="64M")
    post_max_size: Mapped[str] = mapped_column(String(16), default="64M")
    max_execution_time: Mapped[int] = mapped_column(Integer, default=30)
    display_errors: Mapped[bool] = mapped_column(default=False)
    error_reporting: Mapped[str] = mapped_column(String(128), default="E_ALL & ~E_DEPRECATED & ~E_STRICT")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FtpAccount(Base):
    """Phase 3 feature 5: an FTP sub-account, scoped to a path within its
    hosting account's home dir. A Pure-FTPd *virtual* user (PureDB
    backend), not a real Linux account -- so it can be chrooted to an
    arbitrary subdirectory of the account's home rather than the whole
    home dir, which real Linux/unix-auth FTP users can't do without a
    separate per-user chroot mechanism this project doesn't otherwise
    need. The password itself is never stored here (PureDB's own
    pureftpd.pdb holds the hash) -- same "passwords never stored in the
    panel DB" rule as every other credential in this project."""

    __tablename__ = "ftp_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    ftp_login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PmaToken(Base):
    """Phase 3 feature 3: bookkeeping for a phpMyAdmin single-signon
    token. The token itself is never stored (only its SHA-256 hash, same
    pattern as ApiToken) -- the ephemeral MariaDB credentials it grants
    access to live in a small per-token JSON file under
    settings.pma_token_dir (NOT under /var/lib/forgehost, which is
    locked to root:forgehost-api -- the phpMyAdmin signon script runs as
    www-data and needs to read+delete that file itself; see
    CHECKPOINT-phase3-3.md). This row exists so a periodic cleanup script
    can drop the ephemeral MariaDB user + stale file even if a token is
    generated but never redeemed."""

    __tablename__ = "pma_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    db_name: Mapped[str] = mapped_column(String(64))
    ephemeral_db_user: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class BackupDestination(Base):
    """Phase 2 feature 7: where backup artifacts are stored. Credentials
    for rclone-backed destinations live in rclone's own config (managed
    via `rclone config create`/`daemon/rclone.py`), never duplicated here
    -- this row is just a pointer (remote name + path prefix), the same
    "secrets live in one restricted-permission place, not the app DB"
    pattern already used for MariaDB/mail/SSL credentials elsewhere in
    this project."""

    __tablename__ = "backup_destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # local | rclone
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rclone_remote: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rclone_path_prefix: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupSchedule(Base):
    """One row per account, or one row with account_id=NULL for the
    server-wide default applied to any account without its own
    override (daemon/backup.py's scheduler resolves account -> its own
    schedule if present, else the account_id=NULL row)."""

    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, unique=True)
    frequency: Mapped[str] = mapped_column(String(16), default="daily")  # daily | weekly | monthly
    retention_count: Mapped[int] = mapped_column(Integer, default=7)
    destination_id: Mapped[int] = mapped_column(ForeignKey("backup_destinations.id"))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BackupJob(Base):
    """A single backup point. `kind`="full" backs up files+DBs+mail+DNS
    zone+config (a manifest.json describing exactly what's inside, so the
    UI's backup browser and restore logic never have to guess); "file"/
    "database"/"mailbox" back up exactly one item (item_ref identifies
    which)."""

    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # full | file | database | mailbox
    item_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled
    destination_id: Mapped[int] = mapped_column(ForeignKey("backup_destinations.id"))
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WordPressInstall(Base):
    """Phase 3 feature 2: bookkeeping for a completed one-click WordPress
    install -- one row per domain (a domain can only ever host one WP
    install through this feature; reinstalling requires removing the
    row/files first, same "don't silently clobber" posture as the install
    step itself refusing a non-empty docroot). No password field here --
    the admin password is returned once, at install-completion time
    (WordPressJob.admin_password, cleared after first read), never
    persisted long-term, matching this project's "passwords never stored"
    rule applied everywhere else (DB/mail/FTP credentials)."""

    __tablename__ = "wordpress_installs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    db_name: Mapped[str] = mapped_column(String(64))
    db_user: Mapped[str] = mapped_column(String(64))
    wp_version: Mapped[str] = mapped_column(String(32))
    admin_user: Mapped[str] = mapped_column(String(64))
    installed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WordPressJob(Base):
    """Async install job (goal requirement: "shows install progress
    async"). admin_password is stored here ONLY transiently -- it has to
    survive from the background worker thread until the polling UI/API
    call that first observes status=="completed" reads it, since (unlike
    account.create's synchronous response) there's no single request/
    response round trip to hand it back on. The first successful read
    clears it (see get_job) so a second poll -- or a row inspected later
    for any other reason -- never re-exposes it. This is a deliberate,
    minimal-exposure tradeoff forced by the async requirement, not an
    oversight; documented in CHECKPOINT-phase3-2.md."""

    __tablename__ = "wordpress_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    admin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    admin_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admin_password: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RestoreJob(Base):
    """Restores FROM a BackupJob's artifact -- never requires the account
    to be re-terminated/absent first (goal's explicit requirement):
    restoring a still-active account overwrites its current files/DBs/
    mail in place; restoring a terminated account's full backup
    recreates it (Linux user, vhost, DBs, mail, DNS zone) from the
    manifest."""

    __tablename__ = "restore_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_job_id: Mapped[int] = mapped_column(ForeignKey("backup_jobs.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # full | file | database | mailbox
    item_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileAuthDir(Base):
    """Per-directory password protection (Phase 4 feature 4). Deliberately
    holds NO credentials -- users/passwords live only in the directory's own
    `.htpasswd` file inside the account's home (daemon/fileauth.py), per the
    goal's explicit "never in panel DB" requirement. This row is purely
    metadata: which directory is protected, and the OLS realm name that
    references its .htpasswd file (needed at vhost-render time, daemon/ols.py).
    `path` is relative to the account's home dir, matching
    daemon/filemanager.py's own path convention (jailed the same way)."""

    __tablename__ = "file_auth_dirs"
    __table_args__ = (UniqueConstraint("account_id", "path", name="uq_file_auth_dir"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    realm_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GitRepo(Base):
    """Per-account bare git repo + push-to-deploy (Phase 4 feature 5).
    `deploy_target` is relative to the account's home dir (None until
    configured -- a repo can exist with no deploy target set, in which case
    pushes are accepted but nothing is deployed anywhere)."""

    __tablename__ = "git_repos"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_git_repo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    deploy_target: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppInstall(Base):
    """Softaculous-equivalent app-installer metadata (Phase 4 feature 8).
    Same "record metadata, never credentials" posture as WordPressInstall
    (Phase 3 feature 2) -- admin_user/db_name are recorded (needed for the
    installed-apps list and "update available" checks), the admin
    password is not."""

    __tablename__ = "app_installs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    app_id: Mapped[str] = mapped_column(String(32))  # joomla | drupal | prestashop | laravel | static
    version: Mapped[str] = mapped_column(String(32))
    db_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    db_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admin_user: Mapped[str | None] = mapped_column(String(150), nullable=True)
    installed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WafSettings(Base):
    """Phase 5 feature 7: ModSecurity/WAF. Single-row (id=1) global on/off
    switch -- confirmed live that OpenLiteSpeed loads ModSecurity's engine
    and rule files exactly once, server-wide (no per-vhost module load),
    so there is genuinely only one "is the WAF on" toggle, same
    single-row-settings shape as SpamGlobalSettings."""

    __tablename__ = "waf_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WafDomainOverride(Base):
    """A domain that opted OUT of the (server-wide) WAF engine, rendered as
    a `ctl:ruleEngine=Off` SecRule scoped to that domain's Host header
    (daemon/ols.py's `_waf_template_context`) -- the actual mechanism
    "per-domain enable/disable" uses, since the engine itself cannot be
    loaded per-vhost on OpenLiteSpeed (confirmed live, see
    docs/CHECKPOINT-phase5-7-waf.md)."""

    __tablename__ = "waf_domain_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    disabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WafCustomRule(Base):
    """A per-domain custom WAF rule -- rendered as a Host-header-scoped
    SecRule chain (daemon/ols.py) so it only ever applies to requests for
    this one domain, even though the underlying engine/rule file load is
    global. `target`/`pattern` (not a raw free-text rule body) deliberately
    constrains what an admin can express here to ModSecurity's own
    variable+regex operator shape -- enough to block "this header/arg
    matches this pattern," without accepting arbitrary rule-language text
    that would be far harder to validate before it reaches a live,
    server-wide config file."""

    __tablename__ = "waf_custom_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    target: Mapped[str] = mapped_column(String(64))
    pattern: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IpWhitelistEntry(Base):
    """Phase 5 feature 9: panel-login IP/CIDR whitelist. Empty table =
    no restriction (goal's explicit default) -- enforced by a
    request-time check in `api/main.py`'s middleware, not here; this
    model is pure storage. `add_entry` (daemon/ipwhitelist.py) always
    also upserts the requesting admin's own current IP alongside
    whatever value they asked to add, structurally guaranteeing "always
    include current admin IP to prevent lockout" regardless of which
    entry number is being added or how many already exist."""

    __tablename__ = "ip_whitelist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TotpCredential(Base):
    """Phase 5 feature 10: TOTP 2FA. `secret` is stored in plain base32,
    not hashed -- unlike a password, a TOTP secret must be *used*
    (HMAC'd against the current time step) on every verification, not
    just compared, so it can't be one-way-hashed the way
    PanelUser.password_hash is. This project has no generalized
    application-level encryption-at-rest layer for DB row secrets (the
    few genuinely irreversible secrets it holds -- MariaDB admin creds,
    the PowerDNS API key, the session-signing key -- all live in
    root-only files under /etc/forgehost/, never in this SQLite DB); a
    dedicated KMS/envelope-encryption layer for this one field was
    judged out of scope for this feature, so the DB file's own existing
    permission boundary (0640 root:forgehost-api, ARCHITECTURE.md SS4)
    is the actual protection here -- the same real, documented tradeoff
    this project already accepts for the PanelUser table it sits
    alongside. `enabled=False` until a submitted code proves the admin
    actually scanned the QR code and can generate valid codes
    (goal: "verify before enabling")."""

    __tablename__ = "totp_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id"), unique=True, index=True)
    secret: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TotpRecoveryCode(Base):
    """8 single-use recovery codes generated once, at the moment 2FA is
    successfully enabled (goal's explicit count) -- stored hashed
    (SHA-256, same "never store the raw secret" rule as ApiToken/PmaToken),
    each usable exactly once (`used_at` set on redemption, never deleted
    so a reused code can still be rejected rather than silently
    accepted if the row were ever removed)."""

    __tablename__ = "totp_recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HealthSnapshot(Base):
    """Phase 5 feature 1: server health dashboard. One row per ~60s tick
    (scripts/health_snapshot.py, cron), independent of any hosting account
    -- this is host-wide infrastructure telemetry, not a per-tenant
    resource. Network counters are stored as cumulative totals (matches
    /proc/net/dev's own semantics, which is what psutil reads) -- the
    reader computes per-interval in/out from the delta between
    consecutive rows, so one missed tick or a counter reset just yields
    one odd interval rather than corrupting the whole series."""

    __tablename__ = "health_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taken_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    load1: Mapped[float] = mapped_column(Float, default=0.0)
    load5: Mapped[float] = mapped_column(Float, default=0.0)
    load15: Mapped[float] = mapped_column(Float, default=0.0)
    mem_total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mem_used_bytes: Mapped[int] = mapped_column(Integer, default=0)
    disks: Mapped[list] = mapped_column(JSON, default=list)  # [{mount,device,fstype,total,used,free,pct}]
    net_rx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    net_tx_bytes: Mapped[int] = mapped_column(Integer, default=0)


class AppInstallJob(Base):
    """Async install job -- same one-time-reveal pattern as WordPressJob
    (Phase 3 feature 2): admin_password is stored only transiently, until
    the first successful poll that observes status=="completed", then
    cleared so it can never leak from this row again."""

    __tablename__ = "app_install_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    app_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    admin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    admin_user: Mapped[str | None] = mapped_column(String(150), nullable=True)
    admin_password: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
