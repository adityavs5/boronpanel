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
