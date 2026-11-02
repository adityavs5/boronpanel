import os

import pytest

from daemon import htaccess
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError


def _domain(tmp_path, monkeypatch, *, symlink=False):
    home_base = tmp_path / "home"
    docroot = home_base / "owner1" / "domains" / "example.test" / "public_html"
    docroot.mkdir(parents=True)
    value = docroot
    if symlink:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = home_base / "owner1" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        value = link
    monkeypatch.setattr(htaccess.settings, "home_base", str(home_base))
    with write_session() as db:
        account = Account(username="owner1", status="active")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="example.test", docroot=str(value)))
    return docroot


def test_manual_reload_rerenders_owned_vhosts_through_validated_transaction(isolated_db, tmp_path, monkeypatch):
    docroot = _domain(tmp_path, monkeypatch)
    (docroot / "private").mkdir()
    (docroot / "private" / ".htaccess").write_text("Require all denied\n")
    calls = []
    monkeypatch.setattr(htaccess.ols, "refresh_vhost", lambda account: calls.append(account.username))
    result = htaccess.reload_domain({"username": "owner1", "domain": "example.test"})
    assert result["config_valid"] is True
    assert calls == ["owner1"]
    assert result["access_rules"] == [{"directory": "/private", "mode": "deny", "allow": [], "deny": []}]
    assert result["unsupported_access_directives"] == []


def test_manual_reload_rejects_symlink_docroot(isolated_db, tmp_path, monkeypatch):
    _domain(tmp_path, monkeypatch, symlink=True)
    with pytest.raises(ValidationError, match="outside its account home|unavailable or unsafe"):
        htaccess.reload_domain({"username": "owner1", "domain": "example.test"})


# --- watcher ----------------------------------------------------------------


def test_watcher_debounce_setting_exists():
    """Regression: poll() read a misspelled setting, raised AttributeError
    after os.read() had consumed the events, and dropped every change."""
    assert htaccess.settings.htaccess_watch_debounce_seconds > 0


def test_watcher_applies_change_after_debounce(isolated_db, tmp_path, monkeypatch):
    docroot = _domain(tmp_path, monkeypatch)
    monkeypatch.setattr(htaccess.settings, "htaccess_watch_debounce_seconds", 0.0)
    reloads, refreshes = [], []
    monkeypatch.setattr(htaccess.ols, "graceful_reload", lambda params: reloads.append(params) or {"config_valid": True})
    monkeypatch.setattr(htaccess.ols, "refresh_vhost", lambda account: refreshes.append(account.username))
    watcher = htaccess.HtaccessWatcher()
    try:
        watcher.refresh_inventory()
        (docroot / ".htaccess").write_text("RewriteEngine On\nRewriteRule ^rw-me$ /target.txt\n")
        assert watcher.poll()["changed"] == ["example.test"]
        import time
        time.sleep(1.05)  # the debounce floor is one second
        assert watcher.poll()["reloaded"] == ["example.test"]
        # Nothing is known yet about what the live vhost was rendered with,
        # so the first change re-renders the account's vhosts.
        assert refreshes == ["owner1"] and reloads == []
        # A rewrite-only change then needs a reload, not a re-render ...
        (docroot / ".htaccess").write_text("RewriteEngine On\nRewriteRule ^other$ /target.txt\n")
        watcher.poll()
        time.sleep(1.05)
        watcher.poll()
        assert reloads == [{"confirm": True}] and refreshes == ["owner1"]
        # ... while a new access directive re-renders the account's vhosts.
        (docroot / ".htaccess").write_text("Require all denied\n")
        watcher.poll()
        time.sleep(1.05)
        watcher.poll()
        assert refreshes == ["owner1", "owner1"]
    finally:
        os.close(watcher.fd)


def test_watcher_rerenders_again_after_a_failed_rerender(isolated_db, tmp_path, monkeypatch):
    """Regression (review): the new access signature was recorded before the
    re-render ran, so when it failed the retry only reloaded and the vhost
    never received the translated rule."""
    docroot = _domain(tmp_path, monkeypatch)
    (docroot / ".htaccess").write_text("Require all denied\n")
    reloads, refreshes = [], []
    monkeypatch.setattr(htaccess.ols, "graceful_reload", lambda params: reloads.append(params) or {"config_valid": True})

    def failing_refresh(account):
        refreshes.append(account.username)
        raise RuntimeError("OLS config transaction failed")

    monkeypatch.setattr(htaccess.ols, "refresh_vhost", failing_refresh)
    watcher = htaccess.HtaccessWatcher()
    with pytest.raises(RuntimeError):
        watcher._apply(["example.test"])
    assert "example.test" not in watcher.access_signatures
    monkeypatch.setattr(htaccess.ols, "refresh_vhost", lambda account: refreshes.append(account.username))
    watcher._apply(["example.test"])
    assert refreshes == ["owner1", "owner1"] and reloads == []
    assert watcher.access_signatures["example.test"]


