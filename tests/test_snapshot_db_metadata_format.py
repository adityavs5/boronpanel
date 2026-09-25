import json

import pytest

from daemon import snapshot_db_metadata as metadata
from shared.validation import ValidationError


HASH = "*" + "A" * 40


def _database():
    return {
        "name": "alpha_wp", "user": "alpha_wp", "host": "localhost",
        "plugin": "mysql_native_password", "password_hash": HASH,
        "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
    }


def test_format_two_round_trips_independent_users_hosts_and_privileges(tmp_path):
    payload = {
        "format": 2, "username": "alpha", "databases": [_database()],
        "users": [
            {"user": "alpha_wp", "host": "localhost", "plugin": "mysql_native_password", "password_hash": HASH},
            {"user": "alpha_reader", "host": "203.0.113.4", "plugin": "mysql_native_password", "password_hash": HASH},
        ],
        "grants": [
            {"database": "alpha_wp", "user": "alpha_wp", "host": "localhost", "preset": "all", "privileges": []},
            {"database": "alpha_wp", "user": "alpha_reader", "host": "203.0.113.4", "preset": "read_only", "privileges": []},
        ],
    }
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload))
    restored = metadata.read_metadata(path, "alpha")
    assert restored["alpha_wp"]["user"] == "alpha_wp"
    assert {item["host"] for item in restored.users} == {"localhost", "203.0.113.4"}
    assert restored.grants[1]["privileges"] == ["SELECT"]


def test_format_two_rejects_wildcard_remote_host(tmp_path):
    payload = {
        "format": 2, "username": "alpha", "databases": [_database()],
        "users": [{"user": "alpha_wp", "host": "%", "plugin": "mysql_native_password", "password_hash": HASH}],
        "grants": [],
    }
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValidationError):
        metadata.read_metadata(path, "alpha")


def test_format_one_remains_readable(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"format": 1, "username": "alpha", "databases": [_database()]}))
    restored = metadata.read_metadata(path, "alpha")
    assert restored.grants[0]["preset"] == "all"
    assert restored.users[0]["host"] == "localhost"
