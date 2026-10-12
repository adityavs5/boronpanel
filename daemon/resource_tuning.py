"""Preview and explicitly apply RAM/CPU-aware hosting-stack tuning."""
from __future__ import annotations

import difflib
import os
import re
import shutil
import time
from pathlib import Path

from daemon import ols
from daemon.procutil import run
from shared.config import settings
from shared.install_profiles import calculate_resource_tuning, component_enabled
from shared.validation import ValidationError

MYSQL_TARGET = Path("/etc/mysql/mariadb.conf.d/99-boron-resource-tuning.cnf")
REDIS_TARGET = Path("/etc/redis/boron-resource-tuning.conf")
REDIS_MAIN = Path("/etc/redis/redis.conf")


def _memory_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("Could not read total memory")


def recommendation(memory_bytes: int | None = None, cpu_count: int | None = None) -> dict:
    value = calculate_resource_tuning(memory_bytes or _memory_bytes(), cpu_count or os.cpu_count() or 1)
    return value.as_dict()


def _mysql_content(values: dict) -> str:
    return (
        "# Managed by Boron resource tuning.\n[mariadb]\n"
        f"innodb_buffer_pool_size = {values['mariadb_buffer_pool_mb']}M\n"
        f"max_connections = {values['mariadb_max_connections']}\n"
    )


def _php_content(values: dict) -> str:
    return (
        "; Managed by Boron resource tuning.\n"
        f"memory_limit = {values['php_memory_limit_mb']}M\n"
        f"upload_max_filesize = {values['php_upload_limit_mb']}M\n"
        f"post_max_size = {values['php_upload_limit_mb']}M\n"
    )


def _redis_content(values: dict) -> str:
    return (
        "# Managed by Boron resource tuning.\n"
        f"maxmemory {values['redis_maxmemory_mb']}mb\n"
        "maxmemory-policy allkeys-lru\n"
    )


def _php_targets() -> list[Path]:
    result = []
    for version in settings.php_versions:
        compact = version.replace(".", "")
        directory = Path(f"/usr/local/lsws/lsphp{compact}/etc/php/{version}/litespeed/conf.d")
        if directory.parent.exists():
            result.append(directory / "99-boron-resource-tuning.ini")
    return result


def _diff(path: Path, content: str) -> dict:
    before = path.read_text() if path.is_file() else ""
    lines = list(difflib.unified_diff(
        before.splitlines(), content.splitlines(),
        fromfile=str(path) + ":current", tofile=str(path) + ":proposed", lineterm="",
    ))
    return {"path": str(path), "changed": before != content, "diff": lines[:120]}


def preview(params: dict | None = None) -> dict:
    params = params or {}
    values = recommendation(params.get("memory_bytes"), params.get("cpu_count"))
    changes = []
    if component_enabled(settings.install_profile, "database"):
        changes.append(_diff(MYSQL_TARGET, _mysql_content(values)))
    changes.extend(_diff(path, _php_content(values)) for path in _php_targets())
    if component_enabled(settings.install_profile, "redis") and REDIS_MAIN.exists():
        changes.append(_diff(REDIS_TARGET, _redis_content(values)))
        include = f"include {REDIS_TARGET}"
        current = REDIS_MAIN.read_text()
        if include not in current.splitlines():
            changes.append(_diff(REDIS_MAIN, current.rstrip() + "\n" + include + "\n"))
    current_ols = ols._ols_settings()
    changes.append({
        "path": "OpenLiteSpeed settings",
        "changed": current_ols.get("max_connections") != values["ols_max_connections"],
        "diff": [
            f"- max_connections = {current_ols.get('max_connections')}",
            f"+ max_connections = {values['ols_max_connections']}",
            f"  worker recommendation = {values['ols_workers']} (OpenLiteSpeed controls licensed worker count)",
        ],
    })
    return {
        "recommendation": values,
        "changes": changes,
        "requires_confirmation": True,
        "message": "Review this diff. Applying it may briefly restart MariaDB, Redis, and OpenLiteSpeed.",
    }


def _write(path: Path, content: str, backup_root: Path, touched: list[tuple[Path, Path | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = backup_root / path.as_posix().lstrip("/")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(content)
    if path.exists():
        metadata = path.stat()
        os.chmod(temporary, metadata.st_mode & 0o7777)
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
    else:
        os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    touched.append((path, backup))


def _restore(touched: list[tuple[Path, Path | None]]) -> None:
    for path, backup in reversed(touched):
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            shutil.copy2(backup, path)


def apply(params: dict) -> dict:
    if params.get("confirm") is not True:
        raise ValidationError("Review the tuning diff and confirm before applying it")
    proposed = preview(params)
    values = proposed["recommendation"]
    backup_root = Path(settings.backup_dir) / "resource-tuning" / str(time.time_ns())
    touched: list[tuple[Path, Path | None]] = []
    services: list[str] = []
    mariadb_configured = component_enabled(settings.install_profile, "database")
    redis_configured = False
    previous_ols = ols._ols_settings()
    ols_changed = False
    try:
        if mariadb_configured:
            _write(MYSQL_TARGET, _mysql_content(values), backup_root, touched)
        for path in _php_targets():
            _write(path, _php_content(values), backup_root, touched)
        if component_enabled(settings.install_profile, "redis") and REDIS_MAIN.exists():
            _write(REDIS_TARGET, _redis_content(values), backup_root, touched)
            include = f"include {REDIS_TARGET}"
            current = REDIS_MAIN.read_text()
            if include not in current.splitlines():
                _write(REDIS_MAIN, current.rstrip() + "\n" + include + "\n", backup_root, touched)
            redis_configured = True
        if mariadb_configured:
            mysql_check = run(["mariadbd", "--verbose", "--help"], timeout=60)
            if not mysql_check.ok:
                raise RuntimeError("MariaDB rejected the proposed configuration")
        if redis_configured:
            redis_check = run([settings.redis_bin, str(REDIS_MAIN), "--test-memory", "2"], timeout=30)
            # Older Redis does not support a config-only validator.  Exit 0 or
            # a normal memory-test report is accepted; syntax errors are not.
            combined = (redis_check.stdout + redis_check.stderr).lower()
            if not redis_check.ok and "test-memory" not in combined:
                raise RuntimeError("Redis rejected the proposed configuration")
        ols.update_admin_settings({**previous_ols, "max_connections": values["ols_max_connections"], "max_ssl_connections": values["ols_max_connections"]})
        ols_changed = True
        services.append("openlitespeed")
        if mariadb_configured:
            mysql_restart = run(["systemctl", "restart", "mariadb"], timeout=120)
            if not mysql_restart.ok:
                raise RuntimeError("MariaDB did not restart with the proposed tuning")
            services.append("mariadb")
        if redis_configured:
            redis_restart = run(["systemctl", "restart", "redis-server"], timeout=60)
            if not redis_restart.ok:
                raise RuntimeError("Redis did not restart with the proposed tuning")
            services.append("redis")
    except Exception:
        _restore(touched)
        if ols_changed:
            try:
                ols.update_admin_settings(previous_ols)
            except Exception:
                pass
        if mariadb_configured:
            run(["systemctl", "restart", "mariadb"], timeout=120)
        if redis_configured:
            run(["systemctl", "restart", "redis-server"], timeout=60)
        raise
    return {
        "status": "applied", "recommendation": values,
        "services_restarted": services, "backup_path": str(backup_root),
    }
