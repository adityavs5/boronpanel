from pathlib import Path

import pytest

from daemon import resource_tuning
from daemon.procutil import ProcResult


def test_preview_is_read_only_and_reports_diff(monkeypatch, tmp_path):
    mysql = tmp_path / "mysql.cnf"
    mysql.write_text("[mariadb]\nmax_connections = 10\n")
    monkeypatch.setattr(resource_tuning, "MYSQL_TARGET", mysql)
    monkeypatch.setattr(resource_tuning, "REDIS_MAIN", tmp_path / "missing-redis.conf")
    monkeypatch.setattr(resource_tuning, "REDIS_TARGET", tmp_path / "redis-tuning.conf")
    monkeypatch.setattr(resource_tuning, "_php_targets", lambda: [])
    monkeypatch.setattr(resource_tuning.ols, "_ols_settings", lambda: {"max_connections": 1000})
    result = resource_tuning.preview({"memory_bytes": 4 * 1024**3, "cpu_count": 2})
    assert result["requires_confirmation"] is True
    assert result["recommendation"]["mariadb_buffer_pool_mb"] == 1024
    assert any(row["changed"] for row in result["changes"])
    assert mysql.read_text() == "[mariadb]\nmax_connections = 10\n"


def test_apply_requires_confirmation():
    with pytest.raises(Exception, match="confirm"):
        resource_tuning.apply({})


def test_apply_writes_files_and_is_safe_to_repeat(monkeypatch, tmp_path):
    mysql = tmp_path / "mysql.cnf"
    php = tmp_path / "php" / "99-boron.ini"
    backup = tmp_path / "backups"
    monkeypatch.setattr(resource_tuning, "MYSQL_TARGET", mysql)
    monkeypatch.setattr(resource_tuning, "REDIS_MAIN", tmp_path / "missing-redis.conf")
    monkeypatch.setattr(resource_tuning, "_php_targets", lambda: [php])
    monkeypatch.setattr(resource_tuning.settings, "backup_dir", str(backup))
    monkeypatch.setattr(resource_tuning.ols, "_ols_settings", lambda: {
        "max_connections": 1000, "max_ssl_connections": 1000,
        "connection_timeout": 300, "keep_alive_timeout": 5,
        "max_keep_alive_requests": 10000, "gzip_level": 6,
        "brotli_level": 5, "log_keep_days": 90, "log_level": "WARN",
        "gzip_enabled": True, "brotli_enabled": True, "quic_enabled": True,
    })
    monkeypatch.setattr(resource_tuning.ols, "update_admin_settings", lambda values: {"settings": values})
    monkeypatch.setattr(resource_tuning, "run", lambda args, **k: ProcResult(args, 0, "", ""))
    params = {"confirm": True, "memory_bytes": 2 * 1024**3, "cpu_count": 2}
    first = resource_tuning.apply(params)
    first_mysql = mysql.read_text()
    second = resource_tuning.apply(params)
    assert first["status"] == second["status"] == "applied"
    assert mysql.read_text() == first_mysql
    assert "innodb_buffer_pool_size = 384M" in first_mysql
    assert "memory_limit = 192M" in php.read_text()


def test_minimal_profile_does_not_configure_or_restart_mariadb(monkeypatch, tmp_path):
    mysql = tmp_path / "mysql.cnf"
    monkeypatch.setattr(resource_tuning, "MYSQL_TARGET", mysql)
    monkeypatch.setattr(resource_tuning, "REDIS_MAIN", tmp_path / "missing-redis.conf")
    monkeypatch.setattr(resource_tuning, "_php_targets", lambda: [])
    monkeypatch.setattr(resource_tuning.settings, "install_profile", "minimal")
    monkeypatch.setattr(resource_tuning.settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(resource_tuning.ols, "_ols_settings", lambda: {
        "max_connections": 1000, "max_ssl_connections": 1000,
        "connection_timeout": 300, "keep_alive_timeout": 5,
        "max_keep_alive_requests": 10000, "gzip_level": 6,
        "brotli_level": 5, "log_keep_days": 90, "log_level": "WARN",
        "gzip_enabled": True, "brotli_enabled": True, "quic_enabled": True,
    })
    monkeypatch.setattr(resource_tuning.ols, "update_admin_settings", lambda values: {"settings": values})
    commands = []
    monkeypatch.setattr(resource_tuning, "run", lambda args, **kwargs: commands.append(args) or ProcResult(args, 0, "", ""))

    preview = resource_tuning.preview({"memory_bytes": 1024**3, "cpu_count": 1})
    result = resource_tuning.apply({"confirm": True, "memory_bytes": 1024**3, "cpu_count": 1})

    assert all(change["path"] != str(mysql) for change in preview["changes"])
    assert not mysql.exists()
    assert "mariadb" not in result["services_restarted"]
    assert not any("mariadb" in " ".join(command) for command in commands)


def test_failed_apply_restores_files_and_ols_settings(monkeypatch, tmp_path):
    mysql = tmp_path / "mysql.cnf"
    mysql.write_text("[mariadb]\nmax_connections = 77\n")
    original_ols = {
        "max_connections": 1000, "max_ssl_connections": 1000,
        "connection_timeout": 300, "keep_alive_timeout": 5,
        "max_keep_alive_requests": 10000, "gzip_level": 6,
        "brotli_level": 5, "log_keep_days": 90, "log_level": "WARN",
        "gzip_enabled": True, "brotli_enabled": True, "quic_enabled": True,
    }
    ols_updates = []
    monkeypatch.setattr(resource_tuning, "MYSQL_TARGET", mysql)
    monkeypatch.setattr(resource_tuning, "REDIS_MAIN", tmp_path / "missing-redis.conf")
    monkeypatch.setattr(resource_tuning, "_php_targets", lambda: [])
    monkeypatch.setattr(resource_tuning.settings, "install_profile", "full")
    monkeypatch.setattr(resource_tuning.settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(resource_tuning.ols, "_ols_settings", lambda: dict(original_ols))
    monkeypatch.setattr(resource_tuning.ols, "update_admin_settings", lambda values: ols_updates.append(dict(values)) or {"settings": values})

    def fake_run(args, **_kwargs):
        if args[:3] == ["systemctl", "restart", "mariadb"]:
            return ProcResult(args, 1, "", "restart failed")
        return ProcResult(args, 0, "", "")

    monkeypatch.setattr(resource_tuning, "run", fake_run)

    with pytest.raises(RuntimeError, match="MariaDB did not restart"):
        resource_tuning.apply({"confirm": True, "memory_bytes": 2 * 1024**3, "cpu_count": 2})

    assert mysql.read_text() == "[mariadb]\nmax_connections = 77\n"
    assert ols_updates[-1] == original_ols
