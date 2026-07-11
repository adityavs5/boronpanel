from __future__ import annotations

import os
import pwd as real_pwd

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import staging
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, StagingEnvironment


# --- small pure helpers -------------------------------------------------


def test_staging_domain_for():
    assert staging._staging_domain_for("example.com") == "staging.example.com"


def test_is_wordpress_true_when_wp_config_present(tmp_path):
    (tmp_path / "wp-config.php").write_text("<?php\n")
    assert staging._is_wordpress(str(tmp_path)) is True


def test_is_wordpress_false_when_absent(tmp_path):
    assert staging._is_wordpress(str(tmp_path)) is False


def test_source_db_name_parses_wp_config(tmp_path):
    (tmp_path / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'prod_shop');\n")
    assert staging._source_db_name(str(tmp_path)) == "prod_shop"


def test_source_db_name_raises_when_missing_define(tmp_path):
    (tmp_path / "wp-config.php").write_text("<?php\n// nothing here\n")
    with pytest.raises(staging.StagingError):
        staging._source_db_name(str(tmp_path))


# --- wp-config rewriting ------------------------------------------------


def test_rewrite_staging_wp_config_replaces_db_constants(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text(
        "<?php\n"
        "define('DB_NAME', 'prod_db');\n"
        "define('DB_USER', 'prod_user');\n"
        "define('DB_PASSWORD', 'prodpass');\n"
        "define('DB_HOST', 'localhost');\n"
        "$table_prefix = 'wp_custom_';\n"
        "define('AUTH_KEY', 'real-salt-value');\n"
    )
    staging._rewrite_staging_wp_config(str(docroot), "demo1_stg", "demo1_stg", "s3cret", "https://staging.example.com")
    content = (docroot / "wp-config.php").read_text()
    assert "demo1_stg" in content
    assert "s3cret" in content
    assert "prod_db" not in content
    assert "prodpass" not in content
    # Salts/table prefix must survive untouched.
    assert "wp_custom_" in content
    assert "real-salt-value" in content


_FULL_WP_CONFIG = (
    "<?php\n"
    "define('DB_NAME', 'prod_db');\n"
    "define('DB_USER', 'prod_user');\n"
    "define('DB_PASSWORD', 'prodpass');\n"
    "define('DB_HOST', 'localhost');\n"
)


def test_rewrite_staging_wp_config_injects_home_and_siteurl(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text(_FULL_WP_CONFIG)
    staging._rewrite_staging_wp_config(str(docroot), "demo1_stg", "demo1_stg", "pw", "https://staging.example.com")
    content = (docroot / "wp-config.php").read_text()
    assert "WP_HOME" in content
    assert "WP_SITEURL" in content
    assert "https://staging.example.com" in content
    # The injected defines must come before any pre-existing content (PHP's
    # define() is first-caller-wins) -- confirm ordering, not just presence.
    assert content.index("WP_HOME") < content.index("DB_NAME")


def test_rewrite_staging_wp_config_password_with_dollar_sign_not_corrupted(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text(_FULL_WP_CONFIG)
    staging._rewrite_staging_wp_config(str(docroot), "db", "user", "s3cret$LU!", "https://staging.example.com")
    content = (docroot / "wp-config.php").read_text()
    assert "s3cret$LU!" in content
    assert '"s3cret$LU!"' not in content  # must be single-quoted (php_str), not double-quoted


def test_rewrite_staging_wp_config_raises_when_a_db_constant_is_missing(tmp_path):
    """Regression test for a real bug found by adversarial review (not
    live testing -- live verification was blocked this pass): the
    original implementation silently left whichever DB_* constants it
    couldn't find untouched, which could leave a staging wp-config.php
    with e.g. the OLD production DB_NAME alongside the NEW staging
    DB_USER/DB_PASSWORD -- a combination that fails to connect at all,
    with no clear error pointing at why."""
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'prod_db');\n")  # missing USER/PASSWORD/HOST
    with pytest.raises(staging.StagingError, match="DB_USER"):
        staging._rewrite_staging_wp_config(str(docroot), "demo1_stg", "demo1_stg", "pw", "https://staging.example.com")


def test_rewrite_staging_wp_config_missing_file_raises(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    with pytest.raises(staging.StagingError):
        staging._rewrite_staging_wp_config(str(docroot), "db", "user", "pw", "https://staging.example.com")


# --- _copy_files: real filesystem + the docroot-permission-reassertion fix --


def test_copy_files_reasserts_docroot_permissions(isolated_db, tmp_path, monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "home_base", str(tmp_path))
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(tmp_path), "/usr/sbin/nologin"))
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: fake_pw)

    source = tmp_path / "source_docroot"
    source.mkdir()
    os.chmod(source, 0o755)  # simulates a typical production docroot
    (source / "index.php").write_text("<?php echo 'hi';")

    staging_docroot = tmp_path / "demo1" / "staging_example"
    staging_docroot.mkdir(parents=True)
    os.chmod(staging_docroot, 0o750)  # as add_domain's own ensure_docroot would have set it

    staging._copy_files(str(source), str(staging_docroot), "demo1")

    assert (staging_docroot / "index.php").read_text() == "<?php echo 'hi';"
    assert oct(os.stat(staging_docroot).st_mode & 0o777) == "0o750", (
        "copying files must not leave the staging docroot world-readable"
    )