# --- Apache access directives -----------------------------------------------


@pytest.mark.parametrize("text", [
    "Require all denied\n",
    "Order deny,allow\nDeny from all\n",
    "order allow,deny\ndeny from all\n",
    "Order allow,deny\n",
    "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n  Order deny,allow\n  Deny from all\n</IfModule>\n",
])
def test_deny_all_forms_are_translated(text):
    rule, unsupported = htaccess.parse_access_directives(text)
    assert rule["mode"] == "deny"
    assert unsupported == []


def test_allow_from_ip_list_is_translated():
    rule, unsupported = htaccess.parse_access_directives(
        "Order Deny,Allow\nDeny from all\nAllow from 198.51.100.4 10.1\nAllow from 2001:db8::/32\n")
    assert rule == {"mode": "ip", "allow": ["198.51.100.4", "10.1.0.0/16", "2001:db8::/32"], "deny": ["*"]}
    assert unsupported == []
    rule, _ = htaccess.parse_access_directives("Require ip 192.0.2.0/24 203.0.113.9\n")
    assert rule == {"mode": "ip", "allow": ["192.0.2.0/24", "203.0.113.9"], "deny": ["*"]}


def test_block_list_forms_are_translated():
    rule, _ = htaccess.parse_access_directives("Order allow,deny\nAllow from all\nDeny from 203.0.113.9\n")
    assert rule == {"mode": "ip", "allow": ["*"], "deny": ["203.0.113.9"]}
    rule, _ = htaccess.parse_access_directives(
        "<RequireAll>\nRequire all granted\nRequire not ip 203.0.113.9\n</RequireAll>\n")
    assert rule == {"mode": "ip", "allow": ["*"], "deny": ["203.0.113.9"]}


def test_unexpressible_directives_are_reported_and_fail_closed():
    rule, unsupported = htaccess.parse_access_directives(
        "AuthType Basic\nAuthUserFile /x/.htpasswd\nRequire valid-user\n")
    assert rule == {"mode": "deny", "authentication": True}
    assert unsupported == [{"line": 3, "directive": "Require valid-user"}]
    rule, unsupported = htaccess.parse_access_directives("Deny from example.com\n")
    assert rule is None or rule["mode"] == "allow"
    assert unsupported == [{"line": 1, "directive": "Deny from example.com"}]


def test_per_file_access_blocks_are_reported_not_applied_to_the_directory():
    rule, unsupported = htaccess.parse_access_directives(
        '<FilesMatch "\\.(ini|log)$">\n  Require all denied\n</FilesMatch>\nRewriteEngine On\n')
    assert rule is None
    assert [issue["line"] for issue in unsupported] == [2]


def test_rewrite_only_htaccess_has_no_access_rule():
    assert htaccess.parse_access_directives("RewriteEngine On\nRewriteRule ^ - [F]\n") == (None, [])


def test_scan_finds_nested_rules_without_following_symlinks(tmp_path):
    root = tmp_path / "public_html"
    (root / "private" / "deeper").mkdir(parents=True)
    (root / "private" / ".htaccess").write_text("Require all denied\n")
    (root / "private" / "deeper" / ".htaccess").write_text("Require all granted\n")
    (root / "open").mkdir()
    (root / "open" / ".htaccess").write_text("Require all granted\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".htaccess").write_text("Require all denied\n")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "bad name").mkdir()
    (root / "bad name" / ".htaccess").write_text("Require all denied\n")
    scan = htaccess.scan_access_rules(str(root))
    contexts = htaccess.effective_access_contexts(scan)
    assert [(c["relative_path"], c["mode"]) for c in contexts] == [("private", "deny"), ("private/deeper", "allow")]
    assert scan["complete"] is True
    assert [issue["path"] for issue in scan["unsupported"]] == ["bad name/.htaccess"]


def test_access_status_reports_for_owned_domain(isolated_db, tmp_path, monkeypatch):
    docroot = _domain(tmp_path, monkeypatch)
    (docroot / ".htaccess").write_text("Order deny,allow\nDeny from all\nAllow from 198.51.100.4\n")
    result = htaccess.access_status({"username": "owner1", "domain": "example.test"})
    assert result["access_rules"] == [{"directory": "/", "mode": "ip", "allow": ["198.51.100.4"], "deny": ["*"]}]
    assert result["access_scan_complete"] is True and result["access_scan_reused_previous"] is False


# --- <Files>/<Limit> exceptions (round 1 web still_open) --------------------

ASSETS_ONLY = (
    "Order deny,allow\nDeny from all\n"
    '<FilesMatch "\\.(css|js|png|jpe?g|gif)$">\n  Allow from all\n</FilesMatch>\n'
)


def _contexts(tmp_path, files: dict) -> list[tuple]:
    root = tmp_path / "public_html"
    for relative, text in files.items():
        directory = root / relative if relative else root
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".htaccess").write_text(text)
    scan = htaccess.scan_access_rules(str(root))
    return [(c["relative_path"], c["mode"], c.get("allow"), c.get("deny"))
            for c in htaccess.effective_access_contexts(scan)]


