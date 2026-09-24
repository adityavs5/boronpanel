"""Phase 8 feature 8: WP-CLI UI."""
import pytest

from daemon import wpcli
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError


def _account_with_docroot(docroot, domain="site.com", username="demo1"):
    with write_session() as db:
        account = Account(username=username, status="active", uid=5001, gid=5001)
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain=domain, kind="primary", docroot=str(docroot)))
        return account.id


# --- allowlisted command builder ------------------------------------------


def test_build_rejects_unknown_action():
    with pytest.raises(ValidationError):
        wpcli._build("rm_rf", {})


def test_build_plugin_update_all_vs_named():
    assert wpcli._build("plugin_update", {"all": True})[0] == ["plugin", "update", "--all"]
    assert wpcli._build("plugin_update", {"name": "akismet"})[0] == ["plugin", "update", "akismet"]


def test_build_rejects_bad_slug():
    with pytest.raises(ValidationError):
        wpcli._build("plugin_activate", {"name": "bad name; rm -rf"})


def test_build_search_replace_preview_and_apply():
    preview = wpcli._build("search_replace", {"search": "a", "replace": "b", "preview": True})[0]
    assert "--dry-run" in preview
    apply = wpcli._build("search_replace", {"search": "a", "replace": "b", "preview": False})[0]
    assert "--dry-run" not in apply


def test_build_user_reset_password_reveals_and_redacts():
    args, display, secret, redact = wpcli._build("user_reset_password", {"user": "admin"})
    assert secret is not None and len(secret) >= 12
    assert redact == [secret]  # also mask unexpected WP-CLI output
    assert secret not in display
    assert args == ["user", "update", "admin", "--prompt=user_pass"]


# --- detection -------------------------------------------------------------


def test_detect_finds_wordpress(isolated_db, tmp_path):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php // wp")
    wpinc = docroot / "wp-includes"
    wpinc.mkdir()
    (wpinc / "version.php").write_text("<?php\n$wp_version = '6.5.2';\n")
    _account_with_docroot(docroot)

    result = wpcli.detect_installs({"username": "demo1"})
    assert len(result["installs"]) == 1
    assert result["installs"][0]["id"] == "site.com"
    assert result["installs"][0]["wp_version"] == "6.5.2"


def test_detect_ignores_non_wordpress(isolated_db, tmp_path):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "index.html").write_text("hello")
    _account_with_docroot(docroot)
    assert wpcli.detect_installs({"username": "demo1"})["installs"] == []


# --- run (mocked submit + phar) --------------------------------------------


def test_run_wpcli_builds_argv(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)

    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/usr/local/bin/wp-cli.phar")
    captured = {}
    monkeypatch.setattr(wpcli.cmdjobs, "submit", lambda *a, **k: captured.update(args=a, kwargs=k) or {"id": 1})

    wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "action": "plugin_list"})
    username, kind, target, argv, display = captured["args"]
    assert kind == "wpcli"
    assert target == str(docroot)
    assert argv[0].endswith("php") and argv[1] == "/usr/local/bin/wp-cli.phar"
    assert f"--path={docroot}" in argv
    assert argv[-2:] == ["plugin", "list"] or "list" in argv


def test_password_reset_passes_secret_only_on_stdin(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)
    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/usr/local/bin/wp-cli.phar")
    captured = {}
    monkeypatch.setattr(wpcli.cmdjobs, "submit",
                        lambda *a, **k: captured.update(args=a, kwargs=k) or {"id": 1})

    wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "action": "user_reset_password", "user": "admin"})
    secret = captured["kwargs"]["revealed_secret"]
    assert captured["kwargs"]["input_text"] == secret + "\n"
    assert captured["kwargs"]["redact"] == [secret]
    assert secret not in " ".join(captured["args"][3])


def test_run_wpcli_rejects_domain_without_wordpress(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()  # no wp-config.php
    _account_with_docroot(docroot)
    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/phar")
    with pytest.raises(RuntimeError, match="wp-config.php"):
        wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "action": "plugin_list"})


# --- subdirectory installs (item 3) ----------------------------------------


