"""Forgehost configuration loading.

Non-secret config lives in /etc/forgehost/forgehost.toml. Secrets (MariaDB
admin credentials, PowerDNS API key, session signing key) live in
/etc/forgehost/secrets.env, a 0600 root-owned file, loaded as plain
KEY=VALUE lines (no shell expansion, no execution).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("FORGEHOST_CONFIG", "/etc/forgehost/forgehost.toml"))
SECRETS_PATH = Path(os.environ.get("FORGEHOST_SECRETS", "/etc/forgehost/secrets.env"))


def _load_secrets(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    if not path.exists():
        return secrets
    for line in path.read_text().splitlines():
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
    api_bind_host: str = "127.0.0.1"
    api_bind_port: int = 9443

    # accounts
    php_versions: tuple[str, ...] = ("8.1", "8.3")
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
    settings.secrets = _load_secrets(SECRETS_PATH)
    return settings


settings = load_settings()