def test_files_allow_exception_keeps_those_files_reachable(tmp_path):
    """Hard rule: a top-level deny with <FilesMatch> 'Allow from all'
    exceptions used to become a whole-directory 403, breaking site assets
    on upgrade. Per-file access cannot be expressed in an OLS directory
    context, so the directory keeps what the exceptions allow."""
    parsed = htaccess.parse_access_file(ASSETS_ONLY)
    assert parsed["rule"] == {"mode": "deny"}
    assert parsed["grants"] == [{"mode": "allow"}]
    assert [issue["line"] for issue in parsed["unsupported"]] == [4, 0]
    assert parsed["unsupported"][-1]["directive"].startswith("access rules inside <Files>")
    assert _contexts(tmp_path, {"assets": ASSETS_ONLY}) == []


def test_files_exception_reaches_denied_subdirectories_and_survives_a_denying_parent(tmp_path):
    """Apache applies a .htaccess <Files> section to the whole subtree, after
    the directory sections, so neither a deeper nor an enclosing deny may
    refuse the files it allows."""
    assert _contexts(tmp_path, {"images": ASSETS_ONLY, "images/private": "Require all denied\n"}) == []
    contexts = _contexts(tmp_path / "second", {
        "private": "Require all denied\n",
        "private/public": "<Files index.html>\n  Require all granted\n</Files>\n",
    })
    assert contexts == [("private", "deny", None, None), ("private/public", "allow", None, None)]


def test_report_names_every_directory_a_parent_grant_relaxed(tmp_path):
    """A root <FilesMatch> grant also lifts a deeper directory's own
    restriction; that directory must be reported as not enforced, not only
    the .htaccess holding the grant."""
    root = tmp_path / "public_html"
    (root / "wp-admin").mkdir(parents=True)
    (root / ".htaccess").write_text('<FilesMatch "\\.(css|js)$">\n  Require all granted\n</FilesMatch>\n')
    (root / "wp-admin" / ".htaccess").write_text("Require ip 192.0.2.1\n")
    (root / "kept").mkdir()
    (root / "kept" / ".htaccess").write_text("Require all denied\n")
    (root / "kept" / "open").mkdir()
    (root / "kept" / "open" / ".htaccess").write_text("Require all granted\n")
    report = htaccess._public_access_report(htaccess.scan_access_rules(str(root)))
    relaxed = [issue["path"] for issue in report["unsupported_access_directives"]
               if issue["directive"] == htaccess._RELAXED_NOTE]
    assert relaxed == ["kept/.htaccess", "wp-admin/.htaccess"]
    # Both restrictions are lifted, so no context is rendered at all.
    assert report["access_rules"] == []


def test_ip_exceptions_widen_the_directory_rule_instead_of_opening_it(tmp_path):
    contexts = _contexts(tmp_path, {
        "admin": "Require ip 10.0.0.0/8\n<Files status.php>\n  Require ip 192.0.2.1\n</Files>\n",
    })
    assert contexts == [("admin", "ip", ["10.0.0.0/8", "192.0.2.1"], ["*"])]


