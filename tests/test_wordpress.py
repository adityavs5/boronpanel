import io
import os
import zipfile

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import wordpress as wp
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WordPressInstall


def _fake_wp_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("wordpress/", "")
        zf.writestr("wordpress/wp-load.php", "<?php // fake wp-load.php\n")
        zf.writestr("wordpress/wp-config-sample.php", "<?php // sample config\n")
        zf.writestr("wordpress/wp-admin/includes/upgrade.php", "<?php // fake upgrade.php\n")
    return buf.getvalue()


@pytest.fixture()
def stub_network(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "wp_staging_dir", str(tmp_path / "wp-staging"))
    monkeypatch.setattr(wp, "fetch_latest_version_and_url", lambda: ("7.0", "https://downloads.wordpress.org/release/wordpress-7.0.zip"))
    monkeypatch.setattr(wp, "_fetch_salts", lambda: "define('AUTH_KEY', 'x');\n")

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_fake_wp_zip())

    monkeypatch.setattr(wp, "_download_zip", fake_download)


@pytest.fixture()
def stub_system(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(wp, "run", fake_run)

    import pwd as real_pwd

    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", "/home/demo1", "/usr/sbin/nologin"))
    monkeypatch.setattr(wp.pwd, "getpwnam", lambda name: fake_pw)
    return calls


@pytest.fixture()
def stub_install_script(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return {"success": True, "result": {}}

    monkeypatch.setattr(wp, "_run_silent_install", fake)
    return calls


@pytest.fixture()
def stub_database(monkeypatch):
    created = []
    dropped = []

    def fake_create(params):
        db_name = f"{params['username']}_{params['name']}"
        created.append(db_name)
        return {"db_name": db_name, "db_user": db_name, "password": "genpass123"}

    def fake_drop(params):
        dropped.append(f"{params['username']}_{params['name']}")

    monkeypatch.setattr(wp.handlers_database, "create_database", fake_create)
    monkeypatch.setattr(wp.handlers_database, "drop_database", fake_drop)
    return {"created": created, "dropped": dropped}


@pytest.fixture()
def account_with_domain(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    docroot = tmp_path / "public_html"
    docroot.mkdir()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))

    return {"docroot": docroot}


def test_docroot_is_empty_enough_true_for_new_dir(tmp_path):
    assert wp._docroot_is_empty_enough(str(tmp_path / "doesnotexist"))


def test_docroot_is_empty_enough_ignores_hidden_entries(tmp_path):
    (tmp_path / ".well-known").mkdir()
    assert wp._docroot_is_empty_enough(str(tmp_path))


def test_docroot_is_empty_enough_false_with_visible_file(tmp_path):
    (tmp_path / "index.html").write_text("hi")
    assert not wp._docroot_is_empty_enough(str(tmp_path))


def test_extract_wordpress_strips_top_level_dir(tmp_path):
    zip_path = tmp_path / "wp.zip"
    zip_path.write_bytes(_fake_wp_zip())
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    wp._extract_wordpress(zip_path, str(docroot))

    assert (docroot / "wp-load.php").exists()
    assert (docroot / "wp-admin" / "includes" / "upgrade.php").exists()
    assert not (docroot / "wordpress").exists()


def test_write_wp_config_contains_db_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_fetch_salts", lambda: "define('AUTH_KEY', 'x');\n")
    docroot = tmp_path / "docroot"
    docroot.mkdir()
    wp._write_wp_config(str(docroot), "demo1_wp", "demo1_wp", "s3cret")
    content = (docroot / "wp-config.php").read_text()
    assert "demo1_wp" in content
    assert "s3cret" in content
    assert "AUTH_KEY" in content
    mode = (docroot / "wp-config.php").stat().st_mode & 0o777
    assert mode == 0o640


def test_install_happy_path(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    result = wp.install({"username": "demo1", "domain": "demo1.example"})
    assert result["status"] == "installed"
    assert result["domain"] == "demo1.example"
    assert result["admin_url"] == "https://demo1.example/wp-admin/"
    assert result["db_name"] == "demo1_wp"
    assert (account_with_domain["docroot"] / "wp-config.php").exists()
    assert (account_with_domain["docroot"] / "wp-load.php").exists()
    assert len(stub_install_script) == 1

    with write_session() as session:
        row = session.scalar(select(WordPressInstall).where(WordPressInstall.domain == "demo1.example"))
        assert row is not None
        assert row.db_name == "demo1_wp"


def test_install_refuses_non_empty_docroot(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    (account_with_domain["docroot"] / "index.html").write_text("existing site")
    with pytest.raises(wp.WordPressError):
        wp.install({"username": "demo1", "domain": "demo1.example"})
    assert not stub_database["created"]


def test_install_refuses_duplicate(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    wp.install({"username": "demo1", "domain": "demo1.example"})
    with pytest.raises(wp.WordPressError):
        wp.install({"username": "demo1", "domain": "demo1.example"})


def test_install_cleans_up_database_on_failure(account_with_domain, stub_network, stub_system, stub_database, monkeypatch):
    def failing_install(*a, **k):
        raise wp.WordPressError("simulated wp_install() failure")

    monkeypatch.setattr(wp, "_run_silent_install", failing_install)

    with pytest.raises(wp.WordPressError):
        wp.install({"username": "demo1", "domain": "demo1.example"})

    assert stub_database["created"] == ["demo1_wp"]
    assert stub_database["dropped"] == ["demo1_wp"]

    with write_session() as session:
        row = session.scalar(select(WordPressInstall).where(WordPressInstall.domain == "demo1.example"))
        assert row is None


def test_allocate_database_retries_on_collision(monkeypatch):
    attempts = []

    def fake_create(params):
        attempts.append(params["name"])
        if params["name"] == "wp":
            raise RuntimeError("database 'demo1_wp' already exists")
        return {"db_name": f"demo1_{params['name']}", "db_user": f"demo1_{params['name']}", "password": "x"}

    monkeypatch.setattr(wp.handlers_database, "create_database", fake_create)
    grant, suffix = wp._allocate_database("demo1")
    assert suffix != "wp"
    assert attempts[0] == "wp"


def test_trigger_install_creates_pending_job_and_runs_async(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    result = wp.trigger_install({"username": "demo1", "domain": "demo1.example"})
    assert result["status"] in ("pending", "running", "completed")
    assert result["admin_password"] is None  # never revealed before completion

    import time

    for _ in range(50):
        job = wp.get_job({"job_id": result["id"]})
        if job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("install job did not complete in time")

    assert job["admin_password"] is not None
    # Second read must not re-reveal the password (one-time reveal).
    job2 = wp.get_job({"job_id": result["id"]})
    assert job2["admin_password"] is None


def test_get_job_missing_raises(isolated_db):
    with pytest.raises(wp.WordPressError):
        wp.get_job({"job_id": 999999})


def test_install_helper_lives_outside_var_lib_forgehost():
    """Regression test for a real bug found by live testing: the helper
    script must NOT live anywhere under /var/lib/forgehost, since that
    tree is locked to root:forgehost-api (shared/db.py) and this script
    runs via `runuser -u <hosting-account>` -- no hosting account uid can
    even traverse into /var/lib/forgehost, let alone read a file there."""
    assert "/var/lib/forgehost" not in str(wp.INSTALL_HELPER_PATH)
    assert wp.INSTALL_HELPER_PATH.exists()
    assert wp.INSTALL_HELPER_PATH.read_text().startswith("<?php")


def test_install_helper_world_readable_on_disk():
    mode = wp.INSTALL_HELPER_PATH.stat().st_mode & 0o777
    assert mode & 0o004, "install helper must be world-readable (runs as an arbitrary hosting account uid)"
