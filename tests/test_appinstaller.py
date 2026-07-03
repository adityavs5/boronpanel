import os
import pwd as real_pwd
import time

import pytest

from daemon import appinstaller as ai
from daemon import handlers_account as ha


@pytest.fixture()
def account_with_domain(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    # _set_ownership calls the real pwd.getpwnam + a real `chown` --
    # "demo1" isn't a real Linux user in this test environment, so
    # getpwnam is faked to this test process's own uid/gid (matching
    # tests/test_wordpress.py's stub_system fixture, the established
    # pattern for this exact situation), letting the real chown call
    # succeed against the real tmp_path docroot.
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", "/home/demo1", "/usr/sbin/nologin"))
    monkeypatch.setattr(ai.pwd, "getpwnam", lambda name: fake_pw)

    docroot = tmp_path / "public_html"
    docroot.mkdir()
    with __import__("shared.db", fromlist=["write_session"]).write_session() as session:
        from sqlalchemy import select

        from shared.models import Account, Domain

        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))

    return {"docroot": docroot}


# --- static app: real, fast, exercises the full async job lifecycle ------


def test_trigger_install_static_runs_async_end_to_end(account_with_domain):
    result = ai.trigger_install({"username": "demo1", "domain": "demo1.example", "app_id": "static", "title": "My Site"})
    assert result["status"] in ("pending", "running", "completed")

    for _ in range(50):
        job = ai.get_job({"job_id": result["id"], "username": "demo1"})
        if job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("static install job did not complete in time")

    assert job["admin_url"] == "https://demo1.example/"
    index_html = account_with_domain["docroot"] / "index.html"
    assert index_html.exists()
    assert "My Site" in index_html.read_text()

    apps = ai.list_installed_apps({"username": "demo1"})["apps"]
    assert len(apps) == 1
    assert apps[0]["app_id"] == "static"
    assert apps[0]["domain"] == "demo1.example"


def test_trigger_install_rejects_unknown_app_id(account_with_domain):
    with pytest.raises(ai.AppInstallError):
        ai.trigger_install({"username": "demo1", "domain": "demo1.example", "app_id": "not-a-real-app"})


def test_trigger_install_rejects_second_app_on_same_domain(account_with_domain):
    result = ai.trigger_install({"username": "demo1", "domain": "demo1.example", "app_id": "static"})
    for _ in range(50):
        job = ai.get_job({"job_id": result["id"], "username": "demo1"})
        if job["status"] == "completed":
            break
        time.sleep(0.05)

    with pytest.raises(ai.AppInstallError):
        ai.trigger_install({"username": "demo1", "domain": "demo1.example", "app_id": "static"})


def test_install_static_refuses_nonempty_docroot(account_with_domain):
    (account_with_domain["docroot"] / "existing.html").write_text("hi")
    with pytest.raises(ai.AppInstallError):
        ai._install_static("demo1", "demo1.example", "Title", "admin", "a@b.com", "pw")


# --- get_job ownership check (same fix pattern as WordPress/backup jobs,
# docs/CHECKPOINT-phase4-0b-cross-account-idor.md) -- built in from the
# start here, not retrofitted ------------------------------------------


def test_get_job_rejects_wrong_account(isolated_db):
    from shared.db import write_session
    from shared.models import Account, AppInstallJob

    with write_session() as session:
        acct1 = Account(username="owner1", uid=6001, gid=6001, status="active")
        acct2 = Account(username="owner2", uid=6002, gid=6002, status="active")
        session.add_all([acct1, acct2])
        session.flush()
        job = AppInstallJob(account_id=acct2.id, domain="owner2.example", app_id="static", status="completed", admin_url="https://x/", admin_password="TopSecret123!")
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(ai.AppInstallError):
        ai.get_job({"job_id": job_id, "username": "owner1"})

    result = ai.get_job({"job_id": job_id, "username": "owner2"})
    assert result["admin_password"] == "TopSecret123!"


# --- pure helpers: escaping, config rendering ------------------------------


