"""Install-profile capabilities shared by the installer, API and UI.

Existing installations predate profiles, so ``full`` is the compatibility
default.  Capabilities are deliberately coarse: they describe whether a
feature family was installed, not whether a particular service is healthy.
"""
from __future__ import annotations

from dataclasses import dataclass


PROFILE_COMPONENTS: dict[str, frozenset[str]] = {
    # The minimal profile is intentionally web/PHP only. Database, mail and
    # DNS are separate capabilities rather than silently installed services.
    "minimal": frozenset({"web", "php", "ssl"}),
    "standard": frozenset({
        "web", "database", "php", "ssl", "dns", "mail", "ftp",
        "fail2ban",
    }),
    "full": frozenset({
        "web", "database", "php", "ssl", "dns", "mail", "ftp",
        "fail2ban", "apps", "redis", "wordpress", "backups", "waf",
        "malware", "geoip",
    }),
}


def validate_profile(value: str | None) -> str:
    profile = str(value or "full").strip().lower()
    if profile not in PROFILE_COMPONENTS:
        choices = ", ".join(PROFILE_COMPONENTS)
        raise ValueError(f"install profile must be one of: {choices}")
    return profile


def capabilities_for_profile(value: str | None) -> tuple[str, ...]:
    return tuple(sorted(PROFILE_COMPONENTS[validate_profile(value)]))


def component_enabled(profile: str | None, component: str) -> bool:
    return component in PROFILE_COMPONENTS[validate_profile(profile)]


@dataclass(frozen=True)
class ResourceTuning:
    memory_gb: int
    cpu_count: int
    mariadb_buffer_pool_mb: int
    mariadb_max_connections: int
    ols_workers: int
    ols_max_connections: int
    php_memory_limit_mb: int
    php_upload_limit_mb: int
    redis_maxmemory_mb: int

    def as_dict(self) -> dict[str, int]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def calculate_resource_tuning(memory_bytes: int, cpu_count: int) -> ResourceTuning:
    """Return conservative defaults for a mixed hosting workload.

    The model reserves memory for the OS, OLS/PHP and mail/DNS before sizing
    MariaDB. Values are bounded so small VPSes remain bootable and large hosts
    do not receive unexpectedly aggressive settings.
    """
    if memory_bytes <= 0 or cpu_count <= 0:
        raise ValueError("memory and CPU counts must be positive")
    memory_gb = max(1, int(memory_bytes // (1024 ** 3)))
    cpus = max(1, min(int(cpu_count), 64))
    if memory_gb <= 1:
        buffer_mb, connections, php_mb, upload_mb, redis_mb = 128, 40, 128, 64, 32
    elif memory_gb <= 2:
        buffer_mb, connections, php_mb, upload_mb, redis_mb = 384, 75, 192, 64, 64
    elif memory_gb <= 4:
        buffer_mb, connections, php_mb, upload_mb, redis_mb = 1024, 125, 256, 128, 128
    elif memory_gb <= 8:
        buffer_mb, connections, php_mb, upload_mb, redis_mb = 2048, 200, 384, 128, 256
    else:
        buffer_mb = min(8192, int(memory_gb * 1024 * 0.35))
        connections = min(400, 200 + (memory_gb - 8) * 10)
        php_mb, upload_mb = 512, 256
        redis_mb = min(1024, memory_gb * 32)
    workers = min(cpus, 16)
    return ResourceTuning(
        memory_gb=memory_gb,
        cpu_count=cpus,
        mariadb_buffer_pool_mb=buffer_mb,
        mariadb_max_connections=connections,
        ols_workers=workers,
        ols_max_connections=max(500, min(10000, memory_gb * 750)),
        php_memory_limit_mb=php_mb,
        php_upload_limit_mb=upload_mb,
        redis_maxmemory_mb=redis_mb,
    )