def test_copy_files_raises_on_cp_failure(monkeypatch):
    monkeypatch.setattr(staging, "run", lambda *a, **k: type("R", (), {"ok": False, "stderr": "no such file"})())
    with pytest.raises(staging.StagingError):
        staging._copy_files("/nonexistent/source", "/nonexistent/dest", "demo1")


# --- orchestration (create/sync/get/delete), file-copy + db-clone mocked ---


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_ols(monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)
    monkeypatch.setattr(hd.ols, "remove_domain_vhost", lambda account, domain: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None: None)


@pytest.fixture()
def stub_copy_files(monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_copy_files", lambda src, dst, username: calls.append((src, dst, username)))
    return calls


@pytest.fixture()
def stub_ssl(monkeypatch):
    monkeypatch.setattr(staging.ssl, "issue_certificate", lambda params: {"status": "issued"})


@pytest.fixture()
def stub_database(monkeypatch):
    created = []
    dropped = []

    def fake_create(params):
        db_name = f"{params['username']}_{params['name']}"
        created.append(db_name)
        return {"db_name": db_name, "db_user": db_name, "password": "genpass123!"}

    def fake_drop(params):
        dropped.append(f"{params['username']}_{params['name']}")

    monkeypatch.setattr(staging.handlers_database, "create_database", fake_create)
    monkeypatch.setattr(staging.handlers_database, "drop_database", fake_drop)
    return {"created": created, "dropped": dropped}


@pytest.fixture()
def stub_db_clone(monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_dump_and_restore", lambda src, dst: calls.append((src, dst)))
    return calls


@pytest.fixture()
def account_with_domain(isolated_db, stub_sysops, tmp_path):
    ha.create_account({"username": "demo1"})
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))
    return {"docroot": docroot}


def test_create_staging_non_wordpress_files_only(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    result = staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    assert result["staging_domain"] == "staging.demo1.example"
    assert result["is_wordpress"] is False
    assert result["db_name"] is None
    assert not stub_database["created"]
    assert len(stub_copy_files) == 1

    with write_session() as session:
        row = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == "demo1.example"))
        assert row is not None
        assert row.staging_domain == "staging.demo1.example"


def _grant_db(username: str, db_name: str) -> None:
    """Record that `username` owns `db_name`, the way
    handlers_database.create_database would -- staging's source-DB ownership
    guard (security-audit-2) consults DatabaseGrant as the authoritative
    owner record."""
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        session.add(DatabaseGrant(account_id=account.id, db_name=db_name, db_user=db_name))


def test_create_staging_wordpress_clones_database(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, stub_db_clone, monkeypatch):
    _grant_db("demo1", "demo1_wp")  # the account genuinely owns its production DB
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'demo1_wp');\n")
    monkeypatch.setattr(staging, "_rewrite_staging_wp_config", lambda *a, **k: None)

    result = staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    assert result["is_wordpress"] is True
    assert result["db_name"] == "demo1_stg"
    assert stub_database["created"] == ["demo1_stg"]
    assert stub_db_clone == [("demo1_wp", "demo1_stg")]


def test_create_staging_rejects_foreign_source_database(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, stub_db_clone, monkeypatch):
    """Security-audit-2 (Critical): an account whose wp-config.php names a
    database it does NOT own (another account's DB, or the internal mail
    schema) must NOT have staging dump it. The clone runs mysqldump as the
    MariaDB admin, which can read every DB, so ownership must be enforced
    against DatabaseGrant before any dump."""
    # 'victim_wp' belongs to a different account, not demo1.
    ha.create_account({"username": "victim"})
    with write_session() as session:
        victim = session.scalar(select(Account).where(Account.username == "victim"))
        session.add(DatabaseGrant(account_id=victim.id, db_name="victim_wp", db_user="victim_wp"))
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'victim_wp');\n")
    monkeypatch.setattr(staging, "_rewrite_staging_wp_config", lambda *a, **k: None)

    with pytest.raises(staging.StagingError, match="not owned by account"):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    # Nothing from the victim's DB was ever dumped.
    assert stub_db_clone == []


