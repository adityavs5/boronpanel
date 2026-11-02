from __future__ import annotations

import os
import pwd as real_pwd

import pytest
from testsupport.host_requirements import chown_or_skip
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import staging
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, StagingEnvironment
from shared.validation import ConflictError, NotFoundError


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


# --- _replace_files: real filesystem + the docroot-permission-reassertion fix --


def test_replace_files_reasserts_docroot_permissions(isolated_db, tmp_path, monkeypatch):
    from shared.config import settings

    probe = tmp_path / "uid-map-probe"
    probe.touch()
    chown_or_skip(probe, 5001, 5001)
    monkeypatch.setattr(settings, "home_base", str(tmp_path))
    fake_pw = real_pwd.struct_passwd(("demo1", "x", 5001, 5001, "", str(tmp_path), "/usr/sbin/nologin"))
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: fake_pw)

    source = tmp_path / "source_docroot"
    source.mkdir()
    os.chmod(source, 0o755)  # simulates a typical production docroot
    (source / "index.php").write_text("<?php echo 'hi';")

    staging_docroot = tmp_path / "demo1" / "staging_example"
    staging_docroot.mkdir(parents=True)
    os.chmod(staging_docroot, 0o750)  # as add_domain's own ensure_docroot would have set it

    from daemon.procutil import ProcResult
    import daemon.procutil as procutil
    monkeypatch.setattr(staging, 'run', _run_unprivileged)
    monkeypatch.setattr(hd, '_grant_webserver_acl', lambda *args: None)
    monkeypatch.setattr(hd.sysops, 'ensure_tmp_dir', lambda username: None)
    monkeypatch.setattr(procutil, 'run', lambda argv, **kwargs: ProcResult(argv, 0, '', ''))

    staging._replace_files(str(source), str(staging_docroot), "demo1")

    assert (staging_docroot / "index.php").read_text() == "<?php echo 'hi';"
    assert oct(os.stat(staging_docroot).st_mode & 0o777) == "0o750", (
        "copying files must not leave the staging docroot world-readable"
    )


def test_replace_files_raises_on_cp_failure(monkeypatch):
    monkeypatch.setattr(real_pwd, 'getpwnam', lambda name: real_pwd.struct_passwd((name, 'x', 5001, 5001, '', '/home/demo1', '/bin/bash')))
    monkeypatch.setattr(staging, "run", lambda argv, **k: type("R", (), {"ok": argv[0] != "cp", "stderr": "no such file"})())
    # cp(1) failing is an internal failure (502 by reference), not a
    # caller mistake whose message -- here cp's stderr -- is shown to them.
    with pytest.raises(RuntimeError) as raised:
        staging._replace_files("/nonexistent/source", "/nonexistent/dest", "demo1")
    assert not isinstance(raised.value, staging.ValidationError)


def _run_unprivileged(argv, **kwargs):
    """Execute the account-scoped command for real, without changing uid."""
    import subprocess
    from daemon.procutil import ProcResult

    assert kwargs["uid"] == kwargs["gid"] == 5001
    completed = subprocess.run(argv, capture_output=True, text=True)
    return ProcResult(argv, completed.returncode, completed.stdout, completed.stderr)


def test_replace_files_removes_staging_only_files(tmp_path, monkeypatch):
    """Regression: sync overwrote changed files but kept files that only
    existed in staging, so staging was not a copy of production."""
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", 5001, 5001, "", str(tmp_path), "/usr/sbin/nologin")))
    monkeypatch.setattr(staging, "run", _run_unprivileged)
    reasserted = []
    monkeypatch.setattr(staging.handlers_domain, "ensure_docroot", lambda username, docroot: reasserted.append(docroot))
    source = tmp_path / "public_html"
    source.mkdir()
    (source / "hello.txt").write_text("v2")
    (source / "sub").mkdir()
    (source / "sub" / ".htaccess").write_text("x")
    staging_docroot = tmp_path / "staging.example" / "public_html"
    staging_docroot.mkdir(parents=True)
    (staging_docroot / "hello.txt").write_text("v1")
    (staging_docroot / "stageonly.txt").write_text("stale")

    staging._replace_files(str(source), str(staging_docroot), "demo1")

    assert sorted(p.name for p in staging_docroot.iterdir()) == ["hello.txt", "sub"]
    assert (staging_docroot / "hello.txt").read_text() == "v2"
    assert (staging_docroot / "sub" / ".htaccess").exists()
    assert sorted(p.name for p in staging_docroot.parent.iterdir()) == ["public_html"]
    # Reasserted on the new tree before it is swapped in.
    assert reasserted == [str(staging_docroot) + ".boron-sync"]


