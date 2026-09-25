from daemon import mariadb


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