def test_limit_section_joins_the_surrounding_directives(tmp_path):
    """<Limit GET> directives add to the top-level ones for that method:
    'Deny from all' + '<Limit GET> Allow from X' admits X (for GET)."""
    parsed = htaccess.parse_access_file(
        "Order deny,allow\nDeny from all\n<Limit GET>\nAllow from 192.0.2.7\n</Limit>\n")
    assert parsed["grants"] == [{"mode": "ip", "allow": ["192.0.2.7"], "deny": ["*"]}]
    assert _contexts(tmp_path, {"api": "Order deny,allow\nDeny from all\n<Limit GET>\nAllow from 192.0.2.7\n</Limit>\n"}) == [
        ("api", "ip", ["192.0.2.7"], ["*"])]


def test_sections_that_only_restrict_are_reported_not_applied(tmp_path):
    parsed = htaccess.parse_access_file('<Files ".env">\nRequire all denied\n</Files>\nRequire ip 10.0.0.0/8\n')
    assert parsed["rule"] == {"mode": "ip", "allow": ["10.0.0.0/8"], "deny": ["*"]}
    assert parsed["grants"] == []
    assert [issue["line"] for issue in parsed["unsupported"]] == [2]


def test_allow_in_an_inexpressible_directory_relaxes_the_nearest_context(tmp_path):
    contexts = _contexts(tmp_path, {"private": "Require all denied\n", "private/my files": "Require all granted\n"})
    assert contexts == []


# --- Last complete scan (round 1 web still_open) -----------------------------


def _bounded_tree(tmp_path):
    root = tmp_path / "home" / "owner1" / "public_html"
    for name in ("a", "m", "z"):
        (root / name).mkdir(parents=True)
        (root / name / ".htaccess").write_text("Require all denied\n")
    return root


def test_bounded_scan_keeps_rules_found_by_the_last_complete_scan(isolated_db, tmp_path, monkeypatch):
    """A scan that runs out of budget used to drop the rules it did not
    reach, so enforcement changed between renders."""
    root = _bounded_tree(tmp_path)
    first = htaccess.account_access_scans(7, [str(root)])[str(root)]
    assert first["complete"] is True and set(first["rules"]) == {"a", "m", "z"}

    monkeypatch.setattr(htaccess, "ACCESS_SCAN_MAX_DIRECTORIES", 2)
    bounded = htaccess.account_access_scans(7, [str(root)])[str(root)]
    assert bounded["complete"] is False
    assert set(bounded["rules"]) == {"a", "m", "z"}
    report = htaccess._public_access_report(bounded)
    assert report["access_scan_complete"] is False and report["access_scan_reused_previous"] is True
    assert [rule["directory"] for rule in report["access_rules"]] == ["/a", "/m", "/z"]


def test_complete_scan_replaces_kept_rules_and_deleted_directories_are_not_reused(isolated_db, tmp_path, monkeypatch):
    root = _bounded_tree(tmp_path)
    htaccess.account_access_scans(7, [str(root)])
    (root / "z" / ".htaccess").unlink()
    assert set(htaccess.account_access_scans(7, [str(root)])[str(root)]["rules"]) == {"a", "m"}
    monkeypatch.setattr(htaccess, "ACCESS_SCAN_MAX_DIRECTORIES", 2)
    assert set(htaccess.account_access_scans(7, [str(root)])[str(root)]["rules"]) == {"a", "m"}
    monkeypatch.setattr(htaccess, "ACCESS_SCAN_MAX_DIRECTORIES", 20000)
    htaccess.account_access_scans(7, [str(root)])
    import shutil
    shutil.rmtree(root / "m")
    monkeypatch.setattr(htaccess, "ACCESS_SCAN_MAX_DIRECTORIES", 2)
    assert set(htaccess.account_access_scans(7, [str(root)])[str(root)]["rules"]) == {"a"}


def test_kept_scans_are_private_per_account_and_forgotten(isolated_db, tmp_path, monkeypatch):
    root = _bounded_tree(tmp_path)
    htaccess.account_access_scans(7, [str(root)])
    path = htaccess._scan_cache_path(7)
    assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
    monkeypatch.setattr(htaccess, "ACCESS_SCAN_MAX_DIRECTORIES", 2)
    # Another account's bounded scan of the same path has nothing to reuse.
    assert set(htaccess.account_access_scans(8, [str(root)])[str(root)]["rules"]) == {"a"}
    # Pruning drops document roots the account no longer has.
    other = tmp_path / "other"
    other.mkdir()
    htaccess.account_access_scans(7, [str(other)], prune=True)
    assert set(htaccess._load_scan_cache(7)) == {str(other.resolve())}
    htaccess.forget_account_scans(7)
    assert not path.exists()