def test_replace_files_keeps_previous_staging_when_copy_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", 5001, 5001, "", str(tmp_path), "/usr/sbin/nologin")))
    monkeypatch.setattr(staging, "run", _run_unprivileged)
    staging_docroot = tmp_path / "staging.example" / "public_html"
    staging_docroot.mkdir(parents=True)
    (staging_docroot / "keep.txt").write_text("previous")
    with pytest.raises(RuntimeError):
        staging._replace_files(str(tmp_path / "missing-source"), str(staging_docroot), "demo1")
    assert (staging_docroot / "keep.txt").read_text() == "previous"
    assert sorted(p.name for p in staging_docroot.parent.iterdir()) == ["public_html"]


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
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None, **kwargs: None)


@pytest.fixture()
def stub_copy_files(monkeypatch):
    """No file copy; the new tree's preparation and commit still run."""
    calls = []

    def replace(src, dst, username, prepare=None, commit=None):
        calls.append((src, dst, username))
        if prepare is not None:
            prepare(dst)
        if commit is not None:
            commit()

    monkeypatch.setattr(staging, "_replace_files", replace)
    return calls


@pytest.fixture()
def stub_ssl(monkeypatch):
    monkeypatch.setattr(staging.ssl, "prepare_https",
                        lambda domain, host=None: {"https": True, "certificate": "issued", "detail": None})
    monkeypatch.setattr(staging.ssl, "https_ready", lambda domain, host=None: True)


@pytest.fixture()
def stub_database(monkeypatch):
    created = []
    dropped = []

    def fake_create(params):
        db_name = f"{params['username']}_{params['name']}"
        created.append(db_name)
        _grant_db(params['username'], db_name)
        return {"db_name": db_name, "db_user": db_name, "password": "genpass123!"}

    def fake_drop(params):
        dropped.append(f"{params['username']}_{params['name']}")

    monkeypatch.setattr(staging.handlers_database, "create_database", fake_create)
    monkeypatch.setattr(staging.handlers_database, "drop_database", fake_drop)
    return {"created": created, "dropped": dropped}


@pytest.fixture()
def stub_db_clone(monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_dump_and_restore", lambda src, dst, work: calls.append((src, dst)))
    monkeypatch.setattr(staging.backup, "_dump_database", lambda name, path: path.write_bytes(b"saved " + name.encode()))
    monkeypatch.setattr(staging.backup, "_restore_database_dump", lambda name, path: calls.append(("restore", name)))
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
    with pytest.raises(ConflictError, match="already exists"):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})


def test_create_staging_rejects_when_staging_domain_already_a_real_domain(account_with_domain, stub_ols, stub_filesystem):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="staging.demo1.example", kind="addon", docroot="/x"))
    with pytest.raises(ConflictError, match="already in use"):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})


def test_create_staging_compensates_domain_on_copy_failure(account_with_domain, stub_ols, stub_filesystem, stub_ssl, monkeypatch):
    def boom(src, dst, username, **kwargs):
        raise staging.StagingError("cp failed")

    monkeypatch.setattr(staging, "_replace_files", boom)
    discarded = []
    monkeypatch.setattr(staging, "_discard_staging_files", lambda username, path: discarded.append(path))

    with pytest.raises(staging.StagingError):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    assert discarded == [f"{staging.settings.home_base}/demo1/staging.demo1.example"]

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
    discarded = []
    monkeypatch.setattr(staging, "_discard_staging_files", lambda username, path: discarded.append(path))

    assert staging.delete_staging({"username": "demo1", "domain": "demo1.example"}) == {
        "source_domain": "demo1.example", "status": "deleted"}

    assert remove_calls == [{"username": "demo1", "domain": "staging.demo1.example"}]
    assert discarded == [f"{staging.settings.home_base}/demo1/staging.demo1.example/public_html"]
    assert stub_database["dropped"] == ["demo1_stg"]
    with write_session() as session:
        assert session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == "demo1.example")) is None


