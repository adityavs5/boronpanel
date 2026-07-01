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


def load_settings() -> Settings:
    overrides: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            overrides = tomllib.load(f)
    settings = Settings(**overrides)
    settings.secrets = {**_load_secrets(API_SECRETS_PATH), **_load_secrets(SECRETS_PATH)}
    return settings


settings = load_settings()