def test_detect_finds_subdirectory_install(isolated_db, tmp_path):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "index.html").write_text("marketing site")  # root is NOT WordPress
    blog = docroot / "blog"
    blog.mkdir()
    (blog / "wp-config.php").write_text("<?php // wp")
    (blog / "wp-includes").mkdir()
    (blog / "wp-includes" / "version.php").write_text("<?php\n$wp_version = '6.5.2';\n")
    _account_with_docroot(docroot)

    result = wpcli.detect_installs({"username": "demo1"})
    assert len(result["installs"]) == 1
    install = result["installs"][0]
    assert install["id"] == "site.com::blog"
    assert install["path"] == "blog"
    assert install["wp_version"] == "6.5.2"
    assert install["docroot"] == str(blog)


def test_detect_finds_root_and_subdirectory_installs_together(isolated_db, tmp_path):
    """Item 3: multiple WordPress installs per account/domain, tracked
    separately."""
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php // root wp")
    blog = docroot / "blog"
    blog.mkdir()
    (blog / "wp-config.php").write_text("<?php // subdir wp")
    _account_with_docroot(docroot)

    result = wpcli.detect_installs({"username": "demo1"})
    ids = {i["id"] for i in result["installs"]}
    assert ids == {"site.com", "site.com::blog"}
    paths = {i["id"]: i["path"] for i in result["installs"]}
    assert paths == {"site.com": "", "site.com::blog": "blog"}


def test_detect_can_be_scoped_to_a_single_domain(isolated_db, tmp_path):
    docroot_a = tmp_path / "a"
    docroot_a.mkdir()
    (docroot_a / "wp-config.php").write_text("<?php")
    docroot_b = tmp_path / "b"
    docroot_b.mkdir()
    (docroot_b / "wp-config.php").write_text("<?php")
    with write_session() as db:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="a.example", kind="primary", docroot=str(docroot_a)))
        db.add(Domain(account_id=account.id, domain="b.example", kind="addon", docroot=str(docroot_b)))

    result = wpcli.detect_installs({"username": "demo1", "domain": "a.example"})
    assert [i["domain"] for i in result["installs"]] == ["a.example"]


def test_detect_ignores_hidden_directories(isolated_db, tmp_path):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    hidden = docroot / ".git-wp-backup"
    hidden.mkdir()
    (hidden / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)
    assert wpcli.detect_installs({"username": "demo1"})["installs"] == []


def test_run_wpcli_targets_subdirectory_install(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    blog = docroot / "blog"
    blog.mkdir()
    (blog / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)

    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/usr/local/bin/wp-cli.phar")
    captured = {}
    monkeypatch.setattr(wpcli.cmdjobs, "submit", lambda *a, **k: captured.update(args=a, kwargs=k) or {"id": 1})

    wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "path": "blog", "action": "plugin_list"})
    _username, _kind, target, argv, _display = captured["args"]
    assert target == str(blog)
    assert f"--path={blog}" in argv


def test_run_wpcli_path_cannot_escape_docroot(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)
    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/phar")

    with pytest.raises(ValidationError, match="escapes"):
        wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "path": "../../etc", "action": "plugin_list"})


def test_run_wpcli_default_path_still_targets_root(isolated_db, tmp_path, monkeypatch):
    """Backward-compat: omitting `path` entirely (every pre-existing
    caller, e.g. the account-level DevTools tab) must keep resolving to
    the docroot itself, exactly as before this feature."""
    docroot = tmp_path / "public_html"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php")
    _account_with_docroot(docroot)

    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/phar")
    captured = {}
    monkeypatch.setattr(wpcli.cmdjobs, "submit", lambda *a, **k: captured.update(args=a, kwargs=k) or {"id": 1})

    wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "action": "cache_flush"})
    _username, _kind, target, _argv, _display = captured["args"]
    assert target == str(docroot)


def test_wordpress_version_scan_does_not_follow_file_symlink(tmp_path):
    from daemon import wpcli
    includes = tmp_path / 'wp-includes'
    includes.mkdir()
    outside = tmp_path / 'private'
    outside.write_text("<?php $wp_version = 'outside-canary';")
    (includes / 'version.php').symlink_to(outside)
    assert wpcli._wp_version_at(str(tmp_path)) is None
