from daemon import mariadb
from shared.validation import ValidationError
import pytest


def test_hosted_database_grant_escapes_pattern_wildcards(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, statement):
            statements.append(statement)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(mariadb, "_connect", Connection)
    mariadb.grant_all("demo1_shop", "demo1_shop")
    assert "ON `demo1\\_shop`.*" in statements[0]
    assert "ON `demo1_shop`.*" not in statements[0]
    assert statements[-1] == "FLUSH PRIVILEGES"


def test_hosted_database_revoke_escapes_pattern_wildcards(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, statement): statements.append(statement)

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(mariadb, "_connect", Connection)
    mariadb.revoke_all("demo1_shop", "demo1_reporter")
    assert "ON `demo1\\_shop`.*" in statements[0]
    assert statements[-1] == "FLUSH PRIVILEGES"


@pytest.mark.parametrize("value", ["%", "10.%", "host_name", "db host", "x'@'localhost"])
def test_remote_database_host_rejects_patterns_and_sql_metacharacters(value):
    with pytest.raises(ValidationError):
        mariadb.validate_database_host(value)


def test_remote_database_host_accepts_only_exact_host_or_ip():
    assert mariadb.validate_database_host("DB01.Example.COM.") == "db01.example.com"
    assert mariadb.validate_database_host("203.0.113.8") == "203.0.113.8"
    assert mariadb.validate_database_host("2001:db8::5") == "2001:db8::5"


def test_custom_database_privileges_are_allowlisted_and_deduplicated():
    preset, values = mariadb.normalize_database_privileges("custom", ["select", "UPDATE", "select"])
    assert preset == "custom"
    assert values == ("SELECT", "UPDATE")
    with pytest.raises(ValidationError):
        mariadb.normalize_database_privileges("custom", ["FILE"])


def test_read_only_grant_uses_exact_remote_host(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, statement): statements.append(statement)

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(mariadb, "_connect", Connection)
    mariadb.grant_database_privileges("demo1_shop", "demo1_reader", "203.0.113.8", preset="read_only")
    assert any("GRANT SELECT ON `demo1\\_shop`.* TO 'demo1_reader'@'203.0.113.8'" == statement for statement in statements)
    assert all("%" not in statement for statement in statements)
