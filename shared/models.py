"""SQLAlchemy ORM models for Boron's control-plane SQLite database.

borond is the only writer (see ARCHITECTURE.md SS4); boron-api opens
the same file read-only for fast list/get queries and forwards every mutation
to borond over the RPC socket.
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
    # Run A feature 1 (plan templates): which named preset (if any) this
    # account currently sits on -- NULL means "custom" (never had a plan
    # applied, or its limits were hand-edited since). Purely informational
    # (the UI's "current plan" label); the actual enforced state always
    # lives on the columns a plan writes to (cpu_pct/mem_mb/... above,
    # AccountResourceLimits, RedisInstance), never read back from here.
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)

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
    # Phase 7a feature 5: wildcard SSL. True only when the live cert at
    # letsencrypt_cert_paths(domain) actually covers "*.<domain>" (issued
    # via DNS-01 -d '<domain>' -d '*.<domain>') -- a separate flag from
    # ssl_status rather than overloading its existing none|pending|active|
    # error vocabulary with a fifth value every other reader of ssl_status
    # (dashboard, vhost render) would need to special-case.
    ssl_is_wildcard: Mapped[bool] = mapped_column(default=False)
    # Phase 7a feature 6: per-domain PHP version override. NULL (the
    # default for every pre-existing and newly-created domain) means
    # "inherit the account's own Account.php_version", exactly matching
    # how PhpIniOverride's absence already means "use server defaults" --
    # so a domain that never sets this renders identically to before this
    # feature existed.
    php_version: Mapped[str | None] = mapped_column(String(8), nullable=True)
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


class CloudflareZone(Base):
    """docs/PLAN-cloudflare.md SS1.1: a DNS zone served by Cloudflare
    instead of local PowerDNS. Absence of a row = local (the project-wide
    "absence means default" convention, and create_all friendly). While
    status='pending' (registrar NS not yet flipped to the assigned pair),
    PowerDNS remains authoritative and all panel writes go to PowerDNS
    only; the pending->active transition does a full one-shot resync
    PowerDNS->Cloudflare, then routing flips (daemon/dnsprovider.py). The
    PowerDNS zone is deliberately kept (stale) as the revert target."""

    __tablename__ = "cloudflare_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    zone: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    cf_zone_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active
    name_servers: Mapped[list] = mapped_column(JSON, default=list)  # CF-assigned NS pair
    # Phase 2+3: which CloudflareAccount (pool row) serves this zone. NULL for
    # zones enabled under the legacy single-token config (before the pool
    # existed) -- those fall back to settings.cloudflare_api_token. Added as a
    # nullable column on this pre-existing table via shared/db.py's additive
    # migration (create_all never ALTERs -- plan SS0), safe because the table
    # is empty until a zone is enabled.
    cf_account_id: Mapped[int | None] = mapped_column(ForeignKey("cloudflare_accounts.id"), nullable=True, index=True)
    # Phase 2+3 feature 8: last successful cache purge, shown in the admin
    # zone overview. Nullable additive column (see shared/db.py).
    last_purge_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CloudflareAccount(Base):
    """Phase 2+3: one row per Cloudflare account in the pool (goal feature 1).

    Replaces the single global token with a pool so a provider can spread
    customer zones across several Cloudflare accounts (free plan caps a
    zone-per-account; `max_zones` is the soft cap this pool round-robins
    under). `api_token` is stored Fernet-encrypted at rest (daemon/appcrypto.py,
    same mechanism as NodeApp/PythonApp env vars) -- never in plaintext in
    the DB, never returned to the UI. `account_id` is Cloudflare's own
    account identifier (the value that used to live in
    boron.toml:cloudflare_account_id), needed on the POST /zones payload.
    `zone_count` is a denormalized cache of active+pending zones assigned to
    this account, kept in step by daemon/cloudflare_accounts.py on every
    enable/disable and recomputable from the CloudflareZone rows."""

    __tablename__ = "cloudflare_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    api_token_enc: Mapped[str] = mapped_column(String(512))  # Fernet token, never plaintext
    account_id: Mapped[str] = mapped_column(String(64))  # Cloudflare's account id
    zone_count: Mapped[int] = mapped_column(Integer, default=0)  # denormalized cache
    max_zones: Mapped[int] = mapped_column(Integer, default=800)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CloudflareSettings(Base):
    """Single-row (id=1) table for the runtime-toggleable Cloudflare admin
    settings (goal features 6 + 9), following the SpamGlobalSettings
    single-row convention. These are distinct from boron.toml knobs
    (operator-edited, static): they flip from the admin UI at runtime.

    `auto_enable`: when true, dns.create_zone auto-triggers cf.zone_enable
    for new domains if a pool account has capacity (feature 6). Off by
    default -- turning proxy/DNS on for every new domain is an explicit
    operator decision (plan SS1.9).
    `lockdown_enabled`: whether UFW CF-only web mode is currently engaged
    (feature 9); the marker of record so a daemon restart / status read
    knows the intended state without parsing UFW rules."""

    __tablename__ = "cloudflare_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auto_enable: Mapped[bool] = mapped_column(default=False)
    lockdown_enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DkimKey(Base):
    """Phase 3 feature 1: one DKIM signing keypair per mail domain,
    generated automatically the first time a mail domain is created
    (daemon/dkim.py). The private key itself lives on disk
    (/etc/boron/dkim/<domain>/<selector>.private, root-only) -- this
    row is bookkeeping only (which selector is active, so repeat calls
    reuse rather than silently rotate the key) plus whether the public key
    was actually published to a Boron-managed DNS zone or just
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


