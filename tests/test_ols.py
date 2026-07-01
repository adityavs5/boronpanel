import pytest

from daemon import ols
from shared.models import Account


def make_account(**overrides):
    defaults = dict(username="demo1", status="active", uid=2000, gid=2000, php_version="8.3")
    defaults.update(overrides)
    account = Account(**defaults)
    account.id = 1
    return account


def test_render_vhost_conf_active_uses_real_docroot():
    account = make_account()
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=False)
    assert "docRoot                   /home/demo1/public_html" in content
    assert "extUser                 demo1" in content
    assert "extGroup                demo1" in content
    assert "/usr/local/lsws/lsphp83/bin/lsphp" in content
    assert "_suspended" not in content


def test_render_vhost_conf_suspended_points_at_suspended_page():
    account = make_account()
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=True)
    assert ols.settings.suspended_page_root in content
    assert "RewriteRule ^(.*)$ /index.html [L]" in content
    # PHP external app stays defined even while suspended -- unsuspend must
    # be a pure metadata flip, not a config rebuild from scratch.
    assert "extUser                 demo1" in content


def test_render_vhost_conf_php_version_selects_correct_app_name():
    account = make_account(php_version="8.1")
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=False)
    assert "demo1_php81" in content
    assert "/usr/local/lsws/lsphp81/bin/lsphp" in content


def test_render_httpd_config_empty_vhosts_has_no_virtualhost_block(monkeypatch):
    # Explicit, not incidental: this test's whole point is "no vhosts in ->
    # no virtualHost block out", so it must not depend on whatever
    # webmail_hostname happens to be set to in this environment's real
    # /etc/forgehost/forgehost.toml (shared.config.settings is a
    # module-level singleton loaded from the live system config, not
    # reset between tests) -- caught when Phase 2 feature 3 configured a
    # real webmail_hostname on this deployment and this test started
    # failing for a reason that had nothing to do with what it's testing.
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    content = ols.render_httpd_config([])
    assert "virtualHost" not in content
    assert "listener HTTP{" in content


def test_render_httpd_config_includes_each_vhost_and_its_domains():
    vhosts = [
        {"name": "demo1", "home_dir": "/home/demo1", "domains": ["demo1.example", "www.demo1.example"]},
        {"name": "demo2", "home_dir": "/home/demo2", "domains": ["demo2.example"]},
    ]
    content = ols.render_httpd_config(vhosts)
    assert "virtualHost demo1{" in content
    assert "virtualHost demo2{" in content
    assert "map                      demo1 demo1.example, www.demo1.example" in content
    assert "map                      demo2 demo2.example" in content


def test_php_app_name_and_lsphp_path_helpers():
    assert ols._php_app_name("demo1", "8.3") == "demo1_php83"
    assert ols._lsphp_path("8.1") == "/usr/local/lsws/lsphp81/bin/lsphp"


def test_static_precheck_rejects_unbalanced_braces():
    assert not ols._static_precheck("context / { allowBrowse 1").ok


def test_static_precheck_rejects_empty():
    assert not ols._static_precheck("   ").ok


def test_static_precheck_accepts_balanced():
    assert ols._static_precheck("context / { allowBrowse 1 }").ok


def test_render_httpd_config_includes_webmail_block_when_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "webmail.example.com")
    content = ols.render_httpd_config([])
    assert "virtualHost roundcube{" in content
    assert "extProcessor roundcube_php{" in content
    assert "map                      roundcube webmail.example.com" in content


def test_render_httpd_config_omits_webmail_block_when_not_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    content = ols.render_httpd_config([])
    assert "virtualHost roundcube{" not in content
    assert "extProcessor roundcube_php{" not in content


def test_render_webmail_vhost_conf_uses_configured_docroot(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_docroot", "/var/lib/roundcube/public_html")
    content = ols.render_webmail_vhost_conf("/etc/forgehost/ssl/default.key", "/etc/forgehost/ssl/default.crt")
    assert "docRoot                   /var/lib/roundcube/public_html" in content
    assert "lsapi:roundcube_php php" in content


def test_bootstrap_webmail_requires_hostname_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    with pytest.raises(RuntimeError):
        ols.bootstrap_webmail()
