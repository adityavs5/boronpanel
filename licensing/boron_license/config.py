"""Runtime settings, read from the environment (systemd EnvironmentFile)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg:///boron_license"
    signing_key_path: Path = Path("/etc/boron-license/license-signing.key")
    # How long a panel may run on a cached document without a successful check.
    grace_days: int = 14
    # Only these peers may supply the client address in X-Real-IP (nginx).
    trusted_proxies: frozenset[str] = field(default_factory=lambda: frozenset({"127.0.0.1", "::1"}))


def load() -> Settings:
    return Settings(
        database_url=os.environ.get("BORON_LICENSE_DATABASE_URL", Settings.database_url),
        signing_key_path=Path(os.environ.get("BORON_LICENSE_SIGNING_KEY", str(Settings.signing_key_path))),
        grace_days=int(os.environ.get("BORON_LICENSE_GRACE_DAYS", str(Settings.grace_days))),
    )
