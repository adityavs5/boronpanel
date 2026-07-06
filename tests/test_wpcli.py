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
    assert redact == [secret]  # password redacted from the daemon log
    assert "***" in display  # never displayed in the command string
    assert any(a.startswith("--user_pass=") for a in args)


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


def test_run_wpcli_rejects_domain_without_wordpress(isolated_db, tmp_path, monkeypatch):
    docroot = tmp_path / "public_html"
    docroot.mkdir()  # no wp-config.php
    _account_with_docroot(docroot)
    monkeypatch.setattr(wpcli, "ensure_wpcli", lambda: "/phar")
    with pytest.raises(RuntimeError, match="wp-config.php"):
        wpcli.run_wpcli({"username": "demo1", "domain": "site.com", "action": "plugin_list"})