def test_delete_staging_refuses_before_teardown_while_an_app_uses_it(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database, monkeypatch):
    """remove_domain would refuse the staging domain; refusing first keeps
    the row and database instead of dropping them under a live site."""
    from shared.models import NodeApp

    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(NodeApp(account_id=account.id, domain="staging.demo1.example", name="api",
                            entry_point="server.js", port=30001, node_version="20"))
    monkeypatch.setattr(hd, "remove_domain", lambda params: pytest.fail("refused before removal"))
    with pytest.raises(ConflictError, match="Node.js app 'api'"):
        staging.delete_staging({"username": "demo1", "domain": "demo1.example"})
    assert stub_database["dropped"] == []
    with write_session() as session:
        assert session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == "demo1.example")) is not None


def test_delete_staging_missing_raises(account_with_domain):
    with pytest.raises(NotFoundError):
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
    with pytest.raises(NotFoundError):
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


@pytest.mark.parametrize('ancestor', [False, True])
def test_staging_config_refuses_symlinks_and_preserves_outside_file(tmp_path, ancestor):
    outside = tmp_path / 'outside'
    outside.mkdir()
    canary = outside / 'wp-config.php'
    text = "<?php\ndefine('DB_NAME', 'victim');\ndefine('DB_USER', 'victim');\ndefine('DB_PASSWORD', 'secret');\ndefine('DB_HOST', 'localhost');\n"
    canary.write_text(text)
    site = tmp_path / 'site'
    if ancestor:
        site.symlink_to(outside, target_is_directory=True)
    else:
        site.mkdir()
        (site / 'wp-config.php').symlink_to(canary)
    with pytest.raises(staging.StagingError):
        staging._source_db_name(str(site))
    with pytest.raises(staging.StagingError):
        staging._rewrite_staging_wp_config(str(site), 'new', 'new', 'new', 'https://staging.example.com')
    assert canary.read_text() == text


def test_staging_copy_refuses_root_identity(monkeypatch):
    monkeypatch.setattr(real_pwd, 'getpwnam', lambda name: real_pwd.struct_passwd((name, 'x', 0, 0, '', '/', '/bin/bash')))
    with pytest.raises(staging.StagingError, match='system identity'):
        staging._replace_files('/source', '/destination', 'demo1')
    with pytest.raises(staging.StagingError, match='system identity'):
        staging._discard_staging_files('demo1', '/home/demo1/staging.example/public_html')