def test_create_staging_rejects_duplicate(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(staging.StagingError):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})


def test_create_staging_rejects_when_staging_domain_already_a_real_domain(account_with_domain, stub_ols, stub_filesystem):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="staging.demo1.example", kind="addon", docroot="/x"))
    with pytest.raises(staging.StagingError):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})


def test_create_staging_compensates_domain_on_copy_failure(account_with_domain, stub_ols, stub_filesystem, stub_ssl, monkeypatch):
    def boom(src, dst, username):
        raise staging.StagingError("cp failed")

    monkeypatch.setattr(staging, "_copy_files", boom)

    with pytest.raises(staging.StagingError):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    with write_session() as session:
        orphan = session.scalar(select(Domain).where(Domain.domain == "staging.demo1.example"))
        assert orphan is None, "a failed staging create must not leave an orphaned staging Domain row"
        no_row = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == "demo1.example"))
        assert no_row is None


def test_create_staging_compensates_database_on_rewrite_failure(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, stub_db_clone, monkeypatch):
    _grant_db("demo1", "demo1_wp")
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'demo1_wp');\n")

    def boom(*a, **k):
        raise staging.StagingError("rewrite failed")

    monkeypatch.setattr(staging, "_rewrite_staging_wp_config", boom)

    with pytest.raises(staging.StagingError):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    assert stub_database["created"] == ["demo1_stg"]
    assert stub_database["dropped"] == ["demo1_stg"]
    with write_session() as session:
        orphan = session.scalar(select(Domain).where(Domain.domain == "staging.demo1.example"))
        assert orphan is None


def test_get_staging_reports_not_exists(account_with_domain):
    result = staging.get_staging({"username": "demo1", "domain": "demo1.example"})
    assert result == {"exists": False}


def test_get_staging_reports_existing(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    result = staging.get_staging({"username": "demo1", "domain": "demo1.example"})
    assert result["exists"] is True
    assert result["staging_url"] == "https://staging.demo1.example"


def test_delete_staging_removes_domain_database_and_row(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, monkeypatch):
    _grant_db("demo1", "demo1_wp")
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'demo1_wp');\n")
    monkeypatch.setattr(staging, "_dump_and_restore", lambda *a: None)
    monkeypatch.setattr(staging, "_rewrite_staging_wp_config", lambda *a, **k: None)
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    remove_calls = []
    monkeypatch.setattr(hd, "remove_domain", lambda params: remove_calls.append(params) or {"status": "removed"})

    staging.delete_staging({"username": "demo1", "domain": "demo1.example"})

    assert remove_calls == [{"username": "demo1", "domain": "staging.demo1.example"}]
    assert stub_database["dropped"] == ["demo1_stg"]
    with write_session() as session:
        assert session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == "demo1.example")) is None


def test_delete_staging_missing_raises(account_with_domain):
    with pytest.raises(staging.StagingError):
        staging.delete_staging({"username": "demo1", "domain": "demo1.example"})


def test_sync_staging_recopies_and_reclone_database(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, stub_db_clone, monkeypatch):
    _grant_db("demo1", "demo1_wp")
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'demo1_wp');\n")
    monkeypatch.setattr(staging, "_rewrite_staging_wp_config", lambda *a, **k: None)
    monkeypatch.setattr(staging.mariadb, "generate_password", lambda: "freshpass123!")
    set_password_calls = []
    monkeypatch.setattr(staging.mariadb, "set_password", lambda user, pw: set_password_calls.append((user, pw)))

    created = staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    stub_copy_files.clear()
    stub_db_clone.clear()

    result = staging.sync_staging({"username": "demo1", "domain": "demo1.example"})
    assert result["last_synced_at"] is not None
    assert len(stub_copy_files) == 1
    assert stub_db_clone == [("demo1_wp", created["db_name"])]
    assert set_password_calls == [(created["db_name"], "freshpass123!")]


def test_sync_staging_missing_raises(account_with_domain):
    with pytest.raises(staging.StagingError):
        staging.sync_staging({"username": "demo1", "domain": "demo1.example"})


def test_sync_staging_non_wordpress_skips_database_step(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, stub_db_clone):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    stub_copy_files.clear()
    staging.sync_staging({"username": "demo1", "domain": "demo1.example"})
    assert stub_db_clone == []


# --- account termination cleanup --------------------------------------------


def test_terminate_account_staging_deletes_bookkeeping_row(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))

    staging.terminate_account_staging(account)
    with write_session() as session:
        assert session.scalar(select(StagingEnvironment).where(StagingEnvironment.account_id == account.id)) is None


def test_terminate_account_staging_noop_when_none_exist(account_with_domain):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
    staging.terminate_account_staging(account)  # must not raise
