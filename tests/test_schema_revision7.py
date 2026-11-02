"""Schema revision 7: Force HTTPS column and the one-time merge of stored
disable_functions overrides with the default list."""
from sqlalchemy import create_engine, text

from daemon.phpdirectives import DEFAULT_DISABLE_FUNCTIONS
from shared.migrations import CURRENT_SCHEMA_VERSION, REVISIONS, apply_schema_migrations


def _legacy_database(path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE domains (id INTEGER PRIMARY KEY, account_id INTEGER, domain VARCHAR(253), "
            "kind VARCHAR(16), docroot VARCHAR(512), ssl_status VARCHAR(16))"
        ))
        conn.execute(text(
            "INSERT INTO domains (id, account_id, domain, kind, docroot, ssl_status) "
            "VALUES (1, 1, 'example.test', 'primary', '/home/a/public_html', 'active')"
        ))
        conn.execute(text(
            "CREATE TABLE php_function_overrides (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, "
            "domain VARCHAR(253), disable_functions VARCHAR(2000) NOT NULL, updated_at DATETIME)"
        ))
        for row_id, value in ((1, "phpinfo"), (2, ""), (3, "exec,mail,shell_exec")):
            conn.execute(text(
                "INSERT INTO php_function_overrides (id, account_id, domain, disable_functions) "
                "VALUES (:id, :account, NULL, :value)"
            ), {"id": row_id, "account": row_id, "value": value})
    return engine


def test_revision_7_is_the_current_additive_revision():
    assert CURRENT_SCHEMA_VERSION == 7
    assert REVISIONS[-1].revision == 7 and REVISIONS[-1].compatibility == "additive"


def test_upgrade_adds_force_https_off_and_merges_old_overrides_with_the_default(tmp_path):
    """Overrides stored before this release were never enforced (php.ini's
    default applied), and are now enforced and replace the default: merging
    them once keeps every account at least as restricted as before."""
    engine = _legacy_database(tmp_path / "panel.db")
    apply_schema_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT force_https FROM domains WHERE id = 1")).scalar_one() == 0
        merged = dict(conn.execute(text("SELECT id, disable_functions FROM php_function_overrides")).all())
    default = set(DEFAULT_DISABLE_FUNCTIONS)
    assert merged[1] == ",".join(sorted(default | {"phpinfo"}))
    assert merged[2] == ",".join(sorted(default))
    assert merged[3] == ",".join(sorted(default | {"mail"}))


def test_values_saved_after_the_upgrade_keep_replace_semantics(tmp_path):
    engine = _legacy_database(tmp_path / "panel.db")
    apply_schema_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE php_function_overrides SET disable_functions = 'phpinfo' WHERE id = 1"))
        conn.execute(text(
            "INSERT INTO domains (id, account_id, domain, kind, docroot, ssl_status) "
            "VALUES (2, 1, 'new.test', 'addon', '/home/a/new', 'none')"
        ))
    apply_schema_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT disable_functions FROM php_function_overrides WHERE id = 1")).scalar_one() == "phpinfo"
        # Rows written by an older release (no force_https in its INSERT) get the default.
        assert conn.execute(text("SELECT force_https FROM domains WHERE id = 2")).scalar_one() == 0