def test_sync_refuses_reassigned_staging_domain(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    staging.create_staging({'username': 'demo1', 'domain': 'demo1.example'})
    stub_copy_files.clear()
    with write_session() as session:
        victim = Account(username='victim1', uid=5002, gid=5002, status='active')
        session.add(victim)
        session.flush()
        row = session.scalar(select(Domain).where(Domain.domain == 'staging.demo1.example'))
        row.account_id = victim.id
    with pytest.raises(staging.StagingError, match='no longer exists'):
        staging.sync_staging({'username': 'demo1', 'domain': 'demo1.example'})
    assert stub_copy_files == []


def test_create_staging_refuses_a_staging_domain_as_source(account_with_domain, stub_ols, stub_filesystem, stub_copy_files, stub_ssl, stub_database):
    created = staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(staging.StagingError, match="itself a staging environment"):
        staging.create_staging({"username": "demo1", "domain": created["staging_domain"]})


# --- live finding UW H2: create, sync and rollback with real file trees -----


_WP_CONFIG = (
    "<?php\n"
    "define('DB_NAME', 'demo1_wp');\n"
    "define('DB_USER', 'demo1_wp');\n"
    "define('DB_PASSWORD', 'production-secret');\n"
    "define('DB_HOST', 'localhost');\n"
    "$table_prefix = 'wp_';\n"
)


@pytest.fixture()
def real_tree(account_with_domain, stub_ols, tmp_path, monkeypatch):
    """Real copies, swaps and removals in a temporary home (as the test
    user); add_domain, the vhost and the database stay stubbed."""
    from shared.config import settings

    home_base = tmp_path / "home"
    (home_base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(settings, "home_base", str(home_base))
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: real_pwd.struct_passwd(
        (name, "x", 5001, 5001, "", str(home_base / name), "/usr/sbin/nologin")))
    monkeypatch.setattr(staging, "run", _run_unprivileged)
    monkeypatch.setattr(hd, "ensure_docroot",
                        lambda username, docroot, domain_name=None, **kwargs: os.makedirs(docroot, exist_ok=True))
    production = account_with_domain["docroot"]
    (production / "index.php").write_text("production v1")
    (production / "wp-content" / "uploads").mkdir(parents=True)
    (production / "wp-content" / "uploads" / "logo.png").write_text("png")
    (production / "wp-config.php").write_text(_WP_CONFIG)
    _grant_db("demo1", "demo1_wp")
    return {"production": production, "site_root": home_base / "demo1" / "staging.demo1.example",
            "docroot": home_base / "demo1" / "staging.demo1.example" / "public_html"}


def test_database_dump_uses_a_private_work_directory(isolated_db, monkeypatch):
    """The dump used a mkstemp file in /tmp; the export refuses a work
    directory other users can enter ("Database work directory must be
    private and owned by the backup service"), so every create failed."""
    import stat
    from daemon import snapshot_databases
    from daemon.procutil import ProcResult

    seen = {}

    def fake_dump(argv, **kwargs):
        result_file = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--result-file="))
        config = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--defaults-file="))
        seen["work"] = os.path.dirname(result_file)
        seen["mode"] = stat.S_IMODE(os.stat(seen["work"]).st_mode)
        seen["config_beside_dump"] = os.path.dirname(config) == seen["work"]
        with open(result_file, "w") as handle:
            handle.write("-- dump\n")
        return ProcResult(argv, 0, "", "")

    monkeypatch.setattr(snapshot_databases, "validate_supported_objects", lambda name: None)
    monkeypatch.setattr(snapshot_databases, "run", fake_dump)
    restored = []
    monkeypatch.setattr(staging.backup, "_restore_database_dump",
                        lambda name, path: restored.append((name, path.read_bytes()[:2])))

    with staging._work_dir() as work:
        staging._dump_and_restore("demo1_wp", "demo1_stg", work)
        assert list(work.iterdir()) == []  # the dump is removed after the import

    assert seen["mode"] == 0o700 and seen["config_beside_dump"]
    assert restored == [("demo1_stg", bytes([0x1F, 0x8B]))]  # gzip, as backups store it
    assert not os.path.exists(seen["work"])


def test_create_and_sync_a_wordpress_staging_site(real_tree, stub_ssl, stub_database, stub_db_clone, monkeypatch):
    created = staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    docroot = real_tree["docroot"]
    assert created["staging_url"] == "https://staging.demo1.example"
    assert (docroot / "index.php").read_text() == "production v1"
    assert (docroot / "wp-content" / "uploads" / "logo.png").read_text() == "png"
    config = (docroot / "wp-config.php").read_text()
    assert "'demo1_stg'" in config and "genpass123!" in config and "production-secret" not in config
    assert config.index("define('WP_HOME', 'https://staging.demo1.example');") < config.index("DB_NAME")
    assert (real_tree["production"] / "wp-config.php").read_text() == _WP_CONFIG
    assert stub_db_clone == [("demo1_wp", "demo1_stg")]
    assert not any(path.name.startswith("public_html.boron") for path in real_tree["site_root"].iterdir())

    # Production changes; a file exists only in staging.
    (real_tree["production"] / "index.php").write_text("production v2")
    (real_tree["production"] / "new.php").write_text("new")
    (docroot / "stageonly.txt").write_text("stale")
    monkeypatch.setattr(staging.mariadb, "generate_password", lambda: "rotated-staging-pass")
    passwords = []
    monkeypatch.setattr(staging.mariadb, "set_password", lambda user, password: passwords.append((user, password)))

    synced = staging.sync_staging({"username": "demo1", "domain": "demo1.example"})

    assert synced["last_synced_at"] is not None
    assert sorted(path.name for path in docroot.iterdir()) == ["index.php", "new.php", "wp-config.php", "wp-content"]
    assert (docroot / "index.php").read_text() == "production v2"
    config = (docroot / "wp-config.php").read_text()
    assert "rotated-staging-pass" in config and "production-secret" not in config
    assert passwords == [("demo1_stg", "rotated-staging-pass")]
    assert stub_db_clone[1:] == [("demo1_wp", "demo1_stg")]


def test_failed_create_removes_the_copied_files_database_and_rows(real_tree, stub_ssl, stub_database, monkeypatch):
    def failing_import(source, target, work):
        raise RuntimeError("mysql restore failed")

    monkeypatch.setattr(staging, "_dump_and_restore", failing_import)

    with pytest.raises(RuntimeError, match="mysql restore failed"):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    assert not real_tree["site_root"].exists()
    assert stub_database["dropped"] == ["demo1_stg"]
    with write_session() as session:
        assert session.scalar(select(Domain).where(Domain.domain == "staging.demo1.example")) is None
        assert session.scalar(select(StagingEnvironment)) is None
    assert (real_tree["production"] / "index.php").read_text() == "production v1"


def test_create_refuses_a_staging_folder_that_holds_customer_files(real_tree, stub_ssl, stub_database, stub_db_clone):
    real_tree["docroot"].mkdir(parents=True)
    (real_tree["docroot"] / "notes.txt").write_text("mine")

    with pytest.raises(ConflictError, match="already contains files"):
        staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    assert (real_tree["docroot"] / "notes.txt").read_text() == "mine"
    with write_session() as session:
        assert session.scalar(select(Domain).where(Domain.domain == "staging.demo1.example")) is None


def test_create_accepts_a_folder_holding_only_provisioning_leftovers(real_tree, stub_ssl, stub_database, stub_db_clone):
    (real_tree["docroot"] / ".well-known" / "acme-challenge").mkdir(parents=True)
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    assert (real_tree["docroot"] / "index.php").read_text() == "production v1"


def test_failed_sync_keeps_the_previous_staging_site_and_database(real_tree, stub_ssl, stub_database, stub_db_clone,
                                                                  monkeypatch):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    before = (real_tree["docroot"] / "wp-config.php").read_text()
    (real_tree["production"] / "index.php").write_text("production v2")
    passwords = []
    monkeypatch.setattr(staging.mariadb, "set_password", lambda user, password: passwords.append(password))

    def failing_import(source, target, work):
        raise RuntimeError("mysql restore failed")

    monkeypatch.setattr(staging, "_dump_and_restore", failing_import)
    with pytest.raises(RuntimeError, match="mysql restore failed"):
        staging.sync_staging({"username": "demo1", "domain": "demo1.example"})

    assert (real_tree["docroot"] / "index.php").read_text() == "production v1"
    assert (real_tree["docroot"] / "wp-config.php").read_text() == before
    assert stub_db_clone[-1] == ("restore", "demo1_stg")  # the saved staging database went back
    assert passwords == []
    assert sorted(path.name for path in real_tree["site_root"].iterdir()) == ["public_html"]


def test_sync_swaps_the_previous_site_back_when_the_password_change_fails(real_tree, stub_ssl, stub_database,
                                                                          stub_db_clone, monkeypatch):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    before = (real_tree["docroot"] / "wp-config.php").read_text()
    (real_tree["production"] / "index.php").write_text("production v2")
    monkeypatch.setattr(staging.mariadb, "set_password",
                        lambda user, password: (_ for _ in ()).throw(RuntimeError("password change failed")))

    with pytest.raises(RuntimeError, match="password change failed"):
        staging.sync_staging({"username": "demo1", "domain": "demo1.example"})

    assert (real_tree["docroot"] / "index.php").read_text() == "production v1"
    assert (real_tree["docroot"] / "wp-config.php").read_text() == before
    assert stub_db_clone[-1] == ("restore", "demo1_stg")
    assert sorted(path.name for path in real_tree["site_root"].iterdir()) == ["public_html"]


def test_staging_without_a_certificate_uses_http_and_says_why(real_tree, stub_database, stub_db_clone, monkeypatch):
    """Live finding UW L4: staging was always configured as https."""
    monkeypatch.setattr(staging.ssl, "prepare_https", lambda domain, host=None: {
        "https": False, "certificate": None, "detail": "staging.demo1.example does not point to this server yet"})
    monkeypatch.setattr(staging.ssl, "https_ready", lambda domain, host=None: False)

    created = staging.create_staging({"username": "demo1", "domain": "demo1.example"})

    assert created["staging_url"] == "http://staging.demo1.example"
    assert created["notice"] == (
        "The staging site uses http://staging.demo1.example because staging.demo1.example does not point to "
        "this server yet. Issue an SSL certificate for it, then sync to switch it to https.")
    assert "define('WP_HOME', 'http://staging.demo1.example');" in (real_tree["docroot"] / "wp-config.php").read_text()
    assert staging.get_staging({"username": "demo1", "domain": "demo1.example"})["staging_url"] == "http://staging.demo1.example"


def test_delete_staging_removes_its_copy(real_tree, stub_ssl, stub_database, stub_db_clone):
    staging.create_staging({"username": "demo1", "domain": "demo1.example"})
    result = staging.delete_staging({"username": "demo1", "domain": "demo1.example"})
    assert result == {"source_domain": "demo1.example", "status": "deleted"}
    assert not real_tree["docroot"].exists()
    assert stub_database["dropped"] == ["demo1_stg"]
    assert (real_tree["production"] / "index.php").read_text() == "production v1"
