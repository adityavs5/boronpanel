"""Boron configuration loading.

Non-secret config lives in /etc/boron/boron.toml. Secrets are split
across two files by who needs them (privilege separation applies to secrets
storage too, not just to runtime process boundaries):

  - /etc/boron/secrets.env (0600, root-only) -- MariaDB admin
    credentials, PowerDNS API key. Only borond (root) ever reads this.
  - /etc/boron/api-secrets.env (0640, root:boron-api) --
    SESSION_SECRET only, the one secret boron-api genuinely needs (to
    verify signed session cookies). Phase h originally had boron-api
    try to read the root-only secrets.env directly for this and would have
    hit a PermissionError at startup; split into its own file instead of
    loosening secrets.env's permissions.

Both are loaded as plain KEY=VALUE lines (no shell expansion, no
execution); either file missing or unreadable is silently treated as
"no secrets from this source" rather than an error, since which file(s) a
given process can see is exactly the point.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("BORON_CONFIG", "/etc/boron/boron.toml"))
SECRETS_PATH = Path(os.environ.get("BORON_SECRETS", "/etc/boron/secrets.env"))
API_SECRETS_PATH = Path(os.environ.get("BORON_API_SECRETS", "/etc/boron/api-secrets.env"))


def _load_secrets(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    if not path.exists():
        return secrets
    try:
        text = path.read_text()
    except PermissionError:
        return secrets
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        secrets[key.strip()] = value.strip()
    return secrets


@dataclass
class Settings:
    # paths
    home_base: str = "/home"
    vhost_conf_dir: str = "/usr/local/lsws/conf/vhosts"
    ols_bin: str = "/usr/local/lsws/bin/openlitespeed"
    ols_admin_bin: str = "/usr/local/lsws/bin/lswsctrl"
    lsphp_base: str = "/usr/local/lsws"
    suspended_page_root: str = "/var/www/_suspended"
    db_path: str = "/var/lib/boron/boron.db"
    backup_dir: str = "/var/lib/boron/backups"
    log_dir: str = "/var/log/boron"
    rpc_socket: str = "/run/boron/provisiond.sock"
    mail_base: str = "/var/vmail"

    # network
    # 0.0.0.0, not 127.0.0.1: this is the panel an operator actually logs
    # into from their own browser (ARCHITECTURE.md SS2 originally said
    # 127.0.0.1 "reachable externally", which is self-contradictory --
    # corrected here during Phase h once an actual login flow needed to
    # work end-to-end). It's still not proxied through OLS (avoids the
    # bootstrap circularity of the panel managing the vhost that serves
    # itself) and still terminates its own TLS.
    api_bind_host: str = "0.0.0.0"
    api_bind_port: int = 2222
    api_customer_port: int | None = None
    panel_hostname: str = ""
    panel_acme_webroot: str = "/var/www/boron-panel-acme"

    # accounts
    # Phase 2 goal asked for 7.4/8.0/8.1/8.2/8.3 -- 7.4 and 8.0 are both EOL
    # and LiteSpeed's official repo ships no lsphp74/lsphp80 build at all
    # for Ubuntu 24.04 (noble); confirmed via `apt-cache search`, not
    # assumed. The only way to get them would be pulling packages built for
    # an older Ubuntu/Debian codename, risking glibc/OpenSSL ABI mismatches
    # against this system's actual libraries and running long-unpatched PHP
    # builds with no security fixes available regardless of source -- not a
    # conservative or secure choice for a hosting panel. Substituted with
    # 8.4/8.5 (both free, natively available) instead, documented here and
    # in CHECKPOINT-phase2-1.md.
    php_versions: tuple[str, ...] = ("8.1", "8.2", "8.3", "8.4", "8.5")
    default_php_version: str = "8.3"
    default_quota_soft_mb: int = 5120
    default_quota_hard_mb: int = 6144

    # mariadb (control DB for hosted accounts + boron_mail schema)
    mariadb_socket: str = "/run/mysqld/mysqld.sock"
    # Deliberately not the bare MySQL "root" account (ARCHITECTURE.md SS4) --
    # a dedicated admin-equivalent user the daemon authenticates as, so root
    # itself can keep a password nobody but the operator knows.
    mariadb_admin_user: str = "boron_daemon"

    # powerdns
    powerdns_api_url: str = "http://127.0.0.1:8081/api/v1"
    powerdns_server_id: str = "localhost"
    # This server's own public IP, used as the default A/NS-glue target when
    # Boron creates a new zone. Set explicitly in boron.toml --
    # deliberately not auto-detected at import time (a network call as a
    # config-loading side effect is surprising and fragile).
    server_public_ip: str = ""

    # mail (Phase e) -- Phase e v1 explicitly did not build webmail, this
    # was a link-out placeholder. Phase 2 feature 3 now deploys Roundcube
    # server-wide, so this is set to that real URL (boron.toml);
    # left blank it still degrades gracefully to "no webmail configured".
    webmail_url: str = ""

    # Phase 2 feature 3: the single hostname Roundcube's own OLS vhost
    # answers on -- one shared webmail install for every hosted mail
    # domain, not one per account (RESEARCH.md/goal: "deploy once
    # server-wide"). Login itself needs no per-account wiring at all: it's
    # plain IMAP/SMTP auth against Dovecot/Postfix, so any existing
    # Boron mailbox's address+password already works.
    webmail_hostname: str = ""
    webmail_docroot: str = "/var/lib/roundcube/public_html"

    # Phase 2 feature 6: cgroups v2 resource limits. The block device
    # IOReadBandwidthMax/IOWriteBandwidthMax apply to -- must be the whole
    # disk (e.g. /dev/vda), not a partition (e.g. /dev/vda1): the io
    # controller keys io.max by the block device's own major:minor, which
    # is the whole-disk device's, confirmed against this server's actual
    # `findmnt`/`lsblk` output rather than assumed. Set this to match
    # whatever `findmnt -no SOURCE /` resolves to on the actual deployment
    # target if it differs (e.g. /dev/sda, /dev/nvme0n1).
    cgroup_io_device: str = "/dev/vda"

    # Phase 2 feature 7: backup system
    rclone_bin: str = "/usr/bin/rclone"
    restic_bin: str = "/usr/bin/restic"
    snapshot_private_dir: str = "/var/lib/boron/snapshot-private"
    snapshot_cpu_threads: int = 2
    backup_staging_dir: str = "/var/lib/boron/backup-staging"
    backup_concurrency: int = 2

    # ssl (Phase f)
    certbot_bin: str = "/opt/boron/.venv/bin/certbot"
    letsencrypt_email: str = ""
    powerdns_credentials_file: str = "/etc/boron/ssl/powerdns-credentials.ini"

    # Phase 3 feature 1: DKIM keypair storage
    dkim_base_dir: str = "/etc/boron/dkim"

    # Run A feature 3: white-label branding assets (logo/favicon)
    branding_dir: str = "/etc/boron/branding"
    branding_max_upload_bytes: int = 2 * 1024 * 1024  # 2MB

    # Phase 3 feature 2: one-click WordPress installer
    php_cli_bin: str = "/usr/bin/php"
    wp_version_check_url: str = "https://api.wordpress.org/core/version-check/1.7/"
    wp_salt_api_url: str = "https://api.wordpress.org/secret-key/1.1/salt/"
    wp_staging_dir: str = "/var/lib/boron/wp-staging"
    wp_install_concurrency: int = 2

    # Phase 4 feature 8: Softaculous-equivalent app installer (Joomla/
    # Drupal/PrestaShop/Laravel/static) -- same staging-dir lesson as
    # wp_staging_dir (CHECKPOINT-phase3-2.md: an install helper invoked
    # via `runuser` must live somewhere the target hosting account's own
    # uid can read), kept separate from wp_staging_dir since it's a
    # different, newer feature's own scratch space, not because the two
    # locations need different permissions.
    app_staging_dir: str = "/var/lib/boron/app-staging"
    app_install_concurrency: int = 2

    # Phase 3 feature 3: phpMyAdmin auto-login. pma_token_dir is
    # deliberately NOT under /var/lib/boron (locked to
    # root:boron-api) -- see wp_staging_dir's install-helper lesson
    # in CHECKPOINT-phase3-2.md, which applies identically here: the
    # phpMyAdmin signon script runs as www-data and must be able to
    # read+delete files in this directory itself.
    pma_hostname: str = ""
    pma_docroot: str = "/usr/share/phpmyadmin"
    pma_token_dir: str = "/var/lib/boron-pma-tokens"
    pma_token_ttl_seconds: int = 900

    # Phase 7a feature 1/2: NodeJS/Python app hosting. Node versions are
    # installed side-by-side under node_base_dir/<version>/bin/{node,npm},
    # the same "several full runtime installs side by side, selected per
    # account/app at render time" pattern lsphp already uses for PHP
    # (ARCHITECTURE.md SS6) -- not a single system-wide `node` via apt,
    # which would give only one version and no per-app choice.
    # Deliberately OUTSIDE /opt/boron (the deployed-application-code
    # tree scripts/deploy.sh rsync's with --delete from the git checkout,
    # ARCHITECTURE.md SS3): a first attempt put this at
    # /opt/boron/nodejs and the very next deploy would have silently
    # deleted every installed Node runtime, since it isn't part of the
    # source repo -- caught before it happened, moved to its own sibling
    # directory instead.
    node_base_dir: str = "/opt/boron-nodejs"
    node_versions: tuple[str, ...] = ("18", "20", "22")
    default_node_version: str = "20"
    # Python has no per-app version selector in this goal (only Node does) --
    # every app venv is built with whatever `python3` this host has.
    python_bin: str = "/usr/bin/python3"
    # Shared port range for both NodeApp and PythonApp local backends that
    # OLS's Web-Server(proxy) external app connects to on 127.0.0.1 -- one
    # allocator (daemon/portalloc.py) checks both tables so a Node app and a
    # Python app can never collide on the same port.
    app_port_range_start: int = 30000
    app_port_range_end: int = 31999
    # Root-only (0700): systemd reads each unit's EnvironmentFile itself (as
    # root, before dropping to the app's own uid via User=), so decrypted
    # env vars are never written anywhere the hosting account's own uid can
    # read -- the DB row keeps only the Fernet-encrypted form (goal: "env
    # vars stored encrypted").
    app_env_dir: str = "/etc/boron/app-env"
    # Phase 7a feature 3: per-account Redis. Unix-socket only (no TCP port
    # at all, so there is no port to firewall/misconfigure) -- goal's own
    # explicit path convention.
    redis_bin: str = "/usr/bin/redis-server"
    redis_cli_bin: str = "/usr/bin/redis-cli"
    redis_run_dir: str = "/run/redis"
    redis_default_mem_mb: int = 64

    # Phase 7b feature 1: cPanel backup import. Separate staging dir from
    # backup_staging_dir/wp_staging_dir/app_staging_dir (same "own scratch
    # space per feature" convention those establish) -- holds the uploaded/
    # downloaded tarball and its extracted contents for the lifetime of one
    # import job only, cleaned up (success or failure) when the job ends.
    cpanel_import_staging_dir: str = "/var/lib/boron/cpanel-import-staging"
    cpanel_import_concurrency: int = 1
    cpanel_import_max_upload_bytes: int = 10 * 1024 * 1024 * 1024  # 10GB
    # Security-audit-2 (Medium): the compressed-upload/download cap above does
    # not bound the *decompressed* size, so a tar.gz decompression bomb could
    # exhaust the (root-owned, outside any account quota) staging disk before
    # any account limit applies. This caps the summed declared member size,
    # checked before extraction. Generous vs. a real cPanel account (bounded
    # by its own hosting quota, a few-to-tens of GB) while refusing multi-TB
    # bombs.
    cpanel_import_max_extracted_bytes: int = 50 * 1024 * 1024 * 1024  # 50GB

    # Phase 7b feature 3: email notifications. Postfix on this same server
    # relays outbound transactional mail -- no external SMTP credentials
    # needed (matches this server's own existing Postfix install, already a
    # hard dependency for hosted mail), submitted via the standard
    # MTA-on-localhost pattern every one of this project's reference panels
    # also uses for its own transactional mail.
    smtp_relay_host: str = "127.0.0.1"
    smtp_relay_port: int = 25
    notifications_default_sender: str = "boron@localhost"

    # Phase 7b feature 4: webhooks. Bounded so a slow/hanging external
    # endpoint can never stall the shared delivery worker pool indefinitely.
    webhook_delivery_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 3
    webhook_concurrency: int = 2

    # Phase 7b feature 6: staging environments.
    staging_db_prefix: str = "stg_"

    # Phase 8 feature 5: email delivery log. Postfix's mail log on Ubuntu.
    # Read by borond (root) only -- it's mode 0640 syslog:adm, so the
    # unprivileged boron-api can't read it, matching the "only the daemon
    # touches privileged files, every access is an audited RPC" invariant.
    mail_log_path: str = "/var/log/mail.log"
    mail_delivery_log_max_entries: int = 500
    # How much of the tail of the mail log to parse. Bounded so a huge,
    # un-rotated log can never make one request read gigabytes.
    mail_log_scan_max_bytes: int = 12 * 1024 * 1024

    # Phase 8 feature 6: email routing. 'backup' MX mode writes accepted
    # domains into this Postfix relay-domains map (postmap'd + reload).
    postfix_relay_domains_map: str = "/etc/postfix/boron_relay_domains"

    # Cloudflare DNS/CDN provider (docs/PLAN-cloudflare.md). The token itself
    # is a secret (secrets.env CLOUDFLARE_API_TOKEN -> cloudflare_api_token
    # property below); these are the non-secret knobs. default_dns_provider
    # selects the backend for NEWLY created zones only ("local" = PowerDNS,
    # the default until the operator explicitly flips it -- plan SS1.9);
    # existing zones migrate per-zone via explicit ops, never automatically.
    cloudflare_account_id: str = ""
    default_dns_provider: str = "local"  # local | cloudflare
    # Written by us (0600) from the same API token for certbot-dns-cloudflare
    # DNS-01 challenges -- sibling of powerdns_credentials_file above.
    cloudflare_credentials_file: str = "/etc/boron/ssl/cloudflare-credentials.ini"
    # Materialized Cloudflare edge IP ranges (GET /ips), refreshed by cron
    # (plan SS1.7); consumed by the OLS real-IP trust list, fail2ban ignoreip,
    # and (lockdown mode) UFW. Staleness is surfaced in cf.health.
    cloudflare_ranges_file: str = "/etc/boron/cloudflare-ranges.json"

    # Phase 8 features 8/9: WP-CLI + Composer, run async as the account user.
    # composer is already installed on this box (2.7.x); wp-cli.phar is fetched
    # server-wide on first use if missing ("install server-wide if missing").
    composer_bin: str = "/usr/bin/composer"
    composer_download_url: str = "https://getcomposer.org/download/latest-stable/composer.phar"
    composer_phar_fallback: str = "/usr/local/bin/composer.phar"
    wpcli_phar_path: str = "/usr/local/bin/wp-cli.phar"
    wpcli_download_url: str = "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
    command_run_concurrency: int = 3
    command_run_timeout_seconds: int = 600

    # File manager: FileBrowser Quantum (github.com/gtsteffaniak/filebrowser),
    # replaces the custom file manager. A single Go binary run as root (it must
    # read/write across account homes under the 711/750 perms model, exactly as
    # ARCHITECTURE.md §10 decided for the old manager), bound to loopback only —
    # never public — and reached exclusively through boron-api's
    # authenticated proxy, which injects the trusted X-Fb-User header
    # server-side. One shared source at /home + createUserDir gives each
    # proxy-authenticated account its own /home/<user> scope (see
    # docs/CHECKPOINT-filebrowser-quantum.md for why per-account sources are the
    # wrong primitive here). Config is static (FB Quantum does not hot-reload),
    # so account create/terminate never rewrites it.
    filebrowser_bin: str = "/usr/local/bin/filebrowser-quantum"
    filebrowser_config: str = "/etc/boron/filebrowser.yaml"
    filebrowser_data_dir: str = "/var/lib/boron/filebrowser"
    filebrowser_bind_host: str = "127.0.0.1"
    filebrowser_bind_port: int = 8088
    filebrowser_base_url: str = "/files"
    # The one header FB Quantum trusts for identity. boron-api strips any
    # client-supplied copy and sets it from the authenticated session.
    filebrowser_header: str = "X-Fb-User"
    filebrowser_brand: str = "Boron Files"

    # Panel update system. update_github_repo ("owner/repo") is the ONLY
    # place release downloads can come from -- empty means update checks are
    # unconfigured and the whole feature reports "not configured" rather
    # than guessing a repo. The tarball URL itself is never operator- or
    # request-supplied: it must be
    # https://github.com/{update_github_repo}/releases/download/... . The
    # official repository is the safe default; operators can explicitly set
    # this to an empty string to disable update checks.
    update_github_repo: str = "adityavs5/boronpanel"
    update_check_cache_seconds: int = 3600  # goal: cache update.check 1hr
    update_download_dir: str = "/var/lib/boron/update-staging"
    update_max_download_bytes: int = 500 * 1024 * 1024  # 500MB (releases are ~4MB)
    update_backup_dir: str = "/var/backups/boron"
    # Versioned install dirs live at {update_versions_root}/boron-X.Y.Z
    # with {update_live_dir} an atomically-swapped symlink to the active one.
    update_versions_root: str = "/opt"
    update_live_dir: str = "/opt/boron"
    update_keep_old_days: int = 3  # rollback window; older version dirs pruned
    # Pre-flight "abort if test suite failing" (goal 4a). Runs the LIVE
    # install's own pytest suite before touching anything -- slow (~13min)
    # but explicitly required; disable only via boron.toml.
    update_preflight_tests: bool = True
    update_preflight_min_free_mb: int = 2048

    secrets: dict[str, str] = field(default_factory=dict)

    @property
    def filebrowser_internal_url(self) -> str:
        """Base URL boron-api's proxy forwards to (loopback only)."""
        return f"http://{self.filebrowser_bind_host}:{self.filebrowser_bind_port}"

    @property
    def session_secret(self) -> str:
        return self.secrets.get("SESSION_SECRET", INSECURE_SESSION_SECRET_DEFAULT)

    @property
    def mariadb_admin_password(self) -> str:
        return self.secrets.get("MARIADB_DAEMON_PASSWORD", "")

    @property
    def powerdns_api_key(self) -> str:
        return self.secrets.get("POWERDNS_API_KEY", "")

    @property
    def cloudflare_api_token(self) -> str:
        return self.secrets.get("CLOUDFLARE_API_TOKEN", "")

    @property
    def app_env_key(self) -> str:
        """Fernet key encrypting NodeApp/PythonApp env vars at rest
        (daemon/appcrypto.py). Empty when secrets.env hasn't been
        bootstrapped with one yet -- appcrypto.get_key() generates and
        persists one on first use rather than requiring a manual step,
        the same auto-provisioning appcrypto.py documents."""
        return self.secrets.get("APP_ENV_KEY", "")