class AccountEvent(Base):
    """Dedicated account lifecycle log: who created / suspended / unsuspended /
    terminated which account, when, from which client IP. Terminated accounts
    disappear from every list in the panel -- rows here (plus the append-only
    file mirror in daemon/audit.py) are their only remaining record."""

    __tablename__ = "account_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16), index=True)  # created | suspended | unsuspended | terminated
    username: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(16), default="system")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4/IPv6 of the client, None for system actions
    detail: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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


class PhpIniDirective(Base):
    """Additional per-account php.ini overrides beyond PhpIniOverride's six
    original typed columns (max_input_vars, session.gc_maxlifetime, ...).
    One row per (account, directive) -- a key/value table rather than more
    columns on PhpIniOverride for the same create_all-never-ALTERs reason
    that table itself documents, with the bonus that future directives need
    no schema change at all. `name` is only ever one of the keys in
    daemon/handlers_php_ini.py's EXTRA_DIRECTIVES registry (enforced at
    write time); `value` is stored already-validated in its rendered string
    form ("5000", "On", "Asia/Kolkata")."""

    __tablename__ = "php_ini_directives"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_php_ini_directive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PhpExtensionSet(Base):
    """Per-account PHP extension selection. `enabled` is the full list of
    extension names this account wants loaded (e.g. ["curl", "mysqli"]) --
    the daemon materializes it as a root-owned per-version scan directory
    under the account's HOME (~/.php/<ver>/conf.d, symlinks into the stock
    mods-available dir) and points that account's own extProcessor at it
    via a PHP_INI_SCAN_DIR env line (daemon/phpext.py). Under the HOME, not
    /etc or /run, because namespaced accounts (nsisolation, the default)
    only see their own home tree inside the jail -- same lesson
    daemon/redisacct.py's socket path learned, and /etc/boron was
    confirmed invisible from a live jailed lsphp before choosing this. No
    row at all means stock behavior (the compiled-in scan dir), the
    project-wide "absence means default" convention."""

    __tablename__ = "php_extension_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    enabled: Mapped[list] = mapped_column(JSON, default=list)
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
    settings.pma_token_dir (NOT under /var/lib/boron, which is
    locked to root:boron-api -- the phpMyAdmin signon script runs as
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
    install. QA round 2, item 3: originally one row per domain (a UNIQUE
    index on `domain` alone), which only ever allowed a single WP install
    at a domain's docroot. Now scoped by (domain, path) -- `path` is the
    install's location relative to the domain's docroot ("" for the
    docroot itself, "blog" for domain.com/blog/) -- so multiple WordPress
    installs can coexist under one domain (root + subdirectories) and are
    each tracked/managed independently. See shared/db.py's
    `_migrate_wordpress_installs_uniqueness` for how a pre-existing
    deployed DB's old single-column unique index is safely replaced.
    No password field here -- the admin password is returned once, at
    install-completion time (WordPressJob.admin_password, cleared after
    first read), never persisted long-term, matching this project's
    "passwords never stored" rule applied everywhere else (DB/mail/FTP
    credentials)."""

    __tablename__ = "wordpress_installs"
    __table_args__ = (UniqueConstraint("domain", "path", name="uq_wordpress_installs_domain_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    path: Mapped[str] = mapped_column(String(255), default="")
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


class PermanentIpBan(Base):
    """QA round 2, item 14: admin-initiated, server-wide, permanent IP/CIDR
    ban -- distinct from both the per-account/per-domain IP blocker
    (IpBlockEntry, scoped into that one domain's own OLS vhost
    accessControl block) and fail2ban's own automatic, time-bounded jails
    (which only ever expose *unban*, no manual/permanent ban action). The
    actual enforcement is a UFW `deny from <ip>` rule (daemon/ipban.py) --
    this row is metadata (reason, who, when) UFW itself has no field for,
    plus the panel's own list/add/remove surface over it."""

    __tablename__ = "permanent_ip_bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    banned_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WelcomeEmailTemplate(Base):
    """QA round 2, item 10: single-row (id=1) admin-editable override of the
    "account created" welcome email's subject/body -- same single-row shape
    as NotificationSettings/BrandingSettings, since there is genuinely only
    one server-wide welcome-email template. NULL fields (the default,
    unconfigured state) mean "use daemon/notifications.py's built-in
    hardcoded text" -- same absence-means-default convention as everything
    else in this schema. `body` supports a small, documented set of
    {{placeholder}} tokens (username, password, primary_domain, panel_name),
    substituted at send time by daemon/notifications.py, never by this
    model -- this row only stores the template text."""

    __tablename__ = "welcome_email_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(String(8000), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PhpFunctionOverride(Base):
    """QA round 2, item 9: admin-only override of PHP's `disable_functions`
    directive, layered on top of the system-wide hardened default (set
    directly in the real lsphp php.ini files by scripts/install.sh --
    see daemon/phpdirectives.py's DEFAULT_DISABLE_FUNCTIONS). `domain`
    NULL means an account-wide override (applies to every domain under
    the account); a specific domain row takes precedence over the
    account-wide one for that one domain -- the "per-account or
    per-domain, overriding default" the goal asks for. Deliberately NOT
    reachable through the customer-facing php-ini surface
    (api/routers/php_ini.py) or its PhpIniOverride/PhpIniDirective
    tables -- letting a customer re-enable exec/shell_exec/etc. for their
    own account would defeat the entire point of the hardened default."""

    __tablename__ = "php_function_overrides"
    __table_args__ = (UniqueConstraint("account_id", "domain", name="uq_php_function_override_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    disable_functions: Mapped[str] = mapped_column(String(2000))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TotpCredential(Base):
    """Phase 5 feature 10: TOTP 2FA. `secret` is encrypted with the daemon's
    Fernet key (it cannot be hashed because a TOTP secret must be *used*
    (HMAC'd against the current time step) on every verification, not
    just compared, so it can't be one-way-hashed the way
    PanelUser.password_hash is. The Fernet key is kept in the root-only
    secrets file and is available only to the provisioning daemon.
    `enabled=False` until a submitted code proves the admin
    actually scanned the QR code and can generate valid codes
    (goal: "verify before enabling")."""

    __tablename__ = "totp_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id"), unique=True, index=True)
    secret: Mapped[str] = mapped_column(String(512))
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


class NamespaceMigrationJob(Base):
    """Phase 6b Step 3/4: admin bulk-enable job, same async-job-table
    pattern as AppInstallJob above (not per-account state -- see
    daemon/nsisolation.py's module docstring for why per-account "enabled"
    status is deliberately NOT stored in the DB; this table exists only
    because a multi-step, potentially-long-running *migration run* has no
    other natural home for its own progress state). Stops at the first
    per-account failure (Step 4's explicit safety rule) rather than
    skipping and continuing -- `results` records every account attempted
    up to and including the one that failed, in order."""

    __tablename__ = "namespace_migration_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    current_username: Mapped[str | None] = mapped_column(String(16), nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)  # [{username, ok, detail}]
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CpanelImportJob(Base):
    """Phase 7b feature 1: async cPanel full-backup import. Same async-job
    table shape as AppInstallJob/NamespaceMigrationJob (status/progress_
    message/error/started_at/completed_at) -- `results` is the per-item
    success/fail/skip report the goal explicitly requires ("per-item
    success/fail report"), appended to incrementally as each backup
    component (account/domain/database/mailbox/dns/ssl/ftp/cron) is
    attempted, so a poll mid-run already shows partial progress, not just
    a final summary. `source_ref` holds the uploaded tarball's staging path
    or the given URL only until the job starts extracting it -- cleared
    afterward (same "don't keep more than needed" posture as
    WordPressJob.admin_password's one-time reveal, just for an input
    artifact instead of a generated secret)."""

    __tablename__ = "cpanel_import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(8))  # upload | url
    source_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)  # [{item, status: ok|failed|skipped, detail}]
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeApp(Base):
    """Phase 7a feature 1: per-account NodeJS app hosting. One row per app,
    bound 1:1 to one of the account's own domains -- that domain's vhost
    context `/` becomes a pure reverse proxy to this app's local port
    (daemon/ols.py's `_app_proxy_for_domain`), so an app and PHP/WordPress/
    the app installer can never both serve the same domain at once (the
    same "one thing owns this vhost's context /" invariant the suspended-
    page swap already relies on). Supervised by a real systemd unit
    (boron-node-{username}-{id}.service, daemon/nodeapps.py) assigned
    directly to the account's own cgroup slice via `Slice=` at spawn time
    -- unlike LSAPI PHP workers (daemon/cgroups.py's periodic reconciler),
    a systemd-spawned unit can be told its target slice directly, no
    privilege-elevation workaround needed.

    env_vars is the Fernet-encrypted (daemon/appcrypto.py), JSON-encoded
    dict of this app's own environment variables -- decrypted only at
    unit-render time into a root-only (0600) EnvironmentFile that systemd
    itself reads before dropping to the account's own uid, so a decrypted
    secret is never written anywhere the hosting account's own uid can
    read it (goal: "env vars stored encrypted")."""

    __tablename__ = "node_apps"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_node_app_domain"),
        UniqueConstraint("account_id", "name", name="uq_node_app_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    entry_point: Mapped[str] = mapped_column(String(512))
    port: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    node_version: Mapped[str] = mapped_column(String(8))
    env_vars: Mapped[str] = mapped_column(String(8000), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PythonApp(Base):
    """Phase 7a feature 2: per-account Python WSGI/ASGI app hosting -- same
    shape and lifecycle as NodeApp (one row per app, 1:1 domain binding,
    systemd-supervised, cgroup-sliced, Fernet-encrypted env vars), the
    Node-specific fields (node_version) replaced with `app_type` (wsgi via
    gunicorn, asgi via uvicorn -- daemon/pythonapps.py picks the launch
    command from this) and a per-app virtualenv under the account's own
    home (`<home>/pythonapps/<name>/venv`), never a shared/system venv."""

    __tablename__ = "python_apps"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_python_app_domain"),
        UniqueConstraint("account_id", "name", name="uq_python_app_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    entry_point: Mapped[str] = mapped_column(String(512))  # "module:callable", e.g. "app:app"
    app_type: Mapped[str] = mapped_column(String(8), default="wsgi")  # wsgi (gunicorn) | asgi (uvicorn)
    port: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    env_vars: Mapped[str] = mapped_column(String(8000), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RedisInstance(Base):
    """Phase 7a feature 3: per-account Redis via systemd (one instance per
    ACCOUNT, not per-app like NodeApp/PythonApp -- Redis here is shared
    account-level cache/session storage, matching how PhpIniOverride/
    cgroup limits are one row per account too). Reachable only via a
    private Unix socket at /run/redis/<username>.sock, mode 700 owned by
    the account's own uid -- no TCP listener at all, so there is no port
    to firewall or misconfigure; a second account's own process can
    `connect()` to the socket path but the filesystem permission itself
    (0700, not the account's own group) is what makes that fail, the same
    DAC-based isolation model this whole project already relies on for
    account separation. No persistence by default (`save ""` in the
    rendered redis.conf, goal's explicit v1 default) -- data_dir
    (~/.redis/) is still created for an admin who deliberately enables
    persistence later via a config override, just never written to by
    Boron itself otherwise."""

    __tablename__ = "redis_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    mem_mb: Mapped[int] = mapped_column(Integer, default=64)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BandwidthDailyDomain(Base):
    """Phase 7b feature 2: per-domain daily bandwidth breakdown, alongside
    (not replacing) the existing per-account BandwidthDaily -- needed for
    "top 5 domains by bandwidth" without changing the meaning or reader
    contract of the account-level totals Phase 2 feature 5 already
    shipped. Populated by the same daemon/usage.py refresh_bandwidth()
    access-log parse pass, just keyed one level finer."""

    __tablename__ = "bandwidth_daily_domain"
    __table_args__ = (UniqueConstraint("account_id", "domain", "date", name="uq_bandwidth_daily_domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    bytes_served: Mapped[int] = mapped_column(Integer, default=0)


# Phase 7b features 3/4/5: the canonical set of lifecycle events every
# notification/webhook/alert-driven email can fire for. A plain module-level
# tuple (not a DB-backed enum table) -- matches this project's existing
# convention for closed, code-defined vocabularies (BACKUP_KINDS,
# FREQUENCIES in daemon/backup.py) rather than a table nothing else needs to
# query.
NOTIFICATION_EVENT_TYPES = (
    "account.created",
    "account.suspended",
    "account.unsuspended",
    "account.terminated",
    "backup.completed",
    "backup.failed",
    "ssl.expiring",
    "usage.limit.reached",
    "login.new",
    "dns.zone_activated",
)


def _default_event_prefs() -> dict:
    return {event: True for event in NOTIFICATION_EVENT_TYPES}


class NotificationSettings(Base):
    """Phase 7b feature 3: single-row (id=1) admin-configured sender
    address + global per-event-type enable/disable -- same single-row
    shape as SpamGlobalSettings/WafSettings, since there is genuinely only
    one server-wide notification configuration. A per-account send still
    requires BOTH this global switch and the account's own
    AccountNotificationPrefs to allow the event, so an admin can kill a
    noisy event type server-wide without visiting every account."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_address: Mapped[str] = mapped_column(String(253), default="")
    events: Mapped[dict] = mapped_column(JSON, default=_default_event_prefs)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AccountNotificationPrefs(Base):
    """Phase 7b feature 3: per-account customer email + opt-outs. A row is
    created lazily on first read/write (same "no row = defaults" pattern
    as PhpIniOverride) -- customer_email is None until the admin or
    customer sets one, in which case no notification email is ever sent
    for that account (there is nowhere to send it), independent of the
    events dict."""

    __tablename__ = "account_notification_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    customer_email: Mapped[str | None] = mapped_column(String(253), nullable=True)
    events: Mapped[dict] = mapped_column(JSON, default=_default_event_prefs)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


WEBHOOK_EVENT_TYPES = (
    "account.created",
    "account.suspended",
    "account.terminated",
    "backup.completed",
    "ssl.expiring",
    "usage.limit.reached",
    "dns.zone_activated",
)


class Webhook(Base):
    """Phase 7b feature 4: an admin-configured outbound webhook. `secret`
    is stored in plain text (not hashed) -- unlike a login credential, it
    has to be used to *compute* an HMAC on every delivery, not just
    compared, so it can't be one-way-hashed; same documented tradeoff
    TotpCredential.secret already accepts in this same file, protected by
    the DB file's own root:boron-api permission boundary."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    secret: Mapped[str] = mapped_column(String(128))
    events: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    """One row per delivery attempt series for one (webhook, event
    occurrence) pair -- `attempt_count` increments in place rather than
    inserting a new row per retry, so the delivery log shows one entry per
    real-world event with its final outcome, not three near-duplicate rows
    for a single retried delivery."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|success|failed
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_attempted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountResourceLimits(Base):
    """Phase 7b feature 5: alert-facing limits for the resources that have
    no existing quota column -- disk already has Account.quota_soft_mb/
    quota_hard_mb (Phase a, OS-enforced), reused directly as the "disk"
    resource's 80/90/100% reference rather than duplicated here. A row is
    created lazily (no row = every *_limit is "not tracked", i.e. that
    resource is never alerted on for this account) -- matches
    PhpIniOverride's own lazy-row convention."""

    __tablename__ = "account_resource_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    bandwidth_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_account_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subdomain_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Run A feature 1 (plan templates): the same nullable-means-untracked
    # convention as the four fields above, added so a Plan's "max FTP
    # sub-accounts" / "max apps" fields have somewhere to land -- FTP/app
    # counts had no existing quota column anywhere else in the schema.
    ftp_account_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_suspend_at_100: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UsageAlert(Base):
    """Phase 7b feature 5: one row per (account, resource) alert episode.
    `resolved_at` NULL means still active -- a fresh check_usage_alerts()
    pass that finds usage back under 80% resolves it rather than deleting
    it, so the alert history (goal: "alert history") stays a durable log,
    the same "never delete, keep history" posture BackupJob/Account rows
    already use in this project."""

    __tablename__ = "usage_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    resource: Mapped[str] = mapped_column(String(32))  # disk|bandwidth|databases|email_accounts|subdomains
    threshold_pct: Mapped[int] = mapped_column(Integer)  # 80|90|100
    triggered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(default=False)


class StagingEnvironment(Base):
    """Phase 7b feature 6: bookkeeping for a domain's staging clone.
    `source_domain`/`staging_domain` are unique (one staging environment
    per production domain, and a staging domain can never collide with
    any other Domain row since Domain.domain itself is globally unique) --
    `db_name`/`db_user` are None when the source domain has no detected
    WordPress install (goal's DB-clone step is WordPress-specific; a
    non-WP staging clone is files-only)."""

    __tablename__ = "staging_environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    source_domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    staging_domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    is_wordpress: Mapped[bool] = mapped_column(default=False)
    db_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    db_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SslExpiryNotice(Base):
    """Phase 7b feature 3: dedup marker so the daily SSL-expiry cron
    (scripts/ssl_expiry_check.py) sends exactly one "expiring in <=14
    days" notice per certificate issuance, not once per day for two
    straight weeks. Keyed on the cert's own not-after date, not just the
    domain -- a renewed certificate has a new expiry date and is therefore
    correctly treated as a fresh notice-worthy event."""

    __tablename__ = "ssl_expiry_notices"
    __table_args__ = (UniqueConstraint("domain", "expiry_date", name="uq_ssl_expiry_notice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    expiry_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, from the cert's own not_valid_after
    notified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImpersonationToken(Base):
    """Phase 8 feature 1: a single-use, 5-minute "login as user" token an
    admin mints to open a scoped customer session for an account. Stored
    hashed (SHA-256, same "never store the raw token" rule as ApiToken/
    PmaToken) with `used_at` set on first redemption so a captured token
    can never be replayed even inside its 5-minute window (a signed-cookie
    token alone couldn't guarantee single-use -- that needs server-side
    state, which is why this is a DB row and not just an itsdangerous
    token). `admin_username` is recorded so only the issuing admin can
    redeem it, and so the whole impersonation is attributable in the audit
    log from issuance through every action taken while impersonating."""

    __tablename__ = "impersonation_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    admin_username: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImpersonationSession(Base):
    """Phase 8 feature 1: the live "admin is currently acting as customer X"
    record, created when an ImpersonationToken is redeemed. The underlying
    `Session` row is owned by the ADMIN's own panel_user_id (so it stays
    revocable and attributable to a real user), and this row is what
    api/security.get_identity consults to DOWNSCOPE that session to a
    customer identity for `account_id` -- an impersonation session can
    therefore never reach an admin-only endpoint, even though it belongs to
    an admin panel user. `admin_session_id` is the admin's original session
    to restore when they click "Return to admin"; `ended_at` set (plus the
    impersonation Session revoked in the same transaction) marks it
    finished so a stale cookie can never silently keep customer access."""

    __tablename__ = "impersonation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    admin_panel_user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id"))
    admin_username: Mapped[str] = mapped_column(String(64))
    admin_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountNote(Base):
    """Phase 8 feature 11: admin-only, append-only account notes. Never
    exposed on any customer-scoped endpoint (the router that serves these
    is admin-only, and no customer-facing dict ever includes them) -- the
    goal's explicit "never visible to customer" requirement. Append-only:
    there is no update/delete op at all, so the note history is a durable
    record, matching the same "never delete, keep history" posture
    AuditLog/UsageAlert already use. `author` is the admin panel username
    captured at write time."""

    __tablename__ = "account_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    author: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(String(8000))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DomainForwarding(Base):
    """Phase 8 feature 4: whole-domain 301/302 redirect to an external URL,
    rendered into that domain's own vhost as a top-level rewrite
    (daemon/ols.py). A NEW table keyed by domain NAME (unique), not new
    columns on Domain -- same reasoning as Redirect/LscacheSettings/
    WafDomainOverride (Base.metadata.create_all only creates missing
    tables, never ALTERs). Distinct from the existing `Redirect` model,
    which is a per-PATH rewrite within an otherwise-normal site; this
    replaces the ENTIRE domain's serving with a redirect, so a domain can
    have at most one forwarding row (the UNIQUE domain constraint) and it
    is mutually exclusive with normal PHP/app serving for that domain.
    `keep_path` controls whether the original request URI is appended to
    the target (cPanel's "redirect with/without path")."""

    __tablename__ = "domain_forwardings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    target_url: Mapped[str] = mapped_column(String(2048))
    status_code: Mapped[int] = mapped_column(Integer, default=301)
    keep_path: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ParkedDomain(Base):
    """Phase 8 feature 3: an alias ("parked") domain that serves the SAME
    docroot + PHP context as an existing target domain on the same account.
    Implemented as an ordinary `Domain` row with kind='parked' whose docroot
    points at the target domain's docroot (so it reuses the whole existing
    one-vhost-per-domain + shared-extProcessor machinery, daemon/ols.py) --
    this row is the bookkeeping that records which target each parked domain
    aliases, for the UI and for correct teardown. Keyed by the parked domain
    name (globally unique, like every Domain)."""

    __tablename__ = "parked_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    parked_domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    target_domain: Mapped[str] = mapped_column(String(253), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailRouting(Base):
    """Phase 8 feature 6: per-domain mail routing mode (local | remote |
    backup). Stored in the SQLite control plane (not the boron_mail
    MariaDB schema) because the *authoritative* Postfix acceptance switch is
    the `mail_domain.active` flag the daemon already toggles -- this row is
    the panel's own record of the operator's chosen mode, which
    daemon/handlers_email_routing.py reconciles into that flag (+ a
    regenerated relay_domains map for 'backup'). Default (no row) = 'local',
    matching the pre-existing behavior where a provisioned mail domain is
    always accepted locally. Keyed by domain name, the same domain-scoped
    convention as every other per-domain mail feature."""

    __tablename__ = "email_routing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(8), default="local")  # local | remote | backup
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BulkActionJob(Base):
    """Phase 8 feature 12: async multi-account operation (suspend / unsuspend /
    update-limits / notify). Same async-job table shape as
    NamespaceMigrationJob (status/total/completed_count/current_username/results/
    error) and, like it, **stops at the first per-account failure** rather than
    skipping and continuing -- `results` records every account attempted up to
    and including the one that failed, in order, so the UI shows exactly where a
    run stopped. `action_params` carries the action's payload (limits values, or
    the notification subject/body)."""

    __tablename__ = "bulk_action_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(24))  # suspend|unsuspend|update_limits|notify
    action_params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    current_username: Mapped[str | None] = mapped_column(String(16), nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)  # [{username, ok, detail}]
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandRun(Base):
    """Phase 8 features 8/9: one async WP-CLI or Composer run, executed as the
    account user (daemon/cmdjobs.py). Same async-job shape as WordPressJob/
    AppInstallJob (status/error/started_at/completed_at) plus captured
    stdout/stderr/exit_code for the UI to show. `command_display` is the
    human-readable command (secrets already stripped -- a reset-password run
    never records the password); `kind` is 'wpcli' or 'composer', `target` is
    the docroot/app directory it ran in. Never deleted -- a durable run history,
    matching the same posture the other job tables use."""

    __tablename__ = "command_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # wpcli | composer
    target: Mapped[str] = mapped_column(String(1024))
    command_display: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str | None] = mapped_column(String(200000), nullable=True)
    stderr: Mapped[str | None] = mapped_column(String(200000), nullable=True)
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    # For a WP-CLI user-reset-password run, the generated password is surfaced
    # once (cleared on first read, same one-time-reveal pattern as
    # WordPressJob.admin_password).
    revealed_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LscacheSettings(Base):
    """Phase 7a feature 4: per-domain LSCache (OLS's native page-cache
    module, ARCHITECTURE.md SS10.5-adjacent -- unlike ModSecurity, LSCache
    IS genuinely configurable per-vhost on OpenLiteSpeed, confirmed against
    this server's own `module cache {}` block already present in every
    vhost's rendered config (daemon/ols.py) with `enableCache 0` by
    default). One row per domain, created lazily on first enable -- a
    domain that never touches this feature has no row and renders with
    caching off, identical to before this feature existed."""

    __tablename__ = "lscache_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    exclude_paths: Mapped[list] = mapped_column(JSON, default=list)
    last_purged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Plan(Base):
    """Run A feature 1: named hosting-plan presets (Basic/Pro/Business/...)
    an admin defines once and applies to many accounts, cPanel-package
    style. Deliberately holds its OWN copy of every limit value rather than
    a foreign key into a single "current limits" row -- a plan is a
    template, edited independently of any account that was ever built from
    it (matches this project's Account.plan_id docstring: applying a plan
    copies values onto the account at that moment, it does not keep the
    account permanently bound to the plan's row). cpu_pct/mem_mb/io_mb/
    pids_max/quota_soft_mb/quota_hard_mb mirror Account's own columns
    exactly; bandwidth/database/email/subdomain/ftp/app limits mirror
    AccountResourceLimits' nullable-means-unlimited convention. Redis is a
    plain enabled/disabled bit applied via redisacct.enable_redis/
    disable_redis -- RedisInstance.enabled remains the single source of
    truth for actual state, this is only "what the plan specifies".
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cpu_pct: Mapped[int] = mapped_column(Integer)
    mem_mb: Mapped[int] = mapped_column(Integer)
    io_mb: Mapped[int] = mapped_column(Integer)
    pids_max: Mapped[int] = mapped_column(Integer)
    quota_soft_mb: Mapped[int] = mapped_column(Integer)
    quota_hard_mb: Mapped[int] = mapped_column(Integer)
    bandwidth_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_account_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subdomain_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ftp_account_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redis_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MonitoringSettings(Base):
    """Run A feature 5: single-row (id=1) service-monitoring config, same
    convention as NotificationSettings/BrandingSettings. The alert SENDER
    is deliberately not duplicated here -- it reuses
    NotificationSettings.sender_address (one outbound-mail identity to
    configure, not two that drift); only the admin RECIPIENT and the
    re-alert cooldown are monitoring-specific."""

    __tablename__ = "monitoring_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    admin_email: Mapped[str | None] = mapped_column(String(253), nullable=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ServiceCheck(Base):
    """Run A feature 5: one row per (service, monitoring pass) -- the 24h
    uptime history the admin UI draws. Pruned at 48h by the check pass
    itself, same explicit-retention reasoning as HealthSnapshot (no
    natural cleanup event)."""

    __tablename__ = "service_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(default=True)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MonitoringAlertState(Base):
    """Run A feature 5: per-service alert bookkeeping -- current down
    state, when it went down, and when an alert was last SUCCESSFULLY
    handed to the MTA (`last_alert_sent_at` only advances on a real SMTP
    handoff; a failed send retries next pass instead of being silently
    cooled down -- load-bearing when the down service is Postfix itself,
    see daemon/monitoring.py's module docstring)."""

    __tablename__ = "monitoring_alert_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    is_down: Mapped[bool] = mapped_column(default=False)
    down_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_alert_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OnboardingState(Base):
    """Run A feature 4: client onboarding wizard. One row per account,
    created lazily on first read (no row = wizard not yet completed, so a
    brand-new account shows it on first login -- same "no row = defaults"
    pattern as PhpIniOverride/AccountResourceLimits). `skipped`
    distinguishes "clicked through all three steps" from "dismissed it" --
    same completed-once-only outcome either way, kept for operator
    visibility, never re-triggers the wizard."""

    __tablename__ = "onboarding_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    completed: Mapped[bool] = mapped_column(default=False)
    skipped: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrandingSettings(Base):
    """Run A feature 3: white-label branding. Single-row (id=1) shape, same
    convention as NotificationSettings/WafSettings -- there is genuinely
    only one server-wide branding configuration. `logo_filename`/
    `favicon_filename` name the actual file under
    `settings.branding_dir` (daemon-written, group-readable by
    boron-api so the unprivileged API process can stream it back out
    over HTTP to anonymous visitors -- same 0640 root:boron-api
    pattern as the control-plane DB file itself, shared/db.py's
    `_grant_api_group_read`); NULL means "no custom asset, use the
    built-in default" for that asset specifically."""

    __tablename__ = "branding_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_name: Mapped[str] = mapped_column(String(64), default="Boron")
    terminal_banner: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    logo_filename: Mapped[str | None] = mapped_column(String(64), nullable=True)
    favicon_filename: Mapped[str | None] = mapped_column(String(64), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(253), nullable=True)
    support_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UpdateState(Base):
    """Panel update system: single-row (id=1) cache of the last GitHub
    release check, same singleton convention as MonitoringSettings/
    BrandingSettings. `update.check` serves from this row while it is
    fresher than `settings.update_check_cache_seconds` (goal: cache 1hr)
    so admin-dashboard polling never hammers the GitHub API.
    `last_notified_version` makes the daily cron's admin email fire once
    per distinct new release, not once per day forever."""

    __tablename__ = "update_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    changelog_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    tarball_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    checksum_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_notified_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class MaintenanceMode(Base):
    """Missing-features batch, goal feature 2: per-domain maintenance mode.
    A NEW table keyed by domain name (unique), not new columns on Domain --
    same reasoning as Redirect/DomainForwarding/LscacheSettings (create_all()
    only creates missing tables, never ALTERs an existing one; see
    shared/db.py's module docstring). `bypass_token` is generated
    server-side (secrets.token_urlsafe, shared/validation.py's
    generate_bypass_token) -- never customer-chosen -- the same
    "capability token embedded in a URL, not a password" shape as
    ApiToken/PmaToken. `auto_disable_at` is computed once at enable time
    from the goal's four presets (1h/4h/24h/manual -- manual leaves this
    NULL) and swept by scripts/maintenance_autodisable.py (cron), the same
    "compute the deadline once, sweep later" pattern SslExpiryNotice/
    UsageAlert use elsewhere in this file."""

    __tablename__ = "maintenance_modes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    title: Mapped[str] = mapped_column(String(200), default="We'll be right back")
    message: Mapped[str] = mapped_column(
        String(2000), default="This site is currently undergoing scheduled maintenance. Please check back soon."
    )
    estimated_time: Mapped[str] = mapped_column(String(100), default="")
    bypass_token: Mapped[str] = mapped_column(String(64))
    auto_disable_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_disable_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WildcardDomain(Base):
    """Missing-features batch, goal feature 3: *.domain.com -> the same
    vhost (and therefore the same docroot) as the base domain, via OLS's
    own wildcard listener-map syntax (confirmed against this server's
    installed docs, Listeners_General_Help.html: "'*.mydomain.com' will
    match all subdomains of mydomain.com"), daemon/ols.py. A NEW table
    keyed by the base domain name (unique) -- same reasoning as
    MaintenanceMode above. Enabling requires the domain's own zone to be
    Boron-managed (the same precondition ssl.issue_wildcard already
    enforces, daemon/ssl.py _dns01_plan) since a wildcard A record has to
    be written somewhere; `dns_record_created` tracks whether this
    feature's own upsert_record(zone, "*", "A", ...) call has run, so
    disabling can clean it up and re-enabling doesn't double-write."""

    __tablename__ = "wildcard_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    dns_record_created: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


SPAM_FILTER_KINDS = ("blacklist", "whitelist")


class SpamFilterEntry(Base):
    """Missing-features batch, goal feature 5: per-mailbox spam
    blacklist/whitelist, by address or domain. Rendered into a per-mailbox
    SpamAssassin virtual-config-dir user_prefs file as `blacklist_from`/
    `whitelist_from` lines (daemon/spamfilter.py's existing per-DOMAIN
    settings already use this exact spamd --virtual-config-dir mechanism,
    Phase 4 feature 1 -- this feature extends it one level finer, to
    %u=full recipient address, without disturbing the existing per-domain
    file). Whitelist intervenes BEFORE SpamAssassin's own scoring, so a
    whitelisted sender's mail never gets flagged spam in the first place
    (the correct place to implement "never spam" -- a per-mailbox Dovecot
    Sieve rule would run too late, since the existing GLOBAL sieve_before
    script that files X-Spam-Flag:YES mail into Junk already issued `stop`
    by the time any personal script would run, see CHECKPOINT for this
    feature). Blacklist uses the same per-mailbox prefs file's
    `blacklist_from` directive (guarantees a score far above any real
    required_score, so it's always filed to Junk) -- see the CHECKPOINT
    for why this project chose SpamAssassin per-mailbox rules over
    Postfix header_checks (which has no per-recipient scoping without a
    much larger smtpd_restriction_classes buildout the goal's effort level
    doesn't justify)."""

    __tablename__ = "spam_filter_entries"
    __table_args__ = (
        UniqueConstraint("domain", "local_part", "kind", "pattern", name="uq_spam_filter_entry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    local_part: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # blacklist | whitelist
    pattern: Mapped[str] = mapped_column(String(253))  # "user@example.com" or "example.com" (domain-wide)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


IMAP_MIGRATION_STATUSES = ("pending", "connecting", "running", "completed", "failed", "cancelled")


class ImapMigrationJob(Base):
    """Missing-features batch, goal feature 1: async IMAPSync migration job.
    Same async-job table shape as WordPressJob/CpanelImportJob (status/
    progress_message/error/started_at/completed_at, `results` as the
    incremental per-folder report) -- deliberately holds NO source
    credential field of any kind: the goal's explicit "credentials never
    stored after job completes or logged anywhere" requirement is met
    structurally, by never having a column that could hold one, rather
    than by remembering to clear one after the fact (the same "don't keep
    more than needed" posture WordPressJob.admin_password's one-time
    reveal takes for a *generated* secret; here there's nothing to reveal,
    so nothing is stored at all -- daemon/imapsync.py holds the source
    password only as a Python local variable for the lifetime of the
    subprocess call, passed via imapsync's own --password1 stdin-style
    pipe, never argv, never written to this row, this table's own
    __table_args__, or any log line)."""

    __tablename__ = "imap_migration_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    mailbox: Mapped[str] = mapped_column(String(253), index=True)  # local@domain, the BORON destination mailbox
    source_host: Mapped[str] = mapped_column(String(253))
    source_port: Mapped[int] = mapped_column(Integer, default=993)
    source_email: Mapped[str] = mapped_column(String(253))  # source address only -- never the password
    folders: Mapped[list] = mapped_column(JSON, default=list)  # requested folder names, [] = all folders
    status: Mapped[str] = mapped_column(String(16), default="pending")
    current_folder: Mapped[str | None] = mapped_column(String(253), nullable=True)
    folders_total: Mapped[int] = mapped_column(Integer, default=0)
    folders_done: Mapped[int] = mapped_column(Integer, default=0)
    messages_total: Mapped[int] = mapped_column(Integer, default=0)
    messages_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)  # [{folder, status: ok|failed, messages, detail}]
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteStatsDaily(Base):
    """Missing-features batch, goal feature 6: per-domain daily site
    statistics parsed from OLS access logs -- alongside (not replacing)
    the existing BandwidthDaily/BandwidthDailyDomain (Phase 2 feature 5 /
    Phase 7b feature 2), which only ever tracked bytes_served. This is a
    NEW table (same create_all-friendly reasoning as every other table in
    this missing-features batch) rather than added columns on
    BandwidthDailyDomain, since site stats are a materially bigger row
    (top-N breakdowns as JSON, matching HealthSnapshot.disks' own
    "structured list in a JSON column" convention) that no other reader of
    BandwidthDailyDomain needs. Upserted, not appended, by
    daemon/sitestats.py's daily refresh pass -- same "recompute and
    replace this day's row while the day's raw log lines are still on
    disk" semantics BandwidthDaily already documents."""

    __tablename__ = "site_stats_daily"
    __table_args__ = (UniqueConstraint("domain", "date", name="uq_site_stats_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    pageviews: Mapped[int] = mapped_column(Integer, default=0)
    unique_visitors: Mapped[int] = mapped_column(Integer, default=0)
    bytes_served: Mapped[int] = mapped_column(Integer, default=0)
    error_404_count: Mapped[int] = mapped_column(Integer, default=0)
    top_pages: Mapped[list] = mapped_column(JSON, default=list)  # [{path, count}]
    top_referrers: Mapped[list] = mapped_column(JSON, default=list)  # [{referrer, count}]
    top_countries: Mapped[list] = mapped_column(JSON, default=list)  # [{country_code, count}]


class UpdateJob(Base):
    """Panel update system: one row per update/rollback attempt -- both the
    live job the admin UI polls AND the permanent update-history record
    (goal 6: from/to version, timestamp, success/fail, duration, who).
    Same async-job shape as CpanelImportJob; `steps` is the incremental
    per-step log mirrored to /var/log/boron/updates.log.

    status: pending -> running -> finalizing -> completed | failed.
    `finalizing` is update-specific: the daemon has staged everything and
    handed off to the detached stdlib-only finalizer (which survives the
    daemon's own restart) for symlink swap + service restarts + health
    check; the finalizer writes the terminal status. `rolled_back` records
    whether the finalizer had to swap back after a failed health check.
    old_dir/new_dir are the symlink targets involved -- old_dir is what a
    later manual rollback returns to."""

    __tablename__ = "update_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="update")  # update | rollback
    status: Mapped[str] = mapped_column(String(16), default="pending")
    from_version: Mapped[str] = mapped_column(String(32))
    to_version: Mapped[str] = mapped_column(String(32))
    initiated_by: Mapped[str] = mapped_column(String(64))
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{step, status, detail, at}]
    error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    rolled_back: Mapped[bool] = mapped_column(default=False)
    old_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WordPressSiteState(Base):
    """Discovery metadata and explicit suppression of removed panel records."""
    __tablename__ = 'wordpress_site_states'
    __table_args__ = (UniqueConstraint('account_id', 'domain', 'path', name='uq_wp_site_state'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    path: Mapped[str] = mapped_column(String(255), default='')
    hidden: Mapped[bool] = mapped_column(default=False)
    site_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scanned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SnapshotDestination(Base):
    """Public repository metadata. Encryption and SSH keys stay outside SQLite."""
    __tablename__ = 'snapshot_destinations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(1024))
    namespace: Mapped[str] = mapped_column(String(64), unique=True)
    connection: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default='draft')
    error: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SnapshotPolicy(Base):
    __tablename__ = 'snapshot_policies'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    destination_id: Mapped[int] = mapped_column(ForeignKey('snapshot_destinations.id'))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    frequency: Mapped[str] = mapped_column(String(16), default='manual')
    enabled: Mapped[bool] = mapped_column(default=True)
    last_queued_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SnapshotRun(Base):
    __tablename__ = 'snapshot_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey('snapshot_policies.id'), index=True)
    destination_id: Mapped[int] = mapped_column(ForeignKey('snapshot_destinations.id'))
    account_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'), index=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    trigger: Mapped[str] = mapped_column(String(16), default='manual')
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_results: Mapped[dict] = mapped_column(JSON, default=dict)
    progress_message: Mapped[str] = mapped_column(String(256), default='Queued')
    error: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SnapshotRestore(Base):
    __tablename__ = 'snapshot_restores'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('snapshot_runs.id'), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'), index=True)
    selection: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    safety_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_message: Mapped[str] = mapped_column(String(256), default='Queued')
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SnapshotMailRecovery(Base):
    """Internal mailbox-workflow checkpoints; never part of restore API output."""
    __tablename__ = 'snapshot_mail_recovery'
    restore_id: Mapped[int] = mapped_column(ForeignKey('snapshot_restores.id'), primary_key=True)
    phase: Mapped[str] = mapped_column(String(32))
    work: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    journal: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PanelConfigJob(Base):
    __tablename__ = 'panel_config_jobs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    admin_port: Mapped[int] = mapped_column(Integer)
    customer_port: Mapped[int] = mapped_column(Integer)
    initiated_by: Mapped[str] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
