import os
import json
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

    # docroot must live under the account's own home (home_base/<user>) -- the
    # installer now refuses a docroot that resolves outside it (a symlink-escape
    # / cross-tenant guard), so mirror the real layout here.
    monkeypatch.setattr(ai.settings, "home_base", str(tmp_path))
    docroot = tmp_path / "demo1" / "public_html"
    docroot.mkdir(parents=True)
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
        zf.writestr("../../../../tmp/boron_zipslip_app.txt", "pwned")
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    with pytest.raises(ai.AppInstallError):
        ai._extract_zip(zip_path, str(docroot))
    assert not os.path.exists("/tmp/boron_zipslip_app.txt")


def test_extract_zip_rejects_zip_slip_with_root_prefix(tmp_path):
    """Same defect, exercised through the root_prefix-stripping path
    (Joomla/PrestaShop's own wrapped-zip case)."""
    import zipfile

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("app/../../../../tmp/boron_zipslip_app2.txt", "pwned")
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    with pytest.raises(ai.AppInstallError):
        ai._extract_zip(zip_path, str(docroot), root_prefix="app/")
    assert not os.path.exists("/tmp/boron_zipslip_app2.txt")


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


def test_extract_zip_rejects_expansion_before_writing(tmp_path, monkeypatch):
    import zipfile

    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("large.txt", "x" * 32)
    docroot = tmp_path / "docroot"
    docroot.mkdir()
    monkeypatch.setattr(ai, "MAX_APP_FILE_BYTES", 8)
    with pytest.raises(ai.AppInstallError, match="extraction limit"):
        ai._extract_zip(archive, str(docroot))
    assert list(docroot.iterdir()) == []


def test_extract_wrapped_tar_rejects_traversal_and_links(tmp_path):
    import io
    import tarfile

    for name, link in (("../outside", False), ("drupal-1/link", True)):
        archive = tmp_path / ("link.tar" if link else "traversal.tar")
        with tarfile.open(archive, "w") as output:
            info = tarfile.TarInfo(name)
            if link:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                output.addfile(info)
            else:
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
        extracted = tmp_path / ("link-extract" if link else "traversal-extract")
        extracted.mkdir()
        with pytest.raises(ai.AppInstallError):
            ai._extract_wrapped_tar(archive, extracted)
    assert not (tmp_path / "outside").exists()


def test_extract_wrapped_tar_accepts_single_app_directory(tmp_path):
    import io
    import tarfile

    archive = tmp_path / "drupal.tar"
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("drupal-1/index.php")
        info.size = 3
        output.addfile(info, io.BytesIO(b"php"))
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    root = ai._extract_wrapped_tar(archive, extracted)
    assert root.name == "drupal-1"
    assert (root / "index.php").read_bytes() == b"php"


def test_app_sql_import_uses_exclusive_scoped_credentials(monkeypatch):
    from daemon.procutil import ProcResult

    observed = []

    def fake_run(args, input_text=None, **kwargs):
        from pathlib import Path
        observed.append((args, input_text, Path(args[1].split("=", 1)[1]).read_text()))
        return ProcResult(args=args, returncode=1, stdout="", stderr="secret SQL text")

    monkeypatch.setattr(ai, "run", fake_run)
    with pytest.raises(ai.AppInstallError, match="SQL import failed") as exc:
        ai._mysql_import("demo1_app", "demo1_app", 'pass"word', ["SELECT 1;"])
    args, sql, config = observed[0]
    assert args[1].startswith("--defaults-file=")
    assert "--binary-mode" in args
    assert "--local-infile=0" in args
    assert "user=\"demo1_app\"" in config
    assert 'password="pass\\"word"' in config
    assert sql == "SELECT 1;"
    assert "secret SQL text" not in str(exc.value)


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


def test_prestashop_helper_reconstructs_vendor_argv_without_os_password_args(tmp_path):
    """The real PHP process must pass the same option array to index_cli.php."""
    import subprocess

    install_dir = tmp_path / "install"
    install_dir.mkdir()
    installer = install_dir / "index_cli.php"
    installer.write_text("<?php echo json_encode([$argc, $argv, $_SERVER['argv']]);")
    options = {
        "domain": "example.test", "db_server": "localhost", "db_name": "db",
        "db_user": "dbuser", "db_password": "db secret", "email": "a@example.test",
        "firstname": "Store", "lastname": "Admin", "password": "admin secret",
        "language": "en", "country": "us", "admin_dir": "admin1234",
        "newsletter": "0", "send_email": "0",
    }
    cmd = [ai.settings.php_cli_bin, str(ai.PRESTASHOP_CLI_HELPER), str(installer)]
    result = subprocess.run(cmd, input=json.dumps(options), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    argc, argv, server_argv = json.loads(result.stdout)
    assert argc == len(options) + 1
    assert argv == server_argv
    assert argv[0] == str(installer)
    assert "--db_password=db secret" in argv
    assert "--password=admin secret" in argv
    assert "db secret" not in " ".join(cmd)
    assert "admin secret" not in " ".join(cmd)


def test_prestashop_install_sends_passwords_only_through_stdin(account_with_domain, monkeypatch):
    import zipfile
    from daemon.procutil import ProcResult

    monkeypatch.setattr(ai, "fetch_prestashop_latest_version_and_url", lambda: ("test", "local"))
    monkeypatch.setattr(ai.settings, "app_staging_dir", str(account_with_domain["docroot"].parent / "staging"))
    monkeypatch.setattr(ai, "_allocate_database", lambda *_: {
        "db_name": "demo1_ps", "db_user": "demo1_ps", "password": "database secret",
    })

    def fake_download(_url, path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("install/index_cli.php", "<?php")

    monkeypatch.setattr(ai, "_download", fake_download)
    original_run = ai.run
    captured = {}

    def fake_run(args, **kwargs):
        if args[0] == "runuser":
            captured.update(args=args, kwargs=kwargs)
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        return original_run(args, **kwargs)

    monkeypatch.setattr(ai, "run", fake_run)
    ai.install_prestashop("demo1", "demo1.example", "Shop", "admin", "admin@example.test", "admin secret")
    assert "database secret" not in " ".join(captured["args"])
    assert "admin secret" not in " ".join(captured["args"])
    options = json.loads(captured["kwargs"]["input_text"])
    assert options["db_password"] == "database secret"
    assert options["password"] == "admin secret"


def test_app_install_failure_does_not_store_or_log_exception_secrets(isolated_db, monkeypatch, caplog):
    from shared.db import write_session
    from shared.models import Account, AppInstallJob

    with write_session() as session:
        account = Account(username="demo1", uid=6001, gid=6001, status="active")
        session.add(account)
        session.flush()
        job = AppInstallJob(account_id=account.id, domain="demo1.example", app_id="prestashop", status="pending")
        session.add(job)
        session.flush()
        job_id = job.id

    def fail(*_args):
        raise RuntimeError("vendor error: db secret / Admin secret 2026!")

    monkeypatch.setitem(ai.APPS["prestashop"], "installer", fail)
    ai._run_install_job(job_id, "demo1", "demo1.example", "prestashop", {"admin_password": "Admin secret 2026!"})
    with write_session() as session:
        job = session.get(AppInstallJob, job_id)
        assert job.status == "failed"
        assert "secret" not in job.error
    assert "secret" not in caplog.text
