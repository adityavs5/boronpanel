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


def test_extract_wordpress_rejects_zip_slip(tmp_path):
    """Security audit finding F1: a malicious/compromised release zip
    with a '../' member name must not be able to write outside docroot --
    extraction runs as root, before ownership is chowned to the account,
    so an unchecked escape here would be a root-level arbitrary write."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("wordpress/../../../../tmp/boron_zipslip_wp.txt", "pwned")
    zip_path = tmp_path / "evil.zip"
    zip_path.write_bytes(buf.getvalue())
    docroot = tmp_path / "docroot"
    docroot.mkdir()

    with pytest.raises(wp.WordPressError):
        wp._extract_wordpress(zip_path, str(docroot))
    assert not os.path.exists("/tmp/boron_zipslip_wp.txt")


def test_extract_wordpress_rejects_expansion_before_writing(tmp_path, monkeypatch):
    import zipfile

    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("wordpress/large.txt", "x" * 32)
    docroot = tmp_path / "docroot"
    docroot.mkdir()
    monkeypatch.setattr(wp, "MAX_WORDPRESS_FILE_BYTES", 8)
    with pytest.raises(wp.WordPressError, match="extraction limit"):
        wp._extract_wordpress(archive, str(docroot))
    assert list(docroot.iterdir()) == []


def test_extract_wordpress_rejects_duplicate_normalized_paths(tmp_path):
    import zipfile

    archive = tmp_path / "duplicates.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("wordpress/a/b.txt", "first")
        output.writestr("wordpress/a\\b.txt", "second")
    docroot = tmp_path / "docroot"
    docroot.mkdir()
    with pytest.raises(wp.WordPressError, match="duplicate path"):
        wp._extract_wordpress(archive, str(docroot))
    assert list(docroot.iterdir()) == []


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
        job = wp.get_job({"job_id": result["id"], "username": "demo1"})
        if job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("install job did not complete in time")

    assert job["admin_password"] is not None
    # Second read must not re-reveal the password (one-time reveal).
    job2 = wp.get_job({"job_id": result["id"], "username": "demo1"})
    assert job2["admin_password"] is None


def test_get_job_missing_raises(account_with_domain):
    with pytest.raises(wp.WordPressError):
        wp.get_job({"job_id": 999999, "username": "demo1"})


def test_install_helper_lives_outside_var_lib_boron():
    """Regression test for a real bug found by live testing: the helper
    script must NOT live anywhere under /var/lib/boron, since that
    tree is locked to root:boron-api (shared/db.py) and this script
    runs via `runuser -u <hosting-account>` -- no hosting account uid can
    even traverse into /var/lib/boron, let alone read a file there."""
    assert "/var/lib/boron" not in str(wp.INSTALL_HELPER_PATH)
    assert wp.INSTALL_HELPER_PATH.exists()
    assert wp.INSTALL_HELPER_PATH.read_text().startswith("<?php")


def test_install_helper_world_readable_on_disk():
    mode = wp.INSTALL_HELPER_PATH.stat().st_mode & 0o777
    assert mode & 0o004, "install helper must be world-readable (runs as an arbitrary hosting account uid)"


# --- subdirectory installs + multi-install per domain (item 3) -------------


def test_install_target_root_is_docroot_itself(tmp_path):
    assert wp._install_target(str(tmp_path), "") == str(tmp_path)


def test_install_target_subdirectory(tmp_path):
    assert wp._install_target(str(tmp_path), "blog") == str(tmp_path / "blog")


def test_install_target_strips_slashes(tmp_path):
    assert wp._install_target(str(tmp_path), "/blog/") == str(tmp_path / "blog")


def test_install_target_rejects_traversal_outside_docroot(tmp_path):
    with pytest.raises(wp.WordPressError, match="outside"):
        wp._install_target(str(tmp_path), "../../etc")


def test_suffix_hint_derives_from_path():
    assert wp._suffix_hint("blog") == "wp_blog"
    assert wp._suffix_hint("My Blog!!") == "wp_my_blog"
    assert wp._suffix_hint("") == "wp"


def test_install_into_subdirectory(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    result = wp.install({"username": "demo1", "domain": "demo1.example", "path": "blog"})
    assert result["status"] == "installed"
    assert result["path"] == "blog"
    assert result["admin_url"] == "https://demo1.example/blog/wp-admin/"

    docroot = account_with_domain["docroot"]
    assert (docroot / "blog" / "wp-config.php").exists()
    assert (docroot / "blog" / "wp-load.php").exists()
    assert not (docroot / "wp-config.php").exists()  # root itself untouched

    with write_session() as session:
        row = session.scalar(
            select(WordPressInstall).where(WordPressInstall.domain == "demo1.example", WordPressInstall.path == "blog")
        )
        assert row is not None
        assert row.db_name == "demo1_wp_blog"


def test_install_root_and_subdirectory_coexist(account_with_domain, stub_network, stub_system, stub_install_script, stub_database):
    """Item 3: multiple WordPress installs per account, tracked
    separately -- a root install and a subdirectory install under the
    SAME domain must not collide with each other."""
    root_result = wp.install({"username": "demo1", "domain": "demo1.example"})
    blog_result = wp.install({"username": "demo1", "domain": "demo1.example", "path": "blog"})

    assert root_result["path"] == ""
    assert blog_result["path"] == "blog"
    assert root_result["db_name"] != blog_result["db_name"]

    with write_session() as session:
        rows = session.scalars(
            select(WordPressInstall).where(WordPressInstall.domain == "demo1.example")
        ).all()
        assert {r.path for r in rows} == {"", "blog"}


def test_install_refuses_duplicate_at_the_same_path_but_allows_a_different_one(
    account_with_domain, stub_network, stub_system, stub_install_script, stub_database
):
    wp.install({"username": "demo1", "domain": "demo1.example", "path": "blog"})
    with pytest.raises(wp.WordPressError, match="already installed"):
        wp.install({"username": "demo1", "domain": "demo1.example", "path": "blog"})
    # A second, different subdirectory is unaffected.
    result = wp.install({"username": "demo1", "domain": "demo1.example", "path": "shop"})
    assert result["status"] == "installed"


def test_install_subdirectory_refuses_non_empty_target(
    account_with_domain, stub_network, stub_system, stub_install_script, stub_database
):
    (account_with_domain["docroot"] / "blog").mkdir()
    (account_with_domain["docroot"] / "blog" / "existing.html").write_text("already something here")
    with pytest.raises(wp.WordPressError):
        wp.install({"username": "demo1", "domain": "demo1.example", "path": "blog"})
    assert not stub_database["created"]


def test_install_subdirectory_rejects_path_traversal(
    account_with_domain, stub_network, stub_system, stub_install_script, stub_database
):
    with pytest.raises(wp.WordPressError, match="outside"):
        wp.install({"username": "demo1", "domain": "demo1.example", "path": "../../etc"})
    assert not stub_database["created"]


@pytest.mark.parametrize('protocol,use_www', [('http',False),('https',False),('http',True),('https',True)])
def test_install_address_options_reach_wordpress_and_inventory(account_with_domain, stub_network, stub_system, stub_install_script, stub_database, protocol, use_www):
    from daemon import wpmanager
    result=wp.install({'username':'demo1','domain':'demo1.example','protocol':protocol,'use_www':use_www})
    url=protocol+'://'+('www.' if use_www else '')+'demo1.example'
    assert result['admin_url']==url+'/wp-admin/'
    assert stub_install_script[0][3]==url
    assert wpmanager.inventory({'username':'demo1'})['installs'][0]['url']==url


def test_www_address_refuses_separate_site(account_with_domain):
    from shared.models import Domain
    with write_session() as s:
        s.add(Domain(account_id=account_with_domain['account_id'] if 'account_id' in account_with_domain else 1,
            domain='www.demo1.example',kind='addon',docroot='/separate/site'))
    with pytest.raises(wp.WordPressError,match='separate site'):
        wp.website_url('demo1.example',use_www=True)