def test_sql_str_escapes_quotes_and_backslashes():
    assert ai._sql_str("O'Brien") == "'O\\'Brien'"
    assert ai._sql_str("back\\slash") == "'back\\\\slash'"


def test_extract_zip_rejects_zip_slip(tmp_path):
    """Security audit finding F1: a malicious/compromised release zip
    with a '../' member name must not be able to write outside docroot --
    extraction runs as root, before ownership is chowned to the account,
    so an unchecked escape here would be a root-level arbitrary write."""
    import zipfile

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../../../tmp/forgehost_zipslip_app.txt", "pwned")
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    with pytest.raises(ai.AppInstallError):
        ai._extract_zip(zip_path, str(docroot))
    assert not os.path.exists("/tmp/forgehost_zipslip_app.txt")


def test_extract_zip_rejects_zip_slip_with_root_prefix(tmp_path):
    """Same defect, exercised through the root_prefix-stripping path
    (Joomla/PrestaShop's own wrapped-zip case)."""
    import zipfile

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("app/../../../../tmp/forgehost_zipslip_app2.txt", "pwned")
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    with pytest.raises(ai.AppInstallError):
        ai._extract_zip(zip_path, str(docroot), root_prefix="app/")
    assert not os.path.exists("/tmp/forgehost_zipslip_app2.txt")


def test_extract_zip_accepts_normal_members(tmp_path):
    import zipfile

    zip_path = tmp_path / "good.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("index.php", "<?php echo 'hi';")
        zf.writestr("assets/style.css", "body {}")
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    ai._extract_zip(zip_path, str(docroot))

    assert (docroot / "index.php").read_text() == "<?php echo 'hi';"
    assert (docroot / "assets" / "style.css").exists()


def test_php_str_escapes_quotes_and_backslashes():
    assert ai._php_str("it's") == "'it\\'s'"


def test_write_joomla_configuration_produces_valid_php(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    ai._write_joomla_configuration(str(docroot), "jm_db", "jm_user", "P@ss'word", "jos_", "My Site")
    content = (docroot / "configuration.php").read_text()
    assert "class JConfig" in content
    assert "jm_db" in content
    assert "jos_" in content
    assert "P@ss\\'word" in content  # escaped, not raw

    lint = ai.run([ai.settings.php_cli_bin, "-l", str(docroot / "configuration.php")], timeout=10)
    assert lint.ok, lint.stderr


def test_joomla_password_hash_produces_real_bcrypt():
    hashed = ai._joomla_password_hash("SuperSecretPassword123!")
    assert hashed.startswith("$2y$")
    verify = ai.run(
        [ai.settings.php_cli_bin, "-r", "exit(password_verify($argv[1], $argv[2]) ? 0 : 1);", "--", "SuperSecretPassword123!", hashed],
        timeout=10,
    )
    assert verify.ok


def test_allocate_database_reuses_or_suffixes(account_with_domain, monkeypatch):
    calls = []

    def fake_create_database(params):
        calls.append(params["name"])
        if params["name"] == "jm":
            raise RuntimeError("already exists")
        return {"db_name": f"demo1_{params['name']}", "db_user": f"demo1_{params['name']}", "password": "x"}

    monkeypatch.setattr(ai.handlers_database, "create_database", fake_create_database)
    result = ai._allocate_database("demo1", "jm")
    assert result["db_name"] != "demo1_jm"
    assert len(calls) == 2  # first attempt "jm" collided, second succeeded


def test_terminate_account_apps_removes_rows(isolated_db):
    from shared.db import write_session
    from shared.models import Account, AppInstall, AppInstallJob

    with write_session() as session:
        account = Account(username="demo1", uid=6001, gid=6001, status="active")
        session.add(account)
        session.flush()
        session.add(AppInstall(account_id=account.id, domain="demo1.example", app_id="static", version="1.0"))
        session.add(AppInstallJob(account_id=account.id, domain="demo1.example", app_id="static", status="completed"))
        account_id = account.id

    class FakeAccount:
        id = account_id

    ai.terminate_account_apps(FakeAccount())
    assert ai.list_installed_apps({"username": "demo1"})["apps"] == []