# The session-signing key falls back to this well-known value only so the
# codebase can be imported/tested/run in dev without a secrets file. It signs
# session cookies AND the 2FA-pending token, so if it ever reached production
# anyone could forge an admin session -- require_secure_session_secret() below
# makes both real entrypoints refuse to start while it (or an empty/weak value)
# is in effect. Kept out of import-time so tests/tooling can still import this
# module with no secrets file present.
INSECURE_SESSION_SECRET_DEFAULT = "dev-insecure-change-me"
MIN_SESSION_SECRET_LENGTH = 32


def require_secure_session_secret() -> None:
    """Fail loudly at process startup if SESSION_SECRET is missing, still the
    built-in dev default, or too short to be a real key. Called by BOTH
    boron-api (ASGI startup) and borond (main) so a misconfigured or
    empty secrets file is a hard boot failure, never a silent, forgeable
    session-signing default."""
    secret = settings.secrets.get("SESSION_SECRET", "")
    if not secret or secret == INSECURE_SESSION_SECRET_DEFAULT:
        raise RuntimeError(
            "SESSION_SECRET is unset or still the insecure built-in dev default. "
            "Set a strong random SESSION_SECRET in the secrets file before starting."
        )
    if len(secret) < MIN_SESSION_SECRET_LENGTH:
        raise RuntimeError(
            f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters of entropy."
        )


def load_settings() -> Settings:
    overrides: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            overrides = tomllib.load(f)
    settings = Settings(**overrides)
    settings.secrets = {**_load_secrets(API_SECRETS_PATH), **_load_secrets(SECRETS_PATH)}
    return settings


settings = load_settings()
