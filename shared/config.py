"""Forgehost configuration loading.

Non-secret config lives in /etc/forgehost/forgehost.toml. Secrets are split
across two files by who needs them (privilege separation applies to secrets
storage too, not just to runtime process boundaries):

  - /etc/forgehost/secrets.env (0600, root-only) -- MariaDB admin
    credentials, PowerDNS API key. Only forgehostd (root) ever reads this.
  - /etc/forgehost/api-secrets.env (0640, root:forgehost-api) --
    SESSION_SECRET only, the one secret forgehost-api genuinely needs (to
    verify signed session cookies). Phase h originally had forgehost-api
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

CONFIG_PATH = Path(os.environ.get("FORGEHOST_CONFIG", "/etc/forgehost/forgehost.toml"))
SECRETS_PATH = Path(os.environ.get("FORGEHOST_SECRETS", "/etc/forgehost/secrets.env"))
API_SECRETS_PATH = Path(os.environ.get("FORGEHOST_API_SECRETS", "/etc/forgehost/api-secrets.env"))


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
    db_path: str = "/var/lib/forgehost/forgehost.db"
    backup_dir: str = "/var/lib/forgehost/backups"
    log_dir: str = "/var/log/forgehost"
    rpc_socket: str = "/run/forgehost/provisiond.sock"
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
    api_bind_port: int = 9443

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

    # mariadb (control DB for hosted accounts + forgehost_mail schema)
    mariadb_socket: str = "/run/mysqld/mysqld.sock"
    # Deliberately not the bare MySQL "root" account (ARCHITECTURE.md SS4) --
    # a dedicated admin-equivalent user the daemon authenticates as, so root
    # itself can keep a password nobody but the operator knows.
    mariadb_admin_user: str = "forgehost_daemon"

    # powerdns
    powerdns_api_url: str = "http://127.0.0.1:8081/api/v1"
    powerdns_server_id: str = "localhost"
    # This server's own public IP, used as the default A/NS-glue target when
    # Forgehost creates a new zone. Set explicitly in forgehost.toml --
    # deliberately not auto-detected at import time (a network call as a
    # config-loading side effect is surprising and fragile).
    server_public_ip: str = ""

    # mail (Phase e) -- Phase e v1 explicitly did not build webmail, this
    # was a link-out placeholder. Phase 2 feature 3 now deploys Roundcube
    # server-wide, so this is set to that real URL (forgehost.toml);
    # left blank it still degrades gracefully to "no webmail configured".
    webmail_url: str = ""

    # Phase 2 feature 3: the single hostname Roundcube's own OLS vhost
    # answers on -- one shared webmail install for every hosted mail
    # domain, not one per account (RESEARCH.md/goal: "deploy once
    # server-wide"). Login itself needs no per-account wiring at all: it's
    # plain IMAP/SMTP auth against Dovecot/Postfix, so any existing
    # Forgehost mailbox's address+password already works.
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
    backup_staging_dir: str = "/var/lib/forgehost/backup-staging"
    backup_concurrency: int = 2

    # ssl (Phase f)
    certbot_bin: str = "/opt/forgehost/.venv/bin/certbot"
    letsencrypt_email: str = ""
    powerdns_credentials_file: str = "/etc/forgehost/ssl/powerdns-credentials.ini"

    # Phase 3 feature 1: DKIM keypair storage
    dkim_base_dir: str = "/etc/forgehost/dkim"

    # Phase 3 feature 2: one-click WordPress installer
    php_cli_bin: str = "/usr/bin/php"
    wp_version_check_url: str = "https://api.wordpress.org/core/version-check/1.7/"
    wp_salt_api_url: str = "https://api.wordpress.org/secret-key/1.1/salt/"
    wp_staging_dir: str = "/var/lib/forgehost/wp-staging"
    wp_install_concurrency: int = 2

    # Phase 4 feature 8: Softaculous-equivalent app installer (Joomla/
    # Drupal/PrestaShop/Laravel/static) -- same staging-dir lesson as
    # wp_staging_dir (CHECKPOINT-phase3-2.md: an install helper invoked
    # via `runuser` must live somewhere the target hosting account's own
    # uid can read), kept separate from wp_staging_dir since it's a
    # different, newer feature's own scratch space, not because the two
    # locations need different permissions.
    app_staging_dir: str = "/var/lib/forgehost/app-staging"
    app_install_concurrency: int = 2

    # Phase 3 feature 3: phpMyAdmin auto-login. pma_token_dir is
    # deliberately NOT under /var/lib/forgehost (locked to
    # root:forgehost-api) -- see wp_staging_dir's install-helper lesson
    # in CHECKPOINT-phase3-2.md, which applies identically here: the
    # phpMyAdmin signon script runs as www-data and must be able to
    # read+delete files in this directory itself.
    pma_hostname: str = ""
    pma_docroot: str = "/usr/share/phpmyadmin"
    pma_token_dir: str = "/var/lib/forgehost-pma-tokens"
    pma_token_ttl_seconds: int = 900

    # Phase 7a feature 1/2: NodeJS/Python app hosting. Node versions are
    # installed side-by-side under node_base_dir/<version>/bin/{node,npm},
    # the same "several full runtime installs side by side, selected per
    # account/app at render time" pattern lsphp already uses for PHP
    # (ARCHITECTURE.md SS6) -- not a single system-wide `node` via apt,
    # which would give only one version and no per-app choice.
    # Deliberately OUTSIDE /opt/forgehost (the deployed-application-code
    # tree scripts/deploy.sh rsync's with --delete from the git checkout,
    # ARCHITECTURE.md SS3): a first attempt put this at
    # /opt/forgehost/nodejs and the very next deploy would have silently
    # deleted every installed Node runtime, since it isn't part of the
    # source repo -- caught before it happened, moved to its own sibling
    # directory instead.
    node_base_dir: str = "/opt/forgehost-nodejs"
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
    app_env_dir: str = "/etc/forgehost/app-env"
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
    cpanel_import_staging_dir: str = "/var/lib/forgehost/cpanel-import-staging"
    cpanel_import_concurrency: int = 1
    cpanel_import_max_upload_bytes: int = 10 * 1024 * 1024 * 1024  # 10GB

    # Phase 7b feature 3: email notifications. Postfix on this same server
    # relays outbound transactional mail -- no external SMTP credentials
    # needed (matches this server's own existing Postfix install, already a
    # hard dependency for hosted mail), submitted via the standard
    # MTA-on-localhost pattern every one of this project's reference panels
    # also uses for its own transactional mail.
    smtp_relay_host: str = "127.0.0.1"
    smtp_relay_port: int = 25
    notifications_default_sender: str = "forgehost@localhost"

    # Phase 7b feature 4: webhooks. Bounded so a slow/hanging external
    # endpoint can never stall the shared delivery worker pool indefinitely.
    webhook_delivery_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 3
    webhook_concurrency: int = 2

    # Phase 7b feature 6: staging environments.
    staging_db_prefix: str = "stg_"

    secrets: dict[str, str] = field(default_factory=dict)

    @property
    def session_secret(self) -> str:
        return self.secrets.get("SESSION_SECRET", "dev-insecure-change-me")

    @property
    def mariadb_admin_password(self) -> str:
        return self.secrets.get("MARIADB_DAEMON_PASSWORD", "")

    @property
    def powerdns_api_key(self) -> str:
        return self.secrets.get("POWERDNS_API_KEY", "")

    @property
    def app_env_key(self) -> str:
        """Fernet key encrypting NodeApp/PythonApp env vars at rest
        (daemon/appcrypto.py). Empty when secrets.env hasn't been
        bootstrapped with one yet -- appcrypto.get_key() generates and
        persists one on first use rather than requiring a manual step,
        the same auto-provisioning appcrypto.py documents."""
        return self.secrets.get("APP_ENV_KEY", "")


def load_settings() -> Settings:
    overrides: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            overrides = tomllib.load(f)
    settings = Settings(**overrides)
    settings.secrets = {**_load_secrets(API_SECRETS_PATH), **_load_secrets(SECRETS_PATH)}
    return settings


settings = load_settings()
